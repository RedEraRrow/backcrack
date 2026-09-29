#!/usr/bin/env python3
"""watch.py - live view of the rip + encode pipeline. Works the same for a
CD, DVD or Blu-ray job - the folders it watches (source/, encoded/, extras/)
look the same regardless of format. Percentages are relative to the disc
actually in the drive, read from its own volume size.

    watch   ·   ONESHOT=1 watch   ·   NO_COLOR=1 watch

Keys: q quits (and offers to stop ripd, encd and sortd), s opens the
settings screen. Set TOTAL_DISCS to the number of discs in the run for a
progress bar and ETA. Growth history and disc-size calibration live in
memory for the life of the process.
"""
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backbone.nav import NAV_STACK
from backbone import ui
from backbone.prompt_core import hint, run_dashboard
from backbone.ui import (
    Colors as C, bar, clip_ansi, content_width, dir_size_kb, get_terminal_width,
    header_box, human_gb, rate_of_change, sparkline, spinner, truncate_text,
)
from backcrack import config as cfg
from backcrack.disc import DVD_1X_BPS
from backcrack.encode import PART
from backcrack.lib import class_file_counts, count_entries, disk_free, ps_listing, stop_daemons

NAV_STACK[:] = ["backcrack", "watch"]

TOTAL_DISCS = cfg.TOTAL_DISCS
INTERVAL = cfg.WATCH_INTERVAL
WINDOW = cfg.WINDOW
ACTIVE_S = cfg.ACTIVE_S
FALLBACK_KB = cfg.FALLBACK_KB

# Restrained palette: PRIMARY marks whatever is active, finished or counted,
# FAIL is red whatever the accent colour is, and everything else is weight
# and brightness (bold, dim, white) rather than hue.
R, B, DIM = C.RESET, C.BOLD, C.DIM
FRAME, TXT, MUTE = C.DIM, C.WHITE, C.DIM
PRIMARY, FAIL = C.PRIMARY, C.RED


# ---- per-dest live state (in memory) ---------------------------------------
_vol_kb: dict = {}          # dest -> this disc's own size in KB, once known
_vol_pending: set = set()   # dest keys with a diskutil lookup already in flight
_calib_samples: list = []   # ripped/expected ratios, learned as discs finish
_calib = 0.92
_learned: set = set()
_growth: dict = {}          # sd (source dir str) -> (last_kb, last_grow_ts)
_history: dict = {}         # sd -> [(ts, kb), ...]
_rate_history: dict = {}    # sd -> [rate, rate, ...]  (sparkline()'s own window)
_folder_counts: dict = {}   # class_file_counts(), refreshed every 15 frames


def dev_for_dest(dest: Path, ps_out: str) -> str:
    target = str(dest)
    for line in ps_out.splitlines():
        if ("makemkvcon" not in line and "cdparanoia" not in line) or target not in line:
            continue
        m = re.search(r"dev:/dev/r(disk\d+)", line) or re.search(r"-d /dev/r(disk\d+)", line)
        if m:
            return m.group(1)
    return ""


def _fetch_disc_kb(key: str, dv: str) -> None:
    try:
        info = subprocess.run(["diskutil", "info", f"/dev/{dv}"], capture_output=True, text=True, timeout=15).stdout
    except subprocess.TimeoutExpired:
        info = ""
    # "Disk Size" is the media's own capacity. "Volume Total Space" often
    # reads 0 here, because rip_video_disc() unmounts the disc to rip it.
    m = re.search(r"Disk Size:.*\((\d+) Bytes\)", info)
    if m:
        _vol_kb[key] = int(int(m.group(1)) / 1024 * _calib)
    _vol_pending.discard(key)


def disc_kb(dest: Path, ps_out: str) -> int:
    """This disc's own reported size in KB, or FALLBACK_KB until it's known.

    `diskutil info` on a drive that is being read can take seconds, so it
    runs in a background thread and later ticks pick up the result from
    _vol_kb, rather than stalling the dashboard.
    """
    key = str(dest)
    if key in _vol_kb:
        return _vol_kb[key]
    dv = dev_for_dest(dest, ps_out)
    if not dv:
        return FALLBACK_KB
    if key not in _vol_pending:
        _vol_pending.add(key)
        threading.Thread(target=_fetch_disc_kb, args=(key, dv), daemon=True).start()
    return FALLBACK_KB


def learn_calib(dest: Path, kb: int, ps_out: str) -> None:
    global _calib
    key = str(dest)
    if key not in _vol_kb or key in _learned:
        return
    if dev_for_dest(dest, ps_out):
        return  # still ripping
    _learned.add(key)
    expected = _vol_kb[key]
    if expected > 0:
        _calib_samples.append(kb / expected)
        _calib = (sum(_calib_samples) / len(_calib_samples)) * 0.92


def sample(sd: str, now: float, kb: int) -> tuple:
    kb_per_s = rate_of_change(_history.setdefault(sd, []), now, kb, WINDOW)
    if kb_per_s is None:
        return "measuring", ""
    mb_s = kb_per_s / 1024
    spark = sparkline(_rate_history.setdefault(sd, []), mb_s)
    return f"{mb_s:.2f} MB/s {(mb_s * 1e6) / DVD_1X_BPS:.1f}x", spark


def hb_pct(log_path: Path) -> int:
    if not log_path.exists():
        return 0
    try:
        tail = log_path.read_bytes()[-6000:].decode(errors="ignore").replace("\r", "\n")
    except OSError:
        return 0
    matches = re.findall(r"(\d+\.\d+) %", tail)
    return int(float(matches[-1])) if matches else 0


FRAMES = 0


def render() -> list:
    global FRAMES
    now = time.time()
    cols = content_width()
    spin = spinner(FRAMES)

    ripped, queued, encd = (count_entries(d) for d in (cfg.DONEDIR, cfg.QUEUE, cfg.ENCDONE))

    if FRAMES % 15 == 0 or not _folder_counts:
        _folder_counts.clear()
        _folder_counts.update(class_file_counts())

    left = f"  BACKCRACK   {cfg.LIBRARY.name}   mode: {cfg.RIP_MODE}"
    right = f"{time.strftime('%H:%M:%S')}  "
    lines = header_box(left, right, cols, spin)
    lines.append("")

    if TOTAL_DISCS > 0:
        pct = ripped * 100 // TOTAL_DISCS
        eta = "calculating"
        if ripped > 0:
            first_start = None
            if cfg.RIPLOG.exists():
                for ln in cfg.RIPLOG.read_text().splitlines():
                    if "START" in ln:
                        try:
                            first_start = time.mktime(time.strptime(ln[:19], "%Y-%m-%d %H:%M:%S"))
                        except ValueError:
                            pass
                        break
            if first_start:
                elapsed_h = (now - first_start) / 3600
                if elapsed_h > 0:
                    remaining = (TOTAL_DISCS - ripped) / (ripped / elapsed_h)
                    eta = f"{int(remaining)}h{int((remaining - int(remaining)) * 60):02d}m left"
        lines.append(f"  {MUTE}DISCS{R}  {bar(pct, 40 if cols > 90 else 24, PRIMARY)}  "
                     f"{B}{ripped}{R}/{TOTAL_DISCS}{TXT}{pct}%{R}  {MUTE}{eta}{R}")
    else:
        lines.append(f"  {MUTE}DISCS{R}   {TXT}{ripped} ripped{R}")

    lines.append(f"  {MUTE}DISK{R}   {TXT}{disk_free(cfg.LIBRARY)} free{R}   {MUTE}library{R} {TXT}{human_gb(dir_size_kb(cfg.LIBRARY))} GB{R}")
    lines.append("")

    # ---- RIPPING ------------------------------------------------------------
    lines.append(f"  {PRIMARY}RIPPING{R}")
    ps_out = ps_listing()   # once per frame: reused for every disc below and by ENCODING
    rows = []
    # Any depth, since a pattern can nest a disc's folder as deep as it likes.
    sources = sorted(p for p in cfg.LIBRARY.rglob("source") if p.is_dir() and ".ripstate" not in p.parts)
    for sd in sources:
        dest = sd.parent
        try:
            rel = str(dest.relative_to(cfg.LIBRARY))
        except ValueError:
            continue
        label = ("?" + dest.name) if "UNSORTED" in dest.parts else rel
        kb = dir_size_kb(sd)
        dv = dev_for_dest(dest, ps_out)
        last_kb, last_grow = _growth.get(str(sd), (None, 0))
        if last_kb is None:
            _growth[str(sd)] = (kb, 0)
            last_grow = 0
        elif kb > last_kb:
            _growth[str(sd)] = (kb, now)
            last_grow = now
        if not dv and (now - last_grow) > ACTIVE_S:
            continue
        tot = disc_kb(dest, ps_out) or FALLBACK_KB
        if kb > tot:
            # The estimate was low. 10% headroom keeps a still-growing rip
            # from sitting at 100%.
            tot = int(kb * 1.1)
        learn_calib(dest, kb, ps_out)
        pct = min(100, kb * 100 // tot) if tot else 0
        if kb == 0:
            rate_s, spark = "analysing", ""
        else:
            rate_s, spark = sample(str(sd), now, kb)
        rows.append((label, dv or "-", pct, human_gb(kb), human_gb(tot), rate_s, spark))

    if not rows:
        lines.append(f"   {DIM}idle, insert a disc{R}")
    else:
        w2 = max(3, max(len(r[1]) for r in rows))
        w3 = max(3, max(len(r[3]) for r in rows))
        w4 = max(3, max(len(r[4]) for r in rows))
        w5 = max(4, max(len(r[5]) for r in rows))
        # Fixed per-row overhead (spacing, "/", "GB", "%", the pointer/pct
        # columns) - everything except the name and the bar, which split
        # whatever's left of `cols` between them instead of a hardcoded cap,
        # so the row can never run past the terminal's actual width.
        fixed = 6 + w2 + w3 + w4 + w5 + 26
        w1 = max(4, min(cols - fixed - 8, max(len(r[0]) for r in rows)))
        bw = max(4, cols - fixed - w1)
        for label, dv, pct, gb, tot_gb, rate_s, spark in rows:
            nm = ("…" + label[-(w1 - 1):]) if len(label) > w1 else label
            lines.append(
                f"   {B}{nm:<{w1}}{R} {DIM}{dv:<{w2}}{R} {bar(pct, bw, PRIMARY)} {TXT}{pct:>3}%{R} "
                f"{TXT}{gb:>{w3}}{R}{MUTE}/{tot_gb:<{w4}} GB{R}  {TXT}{rate_s:>{w5}}{R} {DIM}{spark}{R}"
            )
    lines.append("")

    # ---- ENCODING -------------------------------------------------------------
    folder_counts = "  ".join(f"{MUTE}{folder}{R} {PRIMARY}{n}{R}" for folder, n in _folder_counts.items())
    lines.append(
        f"  {PRIMARY}ENCODING{R}  {MUTE}queue{R} {TXT}{queued}{R}  {MUTE}done{R} {TXT}{encd}{R}  {folder_counts}"
    )
    enc_rows = []
    for m in re.finditer(r"-o (.+?\.mkv) --format", ps_out):
        name = Path(m.group(1)).name.replace(PART + ".mkv", ".mkv")
        pct = hb_pct(cfg.LOGDIR / f"{name}.hb.log")
        enc_rows.append((name, pct))
    if not enc_rows:
        lines.append(f"   {DIM}idle{R}")
    else:
        fixed2 = 6 + 10
        ew = max(8, min(cols - fixed2 - 8, max(len(n) for n, _ in enc_rows)))
        bw2 = max(4, cols - fixed2 - ew)
        for name, pct in enc_rows:
            nm2 = ("…" + name[-(ew - 1):]) if len(name) > ew else name
            lines.append(f"   {TXT}{nm2:<{ew}}{R} {bar(pct, bw2, PRIMARY)} {TXT}{pct:>3}%{R}")
    lines.append("")

    lines.append(f"  {MUTE}RECENT{R}")
    if cfg.RIPLOG.exists():
        for ln in cfg.RIPLOG.read_text().splitlines()[-5:]:
            color = MUTE
            if "OK    " in ln:
                color = PRIMARY
            elif "FAIL  " in ln:
                color = FAIL
            elif "START " in ln:
                color = TXT
            elif "SKIP  " in ln:
                color = DIM
            lines.append(f"   {color}{truncate_text(ln[11:], max(4, cols - 3))}{R}")
    lines.append("")
    lines.append(hint(("q", "quit"), ("s", "settings")))

    FRAMES += 1
    # Clip every line to the terminal's full width so nothing wraps,
    # however narrow the window.
    raw_w = get_terminal_width()
    return [clip_ansi(line, raw_w) for line in lines]


def _current_value_str(name: str, kind: str) -> str:
    v = getattr(cfg, name)
    if kind == "bool":
        return "1" if v else "0"
    if name == "DURATION_CLASSES":
        # Serialise back to _parse_duration_classes()'s own spec grammar.
        return ",".join(
            f"{c.name}:{c.min_s}-{c.max_s}:{c.folder}:{c.quality}" + (":dedup" if c.dedup else "")
            for c in v
        )
    if isinstance(v, list):
        return " ".join(v)
    return str(v)


def _apply_live(name: str, kind: str, new: str) -> None:
    """Apply a changed setting to this running `watch` at once. The daemons
    read settings only at startup, so they need restarting to see it."""
    if name == "LIBRARY":
        cfg.use_library(Path(new).expanduser())
    elif kind == "path":
        setattr(cfg, name, Path(new))
    elif name == "DEINTERLACE_ARGS":
        setattr(cfg, name, new.split())
    elif kind == "bool":
        setattr(cfg, name, new == "1")
    elif kind == "int":
        setattr(cfg, name, int(new))
    elif name == "ACCENT":
        cfg.ACCENT = new
        ui.set_accent(new)
    elif name == "DURATION_CLASSES":
        setattr(cfg, name, cfg._parse_duration_classes(new))
        _folder_counts.clear()
    else:
        setattr(cfg, name, new)


def open_settings() -> None:
    from backbone.prompt import select, text, confirm

    while True:
        rows = [f"{label}  ({_current_value_str(name, kind)})" for name, kind, label in cfg.SETTINGS]
        choice = select("backcrack settings", rows + ["+ add a labels.map entry"])
        if choice is None:
            return
        if choice == "+ add a labels.map entry":
            _add_label_entry()
            continue
        name, kind, label = cfg.SETTINGS[rows.index(choice)]
        cur = _current_value_str(name, kind)
        if kind == "bool":
            new = "1" if confirm(label, default=(cur == "1")) else "0"
        else:
            entered = text(f"{label}  [{name}]", default=cur)
            if entered is None:
                continue
            if kind == "int" and not entered.strip().lstrip("-").isdigit():
                continue
            if kind == "accent" and ui.accent_code(entered.strip()) is None:
                continue                       # not a preset name or #RRGGBB
            entered = entered.strip() if kind == "accent" else entered
            new = entered
        cfg.save_setting(name, new)
        _apply_live(name, kind, new)


def _add_label_entry() -> None:
    """labels.map's manual escape hatch, from inside watch() instead of a
    text editor: pick (or type) a disc label, then fill in whatever %tokens%
    the active pattern needs. See pattern.py's lookup_override()."""
    from backbone.prompt import select, text, confirm

    unsorted = cfg.LIBRARY / "UNSORTED"
    pending = sorted(p.name for p in unsorted.iterdir()) if unsorted.is_dir() else []
    manual = "(type a label manually)"
    label = select("Which disc?", pending + [manual]) if pending else manual
    if label is None:
        return
    if label == manual:
        label = text("Disc label (exact volume label from the log)")
    if not label:
        return

    audio = confirm("Audio disc? (No = video)", default=False)
    pattern = cfg.PATTERN_AUDIO if audio else cfg.PATTERN_VIDEO
    pairs = []
    for tok in re.findall(r"%([a-zA-Z0-9_]+)%", pattern):
        val = text(f"{tok} =")
        if val:
            pairs.append(f"{tok}={val}")
    if not pairs:
        return
    with open(cfg.LABELS_MAP, "a") as f:
        f.write(f"{label}|{','.join(pairs)}\n")


def _on_key(key: str) -> None:
    if key in ("s", "S"):
        open_settings()


def _prompt_stop_daemons() -> None:
    from backbone.prompt import confirm  # deferred: large module, only needed on quit
    if confirm("Stop the ripd, encd and sortd daemons too?", default=False):
        print(f"{stop_daemons('ripd', 'encd', 'sortd')} daemons stopped")
    else:
        print("watcher closed, daemons still running")


def main() -> None:
    if os.environ.get("ONESHOT"):
        print("\n".join(render()))
        return
    run_dashboard(render, interval=INTERVAL, quit_key="q", on_quit=_prompt_stop_daemons, on_key=_on_key)


if __name__ == "__main__":
    main()
