"""Audio CD lookup: the table of contents read from cd-paranoia, and the
disc ids worked out from it."""
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backcrack import config as cfg, disc

EXAMPLE = (1, 6, 95462, [150, 15363, 32314, 46592, 63414, 80489])   # MusicBrainz's documented CD

# cd-paranoia -Q for EXAMPLE: begins and lengths count from track 1's pregap.
LISTING = """\
Table of contents (audio tracks only):
track        length               begin        copy pre ch
===========================================================
  1.    15213 [03:22.63]        0 [00:00.00]    no   no  2
  2.    16951 [03:46.01]    15213 [03:22.63]    no   no  2
  3.    14278 [03:10.28]    32164 [07:08.64]    no   no  2
  4.    16822 [03:44.22]    46442 [10:19.17]    no   no  2
  5.    17075 [03:47.50]    63264 [14:03.39]    no   no  2
  6.    14973 [03:19.48]    80339 [17:51.14]    no   no  2
TOTAL   95312 [21:10.62]    (audio only)
"""


def fake_paranoia(listing: str) -> str:
    """A stand-in cd-paranoia printing `listing` to stderr, as the real one does."""
    folder = tempfile.mkdtemp()
    with open(os.path.join(folder, "listing"), "w") as f:
        f.write(listing)
    path = os.path.join(folder, "cd-paranoia")
    with open(path, "w") as f:
        f.write(f'#!/bin/sh\ncat "{folder}/listing" >&2\n')
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class DiscIdTest(unittest.TestCase):
    def test_matches_musicbrainz_documentation(self):
        self.assertEqual(disc.musicbrainz_discid(EXAMPLE), "49HHV7Eb8UKF3aQiNmu1GR8vKTY-")

    def test_freedb_id(self):
        # Digit sums of 2, 204, 430, 621, 845, 1073 s = 52 (0x34); 1272 - 2 = 1270 s (0x4f6); 6 tracks.
        self.assertEqual(disc.freedb_id(EXAMPLE), "3404f606")
        self.assertEqual(disc.audio_label(EXAMPLE), "AudioCD-6tracks-3404f606")

    def test_audio_bytes_is_the_wav_rip_size(self):
        # 95312 sectors (cd-paranoia's TOTAL) of 2352 bytes, and six WAV headers.
        self.assertEqual(disc.audio_bytes(EXAMPLE), 95312 * 2352 + 6 * 44)


class TocTest(unittest.TestCase):
    def test_toc_from_track_listing(self):
        with patch.object(cfg, "CDPARANOIA", fake_paranoia(LISTING)):
            self.assertEqual(disc.cd_toc("/dev/rdisk4"), EXAMPLE)

    def test_unreadable_disc(self):
        with patch.object(cfg, "CDPARANOIA", fake_paranoia("Unable to open disc.\n")):
            self.assertIsNone(disc.cd_toc("/dev/rdisk4"))

    def test_no_cd_paranoia(self):
        with patch.object(cfg, "CDPARANOIA", ""):
            self.assertIsNone(disc.cd_toc("/dev/rdisk4"))


if __name__ == "__main__":
    unittest.main()
