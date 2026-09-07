#!/usr/bin/env python3
"""diskspeed.py - measure sustained read throughput of every loaded optical
drive. Works for CD, DVD, or Blu-ray - it's a raw block read, format doesn't
matter. Reports live progress and detects a stalled read instead of hanging
silently.

    ./diskspeed.py

Put the SAME disc in each drive in turn so the comparison is fair.
Results accumulate in ~/diskspeed.txt
"""
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

READ_MB = int(os.environ.get("READ_MB", 600))
SKIP_MB = int(os.environ.get("SKIP_MB", 1000))
STALL_S = int(os.environ.get("STALL_S", 60))
DISC_BYTES = int(os.environ.get("DISC_BYTES", 7_000_000_000))
RESULTS = Path(os.environ.get("RESULTS", str(Path.home() / "diskspeed.txt")))
MKV = os.environ.get("MKV", "/Applications/MakeMKV.app/Contents/MacOS/makemkvcon")


def hr() -> None:
    print("-" * 70)


def cooked(dev: str) -> str:
    return "/dev/" + dev.removeprefix("/dev/r")


def drives():
    out = subprocess.run([MKV, "-r", "--cache=1", "info", "disc:9999"],
                          capture_output=True, text=True, timeout=30).stdout
    rows = []
    for line in out.splitlines():
        m = re.match(r'^DRV:(\d+),2,\d+,\d+,"([^"]*)","([^"]*)","([^"]*)"', line)
        if m:
            rows.append(m.groups())
    return rows


def main() -> None:
    if not Path(MKV).exists():
        print(f"makemkvcon not found at {MKV}", file=sys.stderr)
        sys.exit(1)

    disc_drives = drives()
    if not disc_drives:
        print("No discs loaded. Insert a disc in each drive and re-run.", file=sys.stderr)
        sys.exit(1)

    sudo = []
    first_raw = disc_drives[0][3]
    if subprocess.run(["dd", f"if={first_raw}", "of=/dev/null", "bs=2048", "count=1"],
                      capture_output=True).returncode != 0:
        print("Raw device reads need admin rights - password prompt incoming.")
        if subprocess.run(["sudo", "-v"]).returncode != 0:
            sys.exit(1)
        sudo = ["sudo"]

    print(f"\nReading {READ_MB}MB from each drive starting at {SKIP_MB}MB.")
    print("Progress prints every 5s. Ctrl-C is safe at any point.")

    for idx, name, label, raw in disc_drives:
        short = name[:40]
        hr()
        print(short)
        print(f"  disc: {label or '<unlabelled>'}   device: {raw}")

        subprocess.run(["diskutil", "unmountDisk", cooked(raw)], capture_output=True)

        tmp = tempfile.NamedTemporaryFile(prefix="diskspeed", delete=False)
        tmp.close()
        with open(tmp.name, "wb") as errf:
            proc = subprocess.Popen(
                ["dd", f"if={raw}", "of=/dev/null", "bs=1m", f"count={READ_MB}", f"skip={SKIP_MB}"],
                stderr=errf,
            )

        last_bytes = stalled = quiet = 0
        while proc.poll() is None:
            time.sleep(5)
            try:
                proc.send_signal(signal.SIGINFO)
            except (AttributeError, ProcessLookupError):
                pass
            time.sleep(1)
            text = Path(tmp.name).read_text(errors="ignore")
            lines = [l for l in text.splitlines() if "bytes transferred" in l]
            b = 0
            if lines:
                m = re.match(r"^(\d+) bytes", lines[-1])
                if m:
                    b = int(m.group(1))

            if b > last_bytes:
                last_bytes, quiet = b, 0
                m = re.search(r"in ([\d.]+) secs", lines[-1])
                t = float(m.group(1)) if m else 0
                if t > 0:
                    print(f"   {b/1e6:6.0f} MB   {b/1e6/t:6.2f} MB/s   {b/1385000/t:4.1f}x")
            else:
                quiet += 6
                if quiet >= STALL_S:
                    print(f"   STALLED at {last_bytes // 1_000_000}MB - no progress for {quiet}s, aborting")
                    proc.kill()
                    stalled = 1
                    break
                print(f"   ...no progress for {quiet}s")
        proc.wait()

        text = Path(tmp.name).read_text(errors="ignore")
        os.unlink(tmp.name)
        final = [l for l in text.splitlines() if "bytes transferred" in l]
        bps = None
        if final:
            m = re.search(r"\((\d+) bytes/sec\)", final[-1])
            if m:
                bps = int(m.group(1))

        if stalled or bps is None:
            print("  RESULT: no clean measurement (likely bad/protected sectors - try SKIP_MB=4000)")
            continue

        out_line = (f"{short:<42} {label or '?':<20} {bps/1e6:6.2f} MB/s  {bps/1385000:4.1f}x  "
                    f"~{(DISC_BYTES/bps)/60:.0f} min/disc")
        print(f"  RESULT: {out_line}")
        with open(RESULTS, "a") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {out_line}\n")

    hr()
    print(f"Saved to {RESULTS}")
    print("Now swap that disc into the other drive and re-run to compare like with like.")


if __name__ == "__main__":
    main()
