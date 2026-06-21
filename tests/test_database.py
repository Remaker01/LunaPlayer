"""Unit tests for DatabaseManager.

Uses a temporary SQLite file per test (managed via setUp/tearDown).
"""

from __future__ import annotations

import sqlite3
import unittest

from app.models.database import DatabaseManager
from app.models.song import Song

from tests.test_base import create_temp_db, destroy_temp_db


class TestConnection(unittest.TestCase):
    """Database connection lifecycle."""

    def setUp(self) -> None:
        self.mgr = create_temp_db()

    def tearDown(self) -> None:
        destroy_temp_db(self.mgr)

    def test_connect_creates_file(self) -> None:
        self.assertTrue(self.mgr.is_connected)

    def test_close(self) -> None:
        self.mgr.close()
        self.assertFalse(self.mgr.is_connected)

    def test_require_connection_raises(self) -> None:
        mgr = DatabaseManager(":memory:")
        with self.assertRaises(RuntimeError) as ctx:
            mgr.get_all_songs()
        self.assertIn("not connected", str(ctx.exception))


class TestSongs(unittest.TestCase):
    """Read/write operations on the songs table."""

    def setUp(self) -> None:
        self.mgr = create_temp_db()

    def tearDown(self) -> None:
        destroy_temp_db(self.mgr)

    def test_add_song_returns_id(self) -> None:
        song_id = self.mgr.add_song(Song(file_path="/tmp/test.mp3"))
        self.assertGreater(song_id, 0)

    def test_add_duplicate_path_raises(self) -> None:
        self.mgr.add_song(Song(file_path="/dup.mp3"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.mgr.add_song(Song(file_path="/dup.mp3"))

    def test_get_song_by_path(self) -> None:
        self.mgr.add_song(Song(file_path="/findme.mp3"))
        fetched = self.mgr.get_song_by_path("/findme.mp3")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.file_path, "/findme.mp3")

    def test_get_song_by_path_not_found(self) -> None:
        self.assertIsNone(self.mgr.get_song_by_path("/nonexistent.mp3"))

    def test_get_song_by_hash(self) -> None:
        self.mgr.add_song(Song(file_path="/a.mp3", file_hash="abc"))
        fetched = self.mgr.get_song_by_hash("abc")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.file_hash, "abc")

    def test_get_all_songs_empty(self) -> None:
        self.assertEqual(self.mgr.get_all_songs(), [])

    def test_get_all_songs(self) -> None:
        self.mgr.add_song(Song(file_path="/a.mp3", title="A"))
        self.mgr.add_song(Song(file_path="/b.mp3", title="B"))
        songs = self.mgr.get_all_songs()
        self.assertEqual(len(songs), 2)
