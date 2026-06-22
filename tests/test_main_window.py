"""Behavior tests for MainWindow startup restore."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.core.audio_engine import AudioEngine
from app.core.music_scanner import MusicScanner
from app.core.playlist_manager import PlaylistManager
from app.models.song import Song
from app.ui.main_window import MainWindow
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
        self.playlist_manager = PlaylistManager()
        self.audio_engine = AudioEngine()
        self.audio_engine.play = MagicMock()  # type: ignore[method-assign]
        self.music_scanner = MusicScanner()
        self.window = _TestMainWindow(
            playlist_manager=self.playlist_manager,
            audio_engine=self.audio_engine,
            music_scanner=self.music_scanner,
        )

    def tearDown(self) -> None:
        self.playlist_manager.save_to_m3u = MagicMock()  # type: ignore[method-assign]
        self.window.close()
        self.audio_engine.stop()

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


if __name__ == "__main__":
    unittest.main()
