"""Dedicated window for browsing and playing favorites."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QVBoxLayout, QWidget

from app.models.song import Song
from app.ui.widgets.playlist_widget import PlaylistWidget


class FavoritesWindow(QWidget):
    """Non-modal window that hosts the persistent favorites list."""

    play_requested = Signal(int)
    remove_favorite_requested = Signal(int)
    order_changed = Signal(list)  # List[Song]

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("收藏")
        self.resize(720, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._playlist_widget = PlaylistWidget(
            allow_playlist_removal=False,
            show_clear_action=False,
            parent=self,
        )
        self._playlist_widget.setWindowTitle("收藏")
        self._playlist_widget.play_requested.connect(self.play_requested.emit)
        self._playlist_widget.favorite_remove_requested.connect(self.remove_favorite_requested.emit)
        self._playlist_widget.order_changed.connect(self.order_changed.emit)
        layout.addWidget(self._playlist_widget)

    @Slot(list)
    def load_songs(self, songs: list[Song]) -> None:
        """Refresh the favorites list while preserving selection when possible."""
        selected_path = ""
        current_index = self._playlist_widget.current_song_index()
        model = self._playlist_widget.model
        if 0 <= current_index < model.rowCount():
            current_song = model.song_at(current_index)
            if current_song is not None:
                selected_path = current_song.file_path

        self._playlist_widget.load_songs(songs)
        self._playlist_widget.set_favorite_paths({song.file_path for song in songs})

        if selected_path:
            for index, song in enumerate(songs):
                if song.file_path == selected_path:
                    self._playlist_widget.highlight_row(index)
                    break

    def show_and_raise(self) -> None:
        """Show the window and bring it to the foreground."""
        self.show()
        self.raise_()
        self.activateWindow()
