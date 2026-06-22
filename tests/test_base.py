"""Base test utilities: shared helpers and sample data for all tests."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import List

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from app.models.song import Song


def ensure_qapp() -> QCoreApplication:
    """Return an existing QApplication or create one."""
    app = QCoreApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def create_test_wav(
    directory: str | Path,
    name: str,
    duration_sec: float = 0.3,
    frequency: float = 440.0,
    sample_rate: int = 44100,
) -> str:
    """Create a short stereo WAV file for tests and return its path."""
    n = int(sample_rate * duration_sec)
    samples = bytearray()
    for i in range(n):
        val = int(math.sin(2 * math.pi * frequency * i / sample_rate) * 32767 * 0.3)
        samples += struct.pack("<hh", val, val)

    path = Path(directory) / name
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(samples))
    return str(path)


SAMPLE_SONGS: List[Song] = [
    Song(title="Song A", artist="Artist 1", album="Album X",
         duration=210.0, file_path="/music/a.mp3",
         file_format="mp3", file_hash="aaa"),
    Song(title="Song B", artist="Artist 1", album="Album X",
         duration=180.0, file_path="/music/b.flac",
         file_format="flac", file_hash="bbb"),
    Song(title="Song C", artist="Artist 2", album="Album Y",
         duration=240.0, file_path="/music/c.wav",
         file_format="wav", file_hash="ccc"),
    Song(title="Song D", artist="Artist 2", album="Album Y",
         duration=200.0, file_path="/music/d.aac",
         file_format="aac", file_hash="ddd"),
]


class SignalCapture:
    """Context manager that captures emissions of a given Qt signal.

    Usage::

        with SignalCapture(obj.signal_name) as captured:
            obj.do_something()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0], (expected_value,))
    """

    def __init__(self, signal: object) -> None:
        self._signal = signal
        self.args: list = []

    def __enter__(self) -> "SignalCapture":
        self._signal.connect(self._on_emitted)  # type: ignore[attr-defined]
        return self

    def __exit__(self, *args: object) -> None:
        self._signal.disconnect(self._on_emitted)  # type: ignore[attr-defined]

    def _on_emitted(self, *args: object) -> None:
        self.args.append(args)

    def __len__(self) -> int:
        return len(self.args)

    def __getitem__(self, idx: int) -> tuple:
        return self.args[idx]
