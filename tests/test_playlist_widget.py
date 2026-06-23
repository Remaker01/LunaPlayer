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

    def test_favorite_marker_is_shown_in_display_text(self) -> None:
        widget = PlaylistWidget()
        widget.load_songs(SAMPLE_SONGS)
        widget.set_favorite_paths({SAMPLE_SONGS[0].file_path})

        display = widget.model.data(widget.model.index(0, 0))

        self.assertTrue(display.startswith("★ "))

    def test_context_menu_toggles_favorite_action_text(self) -> None:
        widget = PlaylistWidget()
        widget.load_songs(SAMPLE_SONGS)
        index = widget.model.index(0, 0)

        add_menu = widget._build_context_menu(index)
        add_texts = [action.text() for action in add_menu.actions() if action.text()]
        self.assertIn("☆ 加入收藏", add_texts)
        self.assertNotIn("★ 取消收藏", add_texts)

        widget.set_favorite_paths({SAMPLE_SONGS[0].file_path})
        remove_menu = widget._build_context_menu(index)
        remove_texts = [action.text() for action in remove_menu.actions() if action.text()]
        self.assertIn("★ 取消收藏", remove_texts)

    def test_context_menu_hides_favorite_action_for_remote_song(self) -> None:
        widget = PlaylistWidget()
        remote_song = list(SAMPLE_SONGS)
        remote_song[0] = type(SAMPLE_SONGS[0])(
            title="Remote",
            artist="Artist",
            duration=120.0,
            file_path="https://example.com/test.mp3",
            file_format="mp3",
        )
        widget.load_songs(remote_song)

        menu = widget._build_context_menu(widget.model.index(0, 0))
        texts = [action.text() for action in menu.actions() if action.text()]

        self.assertNotIn("☆ 加入收藏", texts)
        self.assertNotIn("★ 取消收藏", texts)


if __name__ == "__main__":
    unittest.main()
