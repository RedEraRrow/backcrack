"""Find discs sitting in optical drives, of any kind, and tell them
apart. All macOS-specific (diskutil, drutil, mount) - the disc hardware layer
is the one part of this pipeline that doesn't travel to another OS for free.
"""
import hashlib
import re
import subprocess
import time
from pathlib import Path

from backbone.log import log as diag

from backcrack import config as cfg


# Optical drive speeds are quoted in multiples of 1x for that kind of disc.
DVD_1X_BPS = 1_385_000
CD_1X_BPS = 176_400


def raw(dev: str) -> str:
    return "/dev/r" + dev.removeprefix("/dev/")


def cooked(dev: str) -> str:
    return "/dev/" + dev.removeprefix("/dev/").removeprefix("r")


def optical_mounts():
    """Yields (device, mountpoint) for every mounted UDF/ISO9660 volume:
    DVD-Video, Blu-ray, and any other data disc. Audio CDs never mount a
    filesystem, so they never show up here - see audio_discs below.
    """
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
    except subprocess.TimeoutExpired:
        return
    for line in out.splitlines():
        m = re.match(r"^(\S+) on (.+) \((udf|cd9660)[,)]", line)
        if m:
            yield m.group(1), m.group(2)


def audio_discs():
    """Yields device paths for every whole-disk carrying a CD_DA (audio)
    partition. diskutil lists these even though nothing gets mounted.
    """
    try:
        out = subprocess.run(["diskutil", "list"], capture_output=True, text=True, timeout=10).stdout
    except subprocess.TimeoutExpired:
        return
    dev = None
    for line in out.splitlines():
        m = re.match(r"^(/dev/disk\S+)", line)
        if m:
            dev = m.group(1)
        elif "CD_DA" in line and dev:
            yield dev


def pending_discs():
    """Yields (device, kind, label) for every disc worth ripping. kind is
    "video" or "audio". label is the volume name for video. For audio it is
    only the device name: reading the CD's id here, on every poll, would
    disturb a rip already running in that drive, so rip_audio_disc names
    the CD with audio_label() once it holds the drive's lock.
    """
    for dev, mnt in optical_mounts():
        yield dev, "video", Path(mnt).name
    for dev in audio_discs():
        yield dev, "audio", Path(dev).name


def cd_toc(rdev: str):
    """The CD's table of contents as MusicBrainz wants it: (first track, last
    track, lead-out, [track offsets]) in sectors from the disc's start, or
    None. Read from cd-paranoia's track list, which counts from track 1's
    pregap, hence the 150. Audio tracks only, so on an enhanced CD the
    lead-out is the last audio track's end (MusicBrainz's toc lookup still
    finds the near match)."""
    if not cfg.CDPARANOIA:
        return None
    try:
        out = subprocess.run([cfg.CDPARANOIA, "-Q", "-d", rdev], capture_output=True, text=True, timeout=30).stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        diag.warning("cd-paranoia -Q on %s failed: %s", rdev, e)
        return None
    # "  1.    16503 [03:40.03]        0 [00:00.00]    no   no  2": number, length, begin
    tracks = [(int(m[1]), int(m[2]), int(m[3]))
              for m in re.finditer(r"^\s*(\d+)\.\s+(\d+)\s+\[[^]]*\]\s+(\d+)", out, re.M)]
    if not tracks:
        diag.warning("cd-paranoia -Q on %s listed no tracks: %s", rdev, out.strip()[-300:])
        return None
    first, last = tracks[0][0], tracks[-1][0]
    return first, last, tracks[-1][2] + tracks[-1][1] + 150, [begin + 150 for _, _, begin in tracks]


def freedb_id(toc) -> str:
    """The freedb (CDDB) disc id for a toc, as cd-discid printed it."""
    _, _, leadout, offsets = toc
    digits = sum(sum(map(int, str(o // 75))) for o in offsets)
    return f"{(digits % 255) << 24 | (leadout // 75 - offsets[0] // 75) << 8 | len(offsets):08x}"


def musicbrainz_discid(toc) -> str:
    """MusicBrainz's disc id for a (first, last, lead-out, offsets) toc: the
    SHA-1 of the toc in fixed-width hex, base64 with MusicBrainz's alphabet."""
    import base64
    first, last, leadout, offsets = toc
    frames = [leadout] + list(offsets) + [0] * (99 - len(offsets))
    text = f"{first:02X}{last:02X}" + "".join(f"{f:08X}" for f in frames)
    digest = base64.b64encode(hashlib.sha1(text.encode()).digest()).decode()
    return digest.translate(str.maketrans("+/=", "._-"))


def audio_label(toc) -> str:
    """A name for a CD that stays the same whichever drive it is in."""
    return f"AudioCD-{len(toc[3])}tracks-{freedb_id(toc)}"


# Holds audio_bytes() for a rip in progress, in its folder, for watch's
# progress bar: cd-paranoia runs inside the folder rather than naming it, so
# watch can't match the rip to its drive the way it does for MakeMKV.
DISC_BYTES_FILE = ".disc-bytes"


def audio_bytes(toc) -> int:
    """What ripping every track to WAV comes to: 2352 bytes a sector, plus
    each file's 44-byte header."""
    _, _, leadout, offsets = toc
    return (leadout - offsets[0]) * 2352 + 44 * len(offsets)


_DRV_RE = re.compile(r'^DRV:(\d+),2,\d+,\d+,"([^"]*)","([^"]*)","([^"]*)"')


def mkv_drives(timeout: int = 60) -> list:
    """(index, drive name, disc label, raw device) for every drive MakeMKV
    sees with a disc loaded."""
    try:
        out = subprocess.run([cfg.MKVCON, "-r", "--cache=1", "info", "disc:9999"],
                             capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        diag.warning("makemkvcon drive listing failed: %s", e)
        return []
    return [m.groups() for m in map(_DRV_RE.match, out.splitlines()) if m]


# MakeMKV drive indices move between insertions, so never cache one.
# Enumeration touches every drive, so it's serialised behind a lock to avoid
# disturbing a rip already running in the other drive. video_ts mode only.
def mkv_index_for(dev: str):
    want = raw(dev)
    lockdir = cfg.STATE / "enum.lock"
    tries = 0
    while True:
        try:
            lockdir.mkdir()
            break
        except FileExistsError:
            tries += 1
            if tries > 90:
                break
            time.sleep(2)
    try:
        drives = mkv_drives()
    finally:
        try:
            lockdir.rmdir()
        except OSError:
            pass
    return next((idx for idx, _, _, device in drives if device == want), None)
