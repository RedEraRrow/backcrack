"""config.py - settings for the backcrack rip pipeline. Edit this, not the daemons.

Every value reads its environment variable first, so a one-off override never
needs editing this file: `RIP_MODE=video_ts python3 ripd.py`.
"""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class DurationClass:
    name: str
    min_s: int
    max_s: int
    folder: str      # output subfolder under a disc's dest, e.g. "encoded"
    quality: str      # HandBrake --quality (RF) for titles in this class
    dedup: bool = False   # drop a second title with this exact duration -
                          # discs sometimes list the main feature twice; two
                          # short extras sharing a duration is coincidence


def _parse_duration_classes(spec: str) -> list:
    """name:min-max:folder:quality[:dedup], comma-separated. First match
    wins, so keep ranges non-overlapping. A duration matching none of them
    is not encoded at all.
    """
    classes = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, rng, folder, quality, *rest = part.split(":")
        lo, hi = rng.split("-")
        classes.append(DurationClass(name, int(lo), int(hi), folder, quality, rest == ["dedup"]))
    return classes


# ---- ntfy --------------------------------------------------------------
# Anyone who knows a topic name can read it on the public ntfy.sh server,
# so generate your own - don't leave this blank in production use.
NTFY_TOPIC = _env("NTFY_TOPIC", "")
NTFY_SERVER = _env("NTFY_SERVER", "https://ntfy.sh")

# Where finished discs land. The layout beneath this is controlled by
# PATTERN_VIDEO / PATTERN_AUDIO below, not fixed here - point LIBRARY at
# whatever you're ripping this run (one show, one CD shelf, a mixed pile).
LIBRARY = Path(_env("LIBRARY", str(Path.home() / "House (2002)")))

MKVCON = _env("MKVCON", "/Applications/MakeMKV.app/Contents/MacOS/makemkvcon")
HBCLI = _env("HBCLI", shutil.which("HandBrakeCLI") or "/opt/homebrew/bin/HandBrakeCLI")
CDPARANOIA = _env("CDPARANOIA", shutil.which("cdparanoia") or "")
CD_DISCID = _env("CD_DISCID", shutil.which("cd-discid") or "")
FLAC = _env("FLAC", shutil.which("flac") or "")

# ---- rip mode (video discs: DVD, Blu-ray) -------------------------------
# titles    MakeMKV rips each title to its own lossless MKV via dev: - gets
#           DIRECT disc access mode, needs no drive index, and MakeMKV does
#           the title detection. Captures the main feature(s) and extras
#           alike. Works for both DVD and Blu-ray.
# video_ts  Full decrypted disc backup, menus included. DVD only - Blu-ray
#           has no VIDEO_TS folder. Needs a MakeMKV drive index, resolved at
#           rip time, so it runs in the slower OS access mode.
RIP_MODE = _env("RIP_MODE", "titles")

# ---- duration classes (seconds, video titles only) -----------------------
# Each class routes a title's duration to its own output folder and quality.
# A title matching none of them isn't encoded at all - a logo/menu loop
# below the shortest class, or a "play all" runtime above the longest one.
# Run titles.py on the first disc of each kind you rip and tune these to
# match it: a 22-minute sitcom and a 150-minute film need very different
# windows, and a gap between classes silently skips whatever runs in it.
# Add, remove, resize, or rename classes freely - nothing else in the
# pipeline hardcodes "main"/"extra"; only "encoded"/"extras" as the stock
# folder names, which you're free to point elsewhere too.
DURATION_CLASSES = _parse_duration_classes(_env(
    "DURATION_CLASSES", "extra:180-1199:extras:22,main:1200-10800:encoded:20:dedup"
))

# Titles shorter than this never get ripped at all (MakeMKV's own
# --minlength) - defaults to the shortest configured class's floor, so a
# menu loop below every window doesn't even land in source/. Set explicitly
# to rip shorter junk than you keep, e.g. for manual review.
MIN_TITLE_S = _env_int("MIN_TITLE_S", min((c.min_s for c in DURATION_CLASSES), default=180))

# ---- encoding: video -----------------------------------------------------
ENCODE_JOBS = _env_int("ENCODE_JOBS", 2)
VIDEO_ENCODER = _env("VIDEO_ENCODER", "x264")
ENCODER_PRESET = _env("ENCODER_PRESET", "medium")

# Only helps on interlaced sources (older TV masters, some DVD). Leave empty
# for progressive film/Blu-ray sources - decombing a progressive source can
# introduce artefacts that were never there. Verify on one disc either way.
DEINTERLACE_ARGS = _env("DEINTERLACE_ARGS", "").split()

# ---- encoding: audio (CD) ------------------------------------------------
# Lossless, so this is a format choice, not a quality dial. "flac" needs the
# flac(1) binary; falls back to ffmpeg if that's what's installed, and to a
# plain copy of the WAV if neither is - see encode.py.
AUDIO_FORMAT = _env("AUDIO_FORMAT", "flac")

NOTIFY_ENCODES = _env("NOTIFY_ENCODES", "0") == "1"

# ---- bare `backcrack` launches everything ---------------------------------
# Toggle any of these off to run that piece by hand instead (its own
# terminal tab, a separate machine, whatever) - `backcrack ripd`/`encd`/
# `watch` always still work standalone regardless of these.
LAUNCH_RIPD = _env("LAUNCH_RIPD", "1") == "1"
LAUNCH_ENCD = _env("LAUNCH_ENCD", "1") == "1"
LAUNCH_WATCH = _env("LAUNCH_WATCH", "1") == "1"

# ---- dynamic patterning ---------------------------------------------------
# Destination path under LIBRARY, built by substituting %token% once a
# disc's tokens are known - from labels.map, or from auto-detection (season/
# disc parsed from the volume label; artist/album looked up online for audio,
# when the tools for that are installed). See docs/pattern-tokens.md.
# An unresolved token falls the disc to UNSORTED/ rather than guess - the
# same safety net as an unparseable label.
PATTERN_VIDEO = _env("PATTERN_VIDEO", "Season %season%/Disc %disc%")
PATTERN_AUDIO = _env("PATTERN_AUDIO", "%artist%/%album%")

# Give up on a disc after this many failed attempts, then eject it and move
# on. A failed disc is deliberately left in the drive so it retries itself.
MAX_RETRIES = _env_int("MAX_RETRIES", 3)

# Derived. Don't edit.
HERE = Path(__file__).resolve().parent.parent
LABELS_MAP = HERE / "labels.map"
STATE = LIBRARY / ".ripstate"
LOGDIR = STATE / "logs"
QUEUE = STATE / "queue"
DONEDIR = STATE / "done"
ENCDONE = STATE / "encdone"
RIPLOG = STATE / "rip.log"
ENCLOG = STATE / "encode.log"

for _d in (LIBRARY, STATE, LOGDIR, QUEUE, DONEDIR, ENCDONE):
    _d.mkdir(parents=True, exist_ok=True)
