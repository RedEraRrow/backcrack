"""rip.py - rip a single disc (video or audio), eject it, and record the
outcome. Shared by ripd.py; the two rip_*_disc functions are the only
format-specific code in here.
"""
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import config as cfg
from . import disc
from . import pattern
from backbone.ui import dir_size_kb, human_gb
from .lib import log, notify


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


def _dest_has_output(dest: Path) -> bool:
    """True if dest already holds finished encode output - a completed rip
    under a *different* label routing to the same folder (a relabelled
    duplicate, a re-rip) must never silently wipe it. "encoded" covers audio
    (hardcoded in encode_audio_one) as well as the stock video class name."""
    folders = {"encoded"} | {cls.folder for cls in cfg.DURATION_CLASSES}
    return any((dest / f).is_dir() and any((dest / f).iterdir()) for f in folders)


def _make_dest(dest: Path, label: str) -> Path:
    """dest, cleared and (re)created. If dest already holds finished output
    (another disc, or a re-rip, routing to the same folder), it is left alone:
    the rip goes to a sibling "<name> (<label>)" folder instead, and the
    collision is logged and pushed for a human to reconcile.
    """
    if dest.exists() and _dest_has_output(dest):
        alt = dest.parent / f"{dest.name} ({label})"
        log(cfg.RIPLOG, f"WARN  '{label}' would overwrite existing output at "
                         f"{dest.relative_to(cfg.LIBRARY)} - ripping to '{alt.name}/' instead, reconcile manually")
        notify(f"backcrack - {label} redirected",
               f"{dest.name} already has output; ripped alongside it as '{alt.name}' instead.",
               "high", "warning")
        dest = alt
    if dest.exists():
        import shutil as _shutil
        _shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    return dest


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

    (cfg.STATE / f"nagged-{label}").unlink(missing_ok=True)
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
            # Deleting gaveup-<label> is how a human retries, so that retry
            # starts from a fresh count.
            fails_f.unlink(missing_ok=True)
            log(cfg.RIPLOG, f"GIVEUP {label} after {fails} attempts - ejecting, moving on")
            eject_disc(cdev, label)
            notify(f"backcrack - {label} GAVE UP",
                   f"Failed {fails} times. Delete .ripstate/gaveup-{label} to try again.", "high", "rotating_light")
        else:
            if kind == "video":
                # The rip unmounted the disc, and video discs are found by their
                # mounted volume: mount it again so the next poll retries it.
                subprocess.run(["diskutil", "mountDisk", cdev], capture_output=True, timeout=30)
            notify(f"backcrack - {label} failed, retrying",
                   f"Attempt {fails} of {cfg.MAX_RETRIES}. Disc left in; it retries itself.", "default", "warning")
    _play_done_sound()


def rip_video_disc(dev: str, label: str) -> None:
    # dev is the cooked /dev/diskN path, as pending_discs() yields it.
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

    dest = _make_dest(dest, label)
    mark_unsorted_kind(dest, "video")

    # MakeMKV's output is the only record of a read error, a retried sector or
    # a title it gave up on, so keep it, as HandBrake's per-title log is kept.
    mkv_log = cfg.LOGDIR / f"{label}.mkv.log"
    t0 = time.monotonic()
    if cfg.RIP_MODE == "video_ts":
        with open(mkv_log, "ab") as logf:
            rc = subprocess.run(
                [cfg.MKVCON, "--noscan", "backup", "--decrypt", f"disc:{idx}", str(dest)],
                stdout=logf, stderr=subprocess.STDOUT, timeout=None,
            ).returncode
        ok = rc == 0 and (dest / "VIDEO_TS").is_dir()
    else:
        nsrc = dest / "source"; nsrc.mkdir(parents=True, exist_ok=True)
        with open(mkv_log, "ab") as logf:
            rc = subprocess.run(
                [cfg.MKVCON, "--noscan", f"--minlength={cfg.MIN_TITLE_S}", "mkv", f"dev:{rdev}", "all", str(nsrc)],
                stdout=logf, stderr=subprocess.STDOUT, timeout=None,
            ).returncode
        ok = rc == 0 and any(nsrc.glob("*.mkv"))
        # MakeMKV can exit 0 after saving only some titles. Its own "N titles
        # saved, M failed" tally is what shows a partial rip, which must not
        # count as a finished one.
        m = re.search(r"(\d+) titles saved, (\d+) failed", mkv_log.read_text(errors="ignore"))
        if m and int(m.group(2)) > 0:
            ok = False
            log(cfg.RIPLOG, f"      {label}: {m.group(1)} titles saved, {m.group(2)} failed (partial - see {mkv_log})")
        elif not ok or rc != 0:
            log(cfg.RIPLOG, f"      {label}: makemkvcon rc={rc} - see {mkv_log}")
    mins = int((time.monotonic() - t0) / 60)

    finish_rip(label, dest, cdev, ok, mins, "video")


def rip_audio_disc(dev: str, label: str) -> None:
    # dev is the cooked /dev/diskN path, as pending_discs() yields it. label
    # is only the device name there, so name the CD itself now this thread
    # holds the drive.
    cdev, rdev = dev, disc.raw(dev)
    label = disc.audio_label(rdev) or label
    if already_handled(label, cdev):
        return

    dest = route_dest("audio", label, rdev)

    log(cfg.RIPLOG, f"START {label} -> {dest.relative_to(cfg.LIBRARY)}  (audio, {dev})")
    subprocess.run(["diskutil", "unmountDisk", cdev], capture_output=True, timeout=15)

    dest = _make_dest(dest, label)
    mark_unsorted_kind(dest, "audio")

    nsrc = dest / "source"; nsrc.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    rc = subprocess.run(
        [cfg.CDPARANOIA, "-d", rdev, "-B", "1-"], cwd=nsrc, capture_output=True, timeout=None,
    ).returncode
    ok = rc == 0 and any(nsrc.glob("*.wav"))
    mins = int((time.monotonic() - t0) / 60)

    finish_rip(label, dest, cdev, ok, mins, "audio")
