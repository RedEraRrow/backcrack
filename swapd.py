#!/usr/bin/env python3
"""swapd.py - waits until every drive is genuinely idle, then swaps
ripd.py.new into place and restarts the ripper. Exits after.
Named so that `pkill -f ripd.py` cannot match it.
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg
from backcrack.lib import log, notify

HERE = Path(__file__).resolve().parent
SWAPLOG = cfg.STATE / "swap.log"


def idle() -> bool:
    if any(cfg.STATE.glob("lock-*")):
        return False
    out = subprocess.run(["ps", "-Awwo", "command"], capture_output=True, text=True).stdout
    return not any(("makemkvcon" in l or "cdparanoia" in l) for l in out.splitlines())


def main() -> None:
    new_ripd = HERE / "ripd.py.new"
    if not new_ripd.exists():
        print("nothing staged")
        return

    log(SWAPLOG, "waiting for every drive to go idle before swapping ripd.py")
    waited = 0
    while True:
        if idle():
            time.sleep(5)
            if idle():
                break
        time.sleep(10)
        waited += 10
        if waited >= 14400:
            log(SWAPLOG, "gave up waiting after 4h - ripd.py NOT swapped")
            notify("backcrack - swap not done", "Drives never went idle; ripd.py.new still staged.",
                   "default", "warning")
            return

    log(SWAPLOG, f"drives idle after {waited}s - swapping")
    subprocess.run(["pkill", "-f", "ripd.py"], capture_output=True)
    time.sleep(2)
    try:
        new_ripd.replace(HERE / "ripd.py")
    except OSError:
        log(SWAPLOG, "ERROR mv failed; old ripd.py left in place, restarting it")
        subprocess.Popen([sys.executable, str(HERE / "ripd.py")],
                          stdout=open(cfg.STATE / "ripd.out", "ab"), stderr=subprocess.STDOUT)
        return

    for lock in cfg.STATE.glob("lock-*"):
        lock.unlink(missing_ok=True)
    subprocess.Popen([sys.executable, str(HERE / "ripd.py")],
                      stdout=open(cfg.STATE / "ripd.out", "ab"), stderr=subprocess.STDOUT)
    time.sleep(4)
    running = subprocess.run(["pgrep", "-f", "ripd.py"], capture_output=True).returncode == 0
    if running:
        log(SWAPLOG, "SWAPPED and restarted ripd.py")
        notify("backcrack - ripd.py upgraded", "New version is now running.", "low", "white_check_mark")
    else:
        log(SWAPLOG, "ERROR restarted but ripd.py not running - start it by hand")
        notify("backcrack - ripd.py DOWN", "Swap done but the ripper isn't running. Start it manually.",
               "high", "rotating_light")


if __name__ == "__main__":
    main()
