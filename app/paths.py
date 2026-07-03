"""Shared application paths."""

from __future__ import annotations

from pathlib import Path

APP_DIR_NAME = "lunaplayer"


def app_data_dir() -> Path:
    """Return the LunaPlayer data directory, creating it if needed."""
    home = Path.home()
    new_dir = home / f".{APP_DIR_NAME}"
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
