"""rip.py - rip a single disc (video or audio), eject it, and record the
outcome. Shared by ripd.py; the two rip_*_disc functions are the only
format-specific branches in here - everything else (locking, retry, eject,
notify, queueing) is format-agnostic on purpose. Adding a third disc kind
should only ever mean adding a third branch here.
"""
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import config as cfg
from . import disc
from . import pattern
from .lib import log, notify, human_gb, dir_size_kb


def disc_present(cdev: str) -> bool:
    return subprocess.run(["diskutil", "info", cdev], capture_output=True, timeout=10).returncode == 0


def _drutil_num_for(cdev: str) -> Optional[str]:
    try:
        info = subprocess.run(["diskutil", "info", cdev], capture_output=True, text=True, timeout=10).stdout
    except subprocess.TimeoutExpired:
        return None
    name = ""
    for line in info.splitlines():
        if "Device / Media Name" in line:
            name = line.split(":", 1)[1].strip()
            break
    if not name:
        return None
    try:
        listing = subprocess.run(["drutil", "list"], capture_output=True, text=True, timeout=8).stdout
    except subprocess.TimeoutExpired:
        return None
    for word in name.split():
        if len(word) < 5:
            continue
        for line in listing.splitlines():
            if word in line:
                return line.split()[0]
    return None


def eject_disc(cdev: str, label: str) -> bool:
    """Returns True if the media actually left the drive."""
    if not disc_present(cdev):
        return True
    subprocess.run(["diskutil", "eject", cdev], capture_output=True, timeout=15)
    time.sleep(3)
    if not disc_present(cdev):
        return True
    num = _drutil_num_for(cdev)
    if num:
        subprocess.run(["drutil", "-drive", num, "eject"], capture_output=True, timeout=12)
        time.sleep(3)
        if not disc_present(cdev):
            return True
        subprocess.run(["drutil", "-drive", num, "tray", "eject"], capture_output=True, timeout=12)
        time.sleep(3)
        if not disc_present(cdev):
            return True
    subprocess.run(
        ["osascript", "-e", f'tell application "Finder" to eject disk "{label}"'],
        capture_output=True, timeout=10,
    )
    time.sleep(3)
    return not disc_present(cdev)


def _play_done_sound():
    if subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"],
                       capture_output=True, timeout=10).returncode != 0:
        print("\a", end="")


def already_handled(label: str, cdev: str) -> bool:
    """True if this disc should be skipped (already ripped, or given up
    on). Ejects it either way.
    """
    done = (cfg.DONEDIR / label).exists()
    gaveup = (cfg.STATE / f"gaveup-{label}").exists()
    if not (done or gaveup):
        return False
    why = "failed too many times" if gaveup else "already ripped"
    nagged = cfg.STATE / f"nagged-{label}"
    if not nagged.exists():
        log(cfg.RIPLOG, f"SKIP  {label}  {why}")
        nagged.touch()
        if not eject_disc(cdev, label):
            notify("backcrack - remove disc", f"'{label}' {why}. Drive won't auto-eject; pull it out.",
                   "default", "eject")
    else:
        eject_disc(cdev, label)
    return True


def route_dest(kind: str, label: str, rdev: str) -> Path:
    dest = pattern.dest_for(kind, label, rdev)
    if "UNSORTED" in dest.parts:
        log(cfg.RIPLOG, f"WARN  '{label}' did not resolve to a destination - filing under UNSORTED; add it to labels.map")
        notify("backcrack - unrecognised disc", f"'{label}' went to UNSORTED. Add it to labels.map.",
               "high", "warning")
    return dest


def mark_unsorted_kind(dest: Path, kind: str) -> None:
    if "UNSORTED" in dest.parts:
        (dest / ".kind").write_text(kind + "\n")


def finish_rip(label: str, dest: Path, cdev: str, ok: bool, mins: int, kind: str) -> None:
    kb = dir_size_kb(dest)
    gb = human_gb(kb)
    rel = str(dest.relative_to(cfg.LIBRARY))

    if ok:
        (cfg.STATE / f"fails-{label}").unlink(missing_ok=True)
        (cfg.DONEDIR / label).touch()
        (cfg.QUEUE / label).write_text(f"label={label}\ndest={dest}\nkind={kind}\n")
        n = len(list(cfg.DONEDIR.iterdir()))
        log(cfg.RIPLOG, f"OK    {label} -> {rel}  {mins}m  {gb}GB  [{n} done]")
        if eject_disc(cdev, label):
            notify(f"backcrack - {label} ripped, swap disc", f"{mins} min, {gb}GB. {n} done. Drive is open.",
                   "default", "optical_disk,white_check_mark")
        else:
            notify(f"backcrack - {label} ripped, eject it", f"{mins} min, {gb}GB. {n} done. Drive won't auto-eject.",
                   "default", "optical_disk,eject")
    else:
        fails_f = cfg.STATE / f"fails-{label}"
        fails = int(fails_f.read_text()) + 1 if fails_f.exists() else 1
        fails_f.write_text(str(fails))
        log(cfg.RIPLOG, f"FAIL  {label} -> {rel}  {mins}m  (attempt {fails}/{cfg.MAX_RETRIES})")
        if fails >= cfg.MAX_RETRIES:
            (cfg.STATE / f"gaveup-{label}").touch()
            log(cfg.RIPLOG, f"GIVEUP {label} after {fails} attempts - ejecting, moving on")
            eject_disc(cdev, label)
            notify(f"backcrack - {label} GAVE UP",
                   f"Failed {fails} times. Delete .ripstate/gaveup-{label} to try again.", "high", "rotating_light")
        else:
            notify(f"backcrack - {label} failed, retrying",
                   f"Attempt {fails} of {cfg.MAX_RETRIES}. Disc left in; it retries itself.", "default", "warning")
    _play_done_sound()


def rip_video_disc(dev: str, label: str) -> None:
    # dev is already the cooked path (pending_discs() reads it straight from
    # `mount`/`diskutil list`, which never print a raw device name) - passing
    # it through disc.cooked() anyway used to double the "/dev/" prefix into
    # a device path that doesn't exist, silently breaking every eject.
    cdev, rdev = dev, disc.raw(dev)
    if already_handled(label, cdev):
        return

    dest = route_dest("video", label, rdev)

    idx = None
    if cfg.RIP_MODE == "video_ts":
        idx = disc.mkv_index_for(dev)
        if idx is None:
            log(cfg.RIPLOG, f"FAIL  {label} - could not resolve a MakeMKV drive index for {dev}")
            notify(f"backcrack - {label} FAILED", f"No MakeMKV drive index for {dev}.", "high", "rotating_light")
            subprocess.run(["diskutil", "eject", cdev], capture_output=True, timeout=15)
            return

    log(cfg.RIPLOG, f"START {label} -> {dest.relative_to(cfg.LIBRARY)}  (video/{cfg.RIP_MODE}"
                     f"{f', disc:{idx}' if idx else ''}, {dev})")
    subprocess.run(["pkill", "-f", "DVD Player"], capture_output=True, timeout=5)
    subprocess.run(["diskutil", "unmountDisk", cdev], capture_output=True, timeout=15)

    if dest.exists():
        import shutil as _shutil
        _shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    mark_unsorted_kind(dest, "video")

    t0 = time.monotonic()
    if cfg.RIP_MODE == "video_ts":
        rc = subprocess.run(
            [cfg.MKVCON, "--noscan", "backup", "--decrypt", f"disc:{idx}", str(dest)],
            capture_output=True, timeout=None,
        ).returncode
        ok = rc == 0 and (dest / "VIDEO_TS").is_dir()
    else:
        nsrc = dest / "source"; nsrc.mkdir(parents=True, exist_ok=True)
        rc = subprocess.run(
            [cfg.MKVCON, "--noscan", f"--minlength={cfg.MIN_TITLE_S}", "mkv", f"dev:{rdev}", "all", str(nsrc)],
            capture_output=True, timeout=None,
        ).returncode
        ok = rc == 0 and any(nsrc.glob("*.mkv"))
    mins = int((time.monotonic() - t0) / 60)

    finish_rip(label, dest, cdev, ok, mins, "video")


def rip_audio_disc(dev: str, label: str) -> None:
    # dev is already the cooked path (pending_discs() reads it straight from
    # `mount`/`diskutil list`, which never print a raw device name) - passing
    # it through disc.cooked() anyway used to double the "/dev/" prefix into
    # a device path that doesn't exist, silently breaking every eject.
    cdev, rdev = dev, disc.raw(dev)
    if already_handled(label, cdev):
        return

    dest = route_dest("audio", label, rdev)

    log(cfg.RIPLOG, f"START {label} -> {dest.relative_to(cfg.LIBRARY)}  (audio, {dev})")
    subprocess.run(["diskutil", "unmountDisk", cdev], capture_output=True, timeout=15)

    if dest.exists():
        import shutil as _shutil
        _shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    mark_unsorted_kind(dest, "audio")

    nsrc = dest / "source"; nsrc.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    rc = subprocess.run(
        [cfg.CDPARANOIA, "-d", rdev, "-B", "1-"], cwd=nsrc, capture_output=True, timeout=None,
    ).returncode
    ok = rc == 0 and any(nsrc.glob("*.wav"))
    mins = int((time.monotonic() - t0) / 60)

    finish_rip(label, dest, cdev, ok, mins, "audio")
