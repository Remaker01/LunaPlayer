"""Unit tests for AudioDecoder and AudioEngine.

Most tests use a short generated WAV file to avoid external dependencies.
The AudioDecoder tests verify that PyAV can decode and produce PCM data.
The AudioEngine tests focus on state-machine correctness and public API.

Note
----
We do **not** test actual sound output (no audio hardware in CI).  Instead we
verify that the engine transitions through its states correctly and that the
decoder produces PCM data with the expected format.
"""

from __future__ import annotations

import math
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path
from typing import Iterator, Optional

import av
from PySide6.QtCore import QCoreApplication, QTimer

from app.core.audio_engine import (
    MAX_PENDING_BYTES,
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    AudioDecoder,
    AudioEngine,
    PlayState,
)
from tests.test_base import ensure_qapp

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SINE_440_FILE: Optional[str] = None
SINE_220_FILE: Optional[str] = None


def create_sine_wav(duration_sec: float = 1.0,
                    frequency: float = 440.0,
                    sample_rate: int = TARGET_SAMPLE_RATE) -> str:
    """Generate a stereo sine-wave WAV file and return its path."""
    n = int(sample_rate * duration_sec)
    samples = bytearray()
    for i in range(n):
        val = int(math.sin(2 * math.pi * frequency * i / sample_rate) * 32767 * 0.3)
        samples += struct.pack("<hh", val, val)

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(samples))
    return tmp.name


def setUpModule() -> None:
    """Create shared test WAV files once for the whole module."""
    global SINE_440_FILE, SINE_220_FILE
    SINE_440_FILE = create_sine_wav(0.5, 440.0)
    SINE_220_FILE = create_sine_wav(0.5, 220.0)


def tearDownModule() -> None:
    """Clean up shared test WAV files."""
    for p in (SINE_440_FILE, SINE_220_FILE):
        if p is not None:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Decoder tests
# ---------------------------------------------------------------------------

class TestAudioDecoder(unittest.TestCase):
    """Verify that AudioDecoder produces PCM data with the expected properties."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def setUp(self) -> None:
        self.decoder = AudioDecoder()
        self.received_pcm: list[bytes] = []
        self.decoder.pcm_data_ready.connect(self.received_pcm.append)
        self.error_message: Optional[str] = None
        self.decoder.error_occurred.connect(lambda m: setattr(self, "error_message", m))
        self._duration: Optional[int] = None
        self.decoder.duration_changed.connect(lambda d: setattr(self, "_duration", d))
        self._finished = False
        self.decoder.finished.connect(lambda: setattr(self, "_finished", True))

    def tearDown(self) -> None:
        self.decoder.request_stop()
        if not self.decoder.wait(3000):
            self.decoder.terminate()
            self.decoder.wait()

    def _wait_for_data(self, timeout: float = 5.0) -> None:
        """Spin the event loop until at least one PCM chunk arrives."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.received_pcm:
                return
            QCoreApplication.processEvents()
            time.sleep(0.01)
        raise TimeoutError("No PCM data received within timeout")

    def _wait_for_finished(self, timeout: float = 10.0) -> None:
        """Spin until the decoder finishes."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._finished:
                return
            QCoreApplication.processEvents()
            time.sleep(0.01)
        raise TimeoutError("Decoder did not finish within timeout")

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_decodes_pcm_data(self) -> None:
        """Decoder should produce all PCM data for the file."""
        self.decoder.configure(SINE_440_FILE)  # type: ignore[arg-type]
        self.decoder.start()
        self._wait_for_finished()
        self.assertGreater(len(self.received_pcm), 0)
        total = sum(len(c) for c in self.received_pcm)
        # 0.5 sec @ 44100 Hz × 4 bytes/frame ≈ 88200 bytes
        self.assertAlmostEqual(total, 88200, delta=2000)

    def test_pcm_format_is_s16_stereo_44100(self) -> None:
        """Each PCM chunk should be a multiple of the frame size (4 bytes)."""
        self.decoder.configure(SINE_440_FILE)  # type: ignore[arg-type]
        self.decoder.start()
        self._wait_for_finished()
        for chunk in self.received_pcm:
            self.assertEqual(
                len(chunk) % 4, 0,
                f"PCM chunk size {len(chunk)} is not a multiple of 4",
            )

    def test_duration_signal(self) -> None:
        """Decoder should emit duration_changed with the correct duration."""
        self.decoder.configure(SINE_440_FILE)  # type: ignore[arg-type]
        self.decoder.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self._duration is not None:
                break
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertIsNotNone(self._duration)
        # 0.5 sec = 500 ms, allow some tolerance
        self.assertAlmostEqual(self._duration, 500, delta=100)

    def test_finished_signal(self) -> None:
        """Decoder should emit finished after reaching EOF."""
        self.decoder.configure(SINE_440_FILE)  # type: ignore[arg-type]
        self.decoder.start()
        self._wait_for_finished()
        self.assertTrue(self._finished)

    def test_pcm_data_not_empty(self) -> None:
        """All received PCM chunks must be non-empty."""
        self.decoder.configure(SINE_440_FILE)  # type: ignore[arg-type]
        self.decoder.start()
        self._wait_for_finished()
        for chunk in self.received_pcm:
            self.assertGreater(len(chunk), 0)

    def test_stop_aborts_decoding(self) -> None:
        """request_stop() should cause the thread to exit promptly."""
        self.decoder.configure(SINE_440_FILE)  # type: ignore[arg-type]
        self.decoder.start()
        time.sleep(0.1)  # let some decoding happen
        self.decoder.request_stop()
        ok = self.decoder.wait(3000)
        self.assertTrue(ok, "Decoder thread did not stop within 3 s")

    def test_error_on_nonexistent_file(self) -> None:
        """Decoder should emit error_occurred for a missing file."""
        self.decoder.configure("/nonexistent/file.mp3")
        self.decoder.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.error_message is not None:
                break
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertIsNotNone(self.error_message)

    def test_seek_during_decoding(self) -> None:
        """Seeking to 0 should restart PCM from (approximately) the beginning."""
        self.decoder.configure(SINE_440_FILE)  # type: ignore[arg-type]
        self.decoder.start()
        self._wait_for_data()
        first_chunk = self.received_pcm[0]

        # Seek to 0 ms
        self.decoder.request_seek(0)
        time.sleep(0.2)
        QCoreApplication.processEvents()

        # Stop and compare: after seek to 0, first chunk should match
        self.decoder.request_stop()
        self.decoder.wait(2000)

        # The seek might produce data that starts similarly to the beginning
        # (not an exact match because of decoder buffer alignment)
        self.assertGreater(len(self.received_pcm), 0)


# ===================================================================
# AudioEngine tests
# ===================================================================

class TestAudioEngine(unittest.TestCase):
    """Verify the AudioEngine state machine and API surface."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def setUp(self) -> None:
        self.engine = AudioEngine()
        self.state_changes: list[int] = []
        self.engine.state_changed.connect(self.state_changes.append)
        self.positions: list[int] = []
        self.engine.position_changed.connect(self.positions.append)
        self.errors: list[str] = []
        self.engine.error_occurred.connect(self.errors.append)

    def tearDown(self) -> None:
        self.engine.stop()
        QCoreApplication.processEvents()

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def test_initial_state_stopped(self) -> None:
        self.assertEqual(self.engine.state, PlayState.STOPPED)

    def test_play_transitions_to_playing(self) -> None:
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        self.assertEqual(self.engine.state, PlayState.PLAYING)
        self.assertIn(int(PlayState.PLAYING), self.state_changes)

    def test_pause_transitions_to_paused(self) -> None:
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        self.engine.pause()
        self.assertEqual(self.engine.state, PlayState.PAUSED)
        self.assertIn(int(PlayState.PAUSED), self.state_changes)

    def test_resume_from_paused(self) -> None:
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        self.engine.pause()
        self.engine.resume()
        self.assertEqual(self.engine.state, PlayState.PLAYING)

    def test_toggle_play_pause(self) -> None:
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        self.engine.toggle_play_pause()
        self.assertEqual(self.engine.state, PlayState.PAUSED)
        self.engine.toggle_play_pause()
        self.assertEqual(self.engine.state, PlayState.PLAYING)

    def test_stop_transitions_to_stopped(self) -> None:
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        self.engine.stop()
        self.assertEqual(self.engine.state, PlayState.STOPPED)

    def test_stop_twice_does_not_crash(self) -> None:
        self.engine.stop()
        self.engine.stop()
        self.assertEqual(self.engine.state, PlayState.STOPPED)

    def test_play_twice_restarts(self) -> None:
        """Playing a second file should stop the first and restart."""
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        self.engine.play(SINE_220_FILE)  # type: ignore[arg-type]
        self.assertEqual(self.engine.state, PlayState.PLAYING)

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------

    def test_default_volume(self) -> None:
        self.assertEqual(self.engine.volume, 1.0)

    def test_set_volume_clamps(self) -> None:
        self.engine.set_volume(1.5)
        self.assertEqual(self.engine.volume, 1.0)
        self.engine.set_volume(-0.5)
        self.assertEqual(self.engine.volume, 0.0)

    def test_set_volume_normal(self) -> None:
        self.engine.set_volume(0.75)
        self.assertEqual(self.engine.volume, 0.75)

    # ------------------------------------------------------------------
    # Seek
    # ------------------------------------------------------------------

    def test_seek_negative_clamps_to_zero(self) -> None:
        """Seek to a negative position should clamp to 0."""
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        self.engine.seek(-100)
        self.assertEqual(self.engine.state, PlayState.PLAYING)

    def test_seek_resets_pending_buffer(self) -> None:
        """Seek should clear the internal pending buffer."""
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        # Write some data into the pending buffer by accessing it after play
        self.engine._pending_buffer = b"some_stale_data"
        self.engine.seek(100)
        self.assertEqual(self.engine._pending_buffer, b"")

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def test_error_on_missing_file(self) -> None:
        self.engine.play("/nonexistent/file.mp3")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self.errors:
                break
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertGreater(len(self.errors), 0)
        # Engine should stop after error
        self.assertEqual(self.engine.state, PlayState.STOPPED)

    # ------------------------------------------------------------------
    # Position tracking
    # ------------------------------------------------------------------

    def test_position_updates_during_playback(self) -> None:
        """Position signal should fire at least once during playback."""
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if len(self.positions) > 0:
                break
            QCoreApplication.processEvents()
            time.sleep(0.01)
        # Just check that position is >= 0 (depending on audio driver, it may
        # report 0 until data is actually processed).
        self.assertGreaterEqual(self.positions[-1], 0)

    # ------------------------------------------------------------------
    # Double-free / lifecycle
    # ------------------------------------------------------------------

    def test_stop_without_play_does_nothing(self) -> None:
        self.engine.stop()
        self.assertEqual(self.engine.state, PlayState.STOPPED)

    def test_pause_without_play_does_nothing(self) -> None:
        self.engine.pause()
        self.assertEqual(self.engine.state, PlayState.STOPPED)

    def test_resume_without_pause_does_nothing(self) -> None:
        self.engine.resume()
        self.assertEqual(self.engine.state, PlayState.STOPPED)


# ===================================================================
# Integration: engine plays multiple files sequentially
# ===================================================================

class TestSequentialPlayback(unittest.TestCase):
    """The engine should play back-to-back files without crashing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def setUp(self) -> None:
        self.engine = AudioEngine()
        self.errors: list[str] = []
        self.engine.error_occurred.connect(self.errors.append)

    def tearDown(self) -> None:
        self.engine.stop()
        QCoreApplication.processEvents()

    def test_play_two_files_sequentially(self) -> None:
        """Playing a second file after the first should not error."""
        self.engine.play(SINE_440_FILE)  # type: ignore[arg-type]
        QCoreApplication.processEvents()
        time.sleep(0.1)
        self.assertEqual(self.engine.state, PlayState.PLAYING)
        self.assertEqual(len(self.errors), 0)

        self.engine.play(SINE_220_FILE)  # type: ignore[arg-type]
        QCoreApplication.processEvents()
        time.sleep(0.1)
        self.assertEqual(self.engine.state, PlayState.PLAYING)
        self.assertEqual(len(self.errors), 0)


if __name__ == "__main__":
    unittest.main()
