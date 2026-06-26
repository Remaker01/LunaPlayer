"""Shared application paths and legacy-data migration helpers."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

APP_DIR_NAME = "lunaplayer"
LEGACY_APP_DIR_NAME = "smallplayer"


def app_data_dir() -> Path:
    """Return the LunaPlayer data directory, migrating legacy data if present."""
    home = Path.home()
    new_dir = home / f".{APP_DIR_NAME}"
    old_dir = home / f".{LEGACY_APP_DIR_NAME}"
    _migrate_legacy_tree(old_dir, new_dir)
    new_dir.mkdir(parents=True, exist_ok=True)
    return new_dir


def config_path() -> Path:
    """Return the JSON settings path under the application data directory."""
    return app_data_dir() / "config.json"


def playlists_dir() -> Path:
    """Return the application playlists directory, creating it if needed."""
    directory = app_data_dir() / "playlists"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def default_download_dir() -> Path:
    """Return the default local download directory for LunaPlayer."""
    return Path.home() / "Music" / "LunaPlayer"


def _migrate_legacy_tree(old_dir: Path, new_dir: Path) -> None:
    """Copy any missing files from *old_dir* into *new_dir* without overwriting."""
    if not old_dir.exists():
        return

    try:
        new_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Failed to create LunaPlayer data dir %s: %s", new_dir, exc)
        return

    for source in old_dir.rglob("*"):
        target = new_dir / source.relative_to(old_dir)
        if source.is_dir():
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Failed to create migrated directory %s: %s", target, exc)
            continue

        if target.exists():
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError as exc:
            logger.warning("Failed to migrate legacy file %s -> %s: %s", source, target, exc)
