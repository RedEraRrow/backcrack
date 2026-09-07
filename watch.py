#!/usr/bin/env python3
"""watch.py - live view of the rip + encode pipeline. Works the same for a
CD, DVD or Blu-ray job - the folders it watches (source/, encoded/, extras/)
look the same regardless of format. Percentages are relative to the disc
actually in the drive, read from its own volume size.

    ./watch.py   ·   ONESHOT=1 ./watch.py   ·   NO_COLOR=1 ./watch.py

State (growth history, disc-size calibration) lives in memory for the life
of this process - no /tmp scratch files needed, unlike a shell daemon that
has to persist that across separate invocations.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from backbone.prompt import confirm
from backbone.prompt_core import _get_term_attrs, _hint, _read_key, _restore_term_attrs, _set_raw, _wait_for_keypress
from backbone.ui import Colors as C, SPARK, bar, content_width, header_box, spinner, wrap_margins

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg
from backcrack.lib import human_gb, dir_size_kb

TOTAL_DISCS = int(os.environ.get("TOTAL_DISCS", 0))  # 0 = unknown - hides percent/ETA
INTERVAL = float(os.environ.get("INTERVAL", 1))
WINDOW = int(os.environ.get("WINDOW", 20))
ACTIVE_S = int(os.environ.get("ACTIVE_S", 90))
FALLBACK_KB = int(os.environ.get("FALLBACK_KB", 7340032))

# backtrack's real palette, not backcrack's old standalone 256-color set -
# one visual language across the back* suite (see backbone.ui.Colors).
R, B, DIM = C.RESET, C.BOLD, C.DIM
FRAME, TEAL, AMBER = C.DIM, C.CYAN, C.YELLOW
GREEN, RED, TXT, MUTE = C.GREEN, C.ACCENT, C.WHITE, C.DIM


# ---- per-dest live state (in memory) ---------------------------------------
_vol_kb: dict = {}          # dest -> this disc's own size in KB
_calib_samples: list = []   # ripped/expected ratios, learned as discs finish
_calib = 0.92
_learned: set = set()
_growth: dict = {}          # sd (source dir str) -> (last_kb, last_grow_ts)
_history: dict = {}         # sd -> [(ts, kb), ...]
_rate_history: dict = {}    # sd -> [(ts, rate), ...]
_counts_cache = {"t": 0}
_CLASS_FOLDERS = list(dict.fromkeys(c.folder for c in cfg.DURATION_CLASSES))  # unique, order preserved


def dev_for_dest(dest: Path) -> str:
    try:
        out = subprocess.run(["ps", "-Awwo", "command"], capture_output=True, text=True, timeout=10).stdout
    except subprocess.TimeoutExpired:
        return ""
    target = str(dest)
    for line in out.splitlines():
        if ("makemkvcon" not in line and "cdparanoia" not in line) or target not in line:
            continue
        m = re.search(r"dev:/dev/r(disk\d+)", line) or re.search(r"-d /dev/r(disk\d+)", line)
        if m:
            return m.group(1)
    return ""


def disc_kb(dest: Path) -> int:
    key = str(dest)
    if key in _vol_kb:
        return _vol_kb[key]
    dv = dev_for_dest(dest)
    if not dv:
        return FALLBACK_KB
    try:
        info = subprocess.run(["diskutil", "info", f"/dev/{dv}"], capture_output=True, text=True, timeout=10).stdout
    except subprocess.TimeoutExpired:
        return FALLBACK_KB
    m = re.search(r"Total Space:.*\((\d+) Bytes\)", info)
    if not m:
        return FALLBACK_KB
    kb = int(int(m.group(1)) / 1024 * _calib)
    _vol_kb[key] = kb
    return kb


def learn_calib(dest: Path, kb: int) -> None:
    global _calib
    key = str(dest)
    if key not in _vol_kb or key in _learned:
        return
    if dev_for_dest(dest):
        return  # still ripping
    _learned.add(key)
    expected = _vol_kb[key]
    if expected > 0:
        _calib_samples.append(kb / expected)
        _calib = (sum(_calib_samples) / len(_calib_samples)) * 0.92


def sample(sd: str, now: float, kb: int) -> tuple:
    hist = _history.setdefault(sd, [])
    hist.append((now, kb))
    del hist[:-60]
    base_ts = base_kb = None
    for ts, k in hist:
        if ts >= now - WINDOW:
            base_ts, base_kb = ts, k
            break
    if base_ts is None or now - base_ts <= 0:
        return "measuring", ""
    dt = now - base_ts
    rate = (kb - base_kb) / 1024 / dt
    rh = _rate_history.setdefault(sd, [])
    rh.append((now, rate))
    del rh[:-14]
    mx = max((r for _, r in rh), default=1) or 1
    spark = "".join(SPARK[max(0, min(7, int(r / mx * 7.99)))] for _, r in rh)
    return f"{rate:.2f} MB/s {(rate * 1e6) / 1385000:.1f}x", spark


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


def render() -> None:
    global FRAMES
    now = time.time()
    cols = content_width()
    spin = spinner(FRAMES)

    ripped = len(list(cfg.DONEDIR.iterdir())) if cfg.DONEDIR.exists() else 0
    queued = len(list(cfg.QUEUE.iterdir())) if cfg.QUEUE.exists() else 0
    encd = len(list(cfg.ENCDONE.iterdir())) if cfg.ENCDONE.exists() else 0

    if FRAMES % 15 == 0 or _counts_cache["t"] == 0:
        for folder in _CLASS_FOLDERS:
            _counts_cache[folder] = sum(1 for _ in cfg.LIBRARY.glob(f"**/{folder}/*")) if cfg.LIBRARY.exists() else 0
        _counts_cache["t"] = 1

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
        lines.append(f"  {MUTE}DISCS{R}  {bar(pct, 40 if cols > 90 else 24, TEAL)}  "
                     f"{B}{ripped}{R}/{TOTAL_DISCS}{TXT}{pct}%{R}  {MUTE}{eta}{R}")
    else:
        lines.append(f"  {MUTE}DISCS{R}   {TXT}{ripped} ripped{R}")

    free = subprocess.run(["df", "-h", str(cfg.LIBRARY)], capture_output=True, text=True).stdout
    free_gb = free.splitlines()[-1].split()[3] if len(free.splitlines()) > 1 else "?"
    lines.append(f"  {MUTE}DISK{R}   {TXT}{free_gb} free{R}   {MUTE}library{R} {TXT}{human_gb(dir_size_kb(cfg.LIBRARY))} GB{R}")
    lines.append("")

    # ---- RIPPING ------------------------------------------------------------
    lines.append(f"  {B}{TEAL}RIPPING{R}")
    rows = []
    for sd in sorted(cfg.LIBRARY.glob("*/*/source")) if cfg.LIBRARY.exists() else []:
        dest = sd.parent
        try:
            rel = str(dest.relative_to(cfg.LIBRARY))
        except ValueError:
            continue
        nm = ("…" + rel[-21:]) if len(rel) > 22 else rel
        if "UNSORTED" in dest.parts:
            nm = "?" + dest.name
        kb = dir_size_kb(sd)
        dv = dev_for_dest(dest)
        last_kb, last_grow = _growth.get(str(sd), (None, 0))
        if last_kb is None:
            _growth[str(sd)] = (kb, 0)
            last_grow = 0
        elif kb > last_kb:
            _growth[str(sd)] = (kb, now)
            last_grow = now
        if not dv and (now - last_grow) > ACTIVE_S:
            continue
        tot = disc_kb(dest) or FALLBACK_KB
        learn_calib(dest, kb)
        pct = min(100, kb * 100 // tot) if tot else 0
        if kb == 0:
            rate_s, spark = "analysing", ""
        else:
            rate_s, spark = sample(str(sd), now, kb)
        rows.append((nm, dv or "-", pct, human_gb(kb), human_gb(tot), rate_s, spark))

    if not rows:
        lines.append(f"   {DIM}idle — insert a disc{R}")
    else:
        w1 = max(4, max(len(r[0]) for r in rows))
        w2 = max(3, max(len(r[1]) for r in rows))
        w3 = max(3, max(len(r[3]) for r in rows))
        w4 = max(3, max(len(r[4]) for r in rows))
        w5 = max(4, max(len(r[5]) for r in rows))
        bw = max(8, min(40, cols - 6 - w1 - w2 - w3 - w4 - w5 - 26))
        for nm, dv, pct, gb, tot_gb, rate_s, spark in rows:
            lines.append(
                f"   {B}{nm:<{w1}}{R} {DIM}{dv:<{w2}}{R} {bar(pct, bw, TEAL)} {TXT}{pct:>3}%{R} "
                f"{TXT}{gb:>{w3}}{R}{MUTE}/{tot_gb:<{w4}} GB{R}  {TXT}{rate_s:>{w5}}{R} {TEAL}{spark}{R}"
            )
    lines.append("")

    # ---- ENCODING -------------------------------------------------------------
    folder_counts = "  ".join(f"{MUTE}{folder}{R} {GREEN}{_counts_cache.get(folder, 0)}{R}" for folder in _CLASS_FOLDERS)
    lines.append(
        f"  {B}{AMBER}ENCODING{R}  {MUTE}queue{R} {TXT}{queued}{R}  {MUTE}done{R} {TXT}{encd}{R}  {folder_counts}"
    )
    try:
        ps_out = subprocess.run(["ps", "-Awwo", "command"], capture_output=True, text=True, timeout=10).stdout
    except subprocess.TimeoutExpired:
        ps_out = ""
    enc_rows = []
    for m in re.finditer(r"-o (\S+\.mkv) --format", ps_out):
        out_path = Path(m.group(1))
        pct = hb_pct(cfg.LOGDIR / f"{out_path.name}.hb.log")
        enc_rows.append((out_path.name, pct))
    if not enc_rows:
        lines.append(f"   {DIM}idle{R}")
    else:
        ew = max(8, max(len(n) for n, _ in enc_rows))
        bw2 = max(8, min(40, cols - 6 - ew - 10))
        for name, pct in enc_rows:
            lines.append(f"   {TXT}{name:<{ew}}{R} {bar(pct, bw2, AMBER)} {TXT}{pct:>3}%{R}")
    lines.append("")

    lines.append(f"  {MUTE}RECENT{R}")
    if cfg.RIPLOG.exists():
        for ln in cfg.RIPLOG.read_text().splitlines()[-5:]:
            color = MUTE
            if "OK    " in ln:
                color = GREEN
            elif "FAIL  " in ln:
                color = RED
            elif "START " in ln:
                color = TEAL
            elif "SKIP  " in ln:
                color = AMBER
            lines.append(f"   {color}{ln[11:]}{R}")
    lines.append("")
    lines.append(_hint(("q", "quit")))

    sys.stdout.write(wrap_margins(lines) + "\r\n\033[J")
    sys.stdout.flush()


def _prompt_stop_daemons() -> None:
    if confirm("Stop the ripd/encd daemons too?", default=False):
        subprocess.run(["pkill", "-f", "ripd.py|encd.py"], capture_output=True)
        print("daemons stopped")
    else:
        print("watcher closed — daemons still running")


def main() -> None:
    global FRAMES
    if os.environ.get("ONESHOT"):
        render()
        return

    fd = sys.stdin.fileno()
    is_tty = sys.stdin.isatty()
    old_settings = _get_term_attrs(fd) if is_tty else None
    if is_tty:
        _set_raw(fd)  # full raw mode (unlike cbreak) clears ISIG - Ctrl-C
                      # arrives as the key 'CTRL_C', never as a SIGINT

    sys.stdout.write("\033[?25l\033[2J")
    try:
        while True:
            sys.stdout.write("\033[H")
            render()
            FRAMES += 1
            if is_tty:
                if _wait_for_keypress(INTERVAL) and _read_key(fd) == "q":
                    break
            else:
                time.sleep(INTERVAL)
    finally:
        if is_tty:
            _restore_term_attrs(fd, old_settings)
        sys.stdout.write("\033[?25h\n")

    _prompt_stop_daemons()


if __name__ == "__main__":
    main()
