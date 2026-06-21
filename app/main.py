"""
Application entry point for SmallPlayer.

Initialises the Qt application, loads the stylesheet, creates all core
modules (DatabaseManager, AudioEngine, PlaylistManager, MusicScanner),
and instantiates the MainWindow.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure the project root is on sys.path so that ``app`` is importable
# regardless of whether the user runs ``python app/main.py`` or
# ``python main.py`` from inside ``app/``.
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.core.audio_engine import AudioEngine
from app.core.music_scanner import MusicScanner
from app.core.playlist_manager import PlaylistManager
from app.models.database import DatabaseManager
from app.ui.main_window import MainWindow

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_stylesheet(app: QApplication) -> None:
    """Load and apply the QSS stylesheet from ``resources/style.qss``.

    Falls back silently if the file does not exist (the application will
    use the default Qt theme instead).
    """
    style_path = Path(__file__).parent.parent / "resources" / "style.qss"
    if style_path.exists():
        css = style_path.read_text(encoding="utf-8")
        app.setStyleSheet(css)
        logger.info("Stylesheet loaded from %s", style_path)
    else:
        logger.info("No stylesheet found at %s – using default theme", style_path)


def _set_default_font(app: QApplication) -> None:
    """Set a slightly larger default font for better readability."""
    font = QApplication.font()
    font.setPointSize(10)
    app.setFont(font)


def main() -> None:
    """Application entry point."""

    # -- Qt Application --
    QCoreApplication.setApplicationName("SmallPlayer")
    QCoreApplication.setOrganizationName("SmallPlayer")
    QCoreApplication.setApplicationVersion("0.1.0")

    app = QApplication(sys.argv)
    app.setAttribute(Qt.AA_DontCreateNativeWidgetSiblings)

    # -- Styling --
    _set_default_font(app)
    _load_stylesheet(app)

    # -- Application icon --
    icon_path = Path(__file__).parent.parent / "resources" / "icon.ico"
    if icon_path.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon_path)))

    # -- Core modules --
    # Database (shared instance).
    db_manager = DatabaseManager()
    db_manager.connect(check_same_thread=False)

    # Playlist manager (lives in main thread).
    playlist_manager = PlaylistManager()

    # Audio engine (lives in main thread; spawns decoder thread internally).
    audio_engine = AudioEngine()

    # Music scanner (owns its own QThread).
    music_scanner = MusicScanner(db_manager)

    # -- Main Window --
    window = MainWindow(
        playlist_manager=playlist_manager,
        audio_engine=audio_engine,
        music_scanner=music_scanner,
    )
    window.show()

    # -- Bootstrap: restore the last session's playlist or start empty. --
    restored = playlist_manager.load_from_m3u(db_manager)
    if restored:
        logger.info("Restored playlist from ~/.smallplayer/playlists/current.m3u8")
    else:
        logger.info("No saved playlist – starting with an empty queue.")

    # -- Event loop --
    exit_code = app.exec()

    # -- Cleanup --
    audio_engine.stop()
    music_scanner.request_stop()
    music_scanner.wait(2000)
    db_manager.close()

    sys.exit(exit_code)

if __name__ == "__main__":
    main()
