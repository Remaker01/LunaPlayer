"""
Data models for songs, playlists, and playback modes.

All model classes are plain data containers (dataclasses) with no business logic.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class PlayMode(enum.IntEnum):
    """Playback mode for the playlist manager."""

    SEQUENTIAL = 0  # Play through the list once, stop at end.
    LOOP = 1        # Loop the entire list.
    SINGLE_LOOP = 2 # Repeat the current song indefinitely.


@dataclass
class Song:
    """Represents a single audio track."""

    id: Optional[int] = None
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0  # Duration in seconds.
    file_path: str = ""
    file_format: str = ""
    file_hash: str = ""    # SHA-256 hex digest for deduplication.

    def __post_init__(self) -> None:
        """Normalise string fields to avoid accidental None."""
        for field_name in ("title", "artist", "album", "file_path", "file_format", "file_hash"):
            value = getattr(self, field_name)
            if value is None:
                object.__setattr__(self, field_name, "")


@dataclass
class Playlist:
    """Represents a named playlist."""

    id: Optional[int] = None
    name: str = ""
    created_time: str = ""  # ISO-8601 formatted timestamp.


@dataclass
class PlaylistSong:
    """Many-to-many relationship between playlists and songs."""

    playlist_id: int = 0
    song_id: int = 0
    sort_index: int = 0
