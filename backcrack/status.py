"""Where the whole job is up to. Safe to run any time."""
import subprocess

from backcrack import config as cfg
from backbone.ui import dir_size_kb, human_gb
from backbone.files import count_entries, disk_free
from backcrack.common import class_file_counts, find_daemons


def main() -> None:
    ripped, queued, enc = (count_entries(d) for d in (cfg.DONEDIR, cfg.QUEUE, cfg.ENCDONE))
    folder_n = class_file_counts()

    print("================ backcrack status ================")
    print(f"  library          {cfg.LIBRARY}")
    print(f"  discs ripped     {ripped}")
    print(f"  awaiting encode  {queued}")
    print(f"  discs encoded    {enc}")
    for folder, n in folder_n.items():
        print(f"  {folder} files{' ' * max(1, 11 - len(folder))}{n}")
    print()
    print(f"  library size     {human_gb(dir_size_kb(cfg.LIBRARY))} GB")
    print(f"  disk free        {disk_free(cfg.LIBRARY)}")
    print()
    print("-- top-level groups ---------------------------------")
    if cfg.LIBRARY.exists():
        for d in sorted(cfg.LIBRARY.iterdir()):
            if not d.is_dir() or d.name in ("UNSORTED", ".ripstate"):
                continue
            ne = sum(1 for folder in folder_n for _ in d.glob(f"**/{folder}/*"))
            print(f"  {d.name:<28} {ne:>4} files")

    unsorted = cfg.LIBRARY / "UNSORTED"
    if unsorted.is_dir():
        print()
        print("-- UNSORTED (add these to labels.map) ---------------")
        for d in sorted(unsorted.iterdir()):
            print(f"  {d.name}")

    print()
    print("-- running processes --------------------------------")
    procs = [f"{pid} {command}" for pid, command in find_daemons("ripd", "encd", "sortd", "swapd")]
    procs += subprocess.run(
        ["pgrep", "-fl", "makemkvcon|HandBrakeCLI|paranoia"], capture_output=True, text=True,
    ).stdout.splitlines()
    print("\n".join(f"  {l}" for l in procs) or "  none")

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
