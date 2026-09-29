"""Find discs sitting in optical drives, of any kind, and tell them
apart. All macOS-specific (diskutil, drutil, mount) - the disc hardware layer
is the one part of this pipeline that doesn't travel to another OS for free.
"""
import hashlib
import re
import subprocess
import time
from pathlib import Path

from backcrack import config as cfg


# Optical drive speeds are quoted in multiples of DVD 1x.
DVD_1X_BPS = 1_385_000


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


def cd_discid(rdev: str) -> list:
    """cd-discid's fields for the CD in `rdev` (freedb id, track count,
    offsets..., seconds), or [] if it isn't installed or can't read it."""
    if not cfg.CD_DISCID:
        return []
    try:
        return subprocess.run([cfg.CD_DISCID, rdev], capture_output=True, text=True, timeout=15).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        return []


def cd_toc(rdev: str):
    """The CD's table of contents as MusicBrainz wants it: (first track, last
    track, lead-out, [track offsets]) in sectors, or None. Uses cd-discid's
    exact --musicbrainz output; an older cd-discid only gives the length in
    whole seconds, so the lead-out is then approximate (MusicBrainz's toc
    lookup still finds near matches)."""
    if not cfg.CD_DISCID:
        return None
    try:
        out = subprocess.run([cfg.CD_DISCID, "--musicbrainz", rdev], capture_output=True, text=True, timeout=15).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        out = []
    nums = [int(x) for x in out if x.isdigit()]
    if len(nums) >= 3 and nums[0] == len(nums) - 2:          # count, offsets..., lead-out
        return 1, nums[0], nums[-1], nums[1:-1]
    fields = cd_discid(rdev)                                 # freedb: id, count, offsets..., seconds
    if len(fields) < 3 or not fields[1].isdigit():
        return None
    n = int(fields[1])
    try:
        offsets = [int(x) for x in fields[2:2 + n]]
        return 1, n, int(fields[2 + n]) * 75, offsets
    except (ValueError, IndexError):
        return None


def musicbrainz_discid(toc) -> str:
    """MusicBrainz's disc id for a (first, last, lead-out, offsets) toc: the
    SHA-1 of the toc in fixed-width hex, base64 with MusicBrainz's alphabet."""
    import base64
    first, last, leadout, offsets = toc
    frames = [leadout] + list(offsets) + [0] * (99 - len(offsets))
    text = f"{first:02X}{last:02X}" + "".join(f"{f:08X}" for f in frames)
    digest = base64.b64encode(hashlib.sha1(text.encode()).digest()).decode()
    return digest.translate(str.maketrans("+/=", "._-"))


def audio_label(rdev: str):
    """A name for the CD in `rdev` that stays the same whichever drive it is
    in: "AudioCD-<n>tracks-<id>". The id is cd-discid's, or a hash of
    cdparanoia's track table when cd-discid isn't installed. None if
    neither can read the disc.
    """
    fields = cd_discid(rdev)
    if len(fields) >= 2 and fields[1].isdigit():
        return f"AudioCD-{fields[1]}tracks-{fields[0]}"
    if not cfg.CDPARANOIA:
        return None
    try:
        toc = subprocess.run([cfg.CDPARANOIA, "-Q", "-d", rdev], capture_output=True, text=True, timeout=30).stderr
    except (OSError, subprocess.TimeoutExpired):
        return None
    tracks = [line.split() for line in toc.splitlines() if re.match(r"^\s*\d+\.\s+\d+", line)]
    if not tracks:
        return None
    digest = hashlib.sha1(" ".join(t[1] for t in tracks).encode()).hexdigest()[:8]
    return f"AudioCD-{len(tracks)}tracks-{digest}"


_DRV_RE = re.compile(r'^DRV:(\d+),2,\d+,\d+,"([^"]*)","([^"]*)","([^"]*)"')


def mkv_drives(timeout: int = 60) -> list:
    """(index, drive name, disc label, raw device) for every drive MakeMKV
    sees with a disc loaded."""
    try:
        out = subprocess.run([cfg.MKVCON, "-r", "--cache=1", "info", "disc:9999"],
                             capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
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
