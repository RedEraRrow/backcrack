"""disc.py - find discs sitting in optical drives, of any kind, and tell them
apart. All macOS-specific (diskutil, drutil, mount) - the disc hardware layer
is the one part of this pipeline that doesn't travel to another OS for free.
"""
import re
import subprocess
import time
from pathlib import Path

from . import config as cfg


def raw(dev: str) -> str:
    return "/dev/r" + dev.removeprefix("/dev/")


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
    "video" or "audio". label is the volume name for video; audio CDs
    rarely carry a useful one, so label is the device's disk identifier
    there - auto_audio_tokens (pattern.py) is what actually identifies it.
    """
    for dev, mnt in optical_mounts():
        yield dev, "video", Path(mnt).name
    for dev in audio_discs():
        yield dev, "audio", Path(dev).name


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
        out = subprocess.run(
            [cfg.MKVCON, "-r", "--cache=1", "info", "disc:9999"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    finally:
        try:
            lockdir.rmdir()
        except OSError:
            pass
    for line in out.splitlines():
        if not line.startswith("DRV:"):
            continue
        parts = line[4:].split(",")
        if len(parts) >= 6 and parts[1] == "2" and parts[-1].strip('"') == want:
            return parts[0]
    return None
