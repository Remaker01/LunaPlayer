"""Tests for LunaPlayer path helpers and legacy migration."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app.services.config as cfg
from app.paths import app_data_dir, config_path, default_download_dir


class TestPaths(unittest.TestCase):
    def test_config_reads_from_migrated_legacy_directory(self) -> None:
        with TemporaryDirectory(prefix="lunaplayer_paths_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                legacy_dir = Path(tmp_home) / ".smallplayer"
                legacy_dir.mkdir(parents=True, exist_ok=True)
                (legacy_dir / "config.json").write_text(
                    '{"download_dir": "D:/Music/Legacy"}',
                    encoding="utf-8",
                )

                self.assertEqual(cfg.get("download_dir"), "D:/Music/Legacy")
                self.assertTrue((Path(tmp_home) / ".lunaplayer" / "config.json").exists())

    def test_default_download_dir_uses_lunaplayer_name(self) -> None:
        with TemporaryDirectory(prefix="lunaplayer_paths_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                self.assertEqual(
                    default_download_dir(),
                    Path(tmp_home) / "Music" / "LunaPlayer",
                )

    def test_app_data_dir_prefers_new_directory_after_migration(self) -> None:
        with TemporaryDirectory(prefix="lunaplayer_paths_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                legacy_file = Path(tmp_home) / ".smallplayer" / "marker.txt"
                legacy_file.parent.mkdir(parents=True, exist_ok=True)
                legacy_file.write_text("legacy", encoding="utf-8")

                self.assertEqual(app_data_dir(), Path(tmp_home) / ".lunaplayer")
                self.assertEqual(config_path().parent, Path(tmp_home) / ".lunaplayer")
                self.assertTrue((Path(tmp_home) / ".lunaplayer" / "marker.txt").exists())


if __name__ == "__main__":
    unittest.main()
