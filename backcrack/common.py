"""backbone's helpers bound to backcrack's settings, and what only a ripper needs."""
import json
import subprocess

from backbone.files import log_line
from backbone.log import log as diag
from backbone.notify import ntfy
from backbone.procs import find_processes, spawn_module, stop_processes

from backcrack import config as cfg


# The general parts live in backbone; these bind backcrack's own settings.
log = log_line                      # a daemon's timestamped record, printed and appended


def notify(title: str, message: str, priority: str = "default", tags: str = "optical_disk") -> None:
    """Push to your ntfy topic (NTFY_TOPIC); nothing is sent without one."""
    ntfy(cfg.NTFY_SERVER, cfg.NTFY_TOPIC, title, message, priority, tags)


def find_daemons(*names: str) -> list:
    """(pid, command) for each running backcrack daemon in `names`, however it
    was started, including as `backcrack <name>`."""
    return find_processes(*names, launcher="backcrack")


def stop_daemons(*names: str) -> int:
    """SIGTERM every running backcrack daemon in `names`; returns how many."""
    return stop_processes(*names, launcher="backcrack")


def spawn_daemon(name: str) -> None:
    """Start the `name` daemon in the background, output to STATE/<name>.out."""
    cfg.make_library_dirs()
    spawn_module(f"backcrack.{name}", cfg.STATE / f"{name}.out")


def class_folders() -> list:
    """Each duration class's output folder, once each, in config order."""
    return list(dict.fromkeys(c.folder for c in cfg.DURATION_CLASSES))


def class_file_counts() -> dict:
    """{folder: files in every <folder>/ under LIBRARY} for class_folders()."""
    return {f: sum(1 for _ in cfg.LIBRARY.glob(f"**/{f}/*")) if cfg.LIBRARY.exists() else 0
            for f in class_folders()}


def classify_class(seconds):
    """The cfg.DurationClass a video title of `seconds` falls in, or None if
    it fits none of them."""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return None
    for c in cfg.DURATION_CLASSES:
        if c.min_s <= s <= c.max_s:
            return c
    return None


def classify(seconds) -> str:
    cls = classify_class(seconds)
    return cls.name if cls else "skip"


def hb_json(input_path: str) -> dict:
    """HandBrake's JSON title set for a disc folder or media file."""
    try:
        out = subprocess.run(
            [cfg.HBCLI, "-i", input_path, "--title", "0", "--scan", "--json"],
            capture_output=True, text=True, timeout=120,
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        diag.warning("HandBrake scan of %s failed: %s", input_path, e)
        return {}
    marker = "JSON Title Set:"
    idx = out.find(marker)
    if idx < 0:
        diag.warning("HandBrake scan of %s gave no title set", input_path)
        return {}
    try:
        return json.loads(out[idx + len(marker):])
    except json.JSONDecodeError as e:
        diag.warning("HandBrake scan of %s: unreadable JSON: %s", input_path, e)
        return {}


def hb_titles(input_path: str):
    """Yields (index, seconds) for every title HandBrake found, unfiltered."""
    for t in hb_json(input_path).get("TitleList", []):
        d = t.get("Duration", {})
        secs = d.get("Hours", 0) * 3600 + d.get("Minutes", 0) * 60 + d.get("Seconds", 0)
        yield t.get("Index"), secs


def file_duration(path: str) -> int:
    titles = hb_json(path).get("TitleList", [])
    if not titles:
        return 0
    d = titles[0].get("Duration", {})
    return d.get("Hours", 0) * 3600 + d.get("Minutes", 0) * 60 + d.get("Seconds", 0)
