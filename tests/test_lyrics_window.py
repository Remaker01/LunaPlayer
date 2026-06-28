"""Unit tests for LyricsWindow layout and wrapping behavior."""

from __future__ import annotations

import unittest

from app.ui.lyrics_window import LyricsWindow
from tests.test_base import ensure_qapp


class TestLyricsWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def setUp(self) -> None:
        self.window = LyricsWindow()

    def test_long_current_line_wraps_instead_of_staying_single_line(self) -> None:
        long_text = (
            "Common stalls that 'cause 'em all To you they crawl body sprawl "
            "Smokin' pall malls Close call stand tall"
        )
        self.window.load_lyrics([(0, long_text)])
        self.window.sync_position(0)

        block = self.window._build_visible_blocks()[0]
        single_line_height = self.window._measure_text_height(
            "Stone walls",
            self.window._font_for_highlight_strength(1.0),
        )

        self.assertGreater(block.rect.height(), single_line_height)

    def test_visible_blocks_do_not_overlap_when_current_line_is_emphasized(self) -> None:
        self.window.load_lyrics([
            (0, "Cape walls"),
            (1000, "Stone walls Bar brawls"),
            (2000, "Common stalls that 'cause 'em all To you they crawl body sprawl"),
            (3000, "Smokin' pall malls"),
        ])
        self.window.sync_position(1000)

        blocks = self.window._build_visible_blocks()
        for previous, current in zip(blocks, blocks[1:]):
            self.assertLessEqual(previous.rect.bottom(), current.rect.top())

    def test_sync_position_updates_current_line_and_visible_blocks(self) -> None:
        self.window.load_lyrics([
            (0, "One"),
            (1000, "Two"),
            (2000, "Three"),
        ])

        self.window.sync_position(1500)

        self.assertEqual(self.window._current_line_index, 1)
        self.assertIn(1, [block.index for block in self.window._build_visible_blocks()])

    def test_empty_lyrics_produce_no_visible_blocks(self) -> None:
        self.window.load_lyrics([])

        self.assertEqual(self.window._build_visible_blocks(), [])

    def test_larger_font_remeasures_block_height(self) -> None:
        long_text = "Stone walls Bar brawls Common stalls that 'cause 'em all"
        self.window.load_lyrics([(0, long_text)])
        self.window.sync_position(0)
        before = self.window._build_visible_blocks()[0].rect.height()

        self.window.set_lyrics_font_size(38)
        after = self.window._build_visible_blocks()[0].rect.height()

        self.assertGreater(after, before)


if __name__ == "__main__":
    unittest.main()
