"""Simple JSON config stored in ``~/.lunaplayer/config.json``."""

from __future__ import annotations

import json
from typing import Any

from app.paths import config_path


def get(key: str, default: Any = None) -> Any:
    """Read a config value."""
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get(key, default)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def set(key: str, value: Any) -> None:
    """Write a config value (creates the file if needed)."""
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    data[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
