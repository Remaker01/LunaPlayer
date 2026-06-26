"""Application metadata shared across startup and UI surfaces."""

from __future__ import annotations

APP_NAME = "LunaPlayer"
APP_ORGANIZATION = "LunaPlayer"
APP_DISPLAY_NAME = "LunaPlayer - 音乐播放器"
APP_VERSION = "1.0.0-Beta"

window_title = f"{APP_DISPLAY_NAME} {APP_VERSION}"
about_title = f"关于 {APP_NAME}"

def about_text() -> str:
    """Return the text shown in the About dialog."""
    return (
        f"{APP_DISPLAY_NAME}\n"
        f"版本：{APP_VERSION}\n\n"
        "一个基于 PySide6、FFmpeg 和 Qt Multimedia 的音乐播放器。"
    )
