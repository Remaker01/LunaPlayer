"""Unit tests for MusicScanner.

Uses generated WAV files to verify metadata extraction and directory scanning.
"""

from __future__ import annotations

import math
import os
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from app.core.music_scanner import MusicScanner, extract_metadata, _compute_hash
from app.models.song import Song
from tests.test_base import create_temp_db, destroy_temp_db, ensure_qapp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_RATE = 44100


def _make_wav(directory: str, name: str, duration_sec: float = 0.3,
              frequency: float = 440.0) -> str:
    """Create a short stereo WAV file and return its full path."""
    n = int(SAMPLE_RATE * duration_sec)
    samples = bytearray()
    for i in range(n):
        v = int(math.sin(2 * math.pi * frequency * i / SAMPLE_RATE) * 32767 * 0.3)
        samples += struct.pack("<hh", v, v)

    path = os.path.join(directory, name)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(samples))
    return path


# ===================================================================
# Test extract_metadata (module-level helper)
# ===================================================================

class TestExtractMetadata(unittest.TestCase):
    """Verify metadata extraction from real audio files."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.mkdtemp(prefix="scanner_test_")

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        self.wav_path = _make_wav(self._tmpdir, "test_song.wav")

    def tearDown(self) -> None:
        Path(self.wav_path).unlink(missing_ok=True)

    def test_returns_song_for_valid_file(self) -> None:
        song = extract_metadata(self.wav_path)
        self.assertIsNotNone(song)
        self.assertIsInstance(song, Song)

    def test_title_falls_back_to_filename(self) -> None:
        """WAV files have no embedded title – the stem should be used."""
        song = extract_metadata(self.wav_path)
        self.assertIsNotNone(song)
        self.assertEqual(song.title, "test_song")

    def test_duration_is_positive(self) -> None:
        song = extract_metadata(self.wav_path)
        self.assertIsNotNone(song)
        self.assertGreater(song.duration, 0)

    def test_file_format_is_wav(self) -> None:
        song = extract_metadata(self.wav_path)
        self.assertIsNotNone(song)
        self.assertEqual(song.file_format, "wav")

    def test_hash_is_non_empty(self) -> None:
        song = extract_metadata(self.wav_path)
        self.assertIsNotNone(song)
        self.assertTrue(len(song.file_hash) > 0)

    def test_returns_none_for_unsupported_file(self) -> None:
        txt = os.path.join(self._tmpdir, "readme.txt")
        Path(txt).write_text("hello")
        song = extract_metadata(txt)
        self.assertIsNone(song)

    def test_returns_none_for_nonexistent_file(self) -> None:
        song = extract_metadata("/nonexistent.wav")
        self.assertIsNone(song)


class TestComputeHash(unittest.TestCase):
    """SHA-256 hash computation."""

    def test_hash_is_consistent(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as f:
            f.write(b"hello world")
            p = f.name
        try:
            h1 = _compute_hash(p)
            h2 = _compute_hash(p)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)  # SHA-256 hex = 64 chars
        finally:
            Path(p).unlink(missing_ok=True)

    def test_different_files_different_hash(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as f:
            f.write(b"file a")
            pa = f.name
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as f:
            f.write(b"file b")
            pb = f.name
        try:
            ha = _compute_hash(pa)
            hb = _compute_hash(pb)
            self.assertNotEqual(ha, hb)
        finally:
            Path(pa).unlink(missing_ok=True)
            Path(pb).unlink(missing_ok=True)


# ===================================================================
# Integration: full MusicScanner (background thread)
# ===================================================================

class TestMusicScanner(unittest.TestCase):
    """Test the MusicScanner QThread."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()
        cls._tmpdir = tempfile.mkdtemp(prefix="scanner_int_")

        # Create some test audio files
        cls._files: list[str] = []
        cls._files.append(_make_wav(cls._tmpdir, "alpha.wav", 0.5, 440))
        cls._files.append(_make_wav(cls._tmpdir, "beta.wav", 0.5, 660))

        # Create a sub-directory with more files
        subdir = os.path.join(cls._tmpdir, "sub")
        os.makedirs(subdir, exist_ok=True)
        cls._files.append(_make_wav(subdir, "gamma.wav", 0.3, 880))

        # Create a non-audio file that should be ignored
        Path(os.path.join(cls._tmpdir, "notes.txt")).write_text("not music")

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        self.db = create_temp_db()
        self.scanner = MusicScanner(self.db)
        self.signals: dict[str, list] = {
            "started": [],
            "progress": [],
            "song_found": [],
            "finished": [],
            "errors": [],
        }
        self.scanner.scan_started.connect(
            lambda d: self.signals["started"].append(d))
        self.scanner.progress.connect(
            lambda c, t: self.signals["progress"].append((c, t)))
        self.scanner.song_found.connect(
            lambda s: self.signals["song_found"].append(s))
        self.scanner.scan_finished.connect(
            lambda n: self.signals["finished"].append(n))
        self.scanner.error_occurred.connect(
            lambda m: self.signals["errors"].append(m))

    def tearDown(self) -> None:
        self.scanner.request_stop()
        if not self.scanner.wait(5000):
            self.scanner.terminate()
            self.scanner.wait()
        destroy_temp_db(self.db)

    def _wait_for_finished(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.signals["finished"]:
                return
            QCoreApplication.processEvents()
            time.sleep(0.02)
        raise TimeoutError("Scanner did not finish within timeout")

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_scanner_finds_all_audio_files(self) -> None:
        """Should find 3 WAV files and ignore notes.txt."""
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        imported = self.signals["finished"][0]
        self.assertEqual(imported, 3, f"Expected 3 files, got {imported}")

    def test_scanner_emits_started_signal(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertEqual(len(self.signals["started"]), 1)
        self.assertIn("scanner_int", self.signals["started"][0])

    def test_scanner_emits_progress(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertGreater(len(self.signals["progress"]), 0)
        last_progress = self.signals["progress"][-1]
        self.assertEqual(last_progress, (3, 3))

    def test_scanner_emits_song_found(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertEqual(len(self.signals["song_found"]), 3)

    def test_songs_are_in_database(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        songs = self.db.get_all_songs()
        self.assertEqual(len(songs), 3)

    def test_songs_have_correct_metadata(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        songs = self.db.get_all_songs()
        titles = {s.title for s in songs}
        self.assertIn("alpha", titles)
        self.assertIn("beta", titles)
        self.assertIn("gamma", titles)

    def test_scanning_twice_is_idempotent(self) -> None:
        """Second scan should **not** import duplicates."""
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()

        # Reset signal capture
        self.signals["song_found"].clear()
        self.signals["finished"].clear()

        # Scan again with a fresh scanner connected to the same DB
        scanner2 = MusicScanner(self.db)
        found2: list[Song] = []
        scanner2.song_found.connect(found2.append)
        finished2: list[int] = []
        scanner2.scan_finished.connect(finished2.append)

        scanner2.scan_directory(self._tmpdir)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if finished2:
                break
            QCoreApplication.processEvents()
            time.sleep(0.02)

        self.assertEqual(finished2[0], 0,
                         "Second scan should import 0 new songs")
        self.assertEqual(len(found2), 0)

        # DB should still have 3 songs
        self.assertEqual(self.db.song_count(), 3)

        scanner2.request_stop()
        scanner2.wait(3000)

    def test_stop_aborts_scan(self) -> None:
        """request_stop() should cause scanner to exit early."""
        self.scanner.scan_directory(self._tmpdir)
        time.sleep(0.1)
        QCoreApplication.processEvents()
        self.scanner.request_stop()
        ok = self.scanner.wait(5000)
        self.assertTrue(ok, "Scanner did not stop within 5 s")
        # finished may or may not have been emitted – that's acceptable.

    def test_no_errors_during_normal_scan(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertEqual(len(self.signals["errors"]), 0,
                         f"Unexpected errors: {self.signals['errors']}")

    def test_songs_have_hash(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        for song in self.db.get_all_songs():
            self.assertTrue(len(song.file_hash) > 0,
                            f"Song {song.title} has empty hash")

    def test_songs_have_duration(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        for song in self.db.get_all_songs():
            self.assertGreater(song.duration, 0,
                               f"Song {song.title} has zero duration")


if __name__ == "__main__":
    unittest.main()
