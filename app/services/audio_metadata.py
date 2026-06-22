"""
Shared helpers for reading local audio metadata and embedded cover art.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

import mutagen

from app.models.song import Song

_HASH_BLOCK_SIZE = 64 * 1024  # 64 KB


def extract_song_metadata(file_path: str) -> Optional[Song]:
    """Extract tags, duration, format, and content hash for a local audio file."""
    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return None
    except Exception:
        return None

    title = _tag_str(audio, "title") or _tag_str(audio, "TIT2") or Path(file_path).stem
    artist = _tag_str(audio, "artist") or _tag_str(audio, "TPE1")
    album = _tag_str(audio, "album") or _tag_str(audio, "TALB")

    duration = 0.0
    if hasattr(audio.info, "length"):
        duration = float(audio.info.length)

    return Song(
        title=title,
        artist=artist or "",
        album=album or "",
        duration=duration,
        file_path=file_path,
        file_format=Path(file_path).suffix.lower().lstrip("."),
        file_hash=compute_hash(file_path),
    )


def extract_cover_art(file_path: str) -> Optional[bytes]:
    """Return the first embedded cover-art image for *file_path*, if any."""
    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return None
    except Exception:
        return None

    pictures = getattr(audio, "pictures", None)
    if pictures:
        picture = pictures[0]
        data = getattr(picture, "data", None)
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)

    tags = getattr(audio, "tags", None)
    if tags is None:
        return None

    getall = getattr(tags, "getall", None)
    if callable(getall):
        try:
            apic_frames = getall("APIC")
        except Exception:
            apic_frames = []
        for frame in apic_frames:
            data = getattr(frame, "data", None)
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)

    try:
        covr = tags.get("covr")
    except Exception:
        covr = None
    if covr:
        first = covr[0] if isinstance(covr, list) else covr
        if isinstance(first, (bytes, bytearray)):
            return bytes(first)

    for key in _iter_tag_keys(tags):
        if not str(key).startswith("APIC"):
            continue
        try:
            frame = tags[key]
        except Exception:
            continue
        data = getattr(frame, "data", None)
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)

    return None


def compute_hash(file_path: str) -> str:
    """SHA-256 hex digest of the file contents."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as handle:
        while True:
            block = handle.read(_HASH_BLOCK_SIZE)
            if not block:
                break
            sha.update(block)
    return sha.hexdigest()


def _tag_str(audio: mutagen.FileType, key: str, default: str = "") -> str:
    """Return the first string value for *key* from *audio*, or *default*."""
    try:
        val = audio.get(key)
        if val is None:
            return default
        if isinstance(val, list):
            return str(val[0]) if val else default
        return str(val)
    except Exception:
        return default


def _iter_tag_keys(tags: object) -> list[object]:
    """Return a safe list of tag keys for ad-hoc frame inspection."""
    try:
        return list(tags.keys())
    except Exception:
        return []
