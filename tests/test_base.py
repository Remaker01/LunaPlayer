"""Base test utilities: shared helpers and sample data for all tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

from PySide6.QtCore import QCoreApplication

from app.models.database import DatabaseManager
from app.models.song import Song


def ensure_qapp() -> QCoreApplication:
    """Return an existing QCoreApplication or create one."""
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


def create_temp_db() -> DatabaseManager:
    """Create a DatabaseManager backed by a temporary SQLite file.

    The caller is responsible for calling ``mgr.close()`` and deleting
    the file when done.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    mgr = DatabaseManager(tmp.name)
    mgr.connect(check_same_thread=False)
    return mgr


def destroy_temp_db(mgr: DatabaseManager) -> None:
    """Close and delete the database created by :func:`create_temp_db`."""
    db_path = mgr._db_path  # type: ignore[attr-defined]
    mgr.close()
    Path(db_path).unlink(missing_ok=True)


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
