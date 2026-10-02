"""Watches UNSORTED/ and files discs into their pattern-resolved
destination as soon as they become resolvable (you added a labels.map line,
or the parser improved). Merges a split disc back together if one ever occurs.

    sortd                   daemon, polls every SORT_INTERVAL seconds (default 5)
    ONCE=1 sortd            single pass
    DRYRUN=1 sortd          say what it would do, change nothing
"""
import os
import time

from backcrack import config as cfg
from backcrack.sort import reconcile, SORTLOG
from backcrack.common import log

INTERVAL = cfg.SORT_INTERVAL


def main() -> None:
    cfg.make_library_dirs()
    dry_run = bool(os.environ.get("DRYRUN"))
    once = bool(os.environ.get("ONCE")) or dry_run

    if once:
        reconcile(dry_run=dry_run)
        return

    log(SORTLOG, f"sortd started - polling UNSORTED every {INTERVAL}s")
    print(f"Watching UNSORTED. Add a labels.map line and it files itself within {INTERVAL}s.")
    try:
        while True:
            reconcile()
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\nsorter stopped")


if __name__ == "__main__":
    main()
