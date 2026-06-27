"""
SearchPanel – search online music and add results to the playlist or
download them locally.

Architecture
------------
The panel connects to a *search provider* (an object that conforms to the
``BaseSearchProvider`` interface defined in ``app.services``).  If no
provider is configured the panel shows a placeholder message.

Search results are displayed in a QListView.  Each result has "Add to
playlist" and "Download" action buttons.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.song import Song


# ===================================================================
# SearchResultListWidget – internal helper for displaying results
# ===================================================================

class _SearchResultItem(QWidget):
    """A single result row: title, artist, duration + action buttons."""

    def __init__(self, song: Song, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.song = song

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Info column ---
        info_layout = QVBoxLayout()
        info_layout.setSpacing(1)

        title_label = QLabel(song.title)
        title_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        info_layout.addWidget(title_label)

        subtitle = song.artist if song.artist else "<未知艺术家>"
        if song.album:
            subtitle += f" · {song.album}"
        artist_label = QLabel(subtitle)
        artist_label.setStyleSheet("color: #a6adc8; font-size: 11px;")
        info_layout.addWidget(artist_label)

        layout.addLayout(info_layout, 1)

        # --- Duration ---
        minutes = int(song.duration) // 60
        seconds = int(song.duration) % 60
        duration_label = QLabel(f"{minutes}:{seconds:02d}")
        duration_label.setStyleSheet("color: #6c7086; font-size: 11px;")
        duration_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(duration_label)

        self.setLayout(layout)


class SearchPanel(QWidget):
    """Search panel with input, results list, and action buttons.

    Signals
    -------
    add_to_playlist_requested(song)
        Emitted when the user clicks "Add to playlist" for a result.
    download_requested(song)
        Emitted when the user clicks "Download" for a result.
    """

    add_to_playlist_requested = Signal(object)  # Song
    download_requested = Signal(object)         # Song

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("搜索")

        # --- Layout ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # --- Search input ---
        search_row = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索在线音乐…")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.returnPressed.connect(self._on_search)
        search_row.addWidget(self._search_input)

        self._search_btn = QPushButton("搜索")
        self._search_btn.clicked.connect(self._on_search)
        search_row.addWidget(self._search_btn)
        layout.addLayout(search_row)

        # --- Source selector ---
        source_row = QHBoxLayout()
        source_label = QLabel("来源:")
        source_label.setStyleSheet("font-size: 12px; color: #a6adc8;")
        source_row.addWidget(source_label)

        self._source_combo = QComboBox()
        self._source_combo.addItem("网易云音乐", "netease")
        self._source_combo.addItem("QQ音乐", "qq")
        self._source_combo.addItem("酷狗音乐", "kugou")
        source_row.addWidget(self._source_combo, 1)
        layout.addLayout(source_row)

        # --- Status / placeholder ---
        self._status_label = QLabel("输入关键词，按回车搜索")
        self._status_label.setAlignment(Qt.AlignCenter)
        self._status_label.setStyleSheet("color: #6c7086; font-size: 12px; padding: 16px;")
        layout.addWidget(self._status_label)

        # --- Results list ---
        self._results_list = QListWidget()
        self._results_list.setFrameShape(QFrame.NoFrame)
        self._results_list.setAlternatingRowColors(True)
        self._results_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._results_list.setSpacing(2)
        self._results_list.setWordWrap(True)
        layout.addWidget(self._results_list, 1)

        # --- Action buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)

        self._add_btn = QPushButton("➕ 添加选中到播放列表")
        self._add_btn.clicked.connect(self._on_add_to_playlist)
        self._add_btn.setEnabled(False)
        btn_layout.addWidget(self._add_btn)

        self._download_btn = QPushButton("⬇ 下载选中")
        self._download_btn.clicked.connect(self._on_download)
        self._download_btn.setEnabled(False)
        btn_layout.addWidget(self._download_btn)

        self._clear_btn = QPushButton("清空结果")
        self._clear_btn.clicked.connect(self.clear_results)
        self._clear_btn.setEnabled(False)
        btn_layout.addWidget(self._clear_btn)

        layout.addLayout(btn_layout)

        # --- State ---
        self._results: list[Song] = []
        self._search_provider: Any = None  # Will be set by MainWindow

        # Enable buttons when result(s) are selected.
        self._results_list.itemSelectionChanged.connect(self._on_selection_changed)
        self._results_list.itemDoubleClicked.connect(self._on_item_double_clicked)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_search_provider(self, provider: Any) -> None:
        """Set the search provider instance (must implement ``search`` method
        and emit ``results_ready`` / ``search_error`` signals)."""
        self._search_provider = provider

    @Slot(list)
    def display_results(self, songs: list[Song]) -> None:
        """Populate the results list with *songs*."""
        self._results = list(songs)
        self._results_list.clear()

        if not songs:
            self._status_label.setText("未找到结果")
            self._status_label.show()
            self._clear_btn.setEnabled(False)
            return

        self._status_label.hide()
        for song in songs:
            widget = _SearchResultItem(song)
            item = QListWidgetItem(self._results_list)
            item.setSizeHint(widget.sizeHint())
            self._results_list.addItem(item)
            self._results_list.setItemWidget(item, widget)

        self._results_list.setCurrentRow(0)
        self._clear_btn.setEnabled(True)

    @Slot(str)
    def display_error(self, friendly: str) -> None:
        """Show a friendly error message in the search panel status label."""
        self._status_label.setText(friendly)
        self._status_label.show()

    def clear_results(self) -> None:
        """Clear all search results."""
        self._results.clear()
        self._results_list.clear()
        self._status_label.setText("输入关键词，按回车搜索")
        self._status_label.show()
        self._add_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_search(self) -> None:
        """Trigger a search via the configured provider."""
        keyword = self._search_input.text().strip()
        if not keyword:
            return

        self._status_label.setText("搜索中…")
        self._status_label.show()
        self._results_list.clear()
        self._add_btn.setEnabled(False)
        self._download_btn.setEnabled(False)
        self._clear_btn.setEnabled(False)

        if self._search_provider is not None and hasattr(self._search_provider, "search"):
            source = self._source_combo.currentData()
            self._search_provider.search(keyword, source=source)
        else:
            self._status_label.setText("未配置搜索服务")

    def _on_selection_changed(self) -> None:
        """Enable/disable action buttons based on selection count."""
        has_selection = len(self._results_list.selectedItems()) > 0
        self._add_btn.setEnabled(has_selection)
        self._download_btn.setEnabled(has_selection)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Double-click adds the song directly to the playlist."""
        row = self._results_list.row(item)
        if 0 <= row < len(self._results):
            self.add_to_playlist_requested.emit(self._results[row])

    def _on_add_to_playlist(self) -> None:
        """Emit signal for all selected results (batch add)."""
        for item in self._results_list.selectedItems():
            row = self._results_list.row(item)
            if 0 <= row < len(self._results):
                self.add_to_playlist_requested.emit(self._results[row])

    def _on_download(self) -> None:
        """Emit download signal for all selected results (batch download)."""
        for item in self._results_list.selectedItems():
            row = self._results_list.row(item)
            if 0 <= row < len(self._results):
                self.download_requested.emit(self._results[row])
