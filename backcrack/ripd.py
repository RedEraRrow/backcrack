"""Watch every optical drive, rip whatever's in it (CD, DVD, or
Blu-ray - detected automatically), sort it into the library, eject, ping
ntfy, hand it to the encoder. Ctrl-C to stop.

    ripd

Insert discs in any drive, in any order. Already-ripped discs eject at once.
"""
import os
import time
from pathlib import Path

from backcrack import config as cfg
from backcrack import disc
from backcrack.rip import rip_video_disc, rip_audio_disc
from backcrack.common import find_daemons, log


def _lock_path(dev: str) -> Path:
    return cfg.STATE / f"lock-{Path(dev).name}"


def _try_lock(dev: str):
    """Claims the lock for `dev`, or returns None if it's already held.

    O_CREAT|O_EXCL makes the claim atomic, so two polls (or two ripd
    processes on the same $LIBRARY) can never both rip the same drive.
    """
    path = _lock_path(dev)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.close(fd)
    return path


def main() -> None:
    cfg.make_library_dirs()
    if not Path(cfg.MKVCON).exists():
        print(f"WARNING: makemkvcon not found at {cfg.MKVCON} - video discs will fail. See `backcrack doctor`.")
    if not cfg.CDPARANOIA:
        print("WARNING: cd-paranoia not found - audio CDs will fail. See `backcrack doctor`.")
    if not cfg.NTFY_TOPIC:
        print("WARNING: NTFY_TOPIC is not set (env var or settings.env) - no pushes will be sent.")

    # A lock left by a ripd that was killed rather than stopped with Ctrl-C
    # would block its drive for good. With no other ripd running, every lock
    # is stale.
    if not find_daemons("ripd"):
        for lock in cfg.STATE.glob("lock-*"):
            lock.unlink(missing_ok=True)

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
