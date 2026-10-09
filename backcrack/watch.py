"""Live view of the rip + encode pipeline. Works the same for a
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
import threading
import time
from pathlib import Path

from backbone.nav import NAV_STACK
from backbone import keys
from backbone.log import log as diag
from backbone.prompt.chrome import chrome_room
from backbone.prompt.core import border_right, box_lines, run_dashboard
from backbone.ui import (
    Colors as C, content_width, dir_size_kb, human_gb, progress_cells, rate_of_change,
    sparkline, spinner, truncate_text, visual_len,
)
from backcrack import config as cfg
from backcrack.disc import CD_1X_BPS, DISC_BYTES_FILE, DVD_1X_BPS
from backcrack.encode import PART
from backbone.files import count_entries, disk_free
from backbone.procs import ps_listing
from backcrack.common import class_file_counts, stop_daemons
from backcrack.settings import open_settings

TOTAL_DISCS = cfg.TOTAL_DISCS
INTERVAL = cfg.WATCH_INTERVAL
WINDOW = cfg.WINDOW
ACTIVE_S = cfg.ACTIVE_S
FALLBACK_KB = cfg.FALLBACK_KB

# Restrained palette: PRIMARY marks whatever is active, finished or counted,
# FAIL is red whatever the accent colour is, and everything else is weight
# and brightness (bold, dim, white) rather than hue.
R, B, DIM = C.RESET, C.BOLD, C.DIM
TXT, MUTE = C.WHITE, C.DIM
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
_audio_kb: dict = {}        # dest str -> exact KB of an audio rip, never calibrated
_folder_counts: dict = {}   # class_file_counts(), refreshed every 15 frames


def dev_for_dest(dest: Path, ps_out: str) -> str:
    target = str(dest)
    for line in ps_out.splitlines():
        if ("makemkvcon" not in line and "paranoia" not in line) or target not in line:
            continue
        m = re.search(r"dev:/dev/r(disk\d+)", line) or re.search(r"-d /dev/r(disk\d+)", line)
        if m:
            return m.group(1)
    return ""


def _fetch_disc_kb(key: str, dv: str) -> None:
    try:
        info = subprocess.run(["diskutil", "info", f"/dev/{dv}"], capture_output=True, text=True, timeout=15).stdout
    except subprocess.TimeoutExpired:
        diag.warning("diskutil info /dev/%s timed out; %s keeps the assumed disc size", dv, key)
        info = ""
    # "Disk Size" is the media's own capacity. "Volume Total Space" often
    # reads 0 here, because rip_video_disc() unmounts the disc to rip it.
    m = re.search(r"Disk Size:.*\((\d+) Bytes\)", info)
    if m:
        _vol_kb[key] = int(int(m.group(1)) / 1024 * _calib)
    _vol_pending.discard(key)


def disc_kb(dest: Path, ps_out: str) -> int:
    """This disc's own reported size in KB, or FALLBACK_KB until it's known.
    An audio CD's is exact, from the size its rip recorded.

    `diskutil info` on a drive that is being read can take seconds, so it
    runs in a background thread and later ticks pick up the result from
    _vol_kb, rather than stalling the dashboard.
    """
    key = str(dest)
    if key in _audio_kb:
        return _audio_kb[key]
    try:
        _audio_kb[key] = int((dest / DISC_BYTES_FILE).read_text()) // 1024
        return _audio_kb[key]
    except (OSError, ValueError):
        pass
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


def sample(sd: str, now: float, kb: int, one_x_bps: int) -> tuple:
    kb_per_s = rate_of_change(_history.setdefault(sd, []), now, kb, WINDOW)
    if kb_per_s is None:
        return "measuring", ""
    mb_s = kb_per_s / 1024
    spark = sparkline(_rate_history.setdefault(sd, []), mb_s)
    return f"{mb_s:.2f} MB/s {(mb_s * 1e6) / one_x_bps:.1f}x", spark


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


def _hints() -> list:
    return [(keys.label("watch.quit", first=True), "quit"),
            (keys.label("watch.settings", first=True), "settings")]


def _stack(boxes: list, room: int, width: int) -> list:
    """`boxes`, each (rank, title, right, lines, keep_end), drawn top to bottom
    in `room` rows. Rows go to the lowest rank first; a box left fewer than
    three (its borders and one line) isn't drawn at all, and one left short of
    its lines shows its first ones, or with keep_end its last."""
    heights, left = {}, room
    for i in sorted(range(len(boxes)), key=lambda i: boxes[i][0]):
        h = min(len(boxes[i][3]) + 2, left)
        if h >= 3:
            heights[i] = h
            left -= h
    out = []
    for i, (_rank, title, right, lines, keep_end) in enumerate(boxes):
        if i in heights:
            shown = lines[len(lines) - (heights[i] - 2):] if keep_end else lines
            out += box_lines(shown, width, heights[i], title, right)
    return out


def _eta(now: float, ripped: int) -> str:
    if ripped <= 0 or not cfg.RIPLOG.exists():
        return "calculating"
    first_start = None
    for ln in cfg.RIPLOG.read_text().splitlines():
        if "START" in ln:
            try:
                first_start = time.mktime(time.strptime(ln[:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                pass
            break
    elapsed_h = (now - first_start) / 3600 if first_start else 0
    if elapsed_h <= 0:
        return "calculating"
    remaining = (TOTAL_DISCS - ripped) / (ripped / elapsed_h)
    return f"{int(remaining)}h{int((remaining - int(remaining)) * 60):02d}m left"


def _first_fitting(options: list, room: int) -> str:
    """The first of `options` no wider than `room` columns, else the last."""
    return next((o for o in options if visual_len(o) <= room), options[-1])


def _overview_lines(now: float, ripped: int, inner: int) -> list:
    """Discs and Disk, each giving up whole parts from its end when short of room."""
    if TOTAL_DISCS > 0:
        pct = ripped * 100 // TOTAL_DISCS
        count = f" {B}{ripped}{R}/{TOTAL_DISCS}"
        tail = _first_fitting([f"{count}  {TXT}{pct}%{R}  {MUTE}{_eta(now, ripped)}{R}",
                               f"{count}  {TXT}{pct}%{R}", count], inner - 7 - 4)
        bw = max(4, min(40, inner - 7 - visual_len(tail)))
        discs = f"{MUTE}Discs{R}  {progress_cells(pct / 100, bw)}{tail}"
    else:
        discs = f"{MUTE}Discs{R}  {TXT}{ripped} ripped{R}"
    free = f"{MUTE}Disk{R}   {TXT}{disk_free(cfg.LIBRARY)} free{R}"
    disk = _first_fitting([f"{free}   {MUTE}library{R} {TXT}{human_gb(dir_size_kb(cfg.LIBRARY))} GB{R}", free], inner)
    return [discs, disk]


def _ripping_lines(now: float, ps_out: str, inner: int) -> list:
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
            rate_s, spark = sample(str(sd), now, kb, CD_1X_BPS if str(dest) in _audio_kb else DVD_1X_BPS)
        rows.append((label, dv or "-", pct, human_gb(kb), human_gb(tot), rate_s, spark))

    if not rows:
        return [f"{DIM}idle, insert a disc{R}"]
    w2 = max(3, max(len(r[1]) for r in rows))
    w3 = max(3, max(len(r[3]) for r in rows))
    w4 = max(3, max(len(r[4]) for r in rows))
    w5 = max(4, max(len(r[5]) for r in rows))
    # Everything but the name and the bar (spacing, "/", "GB", "%", the
    # sparkline), which split what's left of the box between them.
    fixed = w2 + w3 + w4 + w5 + 29
    w1 = max(4, min(inner - fixed - 8, max(len(r[0]) for r in rows)))
    bw = max(4, inner - fixed - w1)
    lines = []
    for label, dv, pct, gb, tot_gb, rate_s, spark in rows:
        nm = truncate_text(label, w1, front=True)
        lines.append(
            f"{B}{nm:<{w1}}{R} {DIM}{dv:<{w2}}{R} {progress_cells(pct / 100, bw)} {TXT}{pct:>3}%{R} "
            f"{TXT}{gb:>{w3}}{R}{MUTE}/{tot_gb:<{w4}} GB{R}  {TXT}{rate_s:>{w5}}{R} {DIM}{spark}{R}"
        )
    return lines


def _encoding_lines(ps_out: str, inner: int) -> list:
    rows = []
    for m in re.finditer(r"-o (.+?\.mkv) --format", ps_out):
        name = Path(m.group(1)).name.replace(PART + ".mkv", ".mkv")
        rows.append((name, hb_pct(cfg.LOGDIR / f"{name}.hb.log")))
    if not rows:
        return [f"{DIM}idle{R}"]
    ew = max(8, min(inner - 6 - 8, max(len(n) for n, _ in rows)))
    bw = max(4, inner - 6 - ew)
    return [f"{TXT}{truncate_text(name, ew, front=True):<{ew}}{R} {progress_cells(pct / 100, bw)} {TXT}{pct:>3}%{R}"
            for name, pct in rows]


def _recent_lines(inner: int) -> list:
    if not cfg.RIPLOG.exists():
        return [f"{DIM}nothing yet{R}"]
    lines = []
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
        lines.append(f"{color}{truncate_text(ln[11:], inner)}{R}")
    return lines


def render() -> list:
    """One frame: the overview, what's ripping, what's encoding and the latest
    rip events, each in its own box. Short of rows, Recent goes first, then
    the overview, then Encoding."""
    global FRAMES
    now = time.time()
    width = content_width()
    inner = max(1, width - 4)

    ripped, queued, encd = (count_entries(d) for d in (cfg.DONEDIR, cfg.QUEUE, cfg.ENCDONE))
    if FRAMES % 15 == 0 or not _folder_counts:
        _folder_counts.clear()
        _folder_counts.update(class_file_counts())
    counts = " · ".join([f"queue {queued}", f"done {encd}", *(f"{f} {n}" for f, n in _folder_counts.items())])
    clock = f"{spinner(FRAMES)} {time.strftime('%H:%M:%S')}"
    ps_out = ps_listing()   # once per frame: reused for every disc and encode below
    FRAMES += 1

    return _stack([
        (2, f"backcrack · {cfg.LIBRARY.name} · {cfg.RIP_MODE}", border_right(clock, True),
         _overview_lines(now, ripped, inner), False),
        (0, "Ripping", "", _ripping_lines(now, ps_out, inner), False),
        (1, "Encoding", border_right(counts, None), _encoding_lines(ps_out, inner), False),
        (3, "Recent", "", _recent_lines(inner), True),
    ], chrome_room(_hints()), width)


keys.define("watch", "Watch", [
    ("quit", ("q", "Q"), "close the watcher"),
    ("settings", ("s", "S"), "settings"),
])


def _on_key(key: str) -> None:
    if keys.pressed(key, "watch.settings"):
        open_settings()
        _folder_counts.clear()          # the duration classes may have changed


def _prompt_stop_daemons() -> None:
    from backbone.prompt import confirm  # deferred: large module, only needed on quit
    if confirm("Stop the ripd, encd and sortd daemons too?", default=False):
        print(f"{stop_daemons('ripd', 'encd', 'sortd')} daemons stopped")
    else:
        print("watcher closed, daemons still running")


def main() -> None:
    NAV_STACK.clear()       # one screen: nothing to say where you are
    if os.environ.get("ONESHOT"):
        print("\n".join(render()))
        return
    run_dashboard(render, interval=INTERVAL, quit_action="watch.quit", on_quit=_prompt_stop_daemons,
                  on_key=_on_key, hints=_hints)


if __name__ == "__main__":
    main()
