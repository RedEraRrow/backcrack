"""The Settings screen's logic: what it refuses, and that a value saved is the
value used, in this watch and after a restart."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backcrack import config as cfg, settings


class ClassesTest(unittest.TestCase):
    def test_good_spec(self):
        self.assertIsNone(settings.classes_problem("extra:180-1199:extras:22,main:1200-10800:encoded:20"))

    def test_bad_specs_are_refused(self):
        for spec in ("", "main", "main:20-x:encoded:20", "main:900-100:encoded:20", "main:1-2:encoded:20x"):
            self.assertIsNotNone(settings.classes_problem(spec), spec)

    def test_saved_value_reads_back(self):
        spec = settings.saved_value("DURATION_CLASSES")
        self.assertEqual(cfg._parse_duration_classes(spec), cfg.DURATION_CLASSES)


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.file = Path(tempfile.mkdtemp()) / "settings.env"
        self.patches = [patch.object(cfg, "SETTINGS_FILE", self.file),
                        patch.dict(cfg._FILE_SETTINGS, clear=True)]
        for p in self.patches:
            p.start()
        self.before = {n: getattr(cfg, n) for n in ("ENCODE_JOBS", "NOTIFY_ENCODES", "DEINTERLACE_ARGS")}

    def tearDown(self):
        for n, v in self.before.items():
            setattr(cfg, n, v)
        for p in self.patches:
            p.stop()

    def test_used_at_once_and_saved(self):
        settings.apply("ENCODE_JOBS", "3")
        settings.apply("NOTIFY_ENCODES", "1")
        settings.apply("DEINTERLACE_ARGS", "--comb-detect --decomb")
        self.assertEqual((cfg.ENCODE_JOBS, cfg.NOTIFY_ENCODES, cfg.DEINTERLACE_ARGS),
                         (3, True, ["--comb-detect", "--decomb"]))
        self.assertEqual(self.file.read_text().splitlines(),
                         ["ENCODE_JOBS=3", "NOTIFY_ENCODES=1", "DEINTERLACE_ARGS=--comb-detect --decomb"])

    def test_every_row_has_a_value_to_show(self):
        for name in settings._ROWS:
            self.assertTrue(settings.shown_value(name), name)


if __name__ == "__main__":
    unittest.main()
