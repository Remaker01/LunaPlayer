"""
LyricsWindow – a frameless, always-on-top overlay window that displays LRC
lyrics synchronised with the current playback position.

Architecture
------------
*LrcParser*
    Parses standard LRC files into a sorted list of (time_ms, text) tuples.

*LyricsWindow* (QWidget)
    A frameless, translucent, always-on-top window that paints lyrics with
    the current line highlighted.  Supports mouse-drag to reposition, a
    right-click context menu, and font size adjustment.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, QPoint, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontDatabase,
    QMouseEvent,
    QPainter,
    QPen,
    QBrush,
)
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QWidget,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LRC parser
# ---------------------------------------------------------------------------

LrcLine = Tuple[int, str]  # (time_ms, text)


class LrcParser:
    """Parse a standard LRC file into a sorted list of (time_ms, text) tuples.

    Supports the ``[mm:ss.xx]`` and ``[mm:ss.xxx]`` timestamp formats.
    Multiple timestamps on the same line (e.g. ``[00:12.34][00:56.78]text``)
    are expanded into separate entries.
    """

    # Pattern: [mm:ss.xx] or [mm:ss.xxx]
    _TIMESTAMP_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")

    @classmethod
    def parse(cls, lrc_text: str) -> List[LrcLine]:
        """Parse *lrc_text* and return a sorted list of (time_ms, text)."""
        lines: List[LrcLine] = []

        for line in lrc_text.splitlines():
            line = line.strip()
            if not line:
                continue

            # Find all timestamps at the start of the line.
            timestamps = list(cls._TIMESTAMP_RE.finditer(line))
            if not timestamps:
                continue

            # The text is everything after the last timestamp.
            last_end = timestamps[-1].end()
            text = line[last_end:].strip()

            for match in timestamps:
                minutes = int(match.group(1))
                seconds = float(match.group(2))
                time_ms = int(minutes * 60_000 + seconds * 1000)
                lines.append((time_ms, text))

        # Sort by time.
        lines.sort(key=lambda x: x[0])
        return lines

    @classmethod
    def load(cls, file_path: str) -> List[LrcLine]:
        """Load and parse an LRC file from disk."""
        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = Path(file_path).read_text(encoding="gbk")
        except FileNotFoundError:
            logger.warning("LRC file not found: %s", file_path)
            return []
        return cls.parse(text)


# ===================================================================
# LyricsWindow
# ===================================================================

class LyricsWindow(QWidget):
    """Frameless, always-on-top lyrics overlay.

    Signals
    -------
    locked_changed(locked)
        Emitted when the window is locked (click-through) or unlocked.
    font_size_changed(size)
        Emitted when the user adjusts the font size.
    """

    locked_changed = Signal(bool)
    font_size_changed = Signal(int)

    # ------------------------------------------------------------------
    # Constants
    # ------------------------------------------------------------------

    # Colours (Catppuccin Mocha inspired)
    BG_COLOR = QColor(30, 30, 46, 200)
    TEXT_COLOR = QColor(205, 214, 244, 180)       # sub-text
    HIGHLIGHT_COLOR = QColor(203, 166, 247)        # mauve
    SHADOW_COLOR = QColor(0, 0, 0, 80)

    MARGIN_H = 30  # horizontal margin
    MARGIN_V = 20  # vertical margin
    LINE_GAP = 8   # spacing between lines

    DEFAULT_WIDTH = 600
    DEFAULT_HEIGHT = 300
    DEFAULT_FONT_SIZE = 22
    MIN_FONT_SIZE = 12
    MAX_FONT_SIZE = 48

    def __init__(self, parent=None):
        super().__init__(parent)

        # -- Window flags --
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("lyricsWindow")

        # -- Geometry --
        self.setFixedSize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self._move_to_bottom_center()

        # -- State --
        self._lyrics: List[LrcLine] = []
        self._current_line_index: int = -1
        self._locked: bool = False
        self._font_size: int = self.DEFAULT_FONT_SIZE

        # -- Drag state --
        self._dragging: bool = False
        self._drag_offset: QPoint = QPoint()

        # -- Build font --
        self._update_font()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @Slot(list)
    def load_lyrics(self, lrc_lines: List[LrcLine]) -> None:
        """Load new lyrics data."""
        self._lyrics = list(lrc_lines)
        self._current_line_index = -1
        self.update()

    @Slot(int)
    def sync_position(self, position_ms: int) -> None:
        """Highlight the lyric line that corresponds to *position_ms*."""
        if not self._lyrics:
            return

        # Find the last line whose timestamp is <= position_ms.
        # Binary search for efficiency.
        lo, hi = 0, len(self._lyrics) - 1
        new_index = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._lyrics[mid][0] <= position_ms:
                new_index = mid
                lo = mid + 1
            else:
                hi = mid - 1

        if new_index != self._current_line_index:
            self._current_line_index = new_index
            self.update()

    def set_locked(self, locked: bool) -> None:
        """Enable / disable lock (click-through) mode."""
        self._locked = locked
        if locked:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        else:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.locked_changed.emit(locked)
        self.update()

    def is_locked(self) -> bool:
        return self._locked

    def set_lyrics_font_size(self, size: int) -> None:
        """Change the lyrics font size."""
        self._font_size = max(self.MIN_FONT_SIZE, min(self.MAX_FONT_SIZE, size))
        self._update_font()
        self.font_size_changed.emit(self._font_size)
        self.update()

    def lyrics_font_size(self) -> int:
        return self._font_size

    # ------------------------------------------------------------------
    # Mouse events – drag to move
    # ------------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and not self._locked:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging and not self._locked:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False
            event.accept()

    def contextMenuEvent(self, event) -> None:
        """Show right-click context menu."""
        menu = QMenu(self)
        menu.setObjectName("lyricsMenu")

        # Lock/Unlock toggle
        lock_text = "� 解锁" if self._locked else "🔒 锁定"
        lock_action = QAction(lock_text, self)
        lock_action.setCheckable(True)
        lock_action.setChecked(self._locked)
        lock_action.triggered.connect(lambda checked: self.set_locked(not self._locked))
        menu.addAction(lock_action)

        menu.addSeparator()

        # Font size
        larger_action = QAction("🔤 增大字体", self)
        larger_action.triggered.connect(lambda: self.set_lyrics_font_size(self._font_size + 2))
        menu.addAction(larger_action)

        smaller_action = QAction("🔤 减小字体", self)
        smaller_action.triggered.connect(lambda: self.set_lyrics_font_size(self._font_size - 2))
        menu.addAction(smaller_action)

        reset_action = QAction("🔤 重置字体大小", self)
        reset_action.triggered.connect(lambda: self.set_lyrics_font_size(self.DEFAULT_FONT_SIZE))
        menu.addAction(reset_action)

        menu.addSeparator()

        close_action = QAction("✕ 关闭", self)
        close_action.triggered.connect(self.hide)
        menu.addAction(close_action)

        menu.exec(event.globalPos())

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        """Paint the lyrics using double-buffering via QPainter."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # --- Background ---
        painter.fillRect(self.rect(), self.BG_COLOR)

        if not self._lyrics:
            # Placeholder when no lyrics are loaded.
            painter.setPen(self.TEXT_COLOR)
            painter.setFont(self._font)
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无歌词")
            painter.end()
            return

        # --- Layout calculation ---
        font_metrics = painter.fontMetrics()
        line_height = font_metrics.height() + self.LINE_GAP
        total_height = len(self._lyrics) * line_height
        # Start Y so that the current line is roughly centred vertically.
        center_y = self.height() // 2
        start_y = center_y - (self._current_line_index * line_height + line_height // 2)

        # --- Draw each line ---
        for i, (time_ms, text) in enumerate(self._lyrics):
            y = start_y + i * line_height

            # Skip lines outside the visible area.
            if y < -line_height or y > self.height() + line_height:
                continue

            is_current = (i == self._current_line_index)

            # Opacity fade for lines near the edges.
            distance_from_center = abs(i - self._current_line_index)
            if distance_from_center <= 1:
                alpha = 255
            elif distance_from_center <= 3:
                alpha = 140
            else:
                alpha = 60

            if is_current:
                painter.setPen(self.HIGHLIGHT_COLOR)
                # Slight glow effect: draw text multiple times with varying opacity.
                glow_color = QColor(self.HIGHLIGHT_COLOR)
                glow_color.setAlpha(60)
                painter.setPen(glow_color)
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1), (0, 0)]:
                    painter.drawText(
                        self.rect().adjusted(self.MARGIN_H + dx, y + dy, -self.MARGIN_H, y + dy + line_height),
                        Qt.AlignLeft | Qt.AlignTop,
                        text,
                    )
                painter.setPen(self.HIGHLIGHT_COLOR)
                # Slightly larger font for current line.
                current_font = QFont(self._font)
                current_font.setPointSize(self._font_size + 2)
                painter.setFont(current_font)
                painter.drawText(
                    self.rect().adjusted(self.MARGIN_H, y, -self.MARGIN_H, y + line_height),
                    Qt.AlignLeft | Qt.AlignTop,
                    text,
                )
                painter.setFont(self._font)
            else:
                color = QColor(self.TEXT_COLOR)
                color.setAlpha(alpha)
                painter.setPen(color)
                painter.drawText(
                    self.rect().adjusted(self.MARGIN_H, y, -self.MARGIN_H, y + line_height),
                    Qt.AlignLeft | Qt.AlignTop,
                    text,
                )

        painter.end()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _move_to_bottom_center(self) -> None:
        """Place the window at the bottom-centre of the screen."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geometry = screen.availableGeometry()
        x = (geometry.width() - self.width()) // 2
        y = geometry.bottom() - self.height() - 60
        self.move(x, y)

    def _update_font(self) -> None:
        """Rebuild the lyrics font at the current size."""
        self._font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        self._font.setPointSize(self._font_size)
        self._font.setBold(False)
