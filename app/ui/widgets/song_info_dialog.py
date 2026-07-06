"""
SongInfoDialog – displays detailed metadata for an audio file.

Shows information from the Song model plus additional technical metadata
extracted via mutagen (sample rate, bitrate, channels, codec, file size).
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import mutagen

from app.models.song import Song

logger = logging.getLogger(__name__)


def _format_duration(seconds: float) -> str:
    """Format seconds as ``m:ss`` or ``h:mm:ss`` for long tracks."""
    if seconds <= 0:
        return "0:00"
    total_secs = int(seconds)
    hours, remainder = divmod(total_secs, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_size(bytes_: int) -> str:
    """Format byte count as human-readable string."""
    if bytes_ <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB"]
    i = int(math.log(bytes_, 1024)) if bytes_ > 0 else 0
    i = min(i, len(units) - 1)
    size = bytes_ / (1024**i)
    return f"{size:.1f} {units[i]}"


def _read_tech_metadata(file_path: str) -> dict[str, str]:
    """Read technical audio properties from *file_path* via mutagen.

    Returns a dict of human-readable key-value pairs suitable for display.
    """
    info: dict[str, str] = {}
    try:
        audio = mutagen.File(file_path)
        if audio is None:
            return {"Error": "Unsupported or corrupt file"}

        # -- Audio info --
        if hasattr(audio.info, "sample_rate"):
            sr = audio.info.sample_rate
            info["Sample Rate"] = f"{sr} Hz"

        if hasattr(audio.info, "bitrate") and audio.info.bitrate > 0:
            br = audio.info.bitrate
            info["Bitrate"] = f"{br // 1000} kbps"

        if hasattr(audio.info, "channels"):
            ch = audio.info.channels
            info["Channels"] = f"{ch} ({'Mono' if ch == 1 else 'Stereo' if ch == 2 else 'Surround'})"

        # -- Codec / format detail --
        if hasattr(audio, "mime"):
            info["Codec"] = str(audio.mime[0]) if audio.mime else "unknown"
        else:
            # Derive from the audio info class name.
            cls_name = type(audio.info).__name__.replace("Info", "").replace("Header", "")
            info["Codec"] = cls_name or Path(file_path).suffix[1:].upper()

        # -- Bit depth (lossless formats) --
        if hasattr(audio.info, "bits_per_sample") and audio.info.bits_per_sample > 0:
            info["Bit Depth"] = f"{audio.info.bits_per_sample} bit"

        # -- File size --
        try:
            stat = Path(file_path).stat()
            info["File Size"] = _format_size(stat.st_size)
        except OSError:
            pass

        # -- Encoding / compression ratio --
        if hasattr(audio.info, "bitrate") and hasattr(audio.info, "sample_rate") and audio.info.bitrate > 0 and audio.info.sample_rate > 0:
            if hasattr(audio.info, "bits_per_sample") and audio.info.bits_per_sample > 0:
                uncompressed_br = audio.info.sample_rate * audio.info.channels * audio.info.bits_per_sample
                ratio = audio.info.bitrate / uncompressed_br
                info["Compression"] = f"{ratio:.0%}"

    except Exception as exc:
        logger.warning("Failed to read metadata for %s: %s", file_path, exc)
        info["Error"] = str(exc)

    return info


class SongInfoDialog(QDialog):
    """Modal dialog displaying detailed song metadata."""

    def __init__(self, song: Song, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._song = song

        self.setWindowTitle("歌曲详情")
        self.setObjectName("songInfoDialog")
        self.resize(560, 520)
        self.setMinimumWidth(520)
        self.setModal(True)

        self._build_ui()
        self._populate_data()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 16)

        # -- Header: title + artist --
        header = QLabel()
        header.setWordWrap(True)
        header.setObjectName("songInfoHeader")
        self._header_label = header
        layout.addWidget(header)

        # -- Separator --
        sep = QLabel()
        sep.setObjectName("songInfoSeparator")
        sep.setFixedHeight(1)
        layout.addWidget(sep)

        # -- Form with metadata rows --
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(6)
        form.setContentsMargins(4, 4, 4, 4)

        self._form_rows: dict[str, QLabel] = {}
        fields = [
            ("艺术家", "artist"),
            ("专辑", "album"),
            ("时长", "duration"),
            ("格式", "format"),
            ("文件路径", "path"),
            ("文件大小", "size"),
            ("采样率", "sample_rate"),
            ("比特率", "bitrate"),
            ("声道", "channels"),
            ("编码", "codec"),
            ("位深", "bit_depth"),
            ("压缩比", "compression"),
            ("文件哈希", "hash"),
        ]
        for label, key in fields:
            value_label = QLabel()
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
            )
            value_label.setObjectName("songInfoValue")
            form.addRow(f"{label}:", value_label)
            self._form_rows[key] = value_label

        layout.addLayout(form)

        # -- Spacer --
        layout.addStretch(1)

        # -- Close button --
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    # ------------------------------------------------------------------

    def _populate_data(self) -> None:
        """Fill the dialog fields from the Song + mutagen metadata."""
        song = self._song

        # Header
        artist = song.artist or "未知艺术家"
        title = song.title or "未知标题"
        self._header_label.setText(f"{artist} — {title}")

        # Basic fields from Song model
        self._form_rows["artist"].setText(artist)
        self._form_rows["album"].setText(song.album or "（无专辑信息）")
        self._form_rows["duration"].setText(_format_duration(song.duration))
        self._form_rows["format"].setText(song.file_format.upper() if song.file_format else "未知")
        self._form_rows["path"].setText(song.file_path)
        self._form_rows["hash"].setText(song.file_hash or "（未计算）")

        # Technical metadata from mutagen
        if song.file_path:
            tech = _read_tech_metadata(song.file_path)
            self._form_rows["sample_rate"].setText(tech.get("Sample Rate", "—"))
            self._form_rows["bitrate"].setText(tech.get("Bitrate", "—"))
            self._form_rows["channels"].setText(tech.get("Channels", "—"))
            self._form_rows["codec"].setText(tech.get("Codec", "—"))
            self._form_rows["bit_depth"].setText(tech.get("Bit Depth", "—"))
            self._form_rows["compression"].setText(tech.get("Compression", "—"))
            self._form_rows["size"].setText(tech.get("File Size", "—"))

            if "Error" in tech:
                self._form_rows["codec"].setText(tech["Error"])
