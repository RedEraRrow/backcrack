"""Audio CD lookup: the table of contents read from cd-discid, and the
MusicBrainz disc id worked out from it."""
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backcrack import config as cfg, disc

EXAMPLE = (1, 6, 95462, [150, 15363, 32314, 46592, 63414, 80489])   # MusicBrainz's documented CD


def fake_cd_discid(musicbrainz_out: str, freedb_out: str) -> str:
    """A stand-in cd-discid printing `musicbrainz_out` for --musicbrainz, else `freedb_out`."""
    path = os.path.join(tempfile.mkdtemp(), "cd-discid")
    with open(path, "w") as f:
        f.write('#!/bin/sh\nif [ "$1" = "--musicbrainz" ]; then echo "%s"; else echo "%s"; fi\n'
                % (musicbrainz_out, freedb_out))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class DiscIdTest(unittest.TestCase):
    def test_matches_musicbrainz_documentation(self):
        self.assertEqual(disc.musicbrainz_discid(EXAMPLE), "49HHV7Eb8UKF3aQiNmu1GR8vKTY-")


class TocTest(unittest.TestCase):
    def test_exact_toc_from_musicbrainz_mode(self):
        tool = fake_cd_discid("6 150 15363 32314 46592 63414 80489 95462", "")
        with patch.object(cfg, "CD_DISCID", tool):
            self.assertEqual(disc.cd_toc("/dev/disk4"), EXAMPLE)

    def test_older_cd_discid_gives_an_approximate_lead_out(self):
        # No --musicbrainz: freedb's "id count offsets... seconds" (95462 / 75 = 1272 s).
        tool = fake_cd_discid("", "4f07e806 6 150 15363 32314 46592 63414 80489 1272")
        with patch.object(cfg, "CD_DISCID", tool):
            first, last, leadout, offsets = disc.cd_toc("/dev/disk4")
        self.assertEqual((first, last, offsets), (1, 6, EXAMPLE[3]))
        self.assertAlmostEqual(leadout, 95462, delta=75)

    def test_no_cd_discid(self):
        with patch.object(cfg, "CD_DISCID", ""):
            self.assertIsNone(disc.cd_toc("/dev/disk4"))


if __name__ == "__main__":
    unittest.main()
