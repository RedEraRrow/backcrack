#!/usr/bin/env python3
"""cli.py - single entry point. Bare `backcrack` starts ripd + encd in the
background and opens the live TUI - everything you need for a morning of
ripping, one command. Toggle LAUNCH_RIPD / LAUNCH_ENCD / LAUNCH_WATCH in
backcrack/config.py (or as env vars) to run any of them by hand instead.

    backcrack             ripd + encd in the background, then the live TUI
    backcrack ripd
    backcrack encd
    backcrack status
    backcrack watch
    backcrack sortd
    backcrack swapd
    backcrack titles "/path/to/disc"
    backcrack diskspeed

Settings are unchanged - still env vars read by backcrack/config.py
(LIBRARY=, RIP_MODE=, etc.), same as running each tool directly:

    LIBRARY=~/Media/rips backcrack ripd
"""
import argparse
import subprocess
import sys
from pathlib import Path

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
from backcrack.lib import find_daemons

HERE = Path(__file__).resolve().parent

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


def _spawn(script: str) -> None:
    out = cfg.STATE / f"{script.removesuffix('.py')}.out"
    subprocess.Popen([sys.executable, "-u", str(HERE / script)],
                      stdout=open(out, "ab"), stderr=subprocess.STDOUT)


def launch_all() -> None:
    started = []
    for name, wanted in (("ripd", cfg.LAUNCH_RIPD), ("encd", cfg.LAUNCH_ENCD), ("sortd", cfg.LAUNCH_SORTD)):
        if wanted and not find_daemons(name):
            _spawn(f"{name}.py")
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
