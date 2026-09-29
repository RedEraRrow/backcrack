"""Upgrades a running ripper without interrupting a rip. Stage
the new version as ripd.py.new beside ripd.py, run swapd, and it waits until
no drive is ripping, swaps ripd.py.new into place and restarts ripd. Exits
after.
"""
import time
from pathlib import Path

from backcrack import config as cfg
from backbone.procs import ps_listing
from backcrack.common import find_daemons, log, notify, spawn_daemon, stop_daemons

HERE = Path(__file__).resolve().parent
SWAPLOG = cfg.STATE / "swap.log"


def idle() -> bool:
    if any(cfg.STATE.glob("lock-*")):
        return False
    return not any(("makemkvcon" in l or "cdparanoia" in l) for l in ps_listing().splitlines())


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
    stop_daemons("ripd")
    time.sleep(2)
    try:
        new_ripd.replace(HERE / "ripd.py")
    except OSError:
        log(SWAPLOG, "ERROR mv failed; old ripd.py left in place, restarting it")
        spawn_daemon("ripd")
        return

    for lock in cfg.STATE.glob("lock-*"):
        lock.unlink(missing_ok=True)
    spawn_daemon("ripd")
    time.sleep(4)
    if find_daemons("ripd"):
        log(SWAPLOG, "SWAPPED and restarted ripd.py")
        notify("backcrack - ripd.py upgraded", "New version is now running.", "low", "white_check_mark")
    else:
        log(SWAPLOG, "ERROR restarted but ripd.py not running - start it by hand")
        notify("backcrack - ripd.py DOWN", "Swap done but the ripper isn't running. Start it manually.",
               "high", "rotating_light")


if __name__ == "__main__":
    main()
