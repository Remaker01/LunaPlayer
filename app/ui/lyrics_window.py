"""
LyricsWindow – a frameless, always-on-top overlay window that displays LRC
lyrics synchronised with the current playback position.

Architecture
------------
*LrcParser*
    Parses standard LRC files into a sorted list of (time_ms, text) tuples.

*LyricsWindow* (QWidget)
    A frameless, translucent, always-on-top window that lays out lyrics as
    centered text blocks with light fade transitions between active lines.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QApplication, QMenu, QWidget

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

    _TIMESTAMP_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")

    @classmethod
    def parse(cls, lrc_text: str) -> List[LrcLine]:
        """Parse *lrc_text* and return a sorted list of (time_ms, text)."""
        lines: List[LrcLine] = []

        for line in lrc_text.splitlines():
            line = line.strip()
            if not line:
                continue

            timestamps = list(cls._TIMESTAMP_RE.finditer(line))
            if not timestamps:
                continue

            last_end = timestamps[-1].end()
            text = line[last_end:].strip()

            for match in timestamps:
                minutes = int(match.group(1))
                seconds = float(match.group(2))
                time_ms = int(minutes * 60_000 + seconds * 1000)
                lines.append((time_ms, text))

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


@dataclass
class _LyricBlock:
    """Measured lyric block ready for painting."""

    index: int
    text: str
    rect: QRectF
    font: QFont
    color: QColor
    highlight_strength: float


class LyricsWindow(QWidget):
    """Frameless, always-on-top lyrics overlay."""

    locked_changed = Signal(bool)
    font_size_changed = Signal(int)

    PANEL_BG_COLOR = QColor(12, 16, 26, 150)
    PANEL_BORDER_COLOR = QColor(255, 255, 255, 28)
    TEXT_COLOR = QColor(239, 243, 255, 210)
    HIGHLIGHT_COLOR = QColor(245, 200, 255, 255)
    SHADOW_COLOR = QColor(0, 0, 0, 70)
    PLACEHOLDER_COLOR = QColor(225, 230, 246, 170)

    WINDOW_MARGIN = 10
    CONTENT_PADDING_H = 44
    CONTENT_PADDING_V = 24
    BLOCK_GAP = 12
    PANEL_RADIUS = 18
    CURRENT_FONT_DELTA = 4
    CONTEXT_FONT_DELTA = -2
    DEFAULT_WIDTH = 600
    DEFAULT_HEIGHT = 200
    DEFAULT_FONT_SIZE = 22
    MIN_FONT_SIZE = 12
    MAX_FONT_SIZE = 48
    TRANSITION_DURATION_MS = 180
    TRANSITION_INTERVAL_MS = 16

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("lyricsWindow")

        self.setFixedSize(self.DEFAULT_WIDTH, self.DEFAULT_HEIGHT)
        self._move_to_bottom_center()

        self._lyrics: List[LrcLine] = []
        self._current_line_index: int = -1
        self._locked: bool = False
        self._font_size: int = self.DEFAULT_FONT_SIZE
        self._dragging: bool = False
        self._drag_offset: QPoint = QPoint()

        self._transition_from_index: int = -1
        self._transition_to_index: int = -1
        self._transition_progress: float = 1.0
        self._transition_started_at: float = 0.0
        self._transition_timer = QTimer(self)
        self._transition_timer.setInterval(self.TRANSITION_INTERVAL_MS)
        self._transition_timer.timeout.connect(self._advance_transition)

        self._update_font()

    @Slot(list)
    def load_lyrics(self, lrc_lines: List[LrcLine]) -> None:
        """Load new lyrics data."""
        self._lyrics = list(lrc_lines)
        self._current_line_index = -1
        self._stop_transition()
        self.update()

    @Slot(int)
    def sync_position(self, position_ms: int) -> None:
        """Highlight the lyric line that corresponds to *position_ms*."""
        if not self._lyrics:
            return

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
            old_index = self._current_line_index
            self._current_line_index = new_index
            self._begin_transition(old_index, new_index)
            self.update()

    def set_locked(self, locked: bool) -> None:
        """Enable / disable lock (click-through) mode."""
        self._locked = locked
        self.setAttribute(Qt.WA_TransparentForMouseEvents, locked)
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

        lock_text = "🔓 解锁" if self._locked else "🔒 锁定"
        lock_action = QAction(lock_text, self)
        lock_action.setCheckable(True)
        lock_action.setChecked(self._locked)
        lock_action.triggered.connect(lambda checked: self.set_locked(not self._locked))
        menu.addAction(lock_action)

        menu.addSeparator()

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

    def paintEvent(self, event) -> None:
        """Paint the lyrics as centered text blocks on a stage-like panel."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)

        self._paint_panel(painter)

        if not self._lyrics:
            self._paint_placeholder(painter)
            painter.end()
            return

        for block in self._build_visible_blocks():
            self._paint_block(painter, block)

        painter.end()

    def _content_rect(self) -> QRectF:
        panel = QRectF(self.rect()).adjusted(
            self.WINDOW_MARGIN,
            self.WINDOW_MARGIN,
            -self.WINDOW_MARGIN,
            -self.WINDOW_MARGIN,
        )
        return panel.adjusted(
            self.CONTENT_PADDING_H,
            self.CONTENT_PADDING_V,
            -self.CONTENT_PADDING_H,
            -self.CONTENT_PADDING_V,
        )

    def _text_flags(self) -> int:
        return int(Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap)

    def _font_for_highlight_strength(self, strength: float) -> QFont:
        """Return a font interpolated between context and active emphasis."""
        context_size = max(self.MIN_FONT_SIZE, self._font_size + self.CONTEXT_FONT_DELTA)
        active_size = min(self.MAX_FONT_SIZE, self._font_size + self.CURRENT_FONT_DELTA)
        font = QFont(self._font)
        font.setPointSizeF(context_size + (active_size - context_size) * strength)
        font.setWeight(QFont.DemiBold if strength >= 0.55 else QFont.Normal)
        return font

    def _measure_text_height(self, text: str, font: QFont) -> int:
        """Measure wrapped text height inside the available content width."""
        metrics = QFontMetrics(font)
        max_width = max(120, int(self._content_rect().width()))
        bounds = metrics.boundingRect(QRect(0, 0, max_width, 10_000), self._text_flags(), text)
        return max(metrics.height(), bounds.height())

    def _build_visible_blocks(self) -> list[_LyricBlock]:
        """Build the centered lyric blocks that fit in the visible stage."""
        if not self._lyrics:
            return []

        content = self._content_rect()
        anchor_index = self._anchor_index()
        anchor_strength = self._highlight_strength(anchor_index)
        anchor_block = self._build_block(anchor_index, anchor_strength)
        anchor_top = content.center().y() - anchor_block.rect.height() / 2
        anchor_block.rect.moveTop(anchor_top)

        blocks: list[_LyricBlock] = [anchor_block]
        top_edge = anchor_block.rect.top()
        bottom_edge = anchor_block.rect.bottom()

        for index in range(anchor_index - 1, -1, -1):
            block = self._build_block(index, self._highlight_strength(index))
            next_top = top_edge - self.BLOCK_GAP - block.rect.height()
            block.rect.moveTop(next_top)
            if block.rect.bottom() < content.top() - self.BLOCK_GAP:
                break
            blocks.insert(0, block)
            top_edge = block.rect.top()

        for index in range(anchor_index + 1, len(self._lyrics)):
            block = self._build_block(index, self._highlight_strength(index))
            next_top = bottom_edge + self.BLOCK_GAP
            block.rect.moveTop(next_top)
            if block.rect.top() > content.bottom() + self.BLOCK_GAP:
                break
            blocks.append(block)
            bottom_edge = block.rect.bottom()

        return blocks

    def _build_block(self, index: int, highlight_strength: float) -> _LyricBlock:
        """Build a single measured lyric block for *index*."""
        content = self._content_rect()
        text = self._lyrics[index][1]
        font = self._font_for_highlight_strength(highlight_strength)
        height = self._measure_text_height(text, font)
        rect = QRectF(content.left(), 0, content.width(), height)
        distance = abs(index - self._anchor_index())
        color = self._color_for_block(distance, highlight_strength)
        return _LyricBlock(
            index=index,
            text=text,
            rect=rect,
            font=font,
            color=color,
            highlight_strength=highlight_strength,
        )

    def _paint_panel(self, painter: QPainter) -> None:
        """Paint the rounded stage panel."""
        panel = QRectF(self.rect()).adjusted(
            self.WINDOW_MARGIN,
            self.WINDOW_MARGIN,
            -self.WINDOW_MARGIN,
            -self.WINDOW_MARGIN,
        )
        painter.setPen(QPen(self.PANEL_BORDER_COLOR, 1))
        painter.setBrush(self.PANEL_BG_COLOR)
        painter.drawRoundedRect(panel, self.PANEL_RADIUS, self.PANEL_RADIUS)

    def _paint_placeholder(self, painter: QPainter) -> None:
        """Paint the empty-state placeholder."""
        placeholder_font = self._font_for_highlight_strength(0.55)
        painter.setFont(placeholder_font)
        painter.setPen(self.PLACEHOLDER_COLOR)
        painter.drawText(self._content_rect(), Qt.AlignCenter | Qt.TextWordWrap, "暂无歌词")

    def _paint_block(self, painter: QPainter, block: _LyricBlock) -> None:
        """Paint one measured lyric block."""
        if block.highlight_strength > 0.25:
            highlight_bg = QColor(self.HIGHLIGHT_COLOR)
            highlight_bg.setAlpha(int(36 * block.highlight_strength))
            painter.setPen(Qt.NoPen)
            painter.setBrush(highlight_bg)
            painter.drawRoundedRect(
                block.rect.adjusted(-12, -8, 12, 8),
                self.PANEL_RADIUS,
                self.PANEL_RADIUS,
            )

        painter.setFont(block.font)

        shadow = QColor(self.SHADOW_COLOR)
        shadow.setAlpha(int(28 + 24 * block.highlight_strength))
        painter.setPen(shadow)
        shadow_rect = QRectF(block.rect)
        shadow_rect.translate(0, 1)
        painter.drawText(shadow_rect, self._text_flags(), block.text)

        painter.setPen(block.color)
        painter.drawText(block.rect, self._text_flags(), block.text)

    def _anchor_index(self) -> int:
        """Return the block index used as the layout anchor."""
        if not self._lyrics:
            return -1
        if 0 <= self._current_line_index < len(self._lyrics):
            return self._current_line_index
        return 0

    def _highlight_strength(self, index: int) -> float:
        """Return the highlight emphasis for *index* during transitions."""
        if self._transition_timer.isActive() and self._transition_from_index != self._transition_to_index:
            if index == self._transition_from_index:
                return 1.0 - self._transition_progress
            if index == self._transition_to_index:
                return self._transition_progress
            return 0.0
        return 1.0 if index == self._current_line_index else 0.0

    def _color_for_block(self, distance: int, highlight_strength: float) -> QColor:
        """Return the text color for a lyric block."""
        if distance <= 1:
            base_alpha = 195
        elif distance == 2:
            base_alpha = 145
        elif distance == 3:
            base_alpha = 105
        else:
            base_alpha = 72

        mix = min(1.0, max(0.0, highlight_strength))
        color = QColor(
            int(self.TEXT_COLOR.red() + (self.HIGHLIGHT_COLOR.red() - self.TEXT_COLOR.red()) * mix),
            int(self.TEXT_COLOR.green() + (self.HIGHLIGHT_COLOR.green() - self.TEXT_COLOR.green()) * mix),
            int(self.TEXT_COLOR.blue() + (self.HIGHLIGHT_COLOR.blue() - self.TEXT_COLOR.blue()) * mix),
        )
        color.setAlpha(int(base_alpha + (255 - base_alpha) * mix))
        return color

    def _begin_transition(self, old_index: int, new_index: int) -> None:
        """Start a light fade transition between adjacent lyric lines."""
        if old_index < 0 or new_index < 0 or abs(new_index - old_index) > 1:
            self._stop_transition()
            self._transition_from_index = new_index
            self._transition_to_index = new_index
            return

        self._transition_from_index = old_index
        self._transition_to_index = new_index
        self._transition_progress = 0.0
        self._transition_started_at = time.monotonic()
        self._transition_timer.start()

    def _advance_transition(self) -> None:
        """Advance the lightweight fade transition."""
        elapsed_ms = (time.monotonic() - self._transition_started_at) * 1000.0
        linear_progress = min(1.0, elapsed_ms / self.TRANSITION_DURATION_MS)
        self._transition_progress = 1.0 - pow(1.0 - linear_progress, 2)

        if linear_progress >= 1.0:
            self._stop_transition()
            self._transition_from_index = self._transition_to_index

        self.update()

    def _stop_transition(self) -> None:
        """Reset the transition state to its steady-state values."""
        self._transition_timer.stop()
        self._transition_progress = 1.0

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
        """Rebuild the base lyrics font at the current size."""
        self._font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
        self._font.setStyleHint(QFont.SansSerif)
        self._font.setStyleStrategy(QFont.PreferAntialias)
        self._font.setPointSize(self._font_size)
        self._font.setWeight(QFont.Normal)
