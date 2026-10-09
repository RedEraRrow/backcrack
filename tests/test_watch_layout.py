"""watch's frame: its boxes share the rows by priority, and nothing it draws
runs past the window, however small."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backbone import ui
from backcrack import config as cfg, watch


def box(rank, title, n, keep_end=False):
    return (rank, title, "", [f"{title} {i}" for i in range(n)], keep_end)


def titles(lines):
    return [ln.split("─ ")[1].split(" ")[0] for ln in map(ui.strip_ansi, lines) if "╭" in ln]


class StackTest(unittest.TestCase):
    def test_all_fit_in_order(self):
        out = watch._stack([box(1, "A", 2), box(0, "B", 1)], 40, 30)
        self.assertEqual(titles(out), ["A", "B"])
        self.assertEqual(len(out), 4 + 3)

    def test_lowest_rank_keeps_its_box(self):
        out = watch._stack([box(1, "A", 2), box(0, "B", 1)], 5, 30)
        self.assertEqual(titles(out), ["B"])           # A needs 4 of the 2 left: no box, no content

    def test_short_box_keeps_first_or_last_lines(self):
        first = watch._stack([box(0, "A", 5)], 4, 30)
        last = watch._stack([box(0, "A", 5, keep_end=True)], 4, 30)
        self.assertIn("A 0", ui.strip_ansi(first[1]))
        self.assertIn("A 4", ui.strip_ansi(last[-2]))

    def test_no_room_no_boxes(self):
        self.assertEqual(watch._stack([box(0, "A", 1)], 2, 30), [])


class FrameTest(unittest.TestCase):
    """A rip and an encode under way, drawn at a range of window sizes."""

    def setUp(self):
        self.lib = Path(tempfile.mkdtemp())
        dest = self.lib / "Some Rather Long Show Name" / "Season 1" / "Disc 1"
        (dest / "source").mkdir(parents=True)
        (dest / "source" / "title_t00.mkv").write_bytes(b"\0" * 300_000)
        self.ps = (f"1 makemkvcon --noscan mkv dev:/dev/rdisk4 all {dest}/source\n"
                   f"2 HandBrakeCLI -i x -o {dest}/encoded/A very long episode title.part.mkv --format av_mkv\n")
        cfg.use_library(self.lib)
        watch._growth.clear()

    def frame(self, cols, rows):
        with patch.object(ui, "get_terminal_size", return_value=(cols, rows)), \
             patch.object(ui, "get_terminal_width", return_value=cols), \
             patch.object(ui, "get_terminal_height", return_value=rows), \
             patch.object(watch, "ps_listing", return_value=self.ps), \
             patch.object(watch, "disc_kb", return_value=7_000_000), \
             patch.object(watch, "TOTAL_DISCS", 12):
            return watch.render()

    def test_never_wider_than_the_window(self):
        for cols in (20, 34, 50, 80, 140):
            for rows in (10, 14, 30):
                for line in self.frame(cols, rows):
                    self.assertLessEqual(ui.visual_len(line), cols - ui.MARGIN_H, (cols, rows, line))

    def test_rip_and_encode_shown(self):
        text = "\n".join(map(ui.strip_ansi, self.frame(100, 30)))
        self.assertIn("disk4", text)
        self.assertIn("A very long episode title.mkv", text)


if __name__ == "__main__":
    unittest.main()
