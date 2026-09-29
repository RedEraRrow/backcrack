#!/usr/bin/env python3
"""encd.py - encode worker. Video jobs go through HandBrake, sorting the
main feature and special features into separate folders; audio jobs get
compressed losslessly. Start it once and leave it; idle is normal. Safe to
stop and restart - finished files are never redone.

    encd
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backcrack import config as cfg
from backcrack.encode import audio_ext, process_job
from backcrack.lib import log


def main() -> None:
    if not Path(cfg.HBCLI).exists():
        print(f"WARNING: HandBrakeCLI not found at {cfg.HBCLI} - video jobs will fail.")
    if audio_ext() != cfg.AUDIO_FORMAT:
        need = "flac or ffmpeg" if cfg.AUDIO_FORMAT == "flac" else "ffmpeg"
        print(f"WARNING: {need} not found - audio tracks will be copied as .wav. brew install {need.split()[0]}.")

    classes = ", ".join(f"{c.name} RF={c.quality}->{c.folder}/" for c in cfg.DURATION_CLASSES)
    print(f"Video: {cfg.VIDEO_ENCODER} preset={cfg.ENCODER_PRESET}  {classes}  ({cfg.ENCODE_JOBS} at a time)")
    print(f"Audio: {audio_ext()}  -> encoded/")
    print("Idle until ripd finishes a disc - that's normal.")
    log(cfg.ENCLOG, "encd started")

    try:
        while True:
            jobfiles = [p for p in cfg.QUEUE.iterdir() if p.is_file()]
            for jobfile in jobfiles:
                process_job(jobfile)
            if not jobfiles:
                time.sleep(15)
    except KeyboardInterrupt:
        print("\nencoder stopped - queue kept in", cfg.QUEUE)


if __name__ == "__main__":
    main()
