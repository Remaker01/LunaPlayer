"""
PlaylistWidget – displays the current playback queue with drag-and-drop
reordering, double-click to play, and a right-click context menu.

Architecture
------------
*PlaylistModel* (QAbstractListModel)
    Wraps a list of Song objects.  Supports internal moves for drag-and-drop.

*PlaylistWidget* (QWidget)
    Hosts the QListView with the model above.  Emits high-level signals that
    the MainWindow connects to the PlaylistManager.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QMimeData, QModelIndex, Qt, QObject, QAbstractListModel, Signal, Slot
from PySide6.QtGui import QAction, QDrag, QPainter, QPalette, QStandardItem
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QInputDialog,
    QListView,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from app.models.song import Song


# ===================================================================
# PlaylistModel – data model
# ===================================================================

class PlaylistModel(QAbstractListModel):
    """A Qt model wrapping a list of Song objects for display in a QListView.

    Each row renders as "artist – title" (or just "title" when artist is
    empty) plus a formatted duration on the right.

    Signals
    -------
    order_changed()
        Emitted after a drag-and-drop operation reorders the playlist.
    """

    order_changed = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._songs: List[Song] = []

    # ------------------------------------------------------------------
    # Data access
    # ------------------------------------------------------------------

    def set_songs(self, songs: List[Song]) -> None:
        """Replace the entire song list."""
        self.beginResetModel()
        self._songs = list(songs)
        self.endResetModel()

    def songs(self) -> List[Song]:
        """Return a copy of the current song list."""
        return list(self._songs)

    def song_at(self, index: int) -> Optional[Song]:
        """Return the song at *index*, or ``None`` if out of range."""
        if 0 <= index < len(self._songs):
            return self._songs[index]
        return None

    def remove_at(self, index: int) -> Optional[Song]:
        """Remove the song at *index* and return it."""
        if 0 <= index < len(self._songs):
            song = self._songs.pop(index)
            self.beginRemoveRows(QModelIndex(), index, index)
            self.endRemoveRows()
            return song
        return None

    def move_row(self, source_row: int, dest_row: int) -> None:
        """Move the item at *source_row* so it ends up at *dest_row*."""
        if source_row == dest_row:
            return
        song = self._songs.pop(source_row)
        # After removal, indices shift.  Adjust dest_row accordingly.
        if dest_row > source_row:
            dest_row -= 1
        self._songs.insert(dest_row, song)
        self.layoutChanged.emit()

    # ------------------------------------------------------------------
    # QAbstractListModel interface
    # ------------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._songs)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._songs):
            return None

        song = self._songs[index.row()]
        if role == Qt.DisplayRole:
            # Format: "Artist – Title     [3:45]"
            minutes = int(song.duration) // 60
            seconds = int(song.duration) % 60
            time_str = f"{minutes}:{seconds:02d}"
            if song.artist:
                return f"{song.artist} – {song.title}    [{time_str}]"
            return f"{song.title}    [{time_str}]"
        if role == Qt.ToolTipRole:
            parts = [f"标题:   {song.title}",
                     f"艺术家: {song.artist or '未知'}"]

            if song.album:
                parts.append(f"专辑:   {song.album}")
            parts.append(f"格式:  {song.file_format.upper()}")
            minutes = int(song.duration) // 60
            seconds = int(song.duration) % 60
            parts.append(f"时长:  {minutes}:{seconds:02d}")
            return "\n".join(parts)
        if role == Qt.UserRole:
            # Expose the raw Song object for external consumers.
            return song
        return None

    # ------------------------------------------------------------------
    # Drag & drop support
    # ------------------------------------------------------------------

    def supportedDropActions(self):
        return Qt.MoveAction

    def flags(self, index):
        default_flags = super().flags(index)
        if index.isValid():
            return default_flags | Qt.ItemIsDragEnabled
        return default_flags | Qt.ItemIsDropEnabled

    def mimeTypes(self):
        return ["application/x-smallplayer-song-index"]

    def mimeData(self, indexes):
        mime = QMimeData()
        # Encode the row index as a simple integer string.
        if indexes:
            row = indexes[0].row()
            mime.setData("application/x-smallplayer-song-index",
                         str(row).encode("utf-8"))
        return mime

    def dropMimeData(self, data, action, row, column, parent):
        if action == Qt.IgnoreAction:
            return True
        if not data.hasFormat("application/x-smallplayer-song-index"):
            return False

        source_row = int(bytes(data.data("application/x-smallplayer-song-index")).decode("utf-8"))

        # Determine the destination row.
        if parent.isValid():
            dest_row = parent.row()
        elif row >= 0:
            dest_row = row
        else:
            dest_row = self.rowCount()

        if source_row == dest_row:
            return False

        self.move_row(source_row, dest_row)
        self.order_changed.emit()
        return True


# ===================================================================
# PlaylistWidget – view + signals
# ===================================================================

class PlaylistWidget(QWidget):
    """Visual playlist with drag reorder, double-click play, and context menu.

    Signals
    -------
    play_requested(index)
        Emitted when the user double-clicks a song or selects "Play" from
        the context menu.
    remove_requested(index)
        Emitted when the user removes a song via the context menu.
    clear_requested()
        Emitted when the user chooses "Clear playlist".
    order_changed(songs)
        Emitted after a drag-and-drop reorder completes, carrying the new
        song ordering.
    """

    play_requested = Signal(int)
    remove_requested = Signal(int)
    clear_requested = Signal()
    order_changed = Signal(list)  # List[Song]
    info_requested = Signal(int)  # Emitted when user picks "View details"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("播放列表")

        # --- Layout ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Model ---
        self._model = PlaylistModel(self)

        # --- View ---
        self._view = QListView()
        self._view.setModel(self._model)
        self._view.setSelectionMode(QAbstractItemView.SingleSelection)
        self._view.setDragDropMode(QAbstractItemView.InternalMove)
        self._view.setDefaultDropAction(Qt.MoveAction)
        self._view.setAlternatingRowColors(True)
        self._view.setFrameShape(QFrame.NoFrame)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)

        # Connect double-click -> play
        self._view.doubleClicked.connect(self._on_double_click)

        # --- Context menu ---
        self._view.setContextMenuPolicy(Qt.CustomContextMenu)
        self._view.customContextMenuRequested.connect(self._show_context_menu)

        layout.addWidget(self._view)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @Slot(list)
    def load_songs(self, songs: List[Song]) -> None:
        """Replace the displayed song list."""
        self._model.set_songs(songs)

    @Slot(int)
    def add_song(self, song: Song) -> int:
        """Append a single song and return its index."""
        self._model.set_songs(self._model.songs() + [song])
        return self._model.rowCount() - 1

    @Slot(int)
    def remove_song(self, index: int) -> None:
        """Remove the song at *index*."""
        self._model.remove_at(index)

    def clear(self) -> None:
        """Remove all songs from the display."""
        self._model.set_songs([])

    def highlight_row(self, index: int) -> None:
        """Select and scroll to the given row index."""
        if 0 <= index < self._model.rowCount():
            idx = self._model.index(index, 0)
            self._view.setCurrentIndex(idx)
            self._view.scrollTo(idx)

    def current_song_index(self) -> int:
        """Return the index of the currently selected row, or -1."""
        idx = self._view.currentIndex()
        return idx.row() if idx.isValid() else -1

    @property
    def model(self) -> PlaylistModel:
        return self._model

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_double_click(self, index) -> None:
        """Handle double-click on a row – request playback."""
        if index.isValid():
            self.play_requested.emit(index.row())

    def _show_context_menu(self, pos) -> None:
        """Build and show the right-click context menu."""
        index = self._view.indexAt(pos)
        menu = QMenu(self)

        if index.isValid():
            song = self._model.song_at(index.row())
            if song:
                # Show song name in the menu header (disabled).
                display = f"{song.artist or '未知'} – {song.title}"
                header_action = QAction(display, self)
                header_action.setEnabled(False)
                menu.addAction(header_action)
                menu.addSeparator()

            play_action = QAction("▶ 播放", self)
            play_action.triggered.connect(lambda: self.play_requested.emit(index.row()))
            menu.addAction(play_action)

            menu.addSeparator()

            remove_action = QAction("🗑 从列表移除", self)
            remove_action.triggered.connect(lambda: self._request_remove(index.row()))
            menu.addAction(remove_action)

            menu.addSeparator()

            info_action = QAction("📋 查看详情", self)
            info_action.triggered.connect(lambda: self.info_requested.emit(index.row()))
            menu.addAction(info_action)

        menu.addSeparator()
        clear_action = QAction("清空播放列表", self)
        clear_action.triggered.connect(self.clear_requested.emit)
        menu.addAction(clear_action)

        menu.exec(self._view.viewport().mapToGlobal(pos))

    def _request_remove(self, row: int) -> None:
        """Emit remove_requested after internal model removal."""
        self.remove_requested.emit(row)
