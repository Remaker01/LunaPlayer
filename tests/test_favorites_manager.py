"""Unit tests for FavoritesManager."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.core.favorites_manager import FavoritesManager
from app.models.song import Song
from tests.test_base import create_test_wav, ensure_qapp


class FavoritesManagerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def setUp(self) -> None:
        self.manager = FavoritesManager()


class TestFavoritesMutation(FavoritesManagerTestCase):
    def test_add_favorite_dedupes_by_absolute_path(self) -> None:
        with TemporaryDirectory(prefix="favorites_add_") as tmpdir:
            path = str(Path(create_test_wav(tmpdir, "song.wav")).resolve())
            song = Song(title="Song", file_path=path, file_format="wav")

            self.assertTrue(self.manager.add_favorite(song))
            self.assertFalse(self.manager.add_favorite(song))
            self.assertEqual(len(self.manager.favorites), 1)

    def test_add_favorite_rejects_remote_and_empty_paths(self) -> None:
        remote = Song(title="Remote", file_path="https://example.com/test.mp3", file_format="mp3")
        empty = Song(title="Empty", file_path="", file_format="mp3")

        self.assertFalse(self.manager.add_favorite(remote))
        self.assertFalse(self.manager.add_favorite(empty))
        self.assertEqual(self.manager.favorites, [])

    def test_remove_favorite_by_path(self) -> None:
        with TemporaryDirectory(prefix="favorites_remove_") as tmpdir:
            path = str(Path(create_test_wav(tmpdir, "song.wav")).resolve())
            song = Song(title="Song", file_path=path, file_format="wav")
            self.manager.add_favorite(song)

            self.assertTrue(self.manager.remove_favorite_by_path(path))
            self.assertEqual(self.manager.favorites, [])


class TestFavoritesPersistence(FavoritesManagerTestCase):
    def test_reorder_and_restore_persists_order(self) -> None:
        with TemporaryDirectory(prefix="favorites_home_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                music_dir = Path(tmp_home) / "music"
                music_dir.mkdir(parents=True, exist_ok=True)
                first = str(Path(create_test_wav(music_dir, "a.wav")).resolve())
                second = str(Path(create_test_wav(music_dir, "b.wav")).resolve())

                song_a = Song(title="A", file_path=first, file_format="wav")
                song_b = Song(title="B", file_path=second, file_format="wav")

                self.manager.add_favorite(song_a)
                self.manager.add_favorite(song_b)
                self.manager.reorder_favorites([song_b, song_a])
                self.manager.save_favorites()

                restored = FavoritesManager()
                self.assertTrue(restored.load_favorites())
                self.assertEqual(
                    [song.file_path for song in restored.favorites],
                    [second, first],
                )

    def test_load_favorites_skips_missing_entries(self) -> None:
        with TemporaryDirectory(prefix="favorites_home_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                music_dir = Path(tmp_home) / "music"
                music_dir.mkdir(parents=True, exist_ok=True)
                existing = str(Path(create_test_wav(music_dir, "existing.wav")).resolve())
                missing = str((music_dir / "missing.wav").resolve())

                self.manager.save_favorites()
                favorites_path = Path(tmp_home) / ".smallplayer" / "playlists" / "favorites.m3u8"
                favorites_path.write_text(
                    "#EXTM3U\n"
                    f"{missing}\n"
                    f"{existing}\n",
                    encoding="utf-8",
                )

                restored = FavoritesManager()
                self.assertTrue(restored.load_favorites())
                self.assertEqual([song.file_path for song in restored.favorites], [existing])


if __name__ == "__main__":
    unittest.main()
