"""Behavior tests for MainWindow startup restore."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.app_info import APP_NAME, window_title
from app.core.audio_engine import AudioEngine
from app.core.favorites_manager import FavoritesManager
from app.core.music_scanner import MusicScanner
from app.core.playlist_manager import PlaylistManager
from app.models.song import Song
from app.ui.main_window import MainWindow, PageId
from tests.test_base import create_test_wav, ensure_qapp


class _TestMainWindow(MainWindow):
    """MainWindow variant that disables tray setup for tests."""

    def _setup_tray_icon(self) -> None:
        self._tray_icon = None
        self._tray_play_action = None

    def _update_tray_play_pause(self) -> None:
        return


class TestMainWindowRestore(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def setUp(self) -> None:
        self._tmp_home = tempfile.TemporaryDirectory(prefix="lunaplayer_home_")
        self._home_patch = patch("pathlib.Path.home", return_value=Path(self._tmp_home.name))
        self._home_patch.start()
        self.playlist_manager = PlaylistManager()
        self.favorites_manager = FavoritesManager()
        self.favorites_manager.save_favorites = MagicMock()  # type: ignore[method-assign]
        self.audio_engine = AudioEngine()
        self.audio_engine.play = MagicMock()  # type: ignore[method-assign]
        self.music_scanner = MusicScanner()
        self.window = _TestMainWindow(
            playlist_manager=self.playlist_manager,
            favorites_manager=self.favorites_manager,
            audio_engine=self.audio_engine,
            music_scanner=self.music_scanner,
        )

    def tearDown(self) -> None:
        self.playlist_manager.save_to_m3u = MagicMock()  # type: ignore[method-assign]
        self.window.close()
        self.audio_engine.stop()
        self._home_patch.stop()
        self._tmp_home.cleanup()

    def test_startup_restore_selects_song_without_loading_audio(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mainwindow_restore_") as tmpdir:
            file_path = str(Path(create_test_wav(tmpdir, "restore_me.wav")).resolve())
            song = Song(
                title="Restore Me",
                artist="Artist",
                duration=0.3,
                file_path=file_path,
                file_format="wav",
            )

            self.playlist_manager.set_session_state({
                "current_file_path": file_path,
                "position_ms": 9999,
                "play_state": 1,
            })
            self.playlist_manager.load_playlist([song], start_index=0)

            self.audio_engine.play.assert_not_called()
            self.assertEqual(self.playlist_manager.current_index, 0)
            self.assertEqual(self.window._song_info_label.text(), "♫  Artist – Restore Me")
            self.assertEqual(self.window._time_current.text(), "0:00")
            self.assertEqual(self.window._time_total.text(), "0:00")
            self.assertIs(
                self.window._page_stack.currentWidget(),
                self.window._page_widgets[PageId.QUEUE],
            )

    def test_song_info_area_is_bounded_to_protect_progress_slider(self) -> None:
        self.assertEqual(self.window.windowTitle(), window_title)

        self.assertEqual(
            self.window._song_info_label.maximumWidth(),
            MainWindow.SONG_INFO_MAX_WIDTH,
        )
        self.assertEqual(
            self.window._progress_slider.minimumWidth(),
            MainWindow.PROGRESS_SLIDER_MIN_WIDTH,
        )

        long_song = Song(
            title="A Very Long Song Title " * 8,
            artist="A Very Long Artist Name " * 4,
            duration=1.0,
            file_path="D:/music/long.wav",
            file_format="wav",
        )

        self.window._update_song_info(long_song)
        self.assertIn("…", self.window._song_info_label.text())

    def test_open_favorites_switches_to_workspace_page(self) -> None:
        self.window.navigate_to(PageId.QUEUE)

        self.window._on_open_favorites()

        self.assertIs(
            self.window._page_stack.currentWidget(),
            self.window._page_widgets[PageId.FAVORITES],
        )
        self.assertTrue(self.window._page_buttons[PageId.FAVORITES].isChecked())

    def test_about_dialog_title_uses_lunaplayer_name(self) -> None:
        self.assertEqual(APP_NAME, "LunaPlayer")

    def test_favorite_add_and_remove_updates_playlist_marker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mainwindow_favorites_") as tmpdir:
            file_path = str(Path(create_test_wav(tmpdir, "favorite_me.wav")).resolve())
            song = Song(title="Favorite Me", artist="Artist", file_path=file_path, file_format="wav")
            self.playlist_manager.load_playlist([song], start_index=0)

            self.window._on_favorite_add_requested(0)
            self.assertTrue(self.favorites_manager.contains_path(file_path))
            display = self.window._playlist_widget.model.data(
                self.window._playlist_widget.model.index(0, 0)
            )
            self.assertTrue(display.startswith("★ "))

            self.window._on_favorite_remove_requested(0)
            self.assertFalse(self.favorites_manager.contains_path(file_path))
            display = self.window._playlist_widget.model.data(
                self.window._playlist_widget.model.index(0, 0)
            )
            self.assertFalse(display.startswith("★ "))

    def test_playing_from_favorites_switches_main_playlist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mainwindow_favorites_") as tmpdir:
            first = str(Path(create_test_wav(tmpdir, "one.wav")).resolve())
            second = str(Path(create_test_wav(tmpdir, "two.wav")).resolve())
            first_song = Song(title="One", file_path=first, file_format="wav")
            second_song = Song(title="Two", file_path=second, file_format="wav")

            self.favorites_manager.add_favorite(first_song)
            self.favorites_manager.add_favorite(second_song)

            self.window._on_favorites_play_requested(1)

            self.assertEqual(self.playlist_manager.current_index, 1)
            self.assertEqual(
                [song.file_path for song in self.playlist_manager.playlist],
                [first, second],
            )
            self.audio_engine.play.assert_called_with(second)

    def test_removing_from_favorites_window_keeps_main_playlist_song(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mainwindow_favorites_") as tmpdir:
            file_path = str(Path(create_test_wav(tmpdir, "stay.wav")).resolve())
            song = Song(title="Stay", file_path=file_path, file_format="wav")

            self.playlist_manager.load_playlist([song], start_index=0)
            self.favorites_manager.add_favorite(song)

            self.window._on_favorites_window_remove_requested(0)

            self.assertEqual(len(self.playlist_manager.playlist), 1)
            self.assertEqual(self.playlist_manager.playlist[0].file_path, file_path)
            self.assertEqual(self.favorites_manager.favorites, [])

    def test_showing_lyrics_window_loads_current_song_lyrics_immediately(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mainwindow_lyrics_") as tmpdir:
            file_path = Path(create_test_wav(tmpdir, "lyrics.wav")).resolve()
            file_path.with_suffix(".lrc").write_text(
                "[00:00.00]Line one\n[00:01.00]Line two\n",
                encoding="utf-8",
            )
            song = Song(title="Lyrics", artist="Artist", file_path=str(file_path), file_format="wav")

            self.playlist_manager.load_playlist([song], start_index=0)
            self.window._last_position_ms = 0

            self.window._on_toggle_lyrics(True)

            self.assertEqual(len(self.window._lyrics_window._lyrics), 2)
            self.assertEqual(self.window._lyrics_window._current_line_index, 0)

    def test_hiding_lyrics_window_externally_clears_view_menu_checkmark(self) -> None:
        self.window._on_toggle_lyrics(True)
        self.assertTrue(self.window._show_lyrics_action.isChecked())

        self.window._lyrics_window.hide()
        self._app.processEvents()

        self.assertFalse(self.window._show_lyrics_action.isChecked())


if __name__ == "__main__":
    unittest.main()
