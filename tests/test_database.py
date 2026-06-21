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
    """CRUD operations on the songs table."""

    def setUp(self) -> None:
        self.mgr = create_temp_db()

    def tearDown(self) -> None:
        destroy_temp_db(self.mgr)

    def test_add_and_get_song(self) -> None:
        song = Song(
            title="Test Song", artist="Tester", album="Test Album",
            duration=123.0, file_path="/tmp/test.mp3",
            file_format="mp3", file_hash="hash123",
        )
        song_id = self.mgr.add_song(song)
        self.assertGreater(song_id, 0)

        fetched = self.mgr.get_song(song_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "Test Song")
        self.assertEqual(fetched.file_path, "/tmp/test.mp3")

    def test_add_duplicate_path_raises(self) -> None:
        self.mgr.add_song(Song(file_path="/dup.mp3"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.mgr.add_song(Song(file_path="/dup.mp3"))

    def test_get_song_not_found(self) -> None:
        self.assertIsNone(self.mgr.get_song(999))

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

    def test_update_song(self) -> None:
        song = Song(title="Old", file_path="/old.mp3")
        song_id = self.mgr.add_song(song)
        song.id = song_id
        song.title = "New Title"
        self.mgr.update_song(song)

        fetched = self.mgr.get_song(song_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "New Title")

    def test_delete_song(self) -> None:
        sid = self.mgr.add_song(Song(file_path="/del.mp3"))
        self.mgr.delete_song(sid)
        self.assertIsNone(self.mgr.get_song(sid))

    def test_song_count(self) -> None:
        self.assertEqual(self.mgr.song_count(), 0)
        self.mgr.add_song(Song(file_path="/a.mp3"))
        self.assertEqual(self.mgr.song_count(), 1)


class TestPlaylists(unittest.TestCase):
    """CRUD operations on the playlists table."""

    def setUp(self) -> None:
        self.mgr = create_temp_db()

    def tearDown(self) -> None:
        destroy_temp_db(self.mgr)

    def test_create_and_get_playlist(self) -> None:
        pid = self.mgr.create_playlist("My Favorites")
        self.assertGreater(pid, 0)

        pl = self.mgr.get_playlist(pid)
        self.assertIsNotNone(pl)
        self.assertEqual(pl.name, "My Favorites")
        self.assertEqual(pl.id, pid)

    def test_get_playlist_not_found(self) -> None:
        self.assertIsNone(self.mgr.get_playlist(999))

    def test_get_all_playlists_empty(self) -> None:
        self.assertEqual(self.mgr.get_all_playlists(), [])

    def test_get_all_playlists(self) -> None:
        self.mgr.create_playlist("A")
        self.mgr.create_playlist("B")
        playlists = self.mgr.get_all_playlists()
        self.assertEqual(len(playlists), 2)

    def test_rename_playlist(self) -> None:
        pid = self.mgr.create_playlist("Old Name")
        self.mgr.rename_playlist(pid, "New Name")
        self.assertEqual(self.mgr.get_playlist(pid).name, "New Name")

    def test_delete_playlist(self) -> None:
        pid = self.mgr.create_playlist("Temp")
        self.mgr.delete_playlist(pid)
        self.assertIsNone(self.mgr.get_playlist(pid))


class TestPlaylistSongs(unittest.TestCase):
    """Many-to-many relationship between playlists and songs."""

    def setUp(self) -> None:
        self.mgr = create_temp_db()

    def tearDown(self) -> None:
        destroy_temp_db(self.mgr)

    def test_add_song_to_playlist(self) -> None:
        pid = self.mgr.create_playlist("Test")
        sid = self.mgr.add_song(Song(file_path="/song.mp3"))
        self.mgr.add_song_to_playlist(pid, sid)

        songs = self.mgr.get_playlist_songs(pid)
        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0].id, sid)

    def test_add_song_with_sort_index(self) -> None:
        pid = self.mgr.create_playlist("Test")
        sid_a = self.mgr.add_song(Song(file_path="/a.mp3"))
        sid_b = self.mgr.add_song(Song(file_path="/b.mp3"))
        self.mgr.add_song_to_playlist(pid, sid_b, sort_index=0)
        self.mgr.add_song_to_playlist(pid, sid_a, sort_index=1)

        songs = self.mgr.get_playlist_songs(pid)
        self.assertEqual(songs[0].id, sid_b)
        self.assertEqual(songs[1].id, sid_a)

    def test_remove_song_from_playlist(self) -> None:
        pid = self.mgr.create_playlist("Test")
        sid = self.mgr.add_song(Song(file_path="/song.mp3"))
        self.mgr.add_song_to_playlist(pid, sid)
        self.mgr.remove_song_from_playlist(pid, sid)
        self.assertEqual(self.mgr.get_playlist_songs(pid), [])

    def test_reorder_playlist(self) -> None:
        pid = self.mgr.create_playlist("Test")
        sid_a = self.mgr.add_song(Song(file_path="/a.mp3"))
        sid_b = self.mgr.add_song(Song(file_path="/b.mp3"))
        sid_c = self.mgr.add_song(Song(file_path="/c.mp3"))
        for s in (sid_a, sid_b, sid_c):
            self.mgr.add_song_to_playlist(pid, s)

        # Reverse order
        self.mgr.reorder_playlist(pid, [sid_c, sid_b, sid_a])
        songs = self.mgr.get_playlist_songs(pid)
        self.assertEqual([s.id for s in songs], [sid_c, sid_b, sid_a])

    def test_clear_playlist(self) -> None:
        pid = self.mgr.create_playlist("Test")
        sid = self.mgr.add_song(Song(file_path="/song.mp3"))
        self.mgr.add_song_to_playlist(pid, sid)
        self.mgr.clear_playlist(pid)
        self.assertEqual(self.mgr.get_playlist_songs(pid), [])

    def test_cascade_delete_playlist(self) -> None:
        """Deleting a playlist should remove its playlist_songs entries."""
        pid = self.mgr.create_playlist("Test")
        sid = self.mgr.add_song(Song(file_path="/song.mp3"))
        self.mgr.add_song_to_playlist(pid, sid)
        self.mgr.delete_playlist(pid)

        # Song should still exist
        self.assertIsNotNone(self.mgr.get_song(sid))

    def test_cascade_delete_song(self) -> None:
        """Deleting a song should remove its playlist_songs entries."""
        pid = self.mgr.create_playlist("Test")
        sid = self.mgr.add_song(Song(file_path="/song.mp3"))
        self.mgr.add_song_to_playlist(pid, sid)
        self.mgr.delete_song(sid)

        self.assertEqual(self.mgr.get_playlist_songs(pid), [])

    def test_auto_sort_index(self) -> None:
        """When sort_index is None, songs should be appended sequentially."""
        pid = self.mgr.create_playlist("Test")
        s1 = self.mgr.add_song(Song(file_path="/a.mp3"))
        s2 = self.mgr.add_song(Song(file_path="/b.mp3"))
        s3 = self.mgr.add_song(Song(file_path="/c.mp3"))
        self.mgr.add_song_to_playlist(pid, s1)
        self.mgr.add_song_to_playlist(pid, s2)
        self.mgr.add_song_to_playlist(pid, s3)

        songs = self.mgr.get_playlist_songs(pid)
        self.assertEqual([s.id for s in songs], [s1, s2, s3])
