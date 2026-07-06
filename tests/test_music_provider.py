"""Unit tests for online music provider helpers."""

from __future__ import annotations

import unittest

from app.services.music_provider import (
    _build_song,
    _select_audio_format,
)


class TestMusicProviderFormatDetection(unittest.TestCase):
    def test_content_disposition_filename_takes_priority(self) -> None:
        fmt = _select_audio_format(
            {"Content-Disposition": 'attachment; filename="track.flac"',
             "Content-Type": "audio/mpeg"},
            "https://example.com/download",
            b"ID3",
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

    def test_build_song_uses_api_format_when_available(self) -> None:
        song = _build_song({
            "name": "Example",
            "artist": "Artist",
            "url": "api.php?get=url&id=1",
            "format": "flac",
        })

        self.assertIsNotNone(song)
        self.assertEqual(song.file_format, "flac")


if __name__ == "__main__":
    unittest.main()
