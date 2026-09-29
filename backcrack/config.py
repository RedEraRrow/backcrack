"""Settings for the backcrack rip pipeline, and their defaults.

Each value is read from its environment variable first, then from
settings.env in CONFIG_DIR, then falls back to the default here. So a
one-off override needs no edit: `RIP_MODE=video_ts ripd`.
"""
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from backbone import ui as _ui

# Everything you edit or that edits itself lives OUTSIDE the checkout, so the
# repo stays code-only and nothing personal needs gitignoring: your saved
# settings, your labels.map overrides, your episodes.map titles. Same
# convention as backtrack's CONFIG_DIR - $BACKCRACK_CONFIG_DIR wins, then
# $XDG_CONFIG_HOME/backcrack, else ~/.config/backcrack. (No Windows branch:
# this pipeline shells out to /Applications/MakeMKV.app and Homebrew binaries,
# so it is macOS/Linux by construction.)
def _default_config_dir() -> Path:
    xdg = os.getenv("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "backcrack"


CONFIG_DIR = Path(os.getenv("BACKCRACK_CONFIG_DIR") or _default_config_dir())
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Settings changed from `backcrack watch`'s settings screen land here rather
# than in real env vars, so they survive past this process without editing
# this file. os.environ still wins when set (a one-off `FOO=bar` override
# stays the escape hatch it always was) - this is just a second, persisted
# fallback beneath it.
SETTINGS_FILE = CONFIG_DIR / "settings.env"


def _load_settings_file() -> dict:
    d = {}
    if SETTINGS_FILE.exists():
        for line in SETTINGS_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


_FILE_SETTINGS = _load_settings_file()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, _FILE_SETTINGS.get(name, default))


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def save_setting(name: str, value: str) -> None:
    """Persist one setting to SETTINGS_FILE, replacing its line if already
    present. Only processes started afterwards see it (ripd/encd need
    restarting); a caller that wants it in its own running `cfg` has to set
    that too (see watch.py).
    """
    lines = SETTINGS_FILE.read_text().splitlines() if SETTINGS_FILE.exists() else []
    out, found = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name:
            out.append(f"{name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{name}={value}")
    SETTINGS_FILE.write_text("\n".join(out) + "\n")
    _FILE_SETTINGS[name] = value


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
# The default suits one particular show; set LIBRARY rather than rely on it.
LIBRARY = Path(_env("LIBRARY", str(Path.home() / "Media" / "rips"))).expanduser()

# MKV is diskspeed's old name for it, still honoured.
MKVCON = _env("MKVCON", _env("MKV", "/Applications/MakeMKV.app/Contents/MacOS/makemkvcon"))
HBCLI = _env("HBCLI", shutil.which("HandBrakeCLI") or "/opt/homebrew/bin/HandBrakeCLI")
CDPARANOIA = _env("CDPARANOIA", shutil.which("cdparanoia") or "")
CD_DISCID = _env("CD_DISCID", shutil.which("cd-discid") or "")
FLAC = _env("FLAC", shutil.which("flac") or "")
FFMPEG = _env("FFMPEG", shutil.which("ffmpeg") or "")

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
# Add, remove, resize, or rename classes freely: nothing hardcodes
# "main"/"extra". The folder names are another matter: audio always goes to
# "encoded", and namer.py only works with "encoded" and "extras".
# dedup is off by default and wrong for TV; see the README before using it.
DURATION_CLASSES = _parse_duration_classes(_env(
    "DURATION_CLASSES", "extra:180-1199:extras:22,main:1200-10800:encoded:20"
))

# Titles shorter than this never get ripped at all (MakeMKV's own
# --minlength) - defaults to the shortest configured class's floor, so a
# menu loop below every window doesn't even land in source/. Set explicitly
# to rip shorter junk than you keep, e.g. for manual review.
MIN_TITLE_S = _env_int("MIN_TITLE_S", min((c.min_s for c in DURATION_CLASSES), default=180))

# ---- encoding: video -----------------------------------------------------
# The defaults for jobs, preset and threads were benchmarked on an M1 Pro;
# see the README's "Encoding throughput" before changing them.
ENCODE_JOBS = _env_int("ENCODE_JOBS", 4)
VIDEO_ENCODER = _env("VIDEO_ENCODER", "x264")
ENCODER_PRESET = _env("ENCODER_PRESET", "fast")

# Caps x264's threads per job, so a job left running alone can't take every
# core and starve the rips (README, "Encoding throughput").
ENCODE_THREADS = _env_int("ENCODE_THREADS", max(1, (os.cpu_count() or 4) // ENCODE_JOBS))

# HandBrake's deinterlace flags, e.g. "--comb-detect --decomb". Only helps
# on interlaced sources (older TV masters, some DVD). Leave empty for
# progressive film/Blu-ray sources - decombing a progressive source can
# introduce artefacts that were never there. Check one disc either way.
DEINTERLACE_ARGS = _env("DEINTERLACE_ARGS", "").split()

# ---- encoding: audio (CD) ------------------------------------------------
# Lossless, so this is a format choice, not a quality dial. "flac" uses the
# flac(1) binary, or ffmpeg if that's what's installed; any other format
# needs ffmpeg. With no encoder for it, tracks are copied as .wav instead
# (encode.audio_ext).
AUDIO_FORMAT = _env("AUDIO_FORMAT", "flac")

NOTIFY_ENCODES = _env("NOTIFY_ENCODES", "0") == "1"

# ---- bare `backcrack` launches everything ---------------------------------
# Toggle any of these off to run that piece by hand instead (its own
# terminal tab, a separate machine, whatever) - `backcrack ripd`/`encd`/
# `sortd`/`watch` always still work standalone regardless of these.
LAUNCH_RIPD = _env("LAUNCH_RIPD", "1") == "1"
LAUNCH_ENCD = _env("LAUNCH_ENCD", "1") == "1"
LAUNCH_SORTD = _env("LAUNCH_SORTD", "1") == "1"
LAUNCH_WATCH = _env("LAUNCH_WATCH", "1") == "1"

# ---- watch, sortd, diskspeed ----------------------------------------------
# WATCH_INTERVAL and SORT_INTERVAL fall back to a plain INTERVAL if one is set.
WATCH_INTERVAL = float(_env("WATCH_INTERVAL", _env("INTERVAL", "1")))
SORT_INTERVAL = _env_int("SORT_INTERVAL", int(_env("INTERVAL", "5")))
TOTAL_DISCS = _env_int("TOTAL_DISCS", 0)       # discs in this run; 0 hides the percentage and ETA
WINDOW = _env_int("WINDOW", 20)                # seconds of history behind watch's MB/s figure
ACTIVE_S = _env_int("ACTIVE_S", 90)            # a source/ idle this long drops out of RIPPING
FALLBACK_KB = _env_int("FALLBACK_KB", 7340032)  # disc size assumed until diskutil reports it
READ_MB = _env_int("READ_MB", 600)             # diskspeed: how much to read per drive
SKIP_MB = _env_int("SKIP_MB", 1000)            # diskspeed: where on the disc to start reading
STALL_S = _env_int("STALL_S", 60)              # diskspeed: give up on a drive after this long without progress
DISC_BYTES = _env_int("DISC_BYTES", 7_000_000_000)  # diskspeed: disc size for its minutes-per-disc estimate
RESULTS = Path(_env("RESULTS", str(Path.home() / "diskspeed.txt")))

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

# ---- settings exposed to `backcrack watch`'s settings screen -------------
# (env var, type, one-line label). Add a tuple here for any setting above
# that should be editable there - type is "str", "int", "bool", or "path".
# Nothing else in the pipeline reads this list; it's UI metadata only.
SETTINGS = [
    ("LIBRARY", "path", "Library root"),
    ("RIP_MODE", "str", "Rip mode (titles / video_ts)"),
    ("DURATION_CLASSES", "str", "Duration classes (name:min-max:folder:quality[:dedup],...)"),
    ("ENCODE_JOBS", "int", "Concurrent encode jobs"),
    ("VIDEO_ENCODER", "str", "HandBrake video encoder"),
    ("ENCODER_PRESET", "str", "HandBrake encoder preset"),
    ("ENCODE_THREADS", "int", "Threads per encode job"),
    ("DEINTERLACE_ARGS", "str", "Deinterlace args (blank = off)"),
    ("AUDIO_FORMAT", "str", "Audio rip format"),
    ("PATTERN_VIDEO", "str", "Video destination pattern"),
    ("PATTERN_AUDIO", "str", "Audio destination pattern"),
    ("MAX_RETRIES", "int", "Max retries before giving up on a disc"),
    ("NOTIFY_ENCODES", "bool", "Notify on encode complete"),
    ("LAUNCH_RIPD", "bool", "Bare `backcrack` also launches ripd"),
    ("LAUNCH_ENCD", "bool", "Bare `backcrack` also launches encd"),
    ("LAUNCH_SORTD", "bool", "Bare `backcrack` also launches sortd"),
    ("LAUNCH_WATCH", "bool", "Bare `backcrack` also launches watch"),
    ("MIN_TITLE_S", "int", "Shortest title MakeMKV rips (seconds)"),
    ("NTFY_TOPIC", "str", "ntfy topic (blank = no pushes)"),
    ("NTFY_SERVER", "str", "ntfy server"),
    ("ACCENT", "accent", "Accent colour (green, red, blue, amber… or #RRGGBB)"),
]

# The accent colour of every screen (backbone.ui.ACCENT_PRESETS key or
# "#RRGGBB"), applied here once so each tool gets it by importing config.
ACCENT = _env("ACCENT", "green")
_ui.set_accent(ACCENT)

# Derived. Don't edit.
# Your files, in CONFIG_DIR (see the top of this file): manual disc->token
# overrides, and the per-season episode titles namer.py renames from.
LABELS_MAP = CONFIG_DIR / "labels.map"
EPISODES_MAP = CONFIG_DIR / "episodes.map"


def use_library(path: Path) -> None:
    """Point LIBRARY and every state path under it at `path`."""
    global LIBRARY, STATE, LOGDIR, QUEUE, DONEDIR, ENCDONE, RIPLOG, ENCLOG
    LIBRARY = path
    STATE = LIBRARY / ".ripstate"
    LOGDIR = STATE / "logs"
    QUEUE = STATE / "queue"
    DONEDIR = STATE / "done"
    ENCDONE = STATE / "encdone"
    RIPLOG = STATE / "rip.log"
    ENCLOG = STATE / "encode.log"
    for d in (LIBRARY, STATE, LOGDIR, QUEUE, DONEDIR, ENCDONE):
        d.mkdir(parents=True, exist_ok=True)


use_library(LIBRARY)
