"""namer renames a finished season and deletes each disc's lossless rip only
once that disc's episodes are all in place; anything doubtful leaves the
season untouched."""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backcrack import config as cfg, namer

TITLES = ["Pilot", "Paternity", "Occam's Razor"]


class RenameSeasonTest(unittest.TestCase):
    def setUp(self):
        self.before = cfg.LIBRARY
        self.fresh()

    def fresh(self) -> None:
        """A finished two-disc season in an empty library."""
        cfg.use_library(Path(tempfile.mkdtemp()))
        self.season = cfg.LIBRARY / "Season 1"
        self.make({
            "Disc 1/encoded/t00.mkv": "e1", "Disc 1/encoded/t00-thumb.jpg": "j1", "Disc 1/encoded/t01.mkv": "e2",
            "Disc 1/extras/f.mkv": "x1", "Disc 1/source/t00.mkv": "rip",
            "Disc 2/encoded/t00.mkv": "e3", "Disc 2/extras/f.mkv": "x2", "Disc 2/source/t00.mkv": "rip",
        })

    def tearDown(self):
        cfg.use_library(self.before)

    def make(self, files: dict) -> None:
        for rel, data in files.items():
            f = self.season / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(data)

    def tree(self) -> dict:
        return {str(p.relative_to(self.season)): p.read_text() for p in sorted(self.season.rglob("*")) if p.is_file()}

    def rename(self, titles=TITLES) -> str:
        with redirect_stdout(io.StringIO()) as out:
            namer.rename_season(self.season, titles)
        return out.getvalue()

    def test_finished_season(self):
        self.rename()
        self.assertEqual(self.tree(), {
            "S01E01 Pilot.mkv": "e1", "S01E01 Pilot-thumb.jpg": "j1", "S01E02 Paternity.mkv": "e2",
            "S01E03 Occam's Razor.mkv": "e3",
            "featurettes/Disc2_f.mkv": "x2", "featurettes/f.mkv": "x1",
        })

    def test_doubtful_seasons_are_left_alone(self):
        def half_encoded():
            (self.season / "Disc 2/encoded/t00.mkv").rename(self.season / "Disc 2/encoded/t00.part.mkv")
            return TITLES

        def emptied():
            self.make({"Disc 2/encoded/t00.mkv": ""})
            return TITLES

        cases = {                       # (what's wrong, what namer says)
            "don't match": lambda: TITLES[:2],
            "encode in progress": half_encoded,
            "is empty": emptied,
        }
        for says, arrange in cases.items():
            with self.subTest(says):
                self.fresh()
                titles = arrange()
                before = self.tree()
                self.assertIn(says, self.rename(titles))
                self.assertEqual(self.tree(), before)

    def test_a_disc_whose_episodes_didnt_all_move_keeps_its_rip(self):
        self.make({"S01E03 Occam's Razor.mkv": "already here"})
        out = self.rename()
        tree = self.tree()
        self.assertNotIn("Disc 1/source/t00.mkv", tree)         # its episodes moved: rip gone
        self.assertEqual(tree["Disc 2/source/t00.mkv"], "rip")   # collided: rip kept
        self.assertEqual(tree["Disc 2/encoded/t00.mkv"], "e3")
        self.assertIn("keeping", out)

    def test_titles_are_made_safe_for_file_names(self):
        self.rename(["A/B", "C: D", "E"])
        self.assertIn("S01E01 A-B.mkv", self.tree())
        self.assertIn("S01E02 C - D.mkv", self.tree())


if __name__ == "__main__":
    unittest.main()
