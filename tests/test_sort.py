"""sortd files a disc out of UNSORTED/ only when every guard says nothing is
using it, and a merge never overwrites what the destination already has."""
import io
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backcrack import config as cfg, sort

LABEL = "MYDISC"


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.before = (cfg.LIBRARY, cfg.LABELS_MAP, sort.SORTLOG)
        self.patches = [
            patch.object(sort, "notify", lambda *a, **k: None),
            patch.object(sort, "ps_listing", lambda: self.ps),
            patch.object(sort, "_files_open", lambda d: self.open_files),
        ]
        for p in self.patches:
            p.start()
        self.fresh()

    def fresh(self) -> None:
        """An empty library, with labels.map resolving LABEL to Season 2/Disc 3."""
        cfg.use_library(Path(tempfile.mkdtemp()))
        cfg.make_library_dirs()
        cfg.LABELS_MAP = cfg.STATE / "labels.map"
        cfg.LABELS_MAP.write_text(f"{LABEL}|season=2,disc=3\n")
        sort.SORTLOG = cfg.STATE / "sort.log"
        self.ps, self.open_files = "", False
        self.src = cfg.LIBRARY / "UNSORTED" / LABEL
        self.target = cfg.LIBRARY / "Season 2" / "Disc 3"

    def tearDown(self):
        for p in self.patches:
            p.stop()
        cfg.use_library(self.before[0])
        cfg.LABELS_MAP, sort.SORTLOG = self.before[1:]

    def disc(self, root: Path, files: dict, age: int = 600) -> None:
        """Files under `root`, last changed `age` seconds ago."""
        for rel, data in files.items():
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(data)
            os.utime(f, (time.time() - age, time.time() - age))

    def test_moves_a_resolved_disc_and_repoints_its_job(self):
        self.disc(self.src, {"source/t1.mkv": "rip"})
        (cfg.QUEUE / LABEL).write_text(f"label={LABEL}\ndest={self.src}\nkind=video\n")
        sort.reconcile()
        self.assertEqual((self.target / "source" / "t1.mkv").read_text(), "rip")
        self.assertFalse((cfg.LIBRARY / "UNSORTED").exists())
        self.assertIn(f"dest={self.target}\n", (cfg.QUEUE / LABEL).read_text())

    def test_each_guard_on_its_own_defers_the_move(self):
        guards = {
            "a rip is in progress": lambda: (cfg.STATE / "lock-disk4").touch(),
            "files changed in the last": lambda: self.disc(self.src, {"source/new.mkv": "x"}, age=5),
            "a ripper/encoder names this path": lambda: setattr(self, "ps", f"1 HandBrakeCLI -i {self.src}/source/t1.mkv"),
            "open file handles inside": lambda: setattr(self, "open_files", True),
        }
        for why, arm in guards.items():
            with self.subTest(why):
                self.fresh()
                self.disc(self.src, {"source/t1.mkv": "rip"})
                arm()
                sort.reconcile()
                self.assertTrue((self.src / "source" / "t1.mkv").exists())
                self.assertFalse(self.target.exists())
                self.assertIn(f"deferring: {why}", sort.SORTLOG.read_text())

    def test_unresolved_disc_stays(self):
        other = cfg.LIBRARY / "UNSORTED" / "NOLABEL"
        self.disc(other, {"source/t1.mkv": "rip"})
        sort.reconcile()
        self.assertTrue((other / "source" / "t1.mkv").exists())

    def test_dry_run_changes_nothing(self):
        self.disc(self.src, {"source/t1.mkv": "rip"})
        with redirect_stdout(io.StringIO()) as out:
            sort.reconcile(dry_run=True)
        self.assertIn("WOULD MOVE", out.getvalue())
        self.assertTrue((self.src / "source" / "t1.mkv").exists())
        self.assertFalse(self.target.exists())

    def test_merge_keeps_what_the_target_has(self):
        self.disc(self.target, {"encoded/t1.mkv": "theirs"})
        self.disc(self.src, {"source/t2.mkv": "rip", "encoded/t1.mkv": "ours"})
        sort.reconcile()
        self.assertEqual((self.target / "encoded" / "t1.mkv").read_text(), "theirs")
        self.assertEqual((self.target / "source" / "t2.mkv").read_text(), "rip")
        self.assertEqual((self.src / "encoded" / "t1.mkv").read_text(), "ours")   # left for a human
        stuck = cfg.STATE / f"mergestuck-{LABEL}"
        self.assertTrue(stuck.exists())
        log = sort.SORTLOG.read_text()
        sort.reconcile()
        self.assertEqual(sort.SORTLOG.read_text(), log)                      # said once, not every pass

    def test_clean_merge_folds_the_disc_in(self):
        self.disc(self.target, {"encoded/t1.mkv": "done"})
        self.disc(self.src, {"source/t1.mkv": "rip", ".kind": "video\n"})
        (cfg.QUEUE / LABEL).write_text(f"label={LABEL}\ndest={self.src}\nkind=video\n")
        sort.reconcile()
        self.assertEqual((self.target / "source" / "t1.mkv").read_text(), "rip")
        self.assertFalse(self.src.exists())
        self.assertIn(f"dest={self.target}\n", (cfg.QUEUE / LABEL).read_text())


if __name__ == "__main__":
    unittest.main()
