#!/usr/bin/env python3
"""ripd.py - watch every optical drive, rip whatever's in it (CD, DVD, or
Blu-ray - detected automatically), sort it into the library, eject, ping
ntfy, hand it to the encoder. Ctrl-C to stop.

    ./ripd.py

Insert discs in any drive, in any order. Already-ripped discs eject at once.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg
from backcrack import disc
from backcrack.rip import rip_video_disc, rip_audio_disc
from backcrack.lib import log


def _lock_path(dev: str) -> Path:
    return cfg.STATE / f"lock-{Path(dev).name}"


def _try_lock(dev: str):
    """Atomically claims the lock for `dev`, or None if it's already held.

    O_CREAT|O_EXCL is one atomic syscall - unlike the old exists()-then-
    touch() check, two ripd polls (even from two separate ripd processes
    accidentally running against the same $LIBRARY - `backcrack ripd` run
    directly alongside the one bare `backcrack` already spawned, say) can't
    both "win" the same drive: whichever's os.open() actually reaches the
    filesystem first succeeds, the other gets FileExistsError. That race
    is exactly what let two makemkvcon instances read the same disc at
    once - drive noise "like two reads out of sync" was two reads, out of
    sync, for real.
    """
    path = _lock_path(dev)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.close(fd)
    return path


def main() -> None:
    if not Path(cfg.MKVCON).exists():
        print(f"WARNING: makemkvcon not found at {cfg.MKVCON} - video discs will fail.")
    if not cfg.CDPARANOIA:
        print("WARNING: cdparanoia not found - audio CDs will fail. brew install cdparanoia.")
    if not cfg.NTFY_TOPIC:
        print("WARNING: NTFY_TOPIC unset in config.py - no pushes will be sent.")

    log(cfg.RIPLOG, f"ripd started - library: {cfg.LIBRARY}  video mode: {cfg.RIP_MODE}")
    print("Watching for discs. Insert them in any drive, in any order.\n")

    import threading
    try:
        while True:
            for dev, kind, label in disc.pending_discs():
                lock = _try_lock(dev)
                if lock is None:
                    continue
                target = rip_audio_disc if kind == "audio" else rip_video_disc

                def _run(dev=dev, label=label, target=target, lock=lock):
                    try:
                        target(dev, label)
                    finally:
                        lock.unlink(missing_ok=True)

                threading.Thread(target=_run, daemon=True).start()
            time.sleep(5)
    except KeyboardInterrupt:
        for lock in cfg.STATE.glob("lock-*"):
            lock.unlink(missing_ok=True)
        print("\nstopped - progress kept in", cfg.STATE)


if __name__ == "__main__":
    main()
