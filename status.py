#!/usr/bin/env python3
"""status.py - where the whole job is up to. Safe to run any time."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg
from backcrack.lib import human_gb, dir_size_kb


def main() -> None:
    ripped = len(list(cfg.DONEDIR.iterdir())) if cfg.DONEDIR.exists() else 0
    queued = len(list(cfg.QUEUE.iterdir())) if cfg.QUEUE.exists() else 0
    enc = len(list(cfg.ENCDONE.iterdir())) if cfg.ENCDONE.exists() else 0
    class_folders = list(dict.fromkeys(c.folder for c in cfg.DURATION_CLASSES))
    folder_n = {
        folder: (sum(1 for _ in cfg.LIBRARY.glob(f"**/{folder}/*")) if cfg.LIBRARY.exists() else 0)
        for folder in class_folders
    }

    print("================ backcrack status ================")
    print(f"  library          {cfg.LIBRARY}")
    print(f"  discs ripped     {ripped}")
    print(f"  awaiting encode  {queued}")
    print(f"  discs encoded    {enc}")
    for folder, n in folder_n.items():
        print(f"  {folder} files{' ' * max(1, 11 - len(folder))}{n}")
    print()
    print(f"  library size     {human_gb(dir_size_kb(cfg.LIBRARY))} GB")
    df = subprocess.run(["df", "-h", str(cfg.LIBRARY)], capture_output=True, text=True).stdout
    free = df.splitlines()[-1].split()[3] if len(df.splitlines()) > 1 else "?"
    print(f"  disk free        {free}")
    print()
    print("-- top-level groups ---------------------------------")
    if cfg.LIBRARY.exists():
        for d in sorted(cfg.LIBRARY.iterdir()):
            if not d.is_dir() or d.name in ("UNSORTED", ".ripstate"):
                continue
            ne = sum(1 for folder in class_folders for _ in d.glob(f"**/{folder}/*"))
            print(f"  {d.name:<28} {ne:>4} files")

    unsorted = cfg.LIBRARY / "UNSORTED"
    if unsorted.is_dir():
        print()
        print("-- UNSORTED (add these to labels.map) ---------------")
        for d in sorted(unsorted.iterdir()):
            print(f"  {d.name}")

    print()
    print("-- running processes --------------------------------")
    out = subprocess.run(
        ["pgrep", "-fl", "ripd.py|encd.py|makemkvcon|HandBrakeCLI|cdparanoia"],
        capture_output=True, text=True,
    ).stdout
    print("\n".join(f"  {l}" for l in out.splitlines()) or "  none")

    print()
    print("-- last 8 rip events -------------------------------")
    if cfg.RIPLOG.exists():
        for ln in cfg.RIPLOG.read_text().splitlines()[-8:]:
            print(f"  {ln}")
    else:
        print("  nothing yet")
    print("====================================================")


if __name__ == "__main__":
    main()
