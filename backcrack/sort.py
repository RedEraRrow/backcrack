"""sort.py - reconcile UNSORTED/ once a disc's tokens become resolvable
(a labels.map line was added, or the parser improved). Shared by sortd.py.

Four independent guards before touching a folder. A previous version of this
kind of reconciler moved a folder out from under an active rip because it
trusted a single ps check; nothing here relies on one signal alone.
"""
import shutil
import subprocess
import time
from pathlib import Path

from . import config as cfg
from . import pattern
from .lib import log, notify

SORTLOG = cfg.STATE / "sort.log"
QUIET_S = 90  # a dir touched more recently than this is live


def _rip_active() -> bool:
    return any(cfg.STATE.glob("lock-*"))


def _recently_touched(d: Path) -> bool:
    cutoff = time.time() - QUIET_S
    return any(f.stat().st_mtime >= cutoff for f in d.rglob("*") if f.is_file())


def _named_by_process(d: Path) -> bool:
    try:
        out = subprocess.run(["ps", "-Awwo", "command"], capture_output=True, text=True, timeout=10).stdout
    except subprocess.TimeoutExpired:
        return False
    target = str(d)
    return any(
        (("makemkvcon" in line) or ("HandBrakeCLI" in line) or ("cdparanoia" in line)) and target in line
        for line in out.splitlines()
    )


def _files_open(d: Path) -> bool:
    try:
        out = subprocess.run(["lsof", "+D", str(d)], capture_output=True, text=True, timeout=10).stdout
    except subprocess.TimeoutExpired:
        return False
    return len(out.splitlines()) > 1


def busy(d: Path, label: str) -> bool:
    why = None
    if _rip_active():
        why = "a rip is in progress"
    elif _recently_touched(d):
        why = f"files changed in the last {QUIET_S}s"
    elif _named_by_process(d):
        why = "a ripper/encoder names this path"
    elif _files_open(d):
        why = "open file handles inside"
    if why is None:
        return False
    sortwait = cfg.STATE / f"sortwait-{label}"
    if not sortwait.exists():
        log(SORTLOG, f"WAIT  {label} - deferring: {why}")
        sortwait.touch()
    return True


def requeue(old: Path, new: Path, label: str) -> None:
    for q in cfg.QUEUE.iterdir():
        fields = dict(line.split("=", 1) for line in q.read_text().splitlines() if "=" in line)
        if fields.get("dest") != str(old):
            continue
        q.write_text(f"label={label}\ndest={new}\nkind={fields.get('kind', '')}\n")
        log(SORTLOG, f"      repointed encode job -> {new}")


def reconcile(dry_run: bool = False) -> None:
    unsorted = cfg.LIBRARY / "UNSORTED"
    if not unsorted.is_dir():
        return

    for d in sorted(unsorted.iterdir()):
        if not d.is_dir():
            continue
        label = d.name
        kind = (d / ".kind").read_text().strip() if (d / ".kind").exists() else "video"
        target = pattern.dest_for(kind, label, None)
        if "UNSORTED" in target.parts:
            continue  # still unresolvable, leave it

        if busy(d, label):
            continue
        (cfg.STATE / f"sortwait-{label}").unlink(missing_ok=True)

        if dry_run:
            verb = "MERGE" if target.exists() else "MOVE "
            print(f"  WOULD {verb} {label} -> {target}")
            continue

        rel = target.relative_to(cfg.LIBRARY)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(d), str(target))
                requeue(d, target, label)
                log(SORTLOG, f"MOVED {label} -> {rel}")
                notify(f"backcrack - filed {rel}", f"{label} moved out of UNSORTED.", "low", "file_folder")
            except OSError:
                log(SORTLOG, f"FAIL  could not move {label}")
            continue

        # Target exists: merge anything the target doesn't already have, so
        # a disc split across both locations is healed rather than left broken.
        log(SORTLOG, f"MERGE {label} into existing {rel}")
        for f in list(d.rglob("*")):
            if not f.is_file() or f.name == ".kind":
                continue
            relf = f.relative_to(d)
            dst = target / relf
            if dst.exists():
                log(SORTLOG, f"      keep existing {relf}")
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dst))
                log(SORTLOG, f"      moved {relf}")
        for sub in sorted(d.rglob("*"), key=lambda p: -len(p.parts)):
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()
        if not d.exists():
            requeue(d, target, label)
            log(SORTLOG, f"MERGE done for {label}")
            notify(f"backcrack - merged {rel}", f"{label} folded into its folder.", "low", "file_folder")
        else:
            log(SORTLOG, f"MERGE incomplete for {label} - files remain in UNSORTED")

    try:
        unsorted.rmdir()
    except OSError:
        pass
