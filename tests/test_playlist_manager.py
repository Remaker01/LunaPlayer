"""Unit tests for PlaylistManager.

PlaylistManager is a QObject – a QCoreApplication must be available for signal
tests to work; the *test_base* module handles this automatically.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


from app.core.playlist_manager import PlaylistManager
from app.models.song import PlayMode, Song

from tests.test_base import (
    SAMPLE_SONGS,
    SignalCapture,
    create_test_wav,
    ensure_qapp,
)


class PlaylistManagerTestCase(unittest.TestCase):
    """Base class for PlaylistManager tests.

    Ensures a QCoreApplication exists (needed for QObject signal/slot).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def setUp(self) -> None:
        self.manager = PlaylistManager()
        self.loaded = PlaylistManager()
        self.loaded.load_playlist(SAMPLE_SONGS, start_index=0)


# ======================================================================
# Initial state
# ======================================================================

class TestInitialState(PlaylistManagerTestCase):
    def test_empty_playlist(self) -> None:
        self.assertEqual(self.manager.playlist, [])
        self.assertEqual(self.manager.current_index, -1)
        self.assertIsNone(self.manager.get_current_song())

    def test_default_play_mode(self) -> None:
        self.assertEqual(self.manager.play_mode, PlayMode.SEQUENTIAL)


# ======================================================================
# Loading
# ======================================================================

class TestLoadPlaylist(PlaylistManagerTestCase):
    def test_load_sets_index_zero(self) -> None:
        self.assertEqual(self.loaded.current_index, 0)
        self.assertIsNotNone(self.loaded.get_current_song())

    def test_load_with_custom_index(self) -> None:
        self.manager.load_playlist(SAMPLE_SONGS, start_index=2)
        self.assertEqual(self.manager.current_index, 2)
        self.assertEqual(self.manager.get_current_song(), SAMPLE_SONGS[2])

    def test_load_out_of_range_index(self) -> None:
        self.manager.load_playlist(SAMPLE_SONGS, start_index=999)
        self.assertEqual(self.manager.current_index, -1)
        self.assertIsNone(self.manager.get_current_song())

    def test_load_empty_list(self) -> None:
        self.manager.load_playlist([])
        self.assertEqual(self.manager.current_index, -1)
        self.assertIsNone(self.manager.get_current_song())

    def test_load_emits_signals(self) -> None:
        with (SignalCapture(self.manager.playlist_loaded) as loaded_sig,
              SignalCapture(self.manager.current_index_changed) as index_sig,
              SignalCapture(self.manager.current_song_changed) as song_sig):
            self.manager.load_playlist(SAMPLE_SONGS, start_index=1)
        self.assertEqual(len(loaded_sig), 1)
        self.assertEqual(len(index_sig), 1)
        self.assertEqual(index_sig[0], (1,))
        self.assertEqual(len(song_sig), 1)
        self.assertEqual(song_sig[0][0], SAMPLE_SONGS[1])


# ======================================================================
# Playlist mutation
# ======================================================================

class TestPlaylistMutation(PlaylistManagerTestCase):
    def test_add_song(self) -> None:
        idx = self.manager.add_song(SAMPLE_SONGS[0])
        self.assertEqual(idx, 0)
        self.assertEqual(len(self.manager.playlist), 1)
        self.assertEqual(self.manager.playlist[0], SAMPLE_SONGS[0])

    def test_add_song_emits_signal(self) -> None:
        with SignalCapture(self.manager.song_added) as sig:
            self.manager.add_song(SAMPLE_SONGS[0])
        self.assertEqual(len(sig), 1)
        self.assertEqual(sig[0], (0,))

    def test_remove_song(self) -> None:
        removed = self.loaded.remove_song(0)
        self.assertIsNotNone(removed)
        self.assertEqual(len(self.loaded.playlist), 3)

    def test_remove_song_out_of_range(self) -> None:
        self.assertIsNone(self.manager.remove_song(0))

    def test_remove_current_song_adjusts_index(self) -> None:
        """Removing the current song should move index to new slot 0."""
        self.loaded.remove_song(0)
        self.assertEqual(self.loaded.current_index, 0)
        self.assertIsNotNone(self.loaded.get_current_song())

    def test_remove_song_before_current(self) -> None:
        self.loaded.go_to(3)
        self.loaded.remove_song(1)
        self.assertEqual(self.loaded.current_index, 2)

    def test_remove_last_song(self) -> None:
        self.manager.load_playlist(SAMPLE_SONGS, start_index=0)
        for _ in range(4):
            self.manager.remove_song(0)
        self.assertEqual(self.manager.current_index, -1)
        self.assertIsNone(self.manager.get_current_song())

    def test_clear(self) -> None:
        self.loaded.clear()
        self.assertEqual(self.loaded.playlist, [])
        self.assertEqual(self.loaded.current_index, -1)

    def test_clear_emits_signals(self) -> None:
        with (SignalCapture(self.loaded.playlist_loaded) as loaded_sig,
              SignalCapture(self.loaded.current_song_changed) as song_sig):
            self.loaded.clear()
        self.assertEqual(len(loaded_sig), 1)
        self.assertEqual(song_sig[0], (None,))

    def test_reorder_playlist_updates_order_without_restarting_current_song(self) -> None:
        self.loaded.go_to(1)
        reordered = [SAMPLE_SONGS[0], SAMPLE_SONGS[2], SAMPLE_SONGS[1], SAMPLE_SONGS[3]]

        with SignalCapture(self.loaded.current_song_changed) as song_sig:
            self.loaded.reorder_playlist(reordered)

        self.assertEqual(self.loaded.current_index, 2)
        self.assertEqual(self.loaded.get_current_song(), SAMPLE_SONGS[1])
        self.assertEqual(len(song_sig), 0)

    def test_reorder_playlist_changes_next_track_to_new_order(self) -> None:
        self.loaded.go_to(1)
        reordered = [SAMPLE_SONGS[0], SAMPLE_SONGS[2], SAMPLE_SONGS[1], SAMPLE_SONGS[3]]
        self.loaded.reorder_playlist(reordered)

        next_song = self.loaded.next()

        self.assertEqual(next_song, SAMPLE_SONGS[3])
        self.assertEqual(self.loaded.current_index, 3)


# ======================================================================
# Navigation – Sequential
# ======================================================================

class TestSequentialNavigation(PlaylistManagerTestCase):
    def test_next_advances_index(self) -> None:
        song = self.loaded.next()
        self.assertEqual(self.loaded.current_index, 1)
        self.assertIsNotNone(song)

    def test_next_at_end_returns_none(self) -> None:
        self.loaded.go_to(3)
        song = self.loaded.next()
        self.assertIsNone(song)
        self.assertEqual(self.loaded.current_index, -1)

    def test_previous_goes_back(self) -> None:
        self.loaded.go_to(2)
        song = self.loaded.previous()
        self.assertEqual(self.loaded.current_index, 1)
        self.assertIsNotNone(song)

    def test_previous_at_start_stays(self) -> None:
        song = self.loaded.previous()
        self.assertEqual(self.loaded.current_index, 0)
        self.assertIsNotNone(song)

    def test_next_empty_playlist(self) -> None:
        self.assertIsNone(self.manager.next())

    def test_previous_empty_playlist(self) -> None:
        self.assertIsNone(self.manager.previous())

    def test_go_to(self) -> None:
        song = self.loaded.go_to(2)
        self.assertEqual(self.loaded.current_index, 2)
        self.assertIsNotNone(song)

    def test_go_to_out_of_range(self) -> None:
        self.assertIsNone(self.loaded.go_to(999))


# ======================================================================
# Navigation – Loop mode
# ======================================================================

class TestLoopNavigation(PlaylistManagerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.loaded.set_play_mode(PlayMode.LOOP)

    def test_next_wraps_to_start(self) -> None:
        self.loaded.go_to(3)
        song = self.loaded.next()
        self.assertEqual(self.loaded.current_index, 0)
        self.assertIsNotNone(song)

    def test_previous_wraps_to_end(self) -> None:
        self.loaded.go_to(0)
        song = self.loaded.previous()
        self.assertEqual(self.loaded.current_index, 3)
        self.assertIsNotNone(song)


# ======================================================================
# Navigation – Single-loop mode
# ======================================================================

class TestSingleLoopNavigation(PlaylistManagerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.loaded.set_play_mode(PlayMode.SINGLE_LOOP)

    def test_next_stays_on_same_song(self) -> None:
        song = self.loaded.next()
        self.assertEqual(self.loaded.current_index, 0)
        self.assertIsNotNone(song)

    def test_previous_stays_on_same_song(self) -> None:
        song = self.loaded.previous()
        self.assertEqual(self.loaded.current_index, 0)
        self.assertIsNotNone(song)


# ======================================================================
# Play mode switching
# ======================================================================

class TestPlayModeSwitch(PlaylistManagerTestCase):
    def test_set_play_mode(self) -> None:
        self.manager.set_play_mode(PlayMode.LOOP)
        self.assertEqual(self.manager.play_mode, PlayMode.LOOP)

    def test_set_same_mode_noop(self) -> None:
        self.manager.set_play_mode(PlayMode.SEQUENTIAL)
        self.assertEqual(self.manager.play_mode, PlayMode.SEQUENTIAL)

    def test_cycle_play_mode(self) -> None:
        self.assertEqual(self.manager.cycle_play_mode(), PlayMode.LOOP)
        self.assertEqual(self.manager.play_mode, PlayMode.LOOP)
        self.assertEqual(self.manager.cycle_play_mode(), PlayMode.SINGLE_LOOP)
        self.assertEqual(self.manager.cycle_play_mode(), PlayMode.SEQUENTIAL)

    def test_cycle_emits_signal(self) -> None:
        with SignalCapture(self.manager.play_mode_changed) as sig:
            self.manager.cycle_play_mode()
        self.assertEqual(len(sig), 1)
        self.assertEqual(sig[0][0], PlayMode.LOOP)


# ======================================================================
# Signal integrity
# ======================================================================

class TestSignals(PlaylistManagerTestCase):
    def test_next_emits_index_and_song(self) -> None:
        with (SignalCapture(self.loaded.current_index_changed) as idx_sig,
              SignalCapture(self.loaded.current_song_changed) as sng_sig):
            self.loaded.next()
        self.assertEqual(len(idx_sig), 1)
        self.assertEqual(len(sng_sig), 1)
        self.assertEqual(idx_sig[0], (1,))
        self.assertEqual(sng_sig[0][0], self.loaded.playlist[1])

    def test_previous_emits_signals(self) -> None:
        self.loaded.go_to(2)
        with (SignalCapture(self.loaded.current_index_changed) as idx_sig,
              SignalCapture(self.loaded.current_song_changed) as sng_sig):
            self.loaded.previous()
        self.assertEqual(idx_sig[0], (1,))
        self.assertEqual(sng_sig[0][0], self.loaded.playlist[1])

    def test_set_play_mode_emits(self) -> None:
        with SignalCapture(self.manager.play_mode_changed) as sig:
            self.manager.set_play_mode(PlayMode.LOOP)
        self.assertEqual(len(sig), 1)

    def test_set_play_mode_same_no_emit(self) -> None:
        with SignalCapture(self.manager.play_mode_changed) as sig:
            self.manager.set_play_mode(PlayMode.SEQUENTIAL)
        self.assertEqual(len(sig), 0)


class TestPlaylistPersistence(PlaylistManagerTestCase):
    def test_save_and_restore_session_state(self) -> None:
        with TemporaryDirectory(prefix="lunaplayer_home_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                custom_songs: list[Song] = []
                music_dir = Path(tmp_home) / "music"
                music_dir.mkdir(parents=True, exist_ok=True)
                for idx, _song in enumerate(SAMPLE_SONGS):
                    file_path = Path(create_test_wav(music_dir, f"track_{idx}.wav")).resolve()
                    custom_songs.append(
                        Song(
                            title=f"Track {idx}",
                            artist="Artist",
                            album="Album",
                            duration=0.3,
                            file_path=str(file_path),
                            file_format="wav",
                            file_hash=f"hash-{idx}",
                        )
                    )

                self.manager.load_playlist(custom_songs, start_index=2)
                self.manager.set_play_mode(PlayMode.LOOP)
                self.manager.set_session_state({
                    "current_file_path": custom_songs[2].file_path,
                    "position_ms": 91234,
                    "play_state": 2,
                })
                saved_path = self.manager.save_to_m3u()
                self.assertTrue(saved_path.endswith("current.m3u8"))

                restored = PlaylistManager()
                self.assertTrue(restored.load_from_m3u())
                self.assertEqual(restored.current_index, 2)
                self.assertEqual(restored.play_mode, PlayMode.LOOP)
                self.assertEqual(len(restored.playlist), 4)
                self.assertEqual(
                    restored.get_current_song().file_path,  # type: ignore[union-attr]
                    custom_songs[2].file_path,
                )
                self.assertEqual(
                    restored.session_state["current_file_path"],
                    custom_songs[2].file_path,
                )
                self.assertEqual(restored.session_state["position_ms"], 91234)
                self.assertEqual(restored.session_state["play_state"], 2)

    def test_load_from_m3u_skips_missing_entries(self) -> None:
        with TemporaryDirectory(prefix="lunaplayer_home_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                missing_song = Song(
                    title="Missing",
                    artist="Ghost",
                    file_path=str((Path(tmp_home) / "gone.mp3").resolve()),
                    file_format="mp3",
                    file_hash="gone-hash",
                )
                self.manager.load_playlist([missing_song], start_index=0)
                self.manager.save_to_m3u()

                restored = PlaylistManager()
                self.assertFalse(restored.load_from_m3u())

    def test_load_from_m3u_realigns_index_after_missing_entries(self) -> None:
        with TemporaryDirectory(prefix="lunaplayer_home_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                music_dir = Path(tmp_home) / "music"
                music_dir.mkdir(parents=True, exist_ok=True)
                missing_path = str((music_dir / "missing.wav").resolve())
                existing_path = str(Path(create_test_wav(music_dir, "survivor.wav")).resolve())

                songs = [
                    Song(title="Missing", file_path=missing_path, file_format="wav"),
                    Song(title="Survivor", file_path=existing_path, file_format="wav"),
                ]
                self.manager.load_playlist(songs, start_index=1)
                self.manager.set_session_state({
                    "current_file_path": existing_path,
                    "position_ms": 1234,
                    "play_state": 0,
                })
                self.manager.save_to_m3u()

                restored = PlaylistManager()
                self.assertTrue(restored.load_from_m3u())
                self.assertEqual(restored.current_index, 0)
                self.assertEqual(
                    restored.get_current_song().file_path,  # type: ignore[union-attr]
                    existing_path,
                )

    def test_load_from_m3u_migrates_legacy_data_directory(self) -> None:
        with TemporaryDirectory(prefix="lunaplayer_home_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                music_dir = Path(tmp_home) / "music"
                music_dir.mkdir(parents=True, exist_ok=True)
                existing_path = str(Path(create_test_wav(music_dir, "legacy.wav")).resolve())

                legacy_dir = Path(tmp_home) / ".smallplayer" / "playlists"
                legacy_dir.mkdir(parents=True, exist_ok=True)
                (legacy_dir / "current.m3u8").write_text(
                    "#EXTM3U\n"
                    f"{existing_path}\n",
                    encoding="utf-8",
                )
                (legacy_dir / "current.meta.json").write_text(
                    '{"current_index": 0, "play_mode": 1, "current_file_path": "%s"}'
                    % existing_path.replace("\\", "\\\\"),
                    encoding="utf-8",
                )

                restored = PlaylistManager()
                self.assertTrue(restored.load_from_m3u())
                self.assertEqual(restored.current_index, 0)
                self.assertEqual(restored.play_mode, PlayMode.LOOP)
                self.assertEqual(
                    restored.get_current_song().file_path,  # type: ignore[union-attr]
                    existing_path,
                )
                self.assertTrue(
                    (Path(tmp_home) / ".lunaplayer" / "playlists" / "current.m3u8").exists()
                )
