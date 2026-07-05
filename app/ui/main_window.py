"""
MainWindow – the primary application window.

Layout
------
┌──────────────────────────────────────────────────┐
│  Menu:  File  |  Playback  |  View  |  Help      │
├──────────────────────────┬───────────────────────┤
│                          │                       │
│    PlaylistWidget        │    SearchPanel        │
│    (current queue)       │    (online search)    │
│                          │                       │
├──────────────────────────┴───────────────────────┤
│ ♫ Title - Artist    ═══●═══ 3:45 / 5:30   🔊━━━●━│
│  [⏮] [▶/⏸] [⏭] [⏹]       Mode: 🔁               │
└──────────────────────────────────────────────────┘

Signals are used to communicate with core modules (AudioEngine,
PlaylistManager, MusicScanner) – the MainWindow never calls core
methods directly except to wire up initial connections.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDir, Qt, Slot, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
    QSystemTrayIcon,
    QMenu,
)

from app.app_info import about_text, about_title, window_title
from app.core.audio_engine import AudioEngine, PlayState
from app.core.favorites_manager import FavoritesManager
from app.core.music_scanner import MusicScanner, SUPPORTED_EXTENSIONS
from app.core.playlist_manager import PlaylistManager
from app.models.song import PlayMode, Song
from app.paths import default_download_dir
from app.services.audio_metadata import extract_cover_art, extract_song_metadata
from app.ui.favorites_window import FavoritesWindow
from app.ui.lyrics_window import LrcParser, LyricsWindow
from app.ui.widgets import PlaylistWidget, SearchPanel, SongInfoDialog, Settings, SettingsDialog
from app.services.music_provider import MusicProvider
import app.services.config as cfg

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window – orchestrates UI and core module interaction."""

    # ------------------------------------------------------------------
    # Constants
    # ------------------------------------------------------------------

    DEFAULT_WIDTH = 1100
    DEFAULT_HEIGHT = 700
    MIN_WIDTH = 800
    MIN_HEIGHT = 500
    SONG_INFO_MAX_WIDTH = 320
    PROGRESS_SLIDER_MIN_WIDTH = 180

    # Play mode display symbols (Chinese labels).
    _PLAY_MODE_SYMBOLS = {
        PlayMode.SEQUENTIAL: "▶ 顺序播放",
        PlayMode.LOOP: "🔁 列表循环",
        PlayMode.SINGLE_LOOP: "🔂 单曲循环",
    }

    def __init__(self, playlist_manager: PlaylistManager,
                 favorites_manager: FavoritesManager,
                 audio_engine: AudioEngine,
                 music_scanner: MusicScanner,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._playlist_manager = playlist_manager
        self._favorites_manager = favorites_manager
        self._audio_engine = audio_engine
        self._music_scanner = music_scanner

        # -- Track whether we are updating the slider programmatically --
        self._slider_dragging: bool = False

        # -- Collect songs from the most recent scan so the UI can either
        #    replace or append the finished scan result in one step.
        self._scanned_songs: list[Song] = []
        self._scan_append_mode: bool = False
        self._pending_scan_requests: list[tuple[str, bool]] = []
        self._last_position_ms: int = 0
        self._restore_selection_only: bool = False
        self._song_info_full_text: str = "未播放"

        # ---- Build UI ----
        self._setup_window()
        self._create_menu_bar()

        # Central wrapper: [splitter (playlist | search), playback bar]
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._create_central_area(root_layout)
        self._create_playback_bar(root_layout)
        self._create_status_bar()

        # ---- Create lyrics window (hidden by default) ----
        self._lyrics_window = LyricsWindow()
        self._lyrics_window.hide()
        self._favorites_window = FavoritesWindow(self)

        # ---- Search provider ----
        self._search_provider = MusicProvider(self)
        self._search_panel.set_search_provider(self._search_provider)
        self._download_dir: str = cfg.get("download_dir", str(default_download_dir()))
        self._search_panel.set_download_dir(self._download_dir)
        self._active_downloads: int = 0

        # ---- Global shortcuts ----
        self._setup_shortcuts()

        # ---- Connect signals ----
        self._connect_signals()

        # ---- Apply persisted settings ----
        self._apply_saved_settings()

        # ---- System tray ----
        self._setup_tray_icon()

    # ================================================================
    # UI Construction
    # ================================================================

    def _setup_window(self) -> None:
        """Configure the main window geometry and properties."""
        self.setWindowTitle(window_title)
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.resize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self.setAcceptDrops(True)

    # ---------------------------------------------------------------
    # Menu bar
    # ---------------------------------------------------------------

    def _create_menu_bar(self) -> None:
        menu_bar = self.menuBar()

        # -- File menu --
        file_menu = menu_bar.addMenu("文件(&F)")

        open_action = QAction("打开目录(&O)…", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self._on_open_directory)
        file_menu.addAction(open_action)

        open_file_action = QAction("打开文件(&F)…", self)
        open_file_action.setShortcut(QKeySequence("Ctrl+F"))
        open_file_action.triggered.connect(self._on_open_files)
        file_menu.addAction(open_file_action)

        file_menu.addSeparator()

        import_action = QAction("导入播放列表(&I)…", self)
        import_action.setShortcut(QKeySequence("Ctrl+I"))
        import_action.triggered.connect(self._on_import_playlist)
        file_menu.addAction(import_action)

        export_action = QAction("导出播放列表(&E)…", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self._on_export_playlist)
        file_menu.addAction(export_action)

        favorites_action = QAction("打开收藏(&V)", self)
        favorites_action.setShortcut(QKeySequence("Ctrl+D"))
        favorites_action.triggered.connect(self._on_open_favorites)
        file_menu.addAction(favorites_action)

        open_dl_action = QAction("打开下载目录(&L)", self)
        open_dl_action.triggered.connect(self._on_open_download_dir)
        file_menu.addAction(open_dl_action)

        file_menu.addSeparator()

        settings_action = QAction("设置(&S)…", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._on_open_settings)
        file_menu.addAction(settings_action)

        file_menu.addSeparator()

        exit_action = QAction("退出(&X)", self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(QApplication.instance().quit)
        file_menu.addAction(exit_action)

        # -- Playback menu --
        playback_menu = menu_bar.addMenu("播放(&P)")

        self._mode_action = QAction("播放模式", self)
        self._mode_action.triggered.connect(self._on_cycle_play_mode)
        playback_menu.addAction(self._mode_action)
        self._update_play_mode_action()

        playback_menu.addSeparator()

        prev_action = QAction("上一首(&R)", self)
        prev_action.setShortcut(QKeySequence("Ctrl+Left"))
        prev_action.triggered.connect(self._on_previous)
        playback_menu.addAction(prev_action)

        next_action = QAction("下一首(&N)", self)
        next_action.setShortcut(QKeySequence("Ctrl+Right"))
        next_action.triggered.connect(self._on_next)
        playback_menu.addAction(next_action)

        playback_menu.addSeparator()

        # -- View menu --
        view_menu = menu_bar.addMenu("视图(&V)")

        self._show_lyrics_action = QAction("显示歌词窗口(&L)", self)
        self._show_lyrics_action.setCheckable(True)
        self._show_lyrics_action.setChecked(False)
        self._show_lyrics_action.setShortcut(QKeySequence("Ctrl+L"))
        self._show_lyrics_action.triggered.connect(self._on_toggle_lyrics)
        view_menu.addAction(self._show_lyrics_action)

        # -- Help menu --
        help_menu = menu_bar.addMenu("帮助(&H)")

        about_action = QAction(f"{about_title}(&A)", self)
        about_action.triggered.connect(self._on_show_about)
        help_menu.addAction(about_action)

    # ---------------------------------------------------------------
    # Central area – playlist + search
    # ---------------------------------------------------------------

    def _create_central_area(self, parent_layout: QVBoxLayout) -> None:
        """Build the splitter layout with PlaylistWidget and SearchPanel."""
        # Outer margin wrapper for the splitter area.
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 4, 8, 4)
        content_layout.setSpacing(4)

        # -- Splitter --
        splitter = QSplitter(Qt.Horizontal)

        # Left: Playlist
        self._playlist_widget = PlaylistWidget()
        splitter.addWidget(self._playlist_widget)

        # Right: Search
        search_scroll = QScrollArea()
        search_scroll.setWidgetResizable(True)
        search_scroll.setFrameShape(QFrame.NoFrame)
        self._search_panel = SearchPanel()
        search_scroll.setWidget(self._search_panel)
        splitter.addWidget(search_scroll)

        # Set reasonable initial sizes.
        splitter.setSizes([int(self.DEFAULT_WIDTH * 0.6),
                           int(self.DEFAULT_WIDTH * 0.4)])

        content_layout.addWidget(splitter, 1)
        parent_layout.addWidget(content, 1)

    # ---------------------------------------------------------------
    # Playback control bar
    # ---------------------------------------------------------------

    def _create_playback_bar(self, parent_layout: QVBoxLayout) -> None:
        """Build the bottom playback controls bar."""
        bar = QWidget()
        bar.setObjectName("playbackBar")
        bar.setStyleSheet("""
            #playbackBar {
                background-color: #181825;
                border-top: 1px solid #313244;
            }
        """)

        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)

        # -- Row 0: Song info + Progress slider --
        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)

        self._cover_label = QLabel("♪")
        self._cover_label.setAlignment(Qt.AlignCenter)
        self._cover_label.setFixedSize(64, 64)
        self._cover_label.setStyleSheet("""
            QLabel {
                background-color: #313244;
                border: 1px solid #45475a;
                border-radius: 6px;
                color: #cdd6f4;
                font-size: 22px;
                font-weight: 700;
            }
        """)
        progress_row.addWidget(self._cover_label)

        # Song info
        self._song_info_label = QLabel("未播放")
        self._song_info_label.setObjectName("titleLabel")
        self._song_info_label.setMaximumWidth(self.SONG_INFO_MAX_WIDTH)
        self._song_info_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        progress_row.addWidget(self._song_info_label, 1)

        # Time labels
        self._time_current = QLabel("0:00")
        self._time_current.setObjectName("timeLabel")
        progress_row.addWidget(self._time_current)

        # Progress slider
        self._progress_slider = QSlider(Qt.Horizontal)
        self._progress_slider.setRange(0, 0)  # will be updated when duration is known
        self._progress_slider.setValue(0)
        self._progress_slider.setMinimumWidth(self.PROGRESS_SLIDER_MIN_WIDTH)
        self._progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self._progress_slider.sliderReleased.connect(self._on_slider_released)
        self._progress_slider.sliderMoved.connect(self._on_slider_moved)
        progress_row.addWidget(self._progress_slider, 3)

        self._time_total = QLabel("0:00")
        self._time_total.setObjectName("timeLabel")
        progress_row.addWidget(self._time_total)

        # Volume
        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setObjectName("volumeSlider")
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(100)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        progress_row.addWidget(self._volume_slider)

        self._volume_label = QLabel("80%")
        self._volume_label.setObjectName("timeLabel")
        self._volume_label.setFixedWidth(36)
        progress_row.addWidget(self._volume_label)

        self._volume_muted: bool = False
        self._volume_before_mute: int = 80
        self._mute_btn = QPushButton("🔊")
        self._mute_btn.setFixedSize(36, 30)
        self._mute_btn.setStyleSheet("background: transparent; border: none; font-size: 16px;")
        self._mute_btn.clicked.connect(self._on_toggle_mute)
        progress_row.addWidget(self._mute_btn)

        layout.addLayout(progress_row)

        # -- Row 1: Transport buttons + Play mode --
        transport_row = QHBoxLayout()
        transport_row.setSpacing(6)

        # Previous
        self._prev_btn = QPushButton("⏮")
        self._prev_btn.setObjectName("transportBtn")
        self._prev_btn.clicked.connect(self._on_previous)
        transport_row.addWidget(self._prev_btn)

        # Play/Pause
        self._play_btn = QPushButton("▶")
        self._play_btn.setObjectName("transportBtn")
        self._play_btn.clicked.connect(self._on_play_pause)
        transport_row.addWidget(self._play_btn)

        # Stop
        self._stop_btn = QPushButton("⏹")
        self._stop_btn.setObjectName("transportBtn")
        self._stop_btn.clicked.connect(self._on_stop)
        transport_row.addWidget(self._stop_btn)

        # Next
        self._next_btn = QPushButton("⏭")
        self._next_btn.setObjectName("transportBtn")
        self._next_btn.clicked.connect(self._on_next)
        transport_row.addWidget(self._next_btn)

        # Spacer
        transport_row.addStretch(1)

        # Play mode button
        self._mode_btn = QPushButton()
        self._mode_btn.clicked.connect(self._on_cycle_play_mode)
        # Init text from the current play mode, since the signal may not
        # fire on startup (the mode hasn't *changed* yet).
        self._refresh_mode_btn()
        transport_row.addWidget(self._mode_btn)

        layout.addLayout(transport_row)

        parent_layout.addWidget(bar)

    def _create_status_bar(self) -> None:
        """Add a status bar for scanning progress and messages."""
        status = QStatusBar()
        status.setStyleSheet("""
            QStatusBar {
                background-color: #181825;
                border-top: 1px solid #313244;
                border-bottom: 1px solid #313244;
                font-size: 11px;
                color: #6c7086;
            }
        """)

        self._scan_progress = QProgressBar()
        self._scan_progress.setRange(0, 100)
        self._scan_progress.setValue(0)
        self._scan_progress.setFixedWidth(160)
        self._scan_progress.hide()
        status.addPermanentWidget(self._scan_progress)

        self._status_label = QLabel("就绪")
        status.addWidget(self._status_label)

        self.setStatusBar(status)

    # ================================================================
    # Signal connections
    # ================================================================

    def _connect_signals(self) -> None:
        """Wire up all core module signals to UI slots."""

        # -- PlaylistManager -> PlaylistWidget --
        self._playlist_manager.playlist_loaded.connect(self._on_playlist_loaded)
        self._playlist_manager.song_added.connect(self._on_song_added)
        self._playlist_manager.song_removed.connect(self._on_song_removed)
        self._playlist_manager.current_song_changed.connect(self._on_current_song_changed)
        self._playlist_manager.current_index_changed.connect(self._on_current_index_changed)
        self._playlist_manager.play_mode_changed.connect(self._on_play_mode_changed)

        # -- PlaylistWidget -> PlaylistManager --
        self._playlist_widget.play_requested.connect(self._on_play_requested)
        self._playlist_widget.remove_requested.connect(self._on_remove_requested)
        self._playlist_widget.batch_remove_requested.connect(self._on_batch_remove_requested)
        self._playlist_widget.clear_requested.connect(self._on_clear_requested)
        self._playlist_widget.order_changed.connect(self._on_order_changed)
        self._playlist_widget.info_requested.connect(self._on_info_requested)
        self._playlist_widget.favorite_add_requested.connect(self._on_favorite_add_requested)
        self._playlist_widget.favorite_remove_requested.connect(self._on_favorite_remove_requested)

        # -- FavoritesManager -> UI --
        self._favorites_manager.favorites_loaded.connect(self._on_favorites_updated)
        self._favorites_manager.favorites_changed.connect(self._on_favorites_updated)

        # -- Favorites window --
        self._favorites_window.play_requested.connect(self._on_favorites_play_requested)
        self._favorites_window.remove_favorite_requested.connect(
            self._on_favorites_window_remove_requested
        )
        self._favorites_window.order_changed.connect(self._on_favorites_reordered)
        self._lyrics_window.visibility_changed.connect(self._on_lyrics_window_visibility_changed)

        # -- AudioEngine -> UI --
        self._audio_engine.position_changed.connect(self._on_position_changed)
        self._audio_engine.duration_changed.connect(self._on_duration_changed)
        self._audio_engine.state_changed.connect(self._on_state_changed)
        self._audio_engine.track_finished.connect(self._on_track_finished)
        self._audio_engine.error_occurred.connect(self._on_audio_error)

        # -- MusicScanner -> UI --
        self._music_scanner.scan_started.connect(self._on_scan_started)
        self._music_scanner.progress.connect(self._on_scan_progress)
        self._music_scanner.song_found.connect(self._on_scan_song_found)
        self._music_scanner.scan_finished.connect(self._on_scan_finished)
        self._music_scanner.error_occurred.connect(self._on_scan_error)

        # -- SearchPanel --
        self._search_panel.add_to_playlist_requested.connect(self._on_search_add_to_playlist)
        self._search_panel.download_requested.connect(self._on_search_download)

        # -- Search provider --
        self._search_provider.results_ready.connect(self._search_panel.display_results)
        self._search_provider.search_error.connect(self._on_search_error)
        self._search_provider.download_ready.connect(self._on_download_ready)
        self._search_provider.download_error.connect(self._on_download_error)

    # ================================================================
    # Slots – Playlist
    # ================================================================

    @Slot()
    def _on_playlist_loaded(self) -> None:
        """Refresh the playlist widget when the playlist is loaded."""
        songs = self._playlist_manager.playlist
        self._playlist_widget.load_songs(songs)
        self._playlist_widget.set_favorite_paths(self._favorites_manager.favorite_paths)
        session_state = self._normalize_session_state(self._playlist_manager.session_state)
        self._restore_selection_only = (
            bool(session_state.get("current_file_path"))
            and self._playlist_manager.current_index >= 0
        )
        self._playlist_manager.clear_session_state()
        if not songs:
            self._reset_cover_art()
        # Preserve the current song label — _on_playlist_loaded is also
        # triggered by drag-and-drop reorder, and blowing away the info
        # text to "未播放" would be wrong while a track is still active.
        current_song = self._playlist_manager.get_current_song()
        self._update_song_info(current_song)

    @Slot(int)
    def _on_song_added(self, index: int) -> None:
        """A song was added at *index* – refresh view."""
        songs = self._playlist_manager.playlist
        self._playlist_widget.load_songs(songs)
        self._playlist_widget.set_favorite_paths(self._favorites_manager.favorite_paths)

    @Slot(int, object)
    def _on_song_removed(self, index: int, song: object) -> None:
        """A song was removed – refresh view."""
        songs = self._playlist_manager.playlist
        self._playlist_widget.load_songs(songs)
        self._playlist_widget.set_favorite_paths(self._favorites_manager.favorite_paths)

    @Slot(object)
    def _on_current_song_changed(self, song: Optional[Song]) -> None:
        """The current song changed – update UI and load into audio engine.

        Preserves pause state so that removing / skipping the current
        song while paused loads the new file but stays paused.
        Explicit user requests (double-click, "Play" menu) always call
        ``stop()`` first, so ``was_paused`` is ``False`` there and
        playback starts normally.
        """
        if song is not None and song.file_path:
            self._update_cover_art(song)
            if self._restore_selection_only:
                self._restore_selection_only = False
                self._progress_slider.setRange(0, 0)
                self._progress_slider.setValue(0)
                self._time_current.setText("0:00")
                self._time_total.setText("0:00")
                self._last_position_ms = 0
            else:
                was_paused = (self._audio_engine.state == PlayState.PAUSED)
                logger.info("Playing: %s - %s", song.artist, song.title)
                self._load_lyrics_for(song)
                self._audio_engine.play(song.file_path)
                if was_paused:
                    self._audio_engine.pause()
        else:
            # Song removed / playlist cleared → stop playback.
            self._audio_engine.stop()
            self._reset_cover_art()

        self._update_song_info(song)

    @Slot(int)
    def _on_current_index_changed(self, index: int) -> None:
        """Highlight the current row in the playlist."""
        self._playlist_widget.highlight_row(index)

    @Slot(object)
    def _on_play_mode_changed(self, mode: PlayMode) -> None:
        """Update play mode display."""
        self._update_play_mode_action()
        self._refresh_mode_btn()

    # ---------------------------------------------------------------
    # Slots – PlaylistWidget actions
    # ---------------------------------------------------------------

    @Slot(int)
    def _on_play_requested(self, index: int) -> None:
        """User double-clicked or chose 'Play' – jump to that song."""
        if self._audio_engine.state != PlayState.STOPPED:
            # Explicit track changes should interrupt the current playback
            # immediately, including songs appended from online search.
            self._audio_engine.stop()
        self._playlist_manager.go_to(index)

    @Slot(int)
    def _on_remove_requested(self, index: int) -> None:
        """Remove the song at *index* from the playlist."""
        self._playlist_manager.remove_song(index)

    @Slot(list)
    def _on_batch_remove_requested(self, indices: list[int]) -> None:
        """Remove multiple songs from the playlist.

        Indices are processed in descending order so that earlier
        removals do not shift the positions of later ones.
        """
        for index in sorted(indices, reverse=True):
            self._playlist_manager.remove_song(index)

    @Slot()
    def _on_clear_requested(self) -> None:
        """Clear the entire playlist."""
        self._playlist_manager.clear()

    @Slot(list)
    def _on_order_changed(self, songs: list[Song]) -> None:
        """Drag-and-drop reorder: update queue ordering without restarting playback."""
        self._playlist_manager.reorder_playlist(songs)

    @Slot(int)
    def _on_info_requested(self, index: int) -> None:
        """Show the SongInfoDialog for the song at *index*."""
        songs = self._playlist_manager.playlist
        if 0 <= index < len(songs):
            dialog = SongInfoDialog(songs[index], self)
            dialog.exec()

    @Slot(int)
    def _on_favorite_add_requested(self, index: int) -> None:
        """Add the selected playlist song to favorites."""
        songs = self._playlist_manager.playlist
        if not 0 <= index < len(songs):
            return
        song = songs[index]
        if self._favorites_manager.add_favorite(song):
            self._favorites_manager.save_favorites()
            self._status_label.setText(f"已收藏: {song.title}")
        else:
            self._status_label.setText(f"无法收藏或已存在: {song.title}")

    @Slot(int)
    def _on_favorite_remove_requested(self, index: int) -> None:
        """Remove the selected playlist song from favorites."""
        songs = self._playlist_manager.playlist
        if not 0 <= index < len(songs):
            return
        song = songs[index]
        if self._favorites_manager.remove_favorite_by_path(song.file_path):
            self._favorites_manager.save_favorites()
            self._status_label.setText(f"已取消收藏: {song.title}")

    @Slot()
    def _on_favorites_updated(self) -> None:
        """Refresh favorite markers and keep the favorites window in sync."""
        self._playlist_widget.set_favorite_paths(self._favorites_manager.favorite_paths)
        self._favorites_window.load_songs(self._favorites_manager.favorites)

    @Slot(int)
    def _on_favorites_play_requested(self, index: int) -> None:
        """Load favorites into the main player and start from *index*."""
        favorites = self._favorites_manager.favorites
        if not 0 <= index < len(favorites):
            return
        if self._audio_engine.state != PlayState.STOPPED:
            self._audio_engine.stop()
        self._playlist_manager.load_playlist(favorites, start_index=index)

    @Slot(int)
    def _on_favorites_window_remove_requested(self, index: int) -> None:
        """Remove the selected song from the favorites window only."""
        favorites = self._favorites_manager.favorites
        if not 0 <= index < len(favorites):
            return
        song = favorites[index]
        if self._favorites_manager.remove_favorite_by_path(song.file_path):
            self._favorites_manager.save_favorites()
            self._status_label.setText(f"已取消收藏: {song.title}")

    @Slot(list)
    def _on_favorites_reordered(self, songs: list[Song]) -> None:
        """Persist drag-and-drop ordering from the favorites window."""
        self._favorites_manager.reorder_favorites(songs)
        self._favorites_manager.save_favorites()

    # ================================================================
    # Slots – Audio Engine
    # ================================================================

    @Slot(int)
    def _on_position_changed(self, position_ms: int) -> None:
        """Update progress slider and time label."""
        self._last_position_ms = position_ms
        if not self._slider_dragging:
            self._progress_slider.setValue(position_ms)
        self._time_current.setText(self._format_time(position_ms))

        # Sync lyrics window.
        if self._lyrics_window.isVisible():
            self._lyrics_window.sync_position(position_ms)

    @Slot(int)
    def _on_duration_changed(self, duration_ms: int) -> None:
        """Update slider range and total time label."""
        self._progress_slider.setRange(0, max(1, duration_ms))
        self._time_total.setText(self._format_time(duration_ms))

    @Slot(int)
    def _on_state_changed(self, state_int: int) -> None:
        """Update play/pause button icon based on state."""
        state = PlayState(state_int)
        if state == PlayState.PLAYING:
            self._play_btn.setText("⏸")
        elif state == PlayState.PAUSED:
            self._play_btn.setText("▶")
        else:  # STOPPED
            self._play_btn.setText("▶")
            self._progress_slider.setValue(0)
            self._time_current.setText("0:00")
            self._last_position_ms = 0
        self._update_tray_play_pause()

    @Slot(str)
    def _on_audio_error(self, message: str) -> None:
        """Display audio engine errors in the status bar."""
        self._status_label.setText(f"⚠ 播放错误: {message}")
        logger.error("Audio error: %s", message)

    @Slot()
    def _on_track_finished(self) -> None:
        """The current track ended naturally – advance to the next song."""
        self._playlist_manager.next()

    # ================================================================
    # Slots – Transport controls
    # ================================================================

    @Slot()
    def _on_play_pause(self) -> None:
        """Toggle between play and pause."""
        state = self._audio_engine.state
        if state == PlayState.PLAYING:
            self._audio_engine.pause()
        elif state == PlayState.PAUSED:
            self._audio_engine.resume()
        else:
            # Stopped – play from the current playlist selection.
            song = self._playlist_manager.get_current_song()
            if song is not None:
                self._audio_engine.play(song.file_path)
            else:
                # Try to start from the first song.
                songs = self._playlist_manager.playlist
                if songs:
                    self._playlist_manager.go_to(0)

    @Slot()
    def _on_stop(self) -> None:
        """Stop playback."""
        self._audio_engine.stop()

    @Slot()
    def _on_previous(self) -> None:
        """Go to the previous track."""
        self._playlist_manager.previous()

    @Slot()
    def _on_next(self) -> None:
        """Go to the next track."""
        self._playlist_manager.next()

    # ================================================================
    # Slots – Progress slider
    # ================================================================

    @Slot()
    def _on_slider_pressed(self) -> None:
        """User starts dragging the progress slider."""
        self._slider_dragging = True

    @Slot()
    def _on_slider_released(self) -> None:
        """User releases the progress slider – seek to the chosen position."""
        self._slider_dragging = False
        pos = self._progress_slider.value()
        max_pos = self._progress_slider.maximum()
        # Clamp to at least 1 s before the end so that a seek to the very
        # tail of the track doesn't trigger immediate EOF and an unexpected
        # playlist-stop in SEQUENTIAL mode.
        if pos > 0 and max_pos > 2000 and pos >= max_pos - 1000:
            pos = max_pos - 1000
        self._last_position_ms = pos
        self._audio_engine.seek(pos)

    @Slot(int)
    def _on_slider_moved(self, value: int) -> None:
        """Update the time label while dragging."""
        self._time_current.setText(self._format_time(value))

    # ================================================================
    # Slots – Volume
    # ================================================================

    @Slot(int)
    def _on_volume_changed(self, value: int) -> None:
        """Adjust audio engine volume (0-100 mapped to 0.0-1.0)."""
        self._volume_label.setText(f"{value}%")
        if value == 0:
            self._mute_btn.setText("🔇")
        else:
            self._mute_btn.setText("🔊" if value <= 70 else "🔊")
        self._audio_engine.set_volume(value / 100.0)

    @Slot()
    def _on_toggle_mute(self) -> None:
        """Toggle between muted and previous volume."""
        if self._volume_muted:
            # Unmute – restore previous volume.
            self._volume_muted = False
            self._volume_slider.setValue(self._volume_before_mute)
        else:
            # Mute – save current volume and set to 0.
            self._volume_muted = True
            self._volume_before_mute = self._volume_slider.value()
            self._volume_slider.setValue(0)

    # ================================================================
    # Slots – Play mode
    # ================================================================

    @Slot()
    def _on_cycle_play_mode(self) -> None:
        """Cycle to the next play mode."""
        self._playlist_manager.cycle_play_mode()

    # ================================================================
    # Slots – Menu actions
    # ================================================================

    @Slot()
    def _on_open_directory(self) -> None:
        """Open a directory dialog and scan for music."""
        directory = QFileDialog.getExistingDirectory(
            self, "选择音乐目录", QDir.homePath(),
        )
        if directory:
            self._queue_scan_directory(directory, append=False)

    @Slot()
    def _on_open_files(self) -> None:
        """Open a file dialog to select audio files and add them to playlist."""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", QDir.homePath(),
            "音频文件 (*.mp3 *.flac *.wav *.ogg *.m4a *.wma *.aac *.au *.opus);;所有文件 (*)",
        )
        self._import_local_files(files)

    @Slot()
    def _on_import_playlist(self) -> None:
        """Import an M3U/M3U8 playlist file and load its songs."""
        path, _ = QFileDialog.getOpenFileName(
            self, "导入播放列表", QDir.homePath(),
            "播放列表 (*.m3u *.m3u8);;所有文件 (*)",
        )
        if not path:
            return
        songs = self._parse_m3u(path)
        if not songs:
            self._status_label.setText("未能导入播放列表（文件为空或路径无效）.")
            return
        self._playlist_manager.load_playlist(songs)
        self._status_label.setText(f"已导入 {len(songs)} 首歌曲.")

    @Slot()
    def _on_export_playlist(self) -> None:
        """Export the current playlist to an M3U file."""
        songs = self._playlist_manager.playlist
        if not songs:
            self._status_label.setText("播放列表为空，无法导出.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出播放列表", QDir.homePath() + "/playlist.m3u8",
            "播放列表 (*.m3u8 *.m3u);;所有文件 (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for song in songs:
                    minutes = int(song.duration) // 60
                    seconds = int(song.duration) % 60
                    duration_str = f"{minutes}:{seconds:02d}"
                    f.write(f"#EXTINF:{int(song.duration)},{song.artist} - {song.title}\n")
                    f.write(f"{song.file_path}\n")
            self._status_label.setText(f"已导出 {len(songs)} 首歌曲到 {Path(path).name}.")
        except Exception as exc:
            self._status_label.setText(f"导出失败: {exc}")
            logger.error("Export error: %s", exc)

    @Slot()
    def _on_open_favorites(self) -> None:
        """Show the dedicated favorites window."""
        self._favorites_window.show_and_raise()

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _parse_m3u(path: str) -> list[Song]:
        """Parse an M3U/M3U8 file and return a list of Songs for existing files."""
        songs: list[Song] = []
        base_dir = Path(path).parent
        try:
            lines = Path(path).read_text(encoding="utf-8").splitlines()
        except Exception:
            return songs

        for line in lines:
            line = line.strip()
            # Skip comments and EXTINF lines.
            if not line or line.startswith("#"):
                continue
            # Resolve relative paths against the playlist directory.
            file_candidate = Path(line)
            if not file_candidate.is_absolute():
                file_candidate = base_dir / line
            if file_candidate.exists():
                try:
                    song = MainWindow._metadata_from_path(str(file_candidate))
                    if song is not None:
                        songs.append(song)
                except Exception:
                    pass
        return songs

    @staticmethod
    def _metadata_from_path(file_path: str) -> Optional[Song]:
        """Extract metadata from *file_path* and return a Song."""
        song = extract_song_metadata(file_path)
        if song is not None:
            return song
        resolved_path = str(Path(file_path).resolve())
        return Song(
            title=Path(resolved_path).stem,
            file_path=resolved_path,
            file_format=Path(resolved_path).suffix.lower().lstrip("."),
        )

    @Slot(bool)
    def _on_toggle_lyrics(self, visible: bool) -> None:
        """Show or hide the lyrics window."""
        if visible:
            # Re-position when showing.
            self._lyrics_window._move_to_bottom_center()
            self._lyrics_window.show()
            current_song = self._playlist_manager.get_current_song()
            if current_song is not None:
                self._load_lyrics_for(current_song)
                self._lyrics_window.sync_position(self._last_position_ms)
            else:
                self._lyrics_window.load_lyrics([])
        else:
            self._lyrics_window.hide()

    @Slot(bool)
    def _on_lyrics_window_visibility_changed(self, visible: bool) -> None:
        """Keep the View menu check state in sync with the lyrics window."""
        if self._show_lyrics_action.isChecked() != visible:
            self._show_lyrics_action.setChecked(visible)

    # ================================================================
    # Slots – Music Scanner
    # ================================================================

    @Slot(str)
    def _on_scan_started(self, directory: str) -> None:
        """Show scanning progress and reset song collector."""
        self._scanned_songs.clear()
        self._scan_progress.setValue(0)
        self._scan_progress.show()
        self._status_label.setText(f"正在扫描 {Path(directory).name}…")

    @Slot(int, int)
    def _on_scan_progress(self, current: int, total: int) -> None:
        """Update the progress bar during scanning."""
        if total > 0:
            pct = int(current * 100 / total)
            self._scan_progress.setValue(pct)
        self._status_label.setText(f"正在扫描… {current}/{total}")

    @Slot(object)
    def _on_scan_song_found(self, song: Song) -> None:
        """Collect newly found songs during scanning."""
        self._scanned_songs.append(song)

    @Slot(int)
    def _on_scan_finished(self, imported: int) -> None:
        """Scan complete – load the scanned songs into the playlist."""
        self._scan_progress.hide()
        songs: list[Song] = list(self._scanned_songs)

        if songs:
            if self._scan_append_mode:
                added = self._append_unique_songs(songs)
                self._status_label.setText(f"扫描完成 – 已追加 {added} 首歌曲.")
            else:
                self._playlist_manager.load_playlist(songs)
                self._status_label.setText(f"扫描完成 – 已加载 {len(songs)} 首歌曲.")
        else:
            self._status_label.setText("扫描完成 – 没有发现歌曲.")

        self._start_next_scan_request()

    @Slot(str)
    def _on_scan_error(self, message: str) -> None:
        """Display scan error in status bar."""
        self._status_label.setText(f"⚠ 扫描错误: {message}")

    # ================================================================
    # Slots – Search Panel & Downloads
    # ================================================================

    @Slot(object)
    def _on_search_add_to_playlist(self, song: Song) -> None:
        """Add a search result song to the current playlist."""
        if self._append_unique_songs([song]) > 0:
            self._status_label.setText(f"已添加 '{song.title}' 到播放列表.")
        else:
            self._status_label.setText(f"'{song.title}' 已在播放列表中.")

    @Slot(object)
    def _on_search_download(self, song: Song) -> None:
        """Download a search result song (async, multiple run in parallel)."""
        self._active_downloads += 1
        self._search_provider.download(song, self._download_dir)
        self._status_label.setText(
            f"正在下载 ({self._active_downloads} 个活跃): {song.title}…")

    @Slot(str, str)
    def _on_search_error(self, category: str, message: str) -> None:
        """Display a friendly search error and update the search panel label."""
        if category == "network":
            friendly = "⚠ 网络连接失败，请检查网络后重试"
        else:
            friendly = f"⚠ 搜索服务暂时不可用 (HTTP {message})" if message.isdigit() else f"⚠ 搜索服务暂时不可用: {message}"
        self._status_label.setText(friendly)
        self._search_panel.display_error(friendly)

    @Slot(object, str)
    def _on_download_ready(self, song: Song, local_path: str) -> None:
        """A song download finished – add it to the playlist if needed."""
        self._active_downloads = max(0, self._active_downloads - 1)
        song.file_path = str(Path(local_path).resolve())
        added = self._append_unique_songs([song])
        status = f"下载完成: {song.title}" if added > 0 else f"下载完成，播放列表中已存在: {song.title}"
        self._status_label.setText(
            status + (f"  (剩余 {self._active_downloads} 个)" if self._active_downloads else "")
        )

    @Slot(object, str)
    def _on_download_error(self, song: Song, message: str) -> None:
        """Display download error."""
        self._active_downloads = max(0, self._active_downloads - 1)
        self._status_label.setText(
            f"⚠ 下载失败 {song.title}: {message}"
            + (f"  (剩余 {self._active_downloads} 个)" if self._active_downloads else ""))

    @Slot()
    def _on_open_download_dir(self) -> None:
        """Open the download directory in the system file manager."""
        path = Path(self._download_dir)
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        self._status_label.setText(f"已打开: {path}")

    # ================================================================
    # Slots – Settings
    # ================================================================

    @Slot()
    def _on_open_settings(self) -> None:
        """Open the settings dialog and apply changes."""
        current = Settings.load()
        dialog = SettingsDialog(current, self)
        if dialog.exec() == QDialog.Accepted:
            new_settings = dialog.settings()
            if new_settings is None:
                return
            self._download_dir = new_settings.download_dir
            self._search_panel.set_download_dir(self._download_dir)
            self._lyrics_window.set_lyrics_font_size(new_settings.lyrics_font_size)
            self._volume_slider.setValue(new_settings.volume)
            self._playlist_manager.set_play_mode(new_settings.default_play_mode)
            self._close_to_tray = new_settings.close_to_tray
            self._status_label.setText("设置已保存")

    @Slot()
    def _on_show_about(self) -> None:
        """Show the About dialog."""
        QMessageBox.about(self, about_title, about_text())

    def _apply_saved_settings(self) -> None:
        """Load persisted settings and apply them to the UI."""
        saved = Settings.load()
        self._lyrics_window.set_lyrics_font_size(saved.lyrics_font_size)
        self._volume_slider.setValue(saved.volume)
        self._playlist_manager.set_play_mode(saved.default_play_mode)
        self._close_to_tray = saved.close_to_tray

    # ================================================================
    # System tray
    # ================================================================

    def _setup_tray_icon(self) -> None:
        """Create the system tray icon with a context menu."""
        self._tray_icon = QSystemTrayIcon(self)

        # Try to load app icon, fall back to a musical note icon.
        icon_path = Path(__file__).parent.parent.parent / "resources" / "icon.png"
        if icon_path.exists():
            self._tray_icon.setIcon(QIcon(str(icon_path)))
        else:
            self._tray_icon.setIcon(self.style().standardIcon(
                self.style().SP_MediaPlay))

        self._tray_icon.setToolTip(window_title)

        # -- Context menu --
        tray_menu = QMenu(self)

        self._tray_play_action = tray_menu.addAction("▶ 播放")
        self._tray_play_action.triggered.connect(self._on_play_pause)

        tray_menu.addSeparator()

        prev_action = tray_menu.addAction("⏮ 上一首")
        prev_action.triggered.connect(self._on_previous)

        next_action = tray_menu.addAction("⏭ 下一首")
        next_action.triggered.connect(self._on_next)

        tray_menu.addSeparator()

        quit_action = tray_menu.addAction("⏹ 退出")
        quit_action.triggered.connect(self._on_tray_quit)

        self._tray_icon.setContextMenu(tray_menu)

        # Double-click on the tray icon → show window.
        self._tray_icon.activated.connect(self._on_tray_activated)

        self._tray_icon.show()

    @Slot()
    def _update_tray_play_pause(self) -> None:
        """Sync the tray menu's play/pause text with the current state."""
        state = self._audio_engine.state
        if state == PlayState.PLAYING:
            self._tray_play_action.setText("⏸ 暂停")
        else:
            self._tray_play_action.setText("▶ 播放")

    @Slot(int)
    def _on_tray_activated(self, reason: int) -> None:
        """Handle tray icon activation (double-click → show window)."""
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()
            self.activateWindow()
            self.raise_()

    @Slot()
    def _on_tray_quit(self) -> None:
        """Actually quit the application (bypass close-to-tray)."""
        self._playlist_manager.save_to_m3u()
        self._favorites_manager.save_favorites()
        self._audio_engine.stop()
        QApplication.instance().quit()

    def dragEnterEvent(self, event) -> None:
        """Accept local audio-file and directory drops anywhere in the window."""
        files, directories = self._extract_drop_paths(event.mimeData())
        if files or directories:
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        """Import dropped audio files and queue dropped directories for scanning."""
        files, directories = self._extract_drop_paths(event.mimeData())
        handled = False

        if files:
            self._import_local_files(files)
            handled = True

        if directories:
            for directory in directories:
                self._queue_scan_directory(directory, append=True)
            handled = True

        if handled:
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    # ================================================================
    # Internal helpers
    # ================================================================

    @Slot()
    def _update_play_mode_action(self) -> None:
        """Refresh the menu item text for play mode."""
        mode = self._playlist_manager.play_mode
        symbol = self._PLAY_MODE_SYMBOLS.get(mode, "▶")
        self._mode_action.setText(f"播放模式: {symbol}")

    def _refresh_mode_btn(self) -> None:
        """Set the play mode button text from the current play mode."""
        mode = self._playlist_manager.play_mode
        symbol = self._PLAY_MODE_SYMBOLS.get(mode, "▶")
        self._mode_btn.setText(symbol)

    def _import_local_files(self, file_paths: list[str]) -> None:
        """Add supported local audio files to the current playlist."""
        if not file_paths:
            return

        songs: list[Song] = []
        failed = 0
        for file_path in file_paths:
            if not self._is_supported_audio_file(file_path):
                continue
            try:
                song = self._metadata_from_path(file_path)
                if song is not None:
                    songs.append(song)
            except Exception as exc:
                failed += 1
                logger.warning("Failed to import %s: %s", file_path, exc)

        if songs:
            added = self._append_unique_songs(songs)
            skipped = len(songs) - added
            message = f"已添加 {added} 首歌曲."
            if skipped:
                message += f" 跳过 {skipped} 首重复歌曲."
            if failed:
                message += f" 另有 {failed} 首导入失败."
            self._status_label.setText(message)
        elif failed:
            self._status_label.setText(f"未能导入文件，共有 {failed} 个文件失败.")

    def _queue_scan_directory(self, directory: str, append: bool) -> None:
        """Start a directory scan now or append it to the scan queue."""
        normalized = str(Path(directory).resolve())
        if self._music_scanner.isRunning():
            self._pending_scan_requests.append((normalized, append))
            self._status_label.setText(f"已加入扫描队列: {Path(directory).name}")
            return

        self._scan_append_mode = append
        self._music_scanner.scan_directory(normalized)

    def _start_next_scan_request(self) -> None:
        """Continue with the next queued directory scan, if any."""
        if not self._pending_scan_requests:
            return

        directory, append = self._pending_scan_requests.pop(0)
        self._scan_append_mode = append
        self._music_scanner.scan_directory(directory)

    def _append_unique_songs(self, songs: list[Song]) -> int:
        """Append songs whose file paths are not already in the active playlist."""
        existing_paths = {song.file_path for song in self._playlist_manager.playlist}
        added = 0
        for song in songs:
            if song.file_path in existing_paths:
                continue
            self._playlist_manager.add_song(song)
            existing_paths.add(song.file_path)
            added += 1
        return added

    def _extract_drop_paths(self, mime_data) -> tuple[list[str], list[str]]:
        """Split dropped local URLs into supported files and directories."""
        files: list[str] = []
        directories: list[str] = []
        if not mime_data or not mime_data.hasUrls():
            return files, directories

        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile()).resolve()
            if path.is_dir():
                directories.append(str(path))
            elif path.is_file() and self._is_supported_audio_file(str(path)):
                files.append(str(path))

        return files, directories

    @staticmethod
    def _is_supported_audio_file(file_path: str) -> bool:
        """Return whether *file_path* looks like a supported local audio file."""
        return Path(file_path).suffix.lower() in SUPPORTED_EXTENSIONS

    def _update_cover_art(self, song: Song) -> None:
        """Display the current song's embedded cover art when available."""
        art_bytes = extract_cover_art(song.file_path)
        if art_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(art_bytes):
                self._cover_label.setPixmap(
                    pixmap.scaled(
                        self._cover_label.size(),
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation,
                    )
                )
                self._cover_label.setText("")
                return
        self._reset_cover_art()

    def _reset_cover_art(self) -> None:
        """Restore the default cover-art placeholder."""
        self._cover_label.clear()
        self._cover_label.setText("♪")

    def _normalize_session_state(self, state: dict[str, object]) -> dict[str, object]:
        """Coerce persisted session metadata into a predictable shape."""
        if not state:
            return {}

        file_path = str(state.get("current_file_path") or "")
        if not file_path:
            return {}

        return {
            "current_file_path": file_path,
            "position_ms": 0,
            "play_state": int(PlayState.STOPPED),
        }

    def _update_song_info(self, song: Optional[Song]) -> None:
        """Update the song info label in the playback bar."""
        if song is not None and song.title:
            if song.artist:
                self._song_info_full_text = f"♫  {song.artist} – {song.title}"
            else:
                self._song_info_full_text = f"♫  {song.title}"
        else:
            self._song_info_full_text = "未播放"
        self._refresh_song_info_label()

    def _refresh_song_info_label(self) -> None:
        """Elide the playback title so it cannot crowd out the progress slider."""
        metrics = self._song_info_label.fontMetrics()
        available_width = max(80, self._song_info_label.width())
        self._song_info_label.setText(
            metrics.elidedText(self._song_info_full_text, Qt.ElideRight, available_width)
        )

    def _format_time(self, ms: int) -> str:
        """Convert milliseconds to 'm:ss' format."""
        seconds = ms // 1000
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"

    def resizeEvent(self, event) -> None:
        """Keep the title elision in sync with the current playback-bar width."""
        super().resizeEvent(event)
        self._refresh_song_info_label()

    # ------------------------------------------------------------------
    # Global shortcuts (QShortcut — works regardless of focus widget)
    # ------------------------------------------------------------------

    def _setup_shortcuts(self) -> None:
        """Register global keyboard shortcuts."""
        # Space → play/pause (ApplicationShortcut context so it works even
        # when the search input or any other child widget has focus).
        sc = QShortcut(QKeySequence(Qt.Key_Space), self)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(self._on_play_pause)

    def _load_lyrics_for(self, song: Song) -> None:
        """Try to load an LRC file for *song* and feed it to the lyrics window."""
        if not self._lyrics_window.isVisible():
            return

        # Common LRC file naming: same path as audio but with .lrc extension.
        lrc_path = Path(song.file_path).with_suffix(".lrc")
        if lrc_path.exists():
            lines = LrcParser.load(str(lrc_path))
            self._lyrics_window.load_lyrics(lines)
        else:
            # Try looking for a .lrc file in the same directory with the
            # same base name.
            self._lyrics_window.load_lyrics([])

    def _build_session_state(self) -> dict[str, object]:
        """Collect the selected song so startup can restore the same row."""
        current_song = self._playlist_manager.get_current_song()
        if current_song is None:
            return {
                "current_file_path": "",
                "position_ms": 0,
                "play_state": int(PlayState.STOPPED),
            }

        return {
            "current_file_path": current_song.file_path,
            "position_ms": 0,
            "play_state": int(PlayState.STOPPED),
        }

    # ================================================================
    # Cleanup
    # ================================================================

    def closeEvent(self, event) -> None:
        """Save state; minimise to tray or quit based on user preference."""
        self._playlist_manager.set_session_state(self._build_session_state())
        self._playlist_manager.save_to_m3u()
        self._favorites_manager.save_favorites()
        self._lyrics_window.close()

        if getattr(self, "_close_to_tray", True) and self._tray_icon is not None:
            # Minimise to tray instead of quitting.
            event.ignore()
            self.hide()
            self._tray_icon.showMessage(
                "LunaPlayer", "播放器已最小化到系统托盘",
                QSystemTrayIcon.Information, 2000,
            )
        else:
            self._audio_engine.stop()
            self._favorites_window.close()
            super().closeEvent(event)
