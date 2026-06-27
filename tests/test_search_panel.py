"""Unit tests for SearchPanel result management."""

from __future__ import annotations

import unittest

from app.models.song import Song
from app.ui.widgets.search_panel import SearchPanel
from tests.test_base import ensure_qapp


class TestSearchPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = ensure_qapp()

    def test_clear_results_button_resets_result_state(self) -> None:
        panel = SearchPanel()
        songs = [
            Song(title="One", artist="Artist", duration=10, file_path="https://example.com/1.mp3"),
            Song(title="Two", artist="Artist", duration=12, file_path="https://example.com/2.mp3"),
        ]

        panel.display_results(songs)

        self.assertEqual(panel._results_list.count(), 2)
        self.assertTrue(panel._clear_btn.isEnabled())

        panel._clear_btn.click()

        self.assertEqual(panel._results, [])
        self.assertEqual(panel._results_list.count(), 0)
        self.assertEqual(panel._status_label.text(), "输入关键词，按回车搜索")
        self.assertFalse(panel._add_btn.isEnabled())
        self.assertFalse(panel._download_btn.isEnabled())
        self.assertFalse(panel._clear_btn.isEnabled())


if __name__ == "__main__":
    unittest.main()
