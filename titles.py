#!/usr/bin/env python3
"""titles.py - show every title on a video disc (or a ripped disc folder)
and how the current thresholds classify it. Run this on the first disc of
each kind before trusting a whole shelf of them. Video discs only - an audio
CD's tracks are unambiguous; list them with `cdparanoia -Q -d <device>`.

    ./titles.py "/path/to/library/Season 1/Disc 1"
    ./titles.py /Volumes/SOME_LABEL             # works on a mounted disc too
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg
from backcrack.lib import classify, file_duration, hb_json


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: ./titles.py '<disc folder or /Volumes/LABEL>'", file=sys.stderr)
        sys.exit(1)
    target = Path(sys.argv[1])
    src = target / "VIDEO_TS" if (target / "VIDEO_TS").is_dir() else target

    print(f"Scanning {src}")
    windows = " | ".join(f"{c.name} {c.min_s}-{c.max_s}s" for c in cfg.DURATION_CLASSES)
    print(f"Duration classes: {windows} | anything else: skip")
    print()

    d = src / "source" if (src / "source").is_dir() else src
    if (src / "source").is_dir() or list(src.glob("*.mkv")):
        print(f"{'FILE':<40} {'SECONDS':<9} CLASS")
        print("-" * 67)
        for f in sorted(d.glob("*.mkv")):
            secs = file_duration(str(f))
            print(f"{f.name:<40} {secs:<9} {classify(secs)}")
        return

    data = hb_json(str(src))
    titles = data.get("TitleList")
    if not titles:
        print("No title list returned.", file=sys.stderr)
        sys.exit(1)

    print(f"{'TITLE':<7} {'DURATION':<11} {'SECONDS':<9} {'CHAPS':<7} {'AUDIO':<7} CLASS")
    print("-" * 71)
    for t in titles:
        d_ = t.get("Duration", {})
        h, m, s = d_.get("Hours", 0), d_.get("Minutes", 0), d_.get("Seconds", 0)
        secs = h * 3600 + m * 60 + s
        chaps = len(t.get("ChapterList", []))
        aud = len(t.get("AudioList", []))
        print(f"{t.get('Index'):<7} {f'{h}h{m}m{s}s':<11} {secs:<9} {chaps:<7} {aud:<7} {classify(secs)}")
    print()
    routes = "   ".join(f"{c.name} -> {c.folder}/" for c in cfg.DURATION_CLASSES)
    print(f"{routes}   skip -> not encoded")


if __name__ == "__main__":
    main()
