"""Tests for LunaPlayer path helpers."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.paths import default_download_dir


class TestPaths(unittest.TestCase):
    def test_default_download_dir_uses_lunaplayer_name(self) -> None:
        with TemporaryDirectory(prefix="lunaplayer_paths_") as tmp_home:
            with patch("pathlib.Path.home", return_value=Path(tmp_home)):
                self.assertEqual(
                    default_download_dir(),
                    Path(tmp_home) / "Music" / "LunaPlayer",
                )


if __name__ == "__main__":
    unittest.main()
