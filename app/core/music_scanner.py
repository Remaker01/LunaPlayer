"""
Music scanner – traverses directories, extracts metadata with mutagen, and
imports songs into the database.

Runs in a background QThread so the UI stays responsive during a full scan.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import List, Optional, Set

from PySide6.QtCore import QMutex, QMutexLocker, QObject, QThread, Signal

import mutagen

from app.models.database import DatabaseManager
from app.models.song import Song

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Audio file extensions that we recognise.
SUPPORTED_EXTENSIONS: Set[str] = {
    ".mp3", ".flac", ".wav", ".ogg", ".m4a", ".m4b",
    ".wma", ".aac", ".au", ".aiff", ".aif", ".opus",
}

# How many bytes to read at a time when hashing.
_HASH_BLOCK_SIZE = 64 * 1024  # 64 KB


# ===================================================================
# MusicScanner – background scanner
# ===================================================================

class MusicScanner(QThread):
    """Scan a directory tree for audio files and import them into the database.

    Signals
    -------
    scan_started(directory)
        Emitted when scanning begins.
    progress(current, total)
        Emitted after each file is processed.
    song_found(song)
        Emitted for every **new** song that was inserted into the database.
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

    def __init__(self, db_manager: DatabaseManager,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._db = db_manager
        self._directory: str = ""
        self._stop_requested: bool = False
        self._mutex = QMutex()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_directory(self, directory: str) -> None:
        """Start scanning *directory* (recursive) in a background thread."""
        self._directory = os.path.normpath(directory)
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
            audio_files: List[str] = []
            for root, _dirs, files in os.walk(directory):
                if self._should_stop():
                    break
                for name in files:
                    if Path(name).suffix.lower() in SUPPORTED_EXTENSIONS:
                        audio_files.append(os.path.join(root, name))

            total = len(audio_files)
            imported = 0

            for idx, file_path in enumerate(audio_files):
                if self._should_stop():
                    break

                self.progress.emit(idx + 1, total)

                # Skip if path is already in database.
                if self._db.get_song_by_path(file_path) is not None:
                    continue

                try:
                    song = extract_metadata(file_path)
                except Exception as exc:
                    logger.warning("Skipping %s: %s", file_path, exc)
                    self.error_occurred.emit(f"Cannot read {file_path}: {exc}")
                    continue

                if song is None:
                    continue

                # Deduplicate by hash.
                if self._db.get_song_by_hash(song.file_hash) is not None:
                    continue

                try:
                    song_id = self._db.add_song(song)
                    song.id = song_id
                    self.song_found.emit(song)
                    imported += 1
                except Exception as exc:
                    logger.warning("Failed to insert %s: %s", file_path, exc)
                    self.error_occurred.emit(f"Database error for {file_path}: {exc}")

            self.scan_finished.emit(imported)

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
    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return None
    except Exception:
        return None

    # --- tags ---
    # mutagen.File returns a dict-like object.  We try convenience names
    # first, then fall back to raw ID3 frame names.
    title: str = _tag_str(audio, "title")
    if not title:
        title = _tag_str(audio, "TIT2")
    if not title:
        # Fall back to file name (without extension).
        title = Path(file_path).stem

    artist: str = _tag_str(audio, "artist")
    if not artist:
        artist = _tag_str(audio, "TPE1")

    album: str = _tag_str(audio, "album")
    if not album:
        album = _tag_str(audio, "TALB")

    # --- duration ---
    duration: float = 0.0
    if hasattr(audio.info, "length"):
        duration = float(audio.info.length)

    # --- format ---
    file_format = Path(file_path).suffix.lower().lstrip(".")

    # --- hash ---
    file_hash = _compute_hash(file_path)

    return Song(
        title=title or Path(file_path).stem,
        artist=artist or "",
        album=album or "",
        duration=duration,
        file_path=file_path,
        file_format=file_format,
        file_hash=file_hash,
    )


# ===================================================================
# Module-level helpers
# ===================================================================

def _tag_str(audio: mutagen.FileType, key: str,
             default: str = "") -> str:
    """Return the first string value for *key* from *audio*, or *default*."""
    try:
        val = audio.get(key)
        if val is None:
            return default
        if isinstance(val, list):
            return str(val[0]) if val else default
        return str(val)
    except Exception:
        return default


def _compute_hash(file_path: str) -> str:
    """SHA-256 hex digest of the file contents."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            block = f.read(_HASH_BLOCK_SIZE)
            if not block:
                break
            sha.update(block)
    return sha.hexdigest()
