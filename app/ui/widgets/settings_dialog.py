"""
SettingsDialog – unified settings window for LunaPlayer.

Settings are persisted to ``~/.lunaplayer/config.json`` via
:mod:`app.services.config` and applied immediately on "OK".
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.models.song import PlayMode
from app.paths import default_download_dir
import app.services.config as cfg

# ---------------------------------------------------------------------------
# Setting keys (single source of truth)
# ---------------------------------------------------------------------------

KEY_DOWNLOAD_DIR = "download_dir"
KEY_LYRICS_FONT_SIZE = "lyrics_font_size"
KEY_DEFAULT_PLAY_MODE = "default_play_mode"
KEY_VOLUME = "volume"
KEY_CLOSE_TO_TRAY = "close_to_tray"

_DEFAULT_DOWNLOAD_DIR = str(default_download_dir())
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
        close_to_tray: bool = True,
    ) -> None:
        self.download_dir = download_dir
        self.lyrics_font_size = lyrics_font_size
        self.default_play_mode = default_play_mode
        self.volume = volume
        self.close_to_tray = close_to_tray

    @classmethod
    def load(cls) -> Settings:
        """Load current settings from the config file."""
        return cls(
            download_dir=cfg.get(KEY_DOWNLOAD_DIR, _DEFAULT_DOWNLOAD_DIR),
            lyrics_font_size=cfg.get(KEY_LYRICS_FONT_SIZE, _DEFAULT_FONT_SIZE),
            default_play_mode=PlayMode(cfg.get(KEY_DEFAULT_PLAY_MODE, PlayMode.SEQUENTIAL)),
            volume=cfg.get(KEY_VOLUME, _DEFAULT_VOLUME),
            close_to_tray=cfg.get(KEY_CLOSE_TO_TRAY, True),
        )

    def save(self) -> None:
        """Persist all settings to the config file."""
        cfg.set(KEY_DOWNLOAD_DIR, self.download_dir)
        cfg.set(KEY_LYRICS_FONT_SIZE, self.lyrics_font_size)
        cfg.set(KEY_DEFAULT_PLAY_MODE, int(self.default_play_mode))
        cfg.set(KEY_VOLUME, self.volume)
        cfg.set(KEY_CLOSE_TO_TRAY, self.close_to_tray)


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
        self.resize(720, 520)
        self.setMinimumWidth(640)
        self.setMinimumHeight(460)

        self._result: Optional[Settings] = None

        # ---- Build UI ----
        layout = QVBoxLayout(self)
        body_layout = QHBoxLayout()
        body_layout.setSpacing(16)
        layout.addLayout(body_layout, 1)

        self._section_list = QListWidget()
        self._section_list.setObjectName("settingsNav")
        self._section_list.setFixedWidth(170)
        self._section_list.setSpacing(4)
        body_layout.addWidget(self._section_list)

        self._section_stack = QStackedWidget()
        body_layout.addWidget(self._section_stack, 1)

        self._build_playback_page(current)
        self._build_download_page(current)
        self._build_interface_page(current)
        self._build_behavior_page(current)

        self._section_list.currentRowChanged.connect(self._section_stack.setCurrentIndex)
        self._section_list.setCurrentRow(0)

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

    def _add_section(self, title: str, subtitle: str, page: QWidget) -> None:
        """Register one visible settings section."""
        item = QListWidgetItem(title)
        item.setToolTip(subtitle)
        self._section_list.addItem(item)
        self._section_stack.addWidget(page)

    def _create_section_page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        """Create a section page with a consistent title block."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("settingsSectionTitle")
        layout.addWidget(title_label)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("settingsSectionSubtitle")
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label)

        return page, layout

    def _build_playback_page(self, current: Settings) -> None:
        """Build the playback defaults section."""
        page, layout = self._create_section_page(
            "播放",
            "设置默认播放方式与启动时音量，适合作为播放器的全局默认行为。",
        )

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
        layout.addStretch(1)
        self._add_section("播放", "默认播放方式和音量", page)

    def _build_download_page(self, current: Settings) -> None:
        """Build the download location section."""
        page, layout = self._create_section_page(
            "下载",
            "管理在线歌曲的默认保存目录，便于未来扩展下载队列和文件管理能力。",
        )

        dl_group = QGroupBox("下载目录")
        dl_layout = QHBoxLayout(dl_group)

        self._dl_path = QLineEdit(current.download_dir)
        self._dl_path.setReadOnly(True)
        dl_layout.addWidget(self._dl_path)

        dl_browse = QPushButton("浏览…")
        dl_browse.clicked.connect(self._on_browse_download_dir)
        dl_layout.addWidget(dl_browse)

        layout.addWidget(dl_group)
        layout.addStretch(1)
        self._add_section("下载", "歌曲下载位置", page)

    def _build_interface_page(self, current: Settings) -> None:
        """Build the interface-related section."""
        page, layout = self._create_section_page(
            "界面",
            "控制界面观感相关的选项。当前保留桌面歌词字体设置，后续可扩展主题与密度配置。",
        )

        lrc_group = QGroupBox("桌面歌词")
        lrc_form = QFormLayout(lrc_group)

        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(12, 48)
        self._font_size_spin.setSuffix(" px")
        self._font_size_spin.setValue(current.lyrics_font_size)
        lrc_form.addRow("字体大小:", self._font_size_spin)

        layout.addWidget(lrc_group)

        preview_group = QGroupBox("界面说明")
        preview_layout = QVBoxLayout(preview_group)
        preview_text = QLabel("当前界面采用深色桌面工作台布局，后续可在这里扩展主题、列表密度和封面显示策略。")
        preview_text.setWordWrap(True)
        preview_layout.addWidget(preview_text)
        layout.addWidget(preview_group)

        layout.addStretch(1)
        self._add_section("界面", "界面与歌词显示", page)

    def _build_behavior_page(self, current: Settings) -> None:
        """Build the behavior section."""
        page, layout = self._create_section_page(
            "行为",
            "控制窗口和托盘的交互方式。",
        )

        beh_group = QGroupBox("应用行为")
        beh_layout = QVBoxLayout(beh_group)

        self._tray_cb = QCheckBox("关闭窗口时最小化到系统托盘")
        self._tray_cb.setChecked(current.close_to_tray)
        beh_layout.addWidget(self._tray_cb)

        layout.addWidget(beh_group)
        layout.addStretch(1)
        self._add_section("行为", "窗口与托盘行为", page)

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
            close_to_tray=self._tray_cb.isChecked(),
        )
        self._result.save()
        self.accept()
