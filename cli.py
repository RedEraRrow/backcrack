#!/usr/bin/env python3
"""cli.py - Bare `backcrack` starts ripd, encd and sortd in the background
and opens the live TUI. Toggle LAUNCH_RIPD / LAUNCH_ENCD / LAUNCH_SORTD /
LAUNCH_WATCH in settings.env (or as env vars) to run any of them by hand instead.

    backcrack             ripd + encd + sortd in the background, then the live TUI
    backcrack ripd
    backcrack encd
    backcrack status
    backcrack watch
    backcrack sortd
    backcrack swapd
    backcrack titles "/path/to/disc"
    backcrack diskspeed
    backcrack namer ["Season N"]

Settings come from env vars, then settings.env, then backcrack/config.py's
defaults, the same as running each tool directly:

    LIBRARY=~/Media/rips backcrack ripd
"""
import argparse
import sys

import diskspeed
import encd
import namer
import ripd
import sortd
import status
import swapd
import titles
import watch
from backcrack import config as cfg
from backcrack.lib import find_daemons, spawn_daemon

COMMANDS = {
    "ripd": ripd.main,
    "encd": encd.main,
    "sortd": sortd.main,
    "watch": watch.main,
    "status": status.main,
    "swapd": swapd.main,
    "titles": titles.main,
    "diskspeed": diskspeed.main,
    "namer": namer.main,
}


def launch_all() -> None:
    started = []
    for name, wanted in (("ripd", cfg.LAUNCH_RIPD), ("encd", cfg.LAUNCH_ENCD), ("sortd", cfg.LAUNCH_SORTD)):
        if wanted and not find_daemons(name):
            spawn_daemon(name)
            started.append(name)

    if cfg.LAUNCH_WATCH:
        watch.main()
        return

    if started:
        print(f"{' + '.join(started)} launched in the background - logs in {cfg.STATE}/*.out")
    print("`backcrack watch` to view, `backcrack status` to check.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="backcrack", description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS, nargs="?", default=None)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    parsed = parser.parse_args()

    if parsed.command is None:
        launch_all()
        return

    sys.argv = [parsed.command] + parsed.args
    COMMANDS[parsed.command]()


if __name__ == "__main__":
    main()
