"""Simple JSON config stored in ``~/.smallplayer/config.json``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path.home() / ".smallplayer" / "config.json"


def get(key: str, default: Any = None) -> Any:
    """Read a config value."""
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return data.get(key, default)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def set(key: str, value: Any) -> None:
    """Write a config value (creates the file if needed)."""
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data[key] = value
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
