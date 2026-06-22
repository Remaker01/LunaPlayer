"""Unit tests for MusicScanner.

Uses generated WAV files to verify metadata extraction and directory scanning.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from app.core.music_scanner import MusicScanner, extract_metadata, _compute_hash
from app.models.song import Song
from tests.test_base import create_test_wav, ensure_qapp


class TestExtractMetadata(unittest.TestCase):
    """Verify metadata extraction from real audio files."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.mkdtemp(prefix="scanner_test_")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        self.wav_path = create_test_wav(self._tmpdir, "test_song.wav")

    def tearDown(self) -> None:
        Path(self.wav_path).unlink(missing_ok=True)

    def test_returns_song_for_valid_file(self) -> None:
        song = extract_metadata(self.wav_path)
        self.assertIsNotNone(song)
        self.assertIsInstance(song, Song)

    def test_title_falls_back_to_filename(self) -> None:
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
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as handle:
            handle.write(b"hello world")
            path = handle.name
        try:
            h1 = _compute_hash(path)
            h2 = _compute_hash(path)
            self.assertEqual(h1, h2)
            self.assertEqual(len(h1), 64)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_different_files_different_hash(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as handle:
            handle.write(b"file a")
            path_a = handle.name
        with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as handle:
            handle.write(b"file b")
            path_b = handle.name
        try:
            self.assertNotEqual(_compute_hash(path_a), _compute_hash(path_b))
        finally:
            Path(path_a).unlink(missing_ok=True)
            Path(path_b).unlink(missing_ok=True)


class TestMusicScanner(unittest.TestCase):
    """Test the MusicScanner QThread as a pure file scanner."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()
        cls._tmpdir = tempfile.mkdtemp(prefix="scanner_int_")

        cls._files: list[str] = []
        cls._files.append(create_test_wav(cls._tmpdir, "alpha.wav", 0.5, 440))
        cls._files.append(create_test_wav(cls._tmpdir, "beta.wav", 0.5, 660))

        subdir = os.path.join(cls._tmpdir, "sub")
        os.makedirs(subdir, exist_ok=True)
        cls._files.append(create_test_wav(subdir, "gamma.wav", 0.3, 880))

        Path(os.path.join(cls._tmpdir, "notes.txt")).write_text("not music")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        self.scanner = MusicScanner()
        self.signals: dict[str, list] = {
            "started": [],
            "progress": [],
            "song_found": [],
            "finished": [],
            "errors": [],
        }
        self.scanner.scan_started.connect(lambda d: self.signals["started"].append(d))
        self.scanner.progress.connect(lambda c, t: self.signals["progress"].append((c, t)))
        self.scanner.song_found.connect(lambda s: self.signals["song_found"].append(s))
        self.scanner.scan_finished.connect(lambda n: self.signals["finished"].append(n))
        self.scanner.error_occurred.connect(lambda m: self.signals["errors"].append(m))

    def tearDown(self) -> None:
        self.scanner.request_stop()
        if not self.scanner.wait(5000):
            self.scanner.terminate()
            self.scanner.wait()

    def _wait_for_finished(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.signals["finished"]:
                return
            QCoreApplication.processEvents()
            time.sleep(0.02)
        raise TimeoutError("Scanner did not finish within timeout")

    def test_scanner_finds_all_audio_files(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertEqual(self.signals["finished"][0], 3)

    def test_scanner_emits_started_signal(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertEqual(len(self.signals["started"]), 1)
        self.assertIn("scanner_int", self.signals["started"][0])

    def test_scanner_emits_progress(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertGreater(len(self.signals["progress"]), 0)
        self.assertEqual(self.signals["progress"][-1], (3, 3))

    def test_scanner_emits_song_found(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertEqual(len(self.signals["song_found"]), 3)

    def test_songs_have_correct_metadata(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        titles = {song.title for song in self.signals["song_found"]}
        self.assertIn("alpha", titles)
        self.assertIn("beta", titles)
        self.assertIn("gamma", titles)

    def test_scanning_twice_returns_same_results(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()

        scanner2 = MusicScanner()
        found2: list[Song] = []
        finished2: list[int] = []
        scanner2.song_found.connect(found2.append)
        scanner2.scan_finished.connect(finished2.append)

        scanner2.scan_directory(self._tmpdir)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if finished2:
                break
            QCoreApplication.processEvents()
            time.sleep(0.02)

        self.assertEqual(finished2[0], 3)
        self.assertEqual(len(found2), 3)

        scanner2.request_stop()
        scanner2.wait(3000)

    def test_stop_aborts_scan(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        time.sleep(0.1)
        QCoreApplication.processEvents()
        self.scanner.request_stop()
        self.assertTrue(self.scanner.wait(5000))

    def test_no_errors_during_normal_scan(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        self.assertEqual(self.signals["errors"], [])

    def test_songs_have_hash(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        for song in self.signals["song_found"]:
            self.assertTrue(len(song.file_hash) > 0)

    def test_songs_have_duration(self) -> None:
        self.scanner.scan_directory(self._tmpdir)
        self._wait_for_finished()
        for song in self.signals["song_found"]:
            self.assertGreater(song.duration, 0)

    def test_moved_file_is_discovered_at_new_path(self) -> None:
        first_dir = tempfile.mkdtemp(prefix="scanner_move_src_")
        second_dir = tempfile.mkdtemp(prefix="scanner_move_dst_")
        try:
            original_path = create_test_wav(first_dir, "moved_song.wav", 0.2, 523.25)
            moved_path = os.path.join(second_dir, "moved_song.wav")
            shutil.move(original_path, moved_path)

            scanner2 = MusicScanner()
            found2: list[Song] = []
            finished2: list[int] = []
            scanner2.song_found.connect(found2.append)
            scanner2.scan_finished.connect(finished2.append)

            scanner2.scan_directory(second_dir)
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if finished2:
                    break
                QCoreApplication.processEvents()
                time.sleep(0.02)

            self.assertEqual(finished2[0], 1)
            self.assertEqual(found2[0].file_path, str(Path(moved_path).resolve()))

            scanner2.request_stop()
            scanner2.wait(3000)
        finally:
            shutil.rmtree(first_dir, ignore_errors=True)
            shutil.rmtree(second_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
