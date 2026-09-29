"""lib.py - format-agnostic helpers shared by every daemon and tool."""
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime
from itertools import dropwhile
from pathlib import Path

from . import config as cfg


def log(path: Path, message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}"
    print(line)
    with open(path, "a") as f:
        f.write(line + "\n")


def notify(title: str, message: str, priority: str = "default", tags: str = "optical_disk") -> None:
    if not cfg.NTFY_TOPIC:
        return

    def _send():
        req = urllib.request.Request(
            f"{cfg.NTFY_SERVER}/{cfg.NTFY_TOPIC}",
            data=message.encode(),
            headers={"Title": title, "Priority": priority, "Tags": tags},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


def find_daemons(*names: str) -> list:
    """(pid, command) for every running backcrack daemon in `names` (e.g.
    "ripd"), however it was started: `ripd`, `ripd.py`, `python3 ripd.py`
    or `backcrack ripd`. The calling process is never included.
    """
    try:
        out = subprocess.run(["ps", "-Awwo", "pid=,command="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        args = list(dropwhile(lambda a: a.startswith("-") or Path(a).name.startswith("python"), command.split()))
        if not args:
            continue
        prog = Path(args[0]).name.removesuffix(".py")
        if prog in names or (prog == "backcrack" and len(args) > 1 and args[1] in names):
            found.append((int(pid), command))
    return found


def ps_listing() -> str:
    """Every running process's full command line, one per line."""
    try:
        return subprocess.run(["ps", "-Awwo", "command"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def disk_free(path: Path) -> str:
    """Free space on `path`'s volume as `df -h` shows it, or "?"."""
    try:
        lines = subprocess.run(["df", "-h", str(path)], capture_output=True, text=True, timeout=10).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    return lines[-1].split()[3] if len(lines) > 1 else "?"


def count_entries(d: Path) -> int:
    """How many entries `d` holds (0 if it doesn't exist): discs ripped for
    DONEDIR, jobs waiting for QUEUE, discs encoded for ENCDONE."""
    return len(list(d.iterdir())) if d.exists() else 0


def class_folders() -> list:
    """Each duration class's output folder, once each, in config order."""
    return list(dict.fromkeys(c.folder for c in cfg.DURATION_CLASSES))


def class_file_counts() -> dict:
    """{folder: files in every <folder>/ under LIBRARY} for class_folders()."""
    return {f: sum(1 for _ in cfg.LIBRARY.glob(f"**/{f}/*")) if cfg.LIBRARY.exists() else 0
            for f in class_folders()}


def spawn_daemon(name: str) -> None:
    """Starts `name`.py from the checkout in the background, its output
    (unbuffered, so it's readable while it runs) appended to STATE/<name>.out."""
    script = Path(__file__).resolve().parent.parent / f"{name}.py"
    subprocess.Popen([sys.executable, "-u", str(script)],
                     stdout=open(cfg.STATE / f"{name}.out", "ab"), stderr=subprocess.STDOUT)


def stop_daemons(*names: str) -> int:
    """SIGTERMs every running daemon in `names`; returns how many it stopped."""
    stopped = 0
    for pid, _ in find_daemons(*names):
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except OSError:
            pass
    return stopped


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
    except (OSError, subprocess.TimeoutExpired):
        return {}
    marker = "JSON Title Set:"
    idx = out.find(marker)
    if idx < 0:
        return {}
    try:
        return json.loads(out[idx + len(marker):])
    except json.JSONDecodeError:
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
