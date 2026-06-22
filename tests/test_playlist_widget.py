"""Unit tests for PlaylistWidget reorder signaling."""

from __future__ import annotations

import unittest

from app.ui.widgets.playlist_widget import PlaylistWidget
from tests.test_base import SAMPLE_SONGS, SignalCapture, ensure_qapp


class TestPlaylistWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def test_reorder_emits_new_song_order(self) -> None:
        widget = PlaylistWidget()
        widget.load_songs(SAMPLE_SONGS)

        with SignalCapture(widget.order_changed) as captured:
            widget.model.move_row(0, 3)
            widget.model.order_changed.emit()

        self.assertEqual(len(captured), 1)
        reordered = captured[0][0]
        self.assertEqual([song.title for song in reordered], ["Song B", "Song C", "Song A", "Song D"])


if __name__ == "__main__":
    unittest.main()
