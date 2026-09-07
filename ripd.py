#!/usr/bin/env python3
"""ripd.py - watch every optical drive, rip whatever's in it (CD, DVD, or
Blu-ray - detected automatically), sort it into the library, eject, ping
ntfy, hand it to the encoder. Ctrl-C to stop.

    ./ripd.py

Insert discs in any drive, in any order. Already-ripped discs eject at once.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg
from backcrack import disc
from backcrack.rip import rip_video_disc, rip_audio_disc
from backcrack.lib import log

_LOCKS = set()


def _lock_path(dev: str) -> Path:
    return cfg.STATE / f"lock-{Path(dev).name}"


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
                lock = _lock_path(dev)
                if lock.exists():
                    continue
                lock.touch()
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
