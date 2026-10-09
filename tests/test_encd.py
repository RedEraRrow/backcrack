"""encd keeps going past a job that fails, and a job that didn't fully encode
is set aside to retry, never marked done."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backcrack import config as cfg, encd, encode

SCAN = "JSON Title Set: " + json.dumps({"TitleList": [{"Index": 1, "Duration": {"Minutes": 25}}]})


def fake_handbrake() -> str:
    """A HandBrakeCLI that scans every file as a 25-minute title and encodes
    any whose name doesn't have "bad" in it."""
    path = Path(tempfile.mkdtemp()) / "HandBrakeCLI"
    path.write_text(f"""#!/bin/sh
case "$*" in *--scan*) echo '{SCAN}'; exit 0;; esac
while [ "$1" != "-o" ]; do shift; done
case "$2" in *bad*) exit 1;; esac
echo encoded > "$2"
""")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.before = (cfg.LIBRARY, cfg.HBCLI, cfg.RIP_MODE)
        cfg.use_library(Path(tempfile.mkdtemp()))
        cfg.make_library_dirs()
        cfg.HBCLI, cfg.RIP_MODE = fake_handbrake(), "titles"
        self.pushes = []
        self.patches = [patch.object(m, "notify", lambda *a, **k: self.pushes.append(a)) for m in (encd, encode)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        cfg.use_library(self.before[0])
        cfg.HBCLI, cfg.RIP_MODE = self.before[1:]

    def queue(self, label, *titles):
        dest = cfg.LIBRARY / label
        (dest / "source").mkdir(parents=True)
        for t in titles:
            (dest / "source" / t).write_bytes(b"rip")
        (cfg.QUEUE / label).write_text(f"label={label}\ndest={dest}\nkind=video\n")
        return dest

    def test_all_encoded_is_done(self):
        dest = self.queue("D1", "a.mkv", "b.mkv")
        self.assertEqual(encd.run_queue(), 1)
        self.assertTrue((dest / "encoded" / "a.mkv").exists() and (dest / "encoded" / "b.mkv").exists())
        self.assertTrue((cfg.ENCDONE / "D1").exists())
        self.assertFalse((cfg.QUEUE / "D1").exists())

    def test_a_failed_encode_sets_the_job_aside_and_a_retry_finishes_it(self):
        dest = self.queue("D2", "good.mkv", "bad.mkv")
        encd.run_queue()
        self.assertTrue((dest / "encoded" / "good.mkv").exists())
        self.assertFalse((dest / "encoded" / "bad.part.mkv").exists())
        self.assertFalse((cfg.ENCDONE / "D2").exists())
        aside = cfg.STATE / "failed-D2"
        self.assertTrue(aside.exists())
        self.assertIn("1 encodes failed", self.pushes[-1][0])

        (dest / "source" / "bad.mkv").rename(dest / "source" / "fixed.mkv")
        aside.rename(cfg.QUEUE / "D2")
        encd.run_queue()
        self.assertTrue((cfg.ENCDONE / "D2").exists())

    def test_a_job_that_raises_doesnt_stop_the_rest(self):
        self.queue("A", "a.mkv")
        self.queue("B", "b.mkv")
        real = encode.process_video_job

        def flaky(label, dest):
            if label == "A":
                raise PermissionError("no")
            return real(label, dest)

        with patch.object(encode, "process_video_job", flaky):
            encd.run_queue()
        self.assertTrue((cfg.STATE / "failed-A").exists())
        self.assertTrue((cfg.ENCDONE / "B").exists())

    def test_no_handbrake_keeps_the_job(self):
        self.queue("C", "a.mkv")
        cfg.HBCLI = "/nonexistent/HandBrakeCLI"
        encd.run_queue()
        self.assertTrue((cfg.STATE / "failed-C").exists())
        self.assertFalse((cfg.ENCDONE / "C").exists())
        self.assertIn("HandBrakeCLI not found", cfg.ENCLOG.read_text())


if __name__ == "__main__":
    unittest.main()
