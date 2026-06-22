"""Application metadata shared across startup and UI surfaces."""

from __future__ import annotations

APP_NAME = "SmallPlayer"
APP_ORGANIZATION = "SmallPlayer"
APP_DISPLAY_NAME = "SmallPlayer - 音乐播放器"
APP_VERSION = "1.0.0-Beta"

window_title = f"{APP_DISPLAY_NAME} {APP_VERSION}"

def about_text() -> str:
    """Return the text shown in the About dialog."""
    return (
        f"{APP_DISPLAY_NAME}\n"
        f"版本：{APP_VERSION}\n\n"
        "一个基于 PySide6、FFmpeg 和 Qt Multimedia 的本地音乐播放器。"
    )
