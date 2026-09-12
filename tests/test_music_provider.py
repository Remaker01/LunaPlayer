"""Unit tests for online music provider helpers."""

from __future__ import annotations

import unittest

from app.services.music_provider import (
    _build_song,
    _select_audio_format,
)


class TestMusicProviderFormatDetection(unittest.TestCase):
    def test_content_disposition_filename_is_used_when_no_magic(self) -> None:
        """Content-Disposition is used as a fallback when magic bytes and
        Content-Type are not available (but not preferred over magic)."""
        fmt = _select_audio_format(
            {"Content-Disposition": 'attachment; filename="track.flac"',
             "Content-Type": "application/octet-stream"},
            "https://example.com/download",
            b"",
            "mp3",
        )

        self.assertEqual(fmt, "flac")

    def test_content_type_detects_common_audio_format(self) -> None:
        fmt = _select_audio_format(
            {"content-type": "audio/x-ms-wma"},
            "https://example.com/download",
            b"",
            "",
        )

        self.assertEqual(fmt, "wma")

    def test_magic_bytes_are_preferred_over_content_type(self) -> None:
        fmt = _select_audio_format(
            {"Content-Type": "audio/mpeg"},
            "https://example.com/audio/song.mp3",
            b"fLaC\x00\x00",
            "",
        )

        self.assertEqual(fmt, "flac")

    def test_url_suffix_is_used_when_headers_are_generic(self) -> None:
        fmt = _select_audio_format(
            {"Content-Type": "application/octet-stream"},
            "https://example.com/audio/song.m4a?token=1",
            b"",
            "",
        )

        self.assertEqual(fmt, "m4a")

    def test_magic_bytes_are_preferred_over_url_suffix(self) -> None:
        fmt = _select_audio_format(
            {"Content-Type": "application/octet-stream"},
            "https://example.com/audio/song.mp3",
            b"fLaC\x00\x00",
            "",
        )

        self.assertEqual(fmt, "flac")

    def test_magic_bytes_are_used_when_metadata_is_missing(self) -> None:
        fmt = _select_audio_format({}, "https://example.com/download", b"fLaC\x00\x00", "")

        self.assertEqual(fmt, "flac")

    def test_fallback_format_is_used_last(self) -> None:
        fmt = _select_audio_format({}, "https://example.com/download", b"", "aac")

        self.assertEqual(fmt, "aac")

    def test_build_song_leaves_format_empty(self) -> None:
        """Format is not determined at search time; it is detected at
        download/play time from the actual file content."""
        song = _build_song({
            "name": "Example",
            "artist": "Artist",
            "url": "api.php?get=url&id=1",
            "format": "flac",
        })

        self.assertIsNotNone(song)
        self.assertEqual(song.file_format, "")

    def test_php_api_url_leaves_format_empty(self) -> None:
        """Regression: PHP API URLs must not set any format at search time."""
        song = _build_song({
            "name": "Test",
            "artist": "Artist",
            "url": "https://music.90svip.cn/api.php?get=url&type=wy&id=xxx&sign=abc&t=123",
        })
        self.assertIsNotNone(song)
        self.assertEqual(song.file_format, "")

    def test_build_song_captures_absolute_lrc_url(self) -> None:
        """The relative 'lrc' field is stored as an absolute URL for later download."""
        song = _build_song({
            "name": "Example",
            "artist": "Artist",
            "url": "api.php?get=url&id=1",
            "lrc": "api.php?get=lrc&id=1",
        })
        self.assertIsNotNone(song)
        self.assertEqual(song.lrc_url, "https://music.90svip.cn/api.php?get=lrc&id=1")

    def test_build_song_lrc_url_stays_empty_when_missing(self) -> None:
        """Songs without a lyrics field must default to an empty lrc_url."""
        song = _build_song({
            "name": "Example",
            "artist": "Artist",
            "url": "api.php?get=url&id=1",
        })
        self.assertIsNotNone(song)
        self.assertEqual(song.lrc_url, "")

    def test_magic_bytes_beat_content_disposition(self) -> None:
        """Magic bytes detection must take priority over Content-Disposition."""
        fmt = _select_audio_format(
            {"Content-Disposition": 'attachment; filename="track.php"',
             "Content-Type": "application/octet-stream"},
            "https://example.com/download.php",
            b"ID3",
            "mp3",
        )
        self.assertEqual(fmt, "mp3")


if __name__ == "__main__":
    unittest.main()
