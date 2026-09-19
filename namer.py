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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg

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


def _move_with_thumb(src: Path, dest: Path) -> None:
    """Moves `src` to `dest`, carrying its "<stem>-thumb.jpg" sidecar (if
    any) along, renamed to match."""
    if dest.exists():
        print(f"  !! collision, not moving: {src} -> {dest}")
        return
    src.rename(dest)
    thumb = src.with_name(src.stem + "-thumb.jpg")
    if thumb.exists():
        thumb.rename(dest.with_name(dest.stem + "-thumb.jpg"))


def rename_season(season_dir: Path, titles: list) -> None:
    discs = sorted((d for d in season_dir.iterdir() if d.is_dir() and d.name != FEATURETTES),
                   key=lambda d: d.name)
    files = []
    for d in discs:
        enc = d / "encoded"
        if enc.is_dir():
            files.extend(sorted(enc.glob("*.mkv")))

    if len(files) != len(titles):
        print(f"  SKIP: {len(files)} encoded files but {len(titles)} titles - counts don't match, not touching it")
        return

    season_num = int(re.search(r"\d+", season_dir.name).group())
    for i, (f, title) in enumerate(zip(files, titles), start=1):
        new_name = f"S{season_num:02d}E{i:02d} {safe(title)}.mkv"
        dest = season_dir / new_name
        if f.parent == season_dir and f.name == new_name:
            continue
        print(f"  {f.relative_to(cfg.LIBRARY)} -> {new_name}")
        _move_with_thumb(f, dest)

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
        extras.rmdir()

    # Each disc folder should now be empty (encoded/ consumed above,
    # extras/ consumed above) once source/ - the raw, already-encoded rip -
    # is dropped too. Kept until now in case an interrupted encode needed a
    # re-run; a season that just passed the count check above is done.
    import shutil
    for d in discs:
        ds = d / ".DS_Store"
        if ds.exists():
            ds.unlink()
        enc = d / "encoded"
        if enc.is_dir() and not any(enc.iterdir()):
            enc.rmdir()
        src = d / "source"
        if src.is_dir():
            shutil.rmtree(src)
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
