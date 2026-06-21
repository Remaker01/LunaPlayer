"""
SettingsDialog – unified settings window for SmallPlayer.

Settings are persisted to ``~/.smallplayer/config.json`` via
:mod:`app.services.config` and applied immediately on "OK".
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models.song import PlayMode
import app.services.config as cfg

# ---------------------------------------------------------------------------
# Setting keys (single source of truth)
# ---------------------------------------------------------------------------

KEY_DOWNLOAD_DIR = "download_dir"
KEY_LYRICS_FONT_SIZE = "lyrics_font_size"
KEY_DEFAULT_PLAY_MODE = "default_play_mode"
KEY_VOLUME = "volume"

_DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Music" / "SmallPlayer")
_DEFAULT_FONT_SIZE = 22
_DEFAULT_VOLUME = 80


# ---------------------------------------------------------------------------
# Settings data container
# ---------------------------------------------------------------------------

class Settings:
    """Immutable snapshot of all settings values."""

    def __init__(
        self,
        download_dir: str = _DEFAULT_DOWNLOAD_DIR,
        lyrics_font_size: int = _DEFAULT_FONT_SIZE,
        default_play_mode: PlayMode = PlayMode.SEQUENTIAL,
        volume: int = _DEFAULT_VOLUME,
    ) -> None:
        self.download_dir = download_dir
        self.lyrics_font_size = lyrics_font_size
        self.default_play_mode = default_play_mode
        self.volume = volume

    @classmethod
    def load(cls) -> Settings:
        """Load current settings from the config file."""
        return cls(
            download_dir=cfg.get(KEY_DOWNLOAD_DIR, _DEFAULT_DOWNLOAD_DIR),
            lyrics_font_size=cfg.get(KEY_LYRICS_FONT_SIZE, _DEFAULT_FONT_SIZE),
            default_play_mode=PlayMode(cfg.get(KEY_DEFAULT_PLAY_MODE, PlayMode.SEQUENTIAL)),
            volume=cfg.get(KEY_VOLUME, _DEFAULT_VOLUME),
        )

    def save(self) -> None:
        """Persist all settings to the config file."""
        cfg.set(KEY_DOWNLOAD_DIR, self.download_dir)
        cfg.set(KEY_LYRICS_FONT_SIZE, self.lyrics_font_size)
        cfg.set(KEY_DEFAULT_PLAY_MODE, int(self.default_play_mode))
        cfg.set(KEY_VOLUME, self.volume)


# ===================================================================
# SettingsDialog
# ===================================================================

class SettingsDialog(QDialog):
    """Modal settings window.

    Returns a :class:`Settings` instance via :meth:`settings` if the user
    pressed OK, or ``None`` if cancelled.
    """

    def __init__(self, current: Settings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(480)

        self._result: Optional[Settings] = None

        # ---- Build UI ----
        layout = QVBoxLayout(self)

        # -- Download directory --
        dl_group = QGroupBox("下载目录")
        dl_layout = QHBoxLayout(dl_group)

        self._dl_path = QLineEdit(current.download_dir)
        self._dl_path.setReadOnly(True)
        dl_layout.addWidget(self._dl_path)

        dl_browse = QPushButton("浏览…")
        dl_browse.clicked.connect(self._on_browse_download_dir)
        dl_layout.addWidget(dl_browse)

        layout.addWidget(dl_group)

        # -- Playback defaults --
        play_group = QGroupBox("播放默认值")
        play_form = QFormLayout(play_group)

        self._play_mode_combo = QComboBox()
        self._play_mode_combo.addItem("顺序播放", PlayMode.SEQUENTIAL)
        self._play_mode_combo.addItem("列表循环", PlayMode.LOOP)
        self._play_mode_combo.addItem("单曲循环", PlayMode.SINGLE_LOOP)
        idx = self._play_mode_combo.findData(current.default_play_mode)
        if idx >= 0:
            self._play_mode_combo.setCurrentIndex(idx)
        play_form.addRow("默认播放模式:", self._play_mode_combo)

        self._volume_spin = QSpinBox()
        self._volume_spin.setRange(0, 100)
        self._volume_spin.setSuffix("%")
        self._volume_spin.setValue(current.volume)
        play_form.addRow("默认音量:", self._volume_spin)

        layout.addWidget(play_group)

        # -- Lyrics --
        lrc_group = QGroupBox("桌面歌词")
        lrc_form = QFormLayout(lrc_group)

        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(12, 48)
        self._font_size_spin.setSuffix(" px")
        self._font_size_spin.setValue(current.lyrics_font_size)
        lrc_form.addRow("字体大小:", self._font_size_spin)

        layout.addWidget(lrc_group)

        # -- Buttons --
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def settings(self) -> Optional[Settings]:
        """Return the settings snapshot if the dialog was accepted."""
        return self._result

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    @Slot()
    def _on_browse_download_dir(self) -> None:
        """Open a directory picker to change the download location."""
        new_dir = QFileDialog.getExistingDirectory(
            self, "选择下载目录", self._dl_path.text(),
        )
        if new_dir:
            self._dl_path.setText(new_dir)

    @Slot()
    def _on_accept(self) -> None:
        """Collect all values, persist, and close."""
        self._result = Settings(
            download_dir=self._dl_path.text(),
            lyrics_font_size=self._font_size_spin.value(),
            default_play_mode=self._play_mode_combo.currentData(),
            volume=self._volume_spin.value(),
        )
        self._result.save()
        self.accept()
