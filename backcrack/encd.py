"""Encode worker. Video jobs go through HandBrake, sorting the
main feature and special features into separate folders; audio jobs get
compressed losslessly. Start it once and leave it; idle is normal. Safe to
stop and restart - finished files are never redone.

    encd
"""
import time
from pathlib import Path

from backbone.log import log as diag

from backcrack import config as cfg
from backcrack.encode import audio_ext, process_job, set_aside
from backcrack.common import log, notify


def run_queue() -> int:
    """One pass over the queue, returning how many jobs ran. A job that
    raises is set aside (encode.set_aside) and pushed, and the rest carry on."""
    ran = 0
    for jobfile in sorted(p for p in cfg.QUEUE.iterdir() if p.is_file()):
        try:
            process_job(jobfile)
            ran += 1
        except Exception as e:
            diag.exception("encode job %s crashed", jobfile.name)
            set_aside(jobfile, f"{type(e).__name__}: {e}")
            notify(f"backcrack - {jobfile.name} not encoded", f"{type(e).__name__}: {e}", "high", "warning")
    return ran


def main() -> None:
    cfg.make_library_dirs()
    if not Path(cfg.HBCLI).exists():
        print(f"WARNING: HandBrakeCLI not found at {cfg.HBCLI} - video jobs will fail. See `backcrack doctor`.")
    if audio_ext() != cfg.AUDIO_FORMAT:
        need = "flac or ffmpeg" if cfg.AUDIO_FORMAT == "flac" else "ffmpeg"
        print(f"WARNING: {need} not found - audio tracks will be copied as .wav. See `backcrack doctor`.")

    classes = ", ".join(f"{c.name} RF={c.quality}->{c.folder}/" for c in cfg.DURATION_CLASSES)
    print(f"Video: {cfg.VIDEO_ENCODER} preset={cfg.ENCODER_PRESET}  {classes}  ({cfg.ENCODE_JOBS} at a time)")
    print(f"Audio: {audio_ext()}  -> encoded/")
    print("Idle until ripd finishes a disc - that's normal.")
    log(cfg.ENCLOG, "encd started")

    try:
        while True:
            if not run_queue():
                time.sleep(15)
    except KeyboardInterrupt:
        print("\nencoder stopped - queue kept in", cfg.QUEUE)


if __name__ == "__main__":
    main()
