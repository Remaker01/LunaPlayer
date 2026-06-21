"""
SQLite-backed persistence layer for songs, playlists, and their relationship.

All public methods use parameterised queries to prevent SQL injection.
Foreign keys are enforced at the database level.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from .song import Playlist, PlaylistSong, Song

# Schema DDL – kept close to the manager for single-source-of-truth.
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS songs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL DEFAULT '',
    artist      TEXT    NOT NULL DEFAULT '',
    album       TEXT    NOT NULL DEFAULT '',
    duration    REAL    NOT NULL DEFAULT 0.0,
    file_path   TEXT    NOT NULL UNIQUE,
    file_format TEXT    NOT NULL DEFAULT '',
    file_hash   TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS playlists (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    created_time TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS playlist_songs (
    playlist_id INTEGER NOT NULL,
    song_id     INTEGER NOT NULL,
    sort_index  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (playlist_id, song_id),
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
    FOREIGN KEY (song_id)     REFERENCES songs(id)     ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_songs_hash    ON songs(file_hash);
CREATE INDEX IF NOT EXISTS idx_songs_path    ON songs(file_path);
CREATE INDEX IF NOT EXISTS idx_ps_playlist   ON playlist_songs(playlist_id);
CREATE INDEX IF NOT EXISTS idx_ps_sort       ON playlist_songs(playlist_id, sort_index);
"""


class DatabaseManager:
    """Manages the SQLite connection and provides CRUD helpers.

    Call *connect()* before any other method.  The caller is responsible for
    calling *close()* when the instance is no longer needed.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """Initialise with an optional custom database path.

        When *db_path* is ``None`` the default location
        ``~/.smallplayer/music.db`` is used.
        """
        if db_path is None:
            db_path = str(Path.home() / ".smallplayer" / "music.db")
        self._db_path: str = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self, check_same_thread: bool = True) -> None:
        """Open (or create) the database and ensure the schema exists.

        Set *check_same_thread* to ``False`` when the connection will be used
        from a different thread (e.g. by :class:`MusicScanner`).
        """
        db_file = Path(self._db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_file), check_same_thread=check_same_thread)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection if it is open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    # ------------------------------------------------------------------
    # Songs – CRUD
    # ------------------------------------------------------------------

    def add_song(self, song: Song) -> int:
        """Insert a new *song* and return its auto-generated id.

        Raises :class:`sqlite3.IntegrityError` if *file_path* already exists.
        """
        self._require_connection()
        cursor = self._conn.execute(
            """INSERT INTO songs (title, artist, album, duration, file_path, file_format, file_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (song.title, song.artist, song.album, song.duration,
             song.file_path, song.file_format, song.file_hash),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_song(self, song_id: int) -> Optional[Song]:
        """Return the song with the given *id*, or ``None``."""
        self._require_connection()
        row = self._conn.execute(
            "SELECT * FROM songs WHERE id = ?", (song_id,)
        ).fetchone()
        return self._row_to_song(row) if row else None

    def get_song_by_path(self, file_path: str) -> Optional[Song]:
        """Return the song at *file_path*, or ``None``."""
        self._require_connection()
        row = self._conn.execute(
            "SELECT * FROM songs WHERE file_path = ?", (file_path,)
        ).fetchone()
        return self._row_to_song(row) if row else None

    def get_song_by_hash(self, file_hash: str) -> Optional[Song]:
        """Return the song with the given *file_hash*, or ``None``."""
        self._require_connection()
        row = self._conn.execute(
            "SELECT * FROM songs WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return self._row_to_song(row) if row else None

    def get_all_songs(self) -> List[Song]:
        """Return every song in the database."""
        self._require_connection()
        rows = self._conn.execute("SELECT * FROM songs ORDER BY id").fetchall()
        return [self._row_to_song(r) for r in rows]

    def update_song(self, song: Song) -> None:
        """Update all columns of an existing song (matched by id)."""
        self._require_connection()
        self._conn.execute(
            """UPDATE songs
               SET title=?, artist=?, album=?, duration=?,
                   file_path=?, file_format=?, file_hash=?
               WHERE id=?""",
            (song.title, song.artist, song.album, song.duration,
             song.file_path, song.file_format, song.file_hash, song.id),
        )
        self._conn.commit()

    def delete_song(self, song_id: int) -> None:
        """Remove the song with *id* (cascade removes playlist entries)."""
        self._require_connection()
        self._conn.execute("DELETE FROM songs WHERE id = ?", (song_id,))
        self._conn.commit()

    def song_count(self) -> int:
        """Return the total number of songs in the database."""
        self._require_connection()
        row = self._conn.execute("SELECT COUNT(*) FROM songs").fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Playlists – CRUD
    # ------------------------------------------------------------------

    def create_playlist(self, name: str) -> int:
        """Create a new playlist and return its auto-generated id."""
        self._require_connection()
        cursor = self._conn.execute(
            "INSERT INTO playlists (name) VALUES (?)", (name,)
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_playlist(self, playlist_id: int) -> Optional[Playlist]:
        """Return the playlist with *id*, or ``None``."""
        self._require_connection()
        row = self._conn.execute(
            "SELECT * FROM playlists WHERE id = ?", (playlist_id,)
        ).fetchone()
        return self._row_to_playlist(row) if row else None

    def get_all_playlists(self) -> List[Playlist]:
        """Return every playlist."""
        self._require_connection()
        rows = self._conn.execute("SELECT * FROM playlists ORDER BY id").fetchall()
        return [self._row_to_playlist(r) for r in rows]

    def rename_playlist(self, playlist_id: int, new_name: str) -> None:
        """Rename an existing playlist."""
        self._require_connection()
        self._conn.execute(
            "UPDATE playlists SET name = ? WHERE id = ?",
            (new_name, playlist_id),
        )
        self._conn.commit()

    def delete_playlist(self, playlist_id: int) -> None:
        """Remove the playlist (cascade removes song associations)."""
        self._require_connection()
        self._conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self._conn.commit()

    # ------------------------------------------------------------------
    # Playlist ↔ Song relationship
    # ------------------------------------------------------------------

    def add_song_to_playlist(self, playlist_id: int, song_id: int,
                             sort_index: Optional[int] = None) -> None:
        """Associate *song_id* with *playlist_id*.

        If *sort_index* is ``None`` the song is appended at the end.
        """
        self._require_connection()
        if sort_index is None:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(sort_index), -1) + 1 FROM playlist_songs "
                "WHERE playlist_id = ?",
                (playlist_id,),
            ).fetchone()
            sort_index = row[0] if row else 0
        self._conn.execute(
            "INSERT OR IGNORE INTO playlist_songs (playlist_id, song_id, sort_index) "
            "VALUES (?, ?, ?)",
            (playlist_id, song_id, sort_index),
        )
        self._conn.commit()

    def remove_song_from_playlist(self, playlist_id: int, song_id: int) -> None:
        """Remove the association between *playlist_id* and *song_id*."""
        self._require_connection()
        self._conn.execute(
            "DELETE FROM playlist_songs WHERE playlist_id = ? AND song_id = ?",
            (playlist_id, song_id),
        )
        self._conn.commit()

    def get_playlist_songs(self, playlist_id: int) -> List[Song]:
        """Return all songs belonging to *playlist_id*, ordered by sort_index."""
        self._require_connection()
        rows = self._conn.execute(
            """SELECT s.* FROM songs s
               JOIN playlist_songs ps ON s.id = ps.song_id
               WHERE ps.playlist_id = ?
               ORDER BY ps.sort_index""",
            (playlist_id,),
        ).fetchall()
        return [self._row_to_song(r) for r in rows]

    def reorder_playlist(self, playlist_id: int, song_ids: List[int]) -> None:
        """Replace the sort order of *playlist_id*'s songs with *song_ids*."""
        self._require_connection()
        self._conn.executemany(
            "UPDATE playlist_songs SET sort_index = ? "
            "WHERE playlist_id = ? AND song_id = ?",
            [(idx, playlist_id, sid) for idx, sid in enumerate(song_ids)],
        )
        self._conn.commit()

    def clear_playlist(self, playlist_id: int) -> None:
        """Remove all song associations from *playlist_id*."""
        self._require_connection()
        self._conn.execute(
            "DELETE FROM playlist_songs WHERE playlist_id = ?", (playlist_id,)
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_connection(self) -> None:
        if self._conn is None:
            raise RuntimeError(
                "DatabaseManager is not connected. Call connect() first."
            )

    @staticmethod
    def _row_to_song(row: sqlite3.Row) -> Song:
        return Song(
            id=row[0],
            title=row[1],
            artist=row[2],
            album=row[3],
            duration=row[4],
            file_path=row[5],
            file_format=row[6],
            file_hash=row[7],
        )

    @staticmethod
    def _row_to_playlist(row: sqlite3.Row) -> Playlist:
        return Playlist(id=row[0], name=row[1], created_time=row[2])
