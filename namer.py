#!/usr/bin/env python3
"""namer.py - finalize a completed season for a media server: rename each
disc's encoded/ episodes to "SxxExx Title.mkv" (verified titles from
episodes.map, one season per block, in broadcast order) and move them up
to the season folder directly, and consolidate every disc's extras/ into
one season-level featurettes/. Matches Jellyfin/Kodi's expected layout -
Series/Season XX/SxxExx.ext, extras in a *recognized* type folder
(featurettes/behind the scenes/deleted scenes/etc, not a bare "extras",
which isn't one of them) directly under the season, not nested under a
per-disc folder.

Only touches a season whose encoded-file count exactly matches its title
count - a mismatch means something's still missing or extra (a disc still
ripping, an unencoded title, a wrongly-deduped one), and guessing the
mapping anyway would silently mislabel every episode after the gap. Each
disc folder is removed once empty; leaves anything it doesn't recognize in
place rather than guessing.

    ./namer.py              finalize every season episodes.map covers
    ./namer.py "Season 4"   just one season
"""
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg
from backcrack.encode import PART

MAP_FILE = cfg.EPISODES_MAP


def load_episode_map() -> dict:
    """{"Season 1": ["Pilot", "Paternity", ...], ...}"""
    if not MAP_FILE.exists():
        sys.exit(f"No episodes.map at {MAP_FILE}\n"
                 "Create it: a '# Season N' header per block, then one episode "
                 "title per line in broadcast order.")
    seasons = {}
    current = None
    for line in MAP_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            m = re.match(r"#\s*(Season\s+\d+)\s*$", line, re.IGNORECASE)
            if m:
                current = m.group(1).title()
                seasons[current] = []
            continue
        if current:
            seasons[current].append(line)
    return seasons


def safe(title: str) -> str:
    return title.replace("/", "-").replace(":", " -")


FEATURETTES = "featurettes"   # a Jellyfin/Kodi-recognized extras type - "extras" itself isn't one


def _move_with_thumb(src: Path, dest: Path) -> bool:
    """Moves `src` to `dest`, bringing its matching "<stem>-thumb.jpg" (if
    any) along, renamed to match. False if `dest` already exists."""
    if dest.exists():
        print(f"  !! collision, not moving: {src} -> {dest}")
        return False
    src.rename(dest)
    thumb = src.with_name(src.stem + "-thumb.jpg")
    if thumb.exists():
        thumb.rename(dest.with_name(dest.stem + "-thumb.jpg"))
    return True


def _natural(p: Path) -> list:
    """Sort key that puts "Disc 2" before "Disc 10"."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name)]


def rename_season(season_dir: Path, titles: list) -> None:
    discs = sorted((d for d in season_dir.iterdir() if d.is_dir() and d.name != FEATURETTES), key=_natural)
    files, by_disc = [], {}
    for d in discs:
        enc = d / "encoded"
        if not enc.is_dir():
            continue
        if any(enc.glob(f"*{PART}.mkv")):
            print(f"  SKIP: {enc.relative_to(cfg.LIBRARY)} still has an encode in progress, not touching it")
            return
        by_disc[d] = sorted(enc.glob("*.mkv"), key=_natural)
        files.extend(by_disc[d])
        empty = [f for f in by_disc[d] if f.stat().st_size == 0]
        if empty:
            print(f"  SKIP: {empty[0].relative_to(cfg.LIBRARY)} is empty, not touching it")
            return

    if len(files) != len(titles):
        print(f"  SKIP: {len(files)} encoded files but {len(titles)} titles - counts don't match, not touching it")
        return

    season_num = int(re.search(r"\d+", season_dir.name).group())
    moved = set()
    for i, (f, title) in enumerate(zip(files, titles), start=1):
        new_name = f"S{season_num:02d}E{i:02d} {safe(title)}.mkv"
        print(f"  {f.relative_to(cfg.LIBRARY)} -> {new_name}")
        if _move_with_thumb(f, season_dir / new_name):
            moved.add(f)

    # Consolidate every disc's extras/ into one season-level featurettes/ -
    # Jellyfin looks for a recognized extras-type folder directly under the
    # season, not nested one level deeper under a disc.
    featurettes = season_dir / FEATURETTES
    for d in discs:
        extras = d / "extras"
        if not extras.is_dir():
            continue
        featurettes.mkdir(exist_ok=True)
        for f in sorted(extras.iterdir()):
            if f.name == ".DS_Store":
                f.unlink()
                continue
            if f.name.endswith("-thumb.jpg"):
                continue   # carried along by _move_with_thumb below
            dest = featurettes / f.name
            if dest.exists():
                dest = featurettes / f"{d.name.replace(' ', '')}_{f.name}"
            _move_with_thumb(f, dest)
        if not any(extras.iterdir()):
            extras.rmdir()

    # source/ is the lossless rip. It is deleted only for a disc whose
    # encoded episodes were all moved into the season above (each one
    # non-empty, checked before anything moved); any other disc keeps it.
    for d in discs:
        ds = d / ".DS_Store"
        if ds.exists():
            ds.unlink()
        enc = d / "encoded"
        if enc.is_dir() and not any(enc.iterdir()):
            enc.rmdir()
        src = d / "source"
        if src.is_dir():
            if by_disc.get(d) and all(f in moved for f in by_disc[d]):
                print(f"  deleting {src.relative_to(cfg.LIBRARY)} (the lossless rip; its episodes are encoded)")
                shutil.rmtree(src)
            else:
                print(f"  keeping {src.relative_to(cfg.LIBRARY)}: this disc's episodes weren't all encoded and moved")
        if not any(d.iterdir()):
            d.rmdir()
        else:
            print(f"  {d.relative_to(cfg.LIBRARY)} not empty, leaving it: {list(d.iterdir())}")


def main() -> None:
    seasons = load_episode_map()
    wanted = sys.argv[1:] or list(seasons)
    for name in wanted:
        titles = seasons.get(name)
        if titles is None:
            print(f"no entry for {name!r} in {MAP_FILE.name}")
            continue
        season_dir = cfg.LIBRARY / name
        if not season_dir.is_dir():
            print(f"no {name}/ under {cfg.LIBRARY}")
            continue
        print(f"{name}:")
        rename_season(season_dir, titles)


if __name__ == "__main__":
    main()
