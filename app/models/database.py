"""
SQLite-backed persistence layer for songs, playlists, and their relationship.

All public methods use parameterised queries to prevent SQL injection.
Foreign keys are enforced at the database level.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Optional

from .song import Song

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

CREATE INDEX IF NOT EXISTS idx_songs_hash    ON songs(file_hash);
CREATE INDEX IF NOT EXISTS idx_songs_path    ON songs(file_path);
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
    # Songs – read / write
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
