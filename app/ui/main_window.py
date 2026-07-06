"""
MainWindow – the primary application window.

Layout
------
┌─────────────────────────────────────────────────────────────┐
│ Menu | Toolbar                                              │
├──────────────┬──────────────────────────────────────────────┤
│ Navigation   │ Stacked workspace pages                      │
│ Queue        │ - Queue page                                 │
│ Search       │ - Search page                                │
│ Favorites    │ - Favorites page                             │
├──────────────┴──────────────────────────────────────────────┤
│ Artwork | current song | progress + transport | volume      │
├─────────────────────────────────────────────────────────────┤
│ Status bar                                                   │
└─────────────────────────────────────────────────────────────┘

Signals are used to communicate with core modules (AudioEngine,
PlaylistManager, MusicScanner) – the MainWindow never calls core
methods directly except to wire up initial connections.
"""

from __future__ import annotations

import enum
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QDir, Qt, Signal, Slot, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolBar,
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
from app.ui.lyrics_window import LrcParser, LyricsWindow
from app.ui.widgets import PlaylistWidget, SearchPanel, SongInfoDialog, Settings, SettingsDialog
from app.services.music_provider import MusicProvider
import app.services.config as cfg

logger = logging.getLogger(__name__)


class PageId(enum.Enum):
    """Logical top-level pages exposed by the main workspace shell."""

    QUEUE = "queue"
    SEARCH = "search"
    FAVORITES = "favorites"


class _InspectorPanel(QWidget):
    """Shared side panel that previews a selected song."""

    details_requested = Signal()

    def __init__(self, empty_title: str, empty_message: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._empty_title = empty_title
        self._empty_message = empty_message
        self._cover_pixmap: Optional[QPixmap] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        section_title = QLabel("检查器")
        section_title.setObjectName("inspectorSectionTitle")
        layout.addWidget(section_title)

        self._cover_label = QLabel("♪")
        self._cover_label.setObjectName("inspectorCover")
        self._cover_label.setAlignment(Qt.AlignCenter)
        self._cover_label.setMinimumSize(96, 96)
        self._cover_label.setMaximumSize(220, 220)
        self._cover_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout.addWidget(self._cover_label, 0, Qt.AlignHCenter)

        self._title_label = QLabel()
        self._title_label.setObjectName("inspectorTitle")
        self._title_label.setWordWrap(True)
        layout.addWidget(self._title_label)

        self._subtitle_label = QLabel()
        self._subtitle_label.setObjectName("inspectorSubtitle")
        self._subtitle_label.setWordWrap(True)
        layout.addWidget(self._subtitle_label)

        self._favorite_label = QLabel()
        self._favorite_label.setObjectName("inspectorFavorite")
        layout.addWidget(self._favorite_label)

        self._meta_form = QFormLayout()
        self._meta_form.setContentsMargins(0, 8, 0, 0)
        self._meta_form.setSpacing(8)
        self._meta_form.setLabelAlignment(Qt.AlignLeft)

        self._album_value = QLabel()
        self._format_value = QLabel()
        self._duration_value = QLabel()
        self._path_value = QLabel()
        self._path_value.setWordWrap(True)
        self._path_value.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )

        for label in (self._album_value, self._format_value, self._duration_value, self._path_value):
            label.setWordWrap(True)

        self._meta_form.addRow("专辑", self._album_value)
        self._meta_form.addRow("格式", self._format_value)
        self._meta_form.addRow("时长", self._duration_value)
        self._meta_form.addRow("路径", self._path_value)
        layout.addLayout(self._meta_form)

        self._note_label = QLabel()
        self._note_label.setObjectName("inspectorNote")
        self._note_label.setWordWrap(True)
        layout.addWidget(self._note_label)

        self._details_btn = QPushButton("查看详情")
        self._details_btn.clicked.connect(self.details_requested.emit)
        layout.addWidget(self._details_btn)

        layout.addStretch(1)
        self.show_placeholder()
        self._update_cover_size()

    def resizeEvent(self, event) -> None:
        """Shrink or expand artwork with the inspector panel size."""
        super().resizeEvent(event)
        self._update_cover_size()

    def _update_cover_size(self) -> None:
        """Keep artwork square and prevent it from crowding the text area."""
        available_width = max(96, self.width() - 48)
        available_height = max(96, self.height() // 3)
        size = max(96, min(220, available_width, available_height))
        if self._cover_label.width() != size:
            self._cover_label.setFixedSize(size, size)
        self._apply_cover_pixmap()

    def _apply_cover_pixmap(self) -> None:
        """Render the currently cached cover pixmap at the active size."""
        if self._cover_pixmap is None or self._cover_pixmap.isNull():
            self._cover_label.setPixmap(QPixmap())
            return
        self._cover_label.setPixmap(
            self._cover_pixmap.scaled(
                self._cover_label.size(),
                Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation,
            )
        )

    def show_placeholder(self) -> None:
        """Reset the inspector to its empty state."""
        self._cover_pixmap = None
        self._cover_label.setPixmap(QPixmap())
        self._cover_label.setText("♪")
        self._title_label.setText(self._empty_title)
        self._subtitle_label.setText(self._empty_message)
        self._favorite_label.setText("")
        self._album_value.setText("—")
        self._format_value.setText("—")
        self._duration_value.setText("—")
        self._path_value.setText("—")
        self._note_label.setText("")
        self._details_btn.setEnabled(False)

    def update_song(
        self,
        song: Optional[Song],
        *,
        is_favorite: bool,
        note: str = "",
        allow_details: bool = True,
    ) -> None:
        """Render the selected song or fall back to the empty state."""
        if song is None:
            self.show_placeholder()
            return

        self._title_label.setText(song.title or "未知标题")
        self._subtitle_label.setText(song.artist or "未知艺术家")
        self._favorite_label.setText("已收藏" if is_favorite else "未收藏")
        self._album_value.setText(song.album or "—")
        self._format_value.setText(song.file_format.upper() if song.file_format else "—")
        duration_seconds = int(song.duration or 0)
        self._duration_value.setText(f"{duration_seconds // 60}:{duration_seconds % 60:02d}")
        self._path_value.setText(song.file_path or "—")
        self._note_label.setText(note)
        self._details_btn.setEnabled(allow_details)

        art_bytes = extract_cover_art(song.file_path) if allow_details and song.file_path else None
        if art_bytes:
            pixmap = QPixmap()
            if pixmap.loadFromData(art_bytes):
                self._cover_pixmap = pixmap
                self._apply_cover_pixmap()
                self._cover_label.setText("")
                return

        self._cover_pixmap = None
        self._cover_label.setPixmap(QPixmap())
        self._cover_label.setText("♪")


class MainWindow(QMainWindow):
    """Main application window – orchestrates UI and core module interaction."""

    # ------------------------------------------------------------------
    # Constants
    # ------------------------------------------------------------------

    DEFAULT_WIDTH = 1280
    DEFAULT_HEIGHT = 820
    MIN_WIDTH = 960
    MIN_HEIGHT = 620
    SONG_INFO_MAX_WIDTH = 360
    PROGRESS_SLIDER_MIN_WIDTH = 260
    NAV_WIDTH = 188

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
        self._page_widgets: dict[PageId, QWidget] = {}
        self._page_buttons: dict[PageId, QPushButton] = {}
        self._queue_selected_index: int = -1
        self._favorites_selected_index: int = -1
        self._search_selected_song: Optional[Song] = None

        # ---- Build UI ----
        self._setup_window()
        self._create_menu_bar()
        self._create_toolbar()

        # Central wrapper: [workspace shell, playback bar]
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

        queue_view_action = QAction("当前队列(&Q)", self)
        queue_view_action.setShortcut(QKeySequence("Ctrl+1"))
        queue_view_action.triggered.connect(lambda: self.navigate_to(PageId.QUEUE))
        view_menu.addAction(queue_view_action)

        search_view_action = QAction("在线搜索(&S)", self)
        search_view_action.setShortcut(QKeySequence("Ctrl+2"))
        search_view_action.triggered.connect(lambda: self.navigate_to(PageId.SEARCH))
        view_menu.addAction(search_view_action)

        favorites_view_action = QAction("收藏(&F)", self)
        favorites_view_action.setShortcut(QKeySequence("Ctrl+3"))
        favorites_view_action.triggered.connect(lambda: self.navigate_to(PageId.FAVORITES))
        view_menu.addAction(favorites_view_action)

        view_menu.addSeparator()

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

    def _create_toolbar(self) -> None:
        """Create a compact primary toolbar for high-frequency actions."""
        toolbar = QToolBar("主工具栏", self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setObjectName("mainToolbar")
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        open_dir_btn = QPushButton("打开目录")
        open_dir_btn.setObjectName("toolbarPrimaryButton")
        open_dir_btn.clicked.connect(self._on_open_directory)
        toolbar.addWidget(open_dir_btn)

        open_file_btn = QPushButton("打开文件")
        open_file_btn.setObjectName("toolbarPrimaryButton")
        open_file_btn.clicked.connect(self._on_open_files)
        toolbar.addWidget(open_file_btn)

        toolbar.addSeparator()

        search_btn = QPushButton("在线搜索")
        search_btn.setObjectName("toolbarSecondaryButton")
        search_btn.clicked.connect(lambda: self.navigate_to(PageId.SEARCH))
        toolbar.addWidget(search_btn)

        favorites_btn = QPushButton("收藏")
        favorites_btn.setObjectName("toolbarSecondaryButton")
        favorites_btn.clicked.connect(lambda: self.navigate_to(PageId.FAVORITES))
        toolbar.addWidget(favorites_btn)

        toolbar.addSeparator()

        settings_btn = QPushButton("设置")
        settings_btn.setObjectName("toolbarSecondaryButton")
        settings_btn.clicked.connect(self._on_open_settings)
        toolbar.addWidget(settings_btn)

    # ---------------------------------------------------------------
    # Central area – navigation + pages
    # ---------------------------------------------------------------

    def _create_central_area(self, parent_layout: QVBoxLayout) -> None:
        """Build the workspace shell with navigation and stacked pages."""
        shell = QWidget()
        shell.setObjectName("workspaceShell")
        shell_layout = QHBoxLayout(shell)
        shell_layout.setContentsMargins(12, 12, 12, 8)
        shell_layout.setSpacing(12)

        self._navigation_panel = self._create_navigation()
        shell_layout.addWidget(self._navigation_panel)

        self._page_stack = QStackedWidget()
        self._page_stack.setObjectName("pageStack")
        shell_layout.addWidget(self._page_stack, 1)

        self._create_pages()

        parent_layout.addWidget(shell, 1)

    def _create_navigation(self) -> QWidget:
        """Create the fixed left navigation rail."""
        panel = QFrame()
        panel.setObjectName("navPanel")
        panel.setFixedWidth(self.NAV_WIDTH)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 16, 14, 16)
        layout.setSpacing(10)

        app_label = QLabel("LunaPlayer")
        app_label.setObjectName("navAppTitle")
        layout.addWidget(app_label)

        caption = QLabel("本地音乐工作台")
        caption.setObjectName("navAppCaption")
        layout.addWidget(caption)
        layout.addSpacing(8)

        button_specs = [
            (PageId.QUEUE, "Ctrl+1", "当前队列"),
            (PageId.SEARCH, "Ctrl+2", "在线搜索"),
            (PageId.FAVORITES, "Ctrl+3", "收藏"),
        ]
        for page_id, shortcut_text, label in button_specs:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("navButton")
            button.setToolTip(shortcut_text)
            button.clicked.connect(lambda checked=False, pid=page_id: self.navigate_to(pid))
            layout.addWidget(button)
            self._page_buttons[page_id] = button

        layout.addStretch(1)
        return panel

    def _create_pages(self) -> None:
        """Create and register all top-level workspace pages."""
        self._create_queue_page()
        self._create_search_page()
        self._create_favorites_page()
        self.navigate_to(PageId.QUEUE)

    def _register_page(self, page_id: PageId, widget: QWidget) -> None:
        """Register a page widget in the page stack."""
        self._page_widgets[page_id] = widget
        self._page_stack.addWidget(widget)

    def _create_page_card(self) -> QFrame:
        """Create a reusable page surface frame."""
        card = QFrame()
        card.setObjectName("pageCard")
        return card

    def _create_page_header(
        self,
        title: str,
        subtitle: str,
        primary_action: Optional[tuple[str, object]] = None,
        secondary_action: Optional[tuple[str, object]] = None,
    ) -> tuple[QWidget, QLabel]:
        """Create a standard page header with a count badge and actions."""
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        title_row.addWidget(title_label)

        count_label = QLabel("0 首")
        count_label.setObjectName("pageCountBadge")
        title_row.addWidget(count_label, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        text_column.addLayout(title_row)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("pageSubtitle")
        subtitle_label.setWordWrap(True)
        text_column.addWidget(subtitle_label)

        layout.addLayout(text_column, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        if secondary_action is not None:
            text, callback = secondary_action
            button = QPushButton(text)
            button.clicked.connect(callback)
            actions.addWidget(button)
        if primary_action is not None:
            text, callback = primary_action
            button = QPushButton(text)
            button.setObjectName("primaryActionButton")
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        return header, count_label

    def _create_queue_page(self) -> None:
        """Create the queue management page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header, self._queue_count_label = self._create_page_header(
            "当前队列",
            "管理本次播放队列，保留拖拽排序和右键操作，并为未来的播放列表工作区预留结构。",
        )
        layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        queue_card = self._create_page_card()
        queue_card_layout = QVBoxLayout(queue_card)
        queue_card_layout.setContentsMargins(12, 12, 12, 12)
        queue_card_layout.setSpacing(8)

        self._playlist_widget = PlaylistWidget(embedded=True)
        queue_card_layout.addWidget(self._playlist_widget)
        splitter.addWidget(queue_card)

        inspector_card = self._create_page_card()
        inspector_layout = QVBoxLayout(inspector_card)
        inspector_layout.setContentsMargins(12, 12, 12, 12)
        self._queue_inspector = _InspectorPanel("选择一首歌曲", "这里会显示当前队列中歌曲的详细信息。")
        self._queue_inspector.details_requested.connect(self._open_queue_song_details)
        inspector_layout.addWidget(self._queue_inspector)
        splitter.addWidget(inspector_card)

        splitter.setSizes([760, 320])
        layout.addWidget(splitter, 1)
        self._register_page(PageId.QUEUE, page)

    def _create_search_page(self) -> None:
        """Create the online search workspace page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header, self._search_count_label = self._create_page_header(
            "在线搜索",
            "将在线搜索从主分屏中独立出来，方便未来加入下载队列、来源筛选和更多结果操作。",
        )
        self._search_count_label.setText("0 条")
        layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        search_card = self._create_page_card()
        search_card_layout = QVBoxLayout(search_card)
        search_card_layout.setContentsMargins(12, 12, 12, 12)
        self._search_panel = SearchPanel(embedded=True)
        search_card_layout.addWidget(self._search_panel)
        splitter.addWidget(search_card)

        inspector_card = self._create_page_card()
        inspector_layout = QVBoxLayout(inspector_card)
        inspector_layout.setContentsMargins(12, 12, 12, 12)
        self._search_inspector = _InspectorPanel("选择一条搜索结果", "这里会显示在线结果的概览信息。")
        self._search_inspector.details_requested.connect(self._open_search_song_details)
        inspector_layout.addWidget(self._search_inspector)
        splitter.addWidget(inspector_card)

        splitter.setSizes([760, 320])
        layout.addWidget(splitter, 1)
        self._register_page(PageId.SEARCH, page)

    def _create_favorites_page(self) -> None:
        """Create the favorites management page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header, self._favorites_count_label = self._create_page_header(
            "收藏",
            "收藏页现在是主工作台的一部分，方便以后扩展成更完整的本地媒体库入口。",
        )
        layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        favorites_card = self._create_page_card()
        favorites_card_layout = QVBoxLayout(favorites_card)
        favorites_card_layout.setContentsMargins(12, 12, 12, 12)
        self._favorites_widget = PlaylistWidget(
            allow_playlist_removal=False,
            show_clear_action=False,
            embedded=True,
        )
        favorites_card_layout.addWidget(self._favorites_widget)
        splitter.addWidget(favorites_card)

        inspector_card = self._create_page_card()
        inspector_layout = QVBoxLayout(inspector_card)
        inspector_layout.setContentsMargins(12, 12, 12, 12)
        self._favorites_inspector = _InspectorPanel("选择一首收藏歌曲", "这里会显示收藏歌曲的详细信息。")
        self._favorites_inspector.details_requested.connect(self._open_favorite_song_details)
        inspector_layout.addWidget(self._favorites_inspector)
        splitter.addWidget(inspector_card)

        splitter.setSizes([760, 320])
        layout.addWidget(splitter, 1)
        self._register_page(PageId.FAVORITES, page)

    def navigate_to(self, page_id: PageId) -> None:
        """Switch the stacked workspace to the requested page."""
        target = self._page_widgets.get(page_id)
        if target is None:
            return
        self._page_stack.setCurrentWidget(target)
        for known_page, button in self._page_buttons.items():
            button.setChecked(known_page == page_id)

    # ---------------------------------------------------------------
    # Playback control bar
    # ---------------------------------------------------------------

    def _create_playback_bar(self, parent_layout: QVBoxLayout) -> None:
        """Build the persistent bottom playback controls bar."""
        bar = QWidget()
        bar.setObjectName("playbackBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(18)
        
        left_section = QWidget()
        left_layout = QHBoxLayout(left_section)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        song_summary = QVBoxLayout()
        song_summary.setContentsMargins(0, 0, 0, 0)
        song_summary.setSpacing(4)
        self._song_info_label = QLabel("未播放")
        self._song_info_label.setObjectName("titleLabel")
        self._song_info_label.setMaximumWidth(self.SONG_INFO_MAX_WIDTH)
        self._song_info_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        song_summary.addWidget(self._song_info_label)

        self._song_meta_label = QLabel("等待播放")
        self._song_meta_label.setObjectName("artistLabel")
        self._song_meta_label.setMaximumWidth(self.SONG_INFO_MAX_WIDTH)
        self._song_meta_label.setWordWrap(True)
        song_summary.addWidget(self._song_meta_label)
        left_layout.addLayout(song_summary, 1)
        layout.addWidget(left_section, 0)

        center_section = QWidget()
        center_layout = QVBoxLayout(center_section)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)

        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(8)
        self._time_current = QLabel("0:00")
        self._time_current.setObjectName("timeLabel")
        progress_row.addWidget(self._time_current)

        self._progress_slider = QSlider(Qt.Horizontal)
        self._progress_slider.setRange(0, 0)
        self._progress_slider.setValue(0)
        self._progress_slider.setMinimumWidth(self.PROGRESS_SLIDER_MIN_WIDTH)
        self._progress_slider.sliderPressed.connect(self._on_slider_pressed)
        self._progress_slider.sliderReleased.connect(self._on_slider_released)
        self._progress_slider.sliderMoved.connect(self._on_slider_moved)
        progress_row.addWidget(self._progress_slider, 1)

        self._time_total = QLabel("0:00")
        self._time_total.setObjectName("timeLabel")
        progress_row.addWidget(self._time_total)
        center_layout.addLayout(progress_row)

        transport_row = QHBoxLayout()
        transport_row.setContentsMargins(0, 0, 0, 0)
        transport_row.setSpacing(6)

        self._prev_btn = QPushButton("⏮")
        self._prev_btn.setObjectName("transportBtn")
        self._prev_btn.clicked.connect(self._on_previous)
        transport_row.addWidget(self._prev_btn)

        self._play_btn = QPushButton("▶")
        self._play_btn.setObjectName("transportBtn")
        self._play_btn.clicked.connect(self._on_play_pause)
        transport_row.addWidget(self._play_btn)

        self._stop_btn = QPushButton("⏹")
        self._stop_btn.setObjectName("transportBtn")
        self._stop_btn.clicked.connect(self._on_stop)
        transport_row.addWidget(self._stop_btn)

        self._next_btn = QPushButton("⏭")
        self._next_btn.setObjectName("transportBtn")
        self._next_btn.clicked.connect(self._on_next)
        transport_row.addWidget(self._next_btn)

        transport_row.addStretch(1)
        self._mode_btn = QPushButton()
        self._mode_btn.clicked.connect(self._on_cycle_play_mode)
        self._refresh_mode_btn()
        transport_row.addWidget(self._mode_btn)

        center_layout.addLayout(transport_row)
        layout.addWidget(center_section, 1)

        right_section = QWidget()
        right_layout = QVBoxLayout(right_section)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        volume_row = QHBoxLayout()
        volume_row.setContentsMargins(0, 0, 0, 0)
        volume_row.setSpacing(8)

        self._mute_btn = QPushButton("🔊")
        self._mute_btn.setFixedSize(38, 32)
        self._mute_btn.clicked.connect(self._on_toggle_mute)
        volume_row.addWidget(self._mute_btn)

        self._volume_slider = QSlider(Qt.Horizontal)
        self._volume_slider.setObjectName("volumeSlider")
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(120)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        volume_row.addWidget(self._volume_slider)

        self._volume_label = QLabel("80%")
        self._volume_label.setObjectName("timeLabel")
        self._volume_label.setFixedWidth(42)
        volume_row.addWidget(self._volume_label)
        right_layout.addLayout(volume_row)

        self._playback_hint_label = QLabel("空格键 播放 / 暂停")
        self._playback_hint_label.setObjectName("timeLabel")
        right_layout.addWidget(self._playback_hint_label, 0, Qt.AlignRight)
        right_layout.addStretch(1)
        layout.addWidget(right_section, 0)

        self._volume_muted = False
        self._volume_before_mute = 80
        parent_layout.addWidget(bar)

    def _create_status_bar(self) -> None:
        """Add a status bar for scanning progress and messages."""
        status = QStatusBar()
        status.setStyleSheet("""
            QStatusBar {
                background-color: #15191F;
                border-top: 1px solid #2A313B;
                border-bottom: 1px solid #2A313B;
                font-size: 11px;
                color: #8E9AA7;
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
        self._playlist_widget.selection_changed.connect(self._on_queue_selection_changed)

        # -- FavoritesManager -> UI --
        self._favorites_manager.favorites_loaded.connect(self._on_favorites_updated)
        self._favorites_manager.favorites_changed.connect(self._on_favorites_updated)

        # -- Favorites page --
        self._favorites_widget.play_requested.connect(self._on_favorites_play_requested)
        self._favorites_widget.favorite_remove_requested.connect(
            self._on_favorites_window_remove_requested
        )
        self._favorites_widget.order_changed.connect(self._on_favorites_reordered)
        self._favorites_widget.selection_changed.connect(self._on_favorites_selection_changed)
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
        self._search_panel.selection_changed.connect(self._on_search_selection_changed)
        self._search_panel.results_changed.connect(self._on_search_results_changed)

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
        self._queue_count_label.setText(f"{len(songs)} 首")
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
        if not songs:
            self._queue_inspector.show_placeholder()

    @Slot(int)
    def _on_song_added(self, index: int) -> None:
        """A song was added at *index* – refresh view."""
        del index
        songs = self._playlist_manager.playlist
        self._playlist_widget.load_songs(songs)
        self._playlist_widget.set_favorite_paths(self._favorites_manager.favorite_paths)
        self._queue_count_label.setText(f"{len(songs)} 首")

    @Slot(int, object)
    def _on_song_removed(self, index: int, song: object) -> None:
        """A song was removed – refresh view."""
        del index, song
        songs = self._playlist_manager.playlist
        self._playlist_widget.load_songs(songs)
        self._playlist_widget.set_favorite_paths(self._favorites_manager.favorite_paths)
        self._queue_count_label.setText(f"{len(songs)} 首")
        if not songs:
            self._queue_inspector.show_placeholder()

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

    @Slot(int)
    def _on_queue_selection_changed(self, index: int) -> None:
        """Refresh the queue inspector when the selected row changes."""
        self._queue_selected_index = index
        song = self._playlist_widget.model.song_at(index)
        self._queue_inspector.update_song(
            song,
            is_favorite=bool(song and song.file_path in self._favorites_manager.favorite_paths),
            note="当前队列中的歌曲可直接播放、移除、收藏或查看详情。",
            allow_details=bool(song and self._can_show_song_details(song)),
        )

    @Slot(object)
    def _on_play_mode_changed(self, mode: PlayMode) -> None:
        """Update play mode display."""
        del mode
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

    @Slot()
    def _open_queue_song_details(self) -> None:
        """Open the details dialog for the selected queue song."""
        if self._queue_selected_index >= 0:
            self._on_info_requested(self._queue_selected_index)

    @Slot()
    def _open_search_song_details(self) -> None:
        """Open the details dialog for the selected search result when local."""
        song = self._search_selected_song
        if song is not None and self._can_show_song_details(song):
            dialog = SongInfoDialog(song, self)
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
        """Refresh favorite markers and keep the favorites page in sync."""
        self._playlist_widget.set_favorite_paths(self._favorites_manager.favorite_paths)
        favorites = self._favorites_manager.favorites
        self._favorites_widget.load_songs(favorites)
        self._favorites_widget.set_favorite_paths(self._favorites_manager.favorite_paths)
        self._favorites_count_label.setText(f"{len(favorites)} 首")
        if not favorites:
            self._favorites_inspector.show_placeholder()
        self._on_queue_selection_changed(self._queue_selected_index)
        self._on_search_selection_changed(self._search_selected_song)

    @Slot(int)
    def _on_favorites_play_requested(self, index: int) -> None:
        """Load favorites into the main player and start from *index*."""
        favorites = self._favorites_manager.favorites
        if not 0 <= index < len(favorites):
            return
        if self._audio_engine.state != PlayState.STOPPED:
            self._audio_engine.stop()
        self._playlist_manager.load_playlist(favorites, start_index=index)
        self.navigate_to(PageId.QUEUE)

    @Slot(int)
    def _on_favorites_window_remove_requested(self, index: int) -> None:
        """Remove the selected song from favorites without touching the queue."""
        favorites = self._favorites_manager.favorites
        if not 0 <= index < len(favorites):
            return
        song = favorites[index]
        if self._favorites_manager.remove_favorite_by_path(song.file_path):
            self._favorites_manager.save_favorites()
            self._status_label.setText(f"已取消收藏: {song.title}")

    @Slot(list)
    def _on_favorites_reordered(self, songs: list[Song]) -> None:
        """Persist drag-and-drop ordering from the favorites page."""
        self._favorites_manager.reorder_favorites(songs)
        self._favorites_manager.save_favorites()

    @Slot(int)
    def _on_favorites_selection_changed(self, index: int) -> None:
        """Refresh the favorites inspector when the selected row changes."""
        self._favorites_selected_index = index
        song = self._favorites_widget.model.song_at(index)
        self._favorites_inspector.update_song(
            song,
            is_favorite=bool(song),
            note="收藏页中的歌曲可以直接播放，也可以取消收藏。",
            allow_details=bool(song and self._can_show_song_details(song)),
        )

    @Slot()
    def _open_favorite_song_details(self) -> None:
        """Open the details dialog for the selected favorite song."""
        favorites = self._favorites_manager.favorites
        if 0 <= self._favorites_selected_index < len(favorites):
            dialog = SongInfoDialog(favorites[self._favorites_selected_index], self)
            dialog.exec()

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
        """Switch to the favorites page inside the main workspace."""
        self.navigate_to(PageId.FAVORITES)

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

    @Slot(object)
    def _on_search_selection_changed(self, song: Optional[Song]) -> None:
        """Refresh the search inspector from the panel selection."""
        self._search_selected_song = song
        self._search_inspector.update_song(
            song,
            is_favorite=bool(song and song.file_path in self._favorites_manager.favorite_paths),
            note="在线结果可以加入当前队列或下载到本地；本地化后可获得完整详情。",
            allow_details=bool(song and self._can_show_song_details(song)),
        )

    @Slot(int)
    def _on_search_results_changed(self, count: int) -> None:
        """Update the header badge for the search page."""
        self._search_count_label.setText(f"{count} 条")
        if count == 0 and self._search_selected_song is None:
            self._search_inspector.show_placeholder()

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

    @staticmethod
    def _can_show_song_details(song: Song) -> bool:
        """Return whether a song has a local file that can be inspected safely."""
        file_path = str(song.file_path or "").strip()
        if not file_path or file_path.startswith(("http://", "https://")):
            return False
        return Path(file_path).suffix != ""

    def _update_cover_art(self, song: Song) -> None:
        """Keep playback-bar cover handling disabled; inspectors own artwork now."""
        del song

    def _reset_cover_art(self) -> None:
        """Playback bar no longer renders artwork."""

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
            meta_parts = [part for part in (song.album, song.file_format.upper() if song.file_format else "") if part]
            self._song_meta_label.setText(" · ".join(meta_parts) if meta_parts else "当前队列中的歌曲")
        else:
            self._song_info_full_text = "未播放"
            self._song_meta_label.setText("等待播放")
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

        for sequence, page_id in (
            ("Ctrl+1", PageId.QUEUE),
            ("Ctrl+2", PageId.SEARCH),
            ("Ctrl+3", PageId.FAVORITES),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(lambda pid=page_id: self.navigate_to(pid))

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
            super().closeEvent(event)
