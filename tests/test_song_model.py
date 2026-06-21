"""Unit tests for data model classes (Song, Playlist, PlaylistSong, PlayMode).

Uses Python's built-in unittest framework.
"""

from __future__ import annotations

import unittest

from app.models.song import PlayMode, Playlist, PlaylistSong, Song


class TestPlayMode(unittest.TestCase):
    """PlayMode enum should contain the four expected modes."""

    def test_members(self) -> None:
        self.assertEqual(PlayMode.SEQUENTIAL, 0)
        self.assertEqual(PlayMode.LOOP, 1)
        self.assertEqual(PlayMode.SINGLE_LOOP, 2)

    def test_iterable(self) -> None:
        modes = list(PlayMode)
        self.assertEqual(len(modes), 3)


class TestSong(unittest.TestCase):
    """Song dataclass basics."""

    def test_default_construction(self) -> None:
        song = Song()
        self.assertIsNone(song.id)
        self.assertEqual(song.title, "")
        self.assertEqual(song.artist, "")
        self.assertEqual(song.duration, 0.0)

    def test_full_construction(self) -> None:
        song = Song(
            id=42,
            title="Hello",
            artist="World",
            album="Test",
            duration=199.5,
            file_path="/tmp/song.mp3",
            file_format="mp3",
            file_hash="abc123",
        )
        self.assertEqual(song.id, 42)
        self.assertEqual(song.title, "Hello")
        self.assertEqual(song.artist, "World")
        self.assertEqual(song.album, "Test")
        self.assertEqual(song.duration, 199.5)
        self.assertEqual(song.file_path, "/tmp/song.mp3")
        self.assertEqual(song.file_format, "mp3")
        self.assertEqual(song.file_hash, "abc123")

    def test_post_init_normalises_none(self) -> None:
        """String fields should be turned into empty strings when None is passed."""
        song = Song(title=None, artist=None)  # type: ignore[arg-type]
        self.assertEqual(song.title, "")
        self.assertEqual(song.artist, "")

    def test_repr(self) -> None:
        song = Song(title="Test", artist="Me")
        r = repr(song)
        self.assertIn("Test", r)
        self.assertIn("Me", r)


class TestPlaylist(unittest.TestCase):
    """Playlist dataclass basics."""

    def test_default_construction(self) -> None:
        pl = Playlist()
        self.assertIsNone(pl.id)
        self.assertEqual(pl.name, "")

    def test_full_construction(self) -> None:
        pl = Playlist(id=1, name="Favorites", created_time="2026-06-20T12:00:00")
        self.assertEqual(pl.id, 1)
        self.assertEqual(pl.name, "Favorites")


class TestPlaylistSong(unittest.TestCase):
    """PlaylistSong dataclass basics."""

    def test_default_construction(self) -> None:
        ps = PlaylistSong()
        self.assertEqual(ps.playlist_id, 0)
        self.assertEqual(ps.song_id, 0)
        self.assertEqual(ps.sort_index, 0)

    def test_full_construction(self) -> None:
        ps = PlaylistSong(playlist_id=1, song_id=5, sort_index=2)
        self.assertEqual(ps.playlist_id, 1)
        self.assertEqual(ps.song_id, 5)
        self.assertEqual(ps.sort_index, 2)


if __name__ == "__main__":
    unittest.main()
