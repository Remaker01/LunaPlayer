"""Favorites manager for persistent local-file favorites."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from app.core.music_scanner import extract_metadata
from app.core.playlist_manager import _playlists_dir
from app.models.song import Song

logger = logging.getLogger(__name__)

FAVORITES_PLAYLIST_NAME = "favorites"


class FavoritesManager(QObject):
    """Manage a persistent favorites list keyed by absolute file path."""

    favorites_loaded = Signal()
    favorites_changed = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._favorites: list[Song] = []

    @property
    def favorites(self) -> list[Song]:
        """Return a copy of the favorites list."""
        return list(self._favorites)

    @property
    def favorite_paths(self) -> set[str]:
        """Return the current favorite paths as an absolute-path set."""
        return {song.file_path for song in self._favorites if song.file_path}

    def contains_path(self, file_path: str) -> bool:
        """Return whether *file_path* is already favorited."""
        normalized = self._normalize_path(file_path)
        return bool(normalized) and normalized in self.favorite_paths

    def add_favorite(self, song: Song) -> bool:
        """Append *song* to favorites if it is a valid local file."""
        normalized = self._normalize_path(song.file_path)
        if not normalized or not Path(normalized).is_file():
            return False
        if normalized in self.favorite_paths:
            return False

        favorite_song = self._song_from_path(normalized)
        if favorite_song is None:
            return False

        self._favorites.append(favorite_song)
        self.favorites_changed.emit()
        return True

    def remove_favorite_by_path(self, file_path: str) -> bool:
        """Remove the favorite matching *file_path*."""
        normalized = self._normalize_path(file_path)
        if not normalized:
            return False

        for index, song in enumerate(self._favorites):
            if song.file_path == normalized:
                self._favorites.pop(index)
                self.favorites_changed.emit()
                return True
        return False

    def reorder_favorites(self, songs: list[Song]) -> None:
        """Replace favorites ordering with unique valid local songs."""
        reordered: list[Song] = []
        seen_paths: set[str] = set()

        for song in songs:
            normalized = self._normalize_path(song.file_path)
            if not normalized or normalized in seen_paths:
                continue
            rebuilt = self._song_from_path(normalized)
            if rebuilt is None:
                continue
            seen_paths.add(normalized)
            reordered.append(rebuilt)

        self._favorites = reordered
        self.favorites_changed.emit()

    def load_favorites(self) -> bool:
        """Load favorites from the persistent M3U8 file."""
        path = self._favorites_path()
        if not path.exists():
            self._favorites = []
            self.favorites_loaded.emit()
            self.favorites_changed.emit()
            return False

        favorites: list[Song] = []
        seen_paths: set[str] = set()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.warning("Failed to read favorites playlist: %s", exc)
            self._favorites = []
            self.favorites_loaded.emit()
            self.favorites_changed.emit()
            return False

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            normalized = self._normalize_path(line)
            if not normalized or normalized in seen_paths or not Path(normalized).exists():
                continue
            song = self._song_from_path(normalized)
            if song is None:
                continue
            seen_paths.add(normalized)
            favorites.append(song)

        self._favorites = favorites
        self.favorites_loaded.emit()
        self.favorites_changed.emit()
        return bool(favorites)

    def save_favorites(self) -> str:
        """Persist favorites order to the fixed M3U8 file."""
        path = self._favorites_path()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("#EXTM3U\n")
                for song in self._favorites:
                    duration = int(song.duration) if song.duration is not None else -1
                    display = f"{song.artist} - {song.title}" if song.artist else song.title
                    handle.write(f"#EXTINF:{duration},{display}\n")
                    handle.write(f"{song.file_path}\n")
        except OSError as exc:
            logger.warning("Failed to save favorites playlist: %s", exc)
            return ""
        return str(path)

    def _favorites_path(self) -> Path:
        return _playlists_dir() / f"{FAVORITES_PLAYLIST_NAME}.m3u8"

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        raw = str(file_path or "").strip()
        if not raw or raw.startswith(("http://", "https://")):
            return ""
        try:
            return str(Path(raw).resolve())
        except OSError:
            return str(Path(raw).absolute())

    @staticmethod
    def _song_from_path(file_path: str) -> Optional[Song]:
        song = extract_metadata(file_path)
        if song is not None:
            return song
        resolved = str(Path(file_path).resolve())
        return Song(
            title=Path(resolved).stem,
            file_path=resolved,
            file_format=Path(resolved).suffix.lower().lstrip("."),
        )
