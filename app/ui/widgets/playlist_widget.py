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

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QItemSelectionModel, QMimeData, QModelIndex, Qt, QObject, QAbstractListModel, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QListView,
    QMenu,
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
        self._favorite_paths: set[str] = set()

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

    def set_favorite_paths(self, favorite_paths: set[str]) -> None:
        """Update the favorite-path set used for display decorations."""
        self._favorite_paths = set(favorite_paths)
        if self._songs:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._songs) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole, Qt.ToolTipRole])

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
            prefix = "★ " if song.file_path in self._favorite_paths else ""
            if song.artist:
                return f"{prefix}{song.artist} – {song.title}    [{time_str}]"
            return f"{prefix}{song.title}    [{time_str}]"
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
        return ["application/x-lunaplayer-song-index"]

    def mimeData(self, indexes):
        mime = QMimeData()
        # Encode the row index as a simple integer string.
        if indexes:
            row = indexes[0].row()
            mime.setData("application/x-lunaplayer-song-index",
                         str(row).encode("utf-8"))
        return mime

    def dropMimeData(self, data, action, row, column, parent):
        if action == Qt.IgnoreAction:
            return True
        if not data.hasFormat("application/x-lunaplayer-song-index"):
            return False

        source_row = int(bytes(data.data("application/x-lunaplayer-song-index")).decode("utf-8"))

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
    batch_remove_requested = Signal(list)  # list[int]
    clear_requested = Signal()
    order_changed = Signal(list)  # List[Song]
    info_requested = Signal(int)  # Emitted when user picks "View details"
    favorite_add_requested = Signal(int)
    favorite_remove_requested = Signal(int)

    def __init__(
        self,
        allow_playlist_removal: bool = True,
        show_clear_action: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        self.setWindowTitle("播放列表")
        self._allow_playlist_removal = allow_playlist_removal
        self._show_clear_action = show_clear_action

        # --- Layout ---
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # --- Model ---
        self._model = PlaylistModel(self)
        self._model.order_changed.connect(self._emit_order_changed)

        # --- View ---
        self._view = QListView()
        self._view.setModel(self._model)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
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

    def set_favorite_paths(self, favorite_paths: set[str]) -> None:
        """Refresh the visible favorite markers."""
        self._model.set_favorite_paths(favorite_paths)

    def highlight_row(self, index: int) -> None:
        """Select and scroll to the given row index (clears any multi-selection)."""
        if 0 <= index < self._model.rowCount():
            idx = self._model.index(index, 0)
            sel_model = self._view.selectionModel()
            sel_model.select(idx, QItemSelectionModel.ClearAndSelect)
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

    def selected_indices(self) -> List[int]:
        """Return the row indices of all currently selected items, sorted."""
        return sorted(
            idx.row() for idx in self._view.selectionModel().selectedIndexes()
            if idx.isValid()
        )

    def _show_context_menu(self, pos) -> None:
        """Build and show the right-click context menu.

        If the right-clicked item is already part of the current selection
        the menu operates on **all** selected rows.  Otherwise the clicked
        item is selected exclusively before showing the menu.
        """
        index = self._view.indexAt(pos)

        # When right-clicking on a valid item that is not yet selected,
        # select it exclusively so the menu operates on a predictable set.
        if index.isValid():
            sel_model = self._view.selectionModel()
            if not sel_model.isSelected(index):
                sel_model.select(index,
                                 QItemSelectionModel.ClearAndSelect)

        rows = self.selected_indices()
        if not rows:
            # Right-click on empty area — playlist-level actions only.
            menu = QMenu(self)
            if self._show_clear_action:
                clear_action = QAction("清空播放列表", self)
                clear_action.triggered.connect(self.clear_requested.emit)
                menu.addAction(clear_action)
            if menu.actions():
                menu.exec(self._view.viewport().mapToGlobal(pos))
            return

        menu = self._build_context_menu(rows)
        if not menu.actions():
            return
        menu.exec(self._view.viewport().mapToGlobal(pos))

    def _build_context_menu(self, rows: List[int]) -> QMenu:
        """Create the context menu for the given row indices."""
        menu = QMenu(self)
        first_row = rows[0]
        first_song = self._model.song_at(first_row)
        is_multi = len(rows) > 1

        # Header (single selection only)
        if first_song is not None and not is_multi:
            display = f"{first_song.artist or '未知'} – {first_song.title}"
            header_action = QAction(display, self)
            header_action.setEnabled(False)
            menu.addAction(header_action)
            menu.addSeparator()

        # Play
        play_text = "▶ 播放"
        if is_multi and first_song is not None:
            play_text = f"▶ 播放 ({first_song.artist or '未知'} – {first_song.title})"
        play_action = QAction(play_text, self)
        play_action.triggered.connect(lambda: self.play_requested.emit(first_row))
        menu.addAction(play_action)

        # Favorite toggle (single selection only)
        if not is_multi and first_song is not None and self._can_toggle_favorite(first_song):
            is_fav = first_song.file_path in self._model._favorite_paths
            fav_text = "★ 取消收藏" if is_fav else "☆ 加入收藏"
            fav_action = QAction(fav_text, self)
            if is_fav:
                fav_action.triggered.connect(
                    lambda: self.favorite_remove_requested.emit(first_row))
            else:
                fav_action.triggered.connect(
                    lambda: self.favorite_add_requested.emit(first_row))
            menu.addAction(fav_action)

        # Remove
        if self._allow_playlist_removal:
            if is_multi:
                remove_action = QAction(f"🗑 从列表移除 ({len(rows)} 首)", self)
                remove_action.triggered.connect(
                    lambda: self.batch_remove_requested.emit(rows))
            else:
                remove_action = QAction("🗑 从列表移除", self)
                remove_action.triggered.connect(
                    lambda: self._request_remove(first_row))
            menu.addAction(remove_action)

        # Info (single selection only)
        if not is_multi:
            info_action = QAction("📋 查看详情", self)
            info_action.triggered.connect(lambda: self.info_requested.emit(first_row))
            menu.addAction(info_action)

        # Clear playlist
        if self._show_clear_action:
            if menu.actions():
                menu.addSeparator()
            clear_action = QAction("清空播放列表", self)
            clear_action.triggered.connect(self.clear_requested.emit)
            menu.addAction(clear_action)
        return menu

    @staticmethod
    def _can_toggle_favorite(song: Song) -> bool:
        """Return whether *song* looks like a local-file favorite candidate."""
        file_path = str(song.file_path or "").strip()
        if not file_path or file_path.startswith(("http://", "https://")):
            return False
        return Path(file_path).suffix != ""

    def _request_remove(self, row: int) -> None:
        """Emit remove_requested after internal model removal."""
        self.remove_requested.emit(row)

    def _emit_order_changed(self) -> None:
        """Forward the model's current ordering to external consumers."""
        self.order_changed.emit(self._model.songs())
