"""
Music scanner – traverses directories and emits Song metadata for audio files.

Runs in a background QThread so the UI stays responsive during a full scan.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Set

from PySide6.QtCore import QMutex, QMutexLocker, QObject, QThread, Signal

from app.models.song import Song
from app.services.audio_metadata import compute_hash, extract_song_metadata

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Audio file extensions that we recognise.
SUPPORTED_EXTENSIONS: Set[str] = {
    ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".m4b",
    ".wma", ".aac", ".au", ".aiff", ".aif", ".opus",
}

# ===================================================================
# MusicScanner – background scanner
# ===================================================================

class MusicScanner(QThread):
    """Scan a directory tree for audio files and emit Song metadata.

    Signals
    -------
    scan_started(directory)
        Emitted when scanning begins.
    progress(current, total)
        Emitted after each file is processed.
    song_found(song)
        Emitted for every song discovered in the current scan.
    scan_finished(imported_count)
        Emitted when scanning completes.
    error_occurred(message)
        Emitted when a non-fatal error happens (e.g. corrupt file).
    """

    scan_started = Signal(str)
    progress = Signal(int, int)  # current, total
    song_found = Signal(object)  # Song
    scan_finished = Signal(int)
    error_occurred = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._directory: str = ""
        self._stop_requested: bool = False
        self._mutex = QMutex()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_directory(self, directory: str) -> None:
        """Start scanning *directory* (recursive) in a background thread."""
        self._directory = str(Path(directory).resolve())
        self.start()

    def request_stop(self) -> None:
        """Ask the scanner to stop after the current file."""
        with QMutexLocker(self._mutex):
            self._stop_requested = True

    # ------------------------------------------------------------------
    # QThread run()
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._stop_requested = False
        directory = self._directory

        self.scan_started.emit(directory)

        try:
            # -- collect all audio files first --
            audio_files: list[str] = []
            seen_paths: set[str] = set()
            for root, _dirs, files in os.walk(directory):
                if self._should_stop():
                    break
                for name in files:
                    if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
                        continue
                    resolved_path = str((Path(root) / name).resolve())
                    if resolved_path in seen_paths:
                        continue
                    seen_paths.add(resolved_path)
                    audio_files.append(resolved_path)

            total = len(audio_files)
            discovered = 0

            for idx, file_path in enumerate(audio_files):
                if self._should_stop():
                    break

                self.progress.emit(idx + 1, total)

                try:
                    song = extract_metadata(file_path)
                except Exception as exc:
                    logger.warning("Skipping %s: %s", file_path, exc)
                    self.error_occurred.emit(f"Cannot read {file_path}: {exc}")
                    continue

                if song is None:
                    continue

                self.song_found.emit(song)
                discovered += 1

            self.scan_finished.emit(discovered)

        except Exception as exc:
            logger.exception("Scan failed")
            self.error_occurred.emit(f"Scan failed: {exc}")
            self.scan_finished.emit(0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_stop(self) -> bool:
        with QMutexLocker(self._mutex):
            return self._stop_requested


# ===================================================================
# Module-level helpers
# ===================================================================

def extract_metadata(file_path: str) -> Optional[Song]:
    """Extract metadata from *file_path* using mutagen.

    Returns a :class:`Song` instance, or ``None`` if the file cannot
    be parsed.
    """
    return extract_song_metadata(file_path)


# ===================================================================
# Module-level helpers
# ===================================================================

def _compute_hash(file_path: str) -> str:
    """SHA-256 hex digest of the file contents."""
    return compute_hash(file_path)
