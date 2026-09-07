"""encode.py - turn a ripped disc's source/ into encoded/ (and extras/ for
video). Shared by encd.py.
"""
import shutil
import subprocess
from pathlib import Path

from . import config as cfg
from .lib import log, notify, classify_class, hb_titles, file_duration


def encode_video_one(src: str, title, out: Path, quality: str) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [cfg.HBCLI, "-i", src]
    if title is not None:
        cmd += ["-t", str(title)]
    cmd += [
        "-o", str(out),
        "--format", "av_mkv",
        "--encoder", cfg.VIDEO_ENCODER, "--encoder-preset", cfg.ENCODER_PRESET, "--quality", quality,
        *cfg.DEINTERLACE_ARGS,
        "--all-audio", "--aencoder", "copy", "--audio-fallback", "ca_aac",
        "--all-subtitles", "--markers",
    ]
    log_path = cfg.LOGDIR / f"{out.name}.hb.log"
    with open(log_path, "ab") as logf:
        return subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=None).returncode


def encode_audio_one(src: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    log_path = cfg.LOGDIR / f"{out.name}.enc.log"
    if cfg.FLAC and cfg.AUDIO_FORMAT == "flac":
        cmd = [cfg.FLAC, "-s", "-f", "-o", str(out), str(src)]
    elif shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-y", "-i", str(src), "-c:a", cfg.AUDIO_FORMAT, str(out)]
    else:
        shutil.copy(src, out)
        return
    with open(log_path, "ab") as logf:
        subprocess.run(cmd, stdout=logf, stderr=subprocess.STDOUT, timeout=None)


def _run_batched(jobs, worker) -> None:
    """Runs `worker(job)` for each job, ENCODE_JOBS at a time in the
    background, waiting for each batch before starting the next - matches
    HandBrake's own single-threaded-per-process model, so ENCODE_JOBS
    processes run in parallel rather than one process trying to use them all.
    """
    import threading
    batch = []
    for job in jobs:
        t = threading.Thread(target=worker, args=(job,))
        t.start()
        batch.append(t)
        if len(batch) >= cfg.ENCODE_JOBS:
            for t in batch:
                t.join()
            batch = []
    for t in batch:
        t.join()


def process_video_job(label: str, dest: Path) -> None:
    counts = {c.name: 0 for c in cfg.DURATION_CLASSES}
    n_already = 0
    seen = {c.name: set() for c in cfg.DURATION_CLASSES if c.dedup}

    def route(secs, ident):
        """The DurationClass for secs, logging + returning None if the
        title is skipped (no matching class, or a deduped repeat)."""
        cls = classify_class(secs)
        if cls is None:
            log(cfg.ENCLOG, f"  skip {ident} ({secs}s - no matching duration class)")
            return None
        if cls.dedup:
            if secs in seen[cls.name]:
                log(cfg.ENCLOG, f"  dup  {ident} ({secs}s) already taken")
                return None
            seen[cls.name].add(secs)
        return cls

    video_ts = dest / "VIDEO_TS"
    if cfg.RIP_MODE == "video_ts" and video_ts.is_dir():
        jobs = []
        for idx, secs in hb_titles(str(video_ts)):
            if idx is None:
                continue
            cls = route(secs, f"{label} t{idx}")
            if cls is None:
                continue
            counts[cls.name] += 1
            outdir = dest / cls.folder
            out = outdir / f"title{idx:02d}.mkv"
            if out.exists() and out.stat().st_size > 0:
                n_already += 1
                continue
            log(cfg.ENCLOG, f"  ->   {label} t{idx}  {secs // 60}m  {cls.name}  -> {outdir.name}/{out.name}")
            jobs.append((str(video_ts), idx, out, cls.quality))
        _run_batched(jobs, lambda j: encode_video_one(*j))
    else:
        src = dest / "source"
        if not src.is_dir():
            log(cfg.ENCLOG, f"no source/ in {dest}")
            return
        jobs = []
        for f in sorted(src.glob("*.mkv")):
            secs = file_duration(str(f))
            cls = route(secs, f.name)
            if cls is None:
                continue
            counts[cls.name] += 1
            outdir = dest / cls.folder
            out = outdir / f.name
            if out.exists() and out.stat().st_size > 0:
                n_already += 1
                continue
            log(cfg.ENCLOG, f"  ->   {f.name}  {secs // 60}m  {cls.name}  -> {outdir.name}/")
            jobs.append((str(f), None, out, cls.quality))
        _run_batched(jobs, lambda j: encode_video_one(*j))

    if sum(counts.values()) + n_already == 0:
        log(cfg.ENCLOG, f"WARN  {label} - nothing encodable found. Check {cfg.LOGDIR}/")
        notify(f"backcrack - {label} nothing found", "No titles matched a duration class.", "high", "warning")
    else:
        breakdown = ", ".join(f"{n} {name}" for name, n in counts.items() if n) or "nothing new"
        log(cfg.ENCLOG, f"DONE  {label} - {breakdown}, {n_already} already present")
        if cfg.NOTIFY_ENCODES:
            notify(f"backcrack - {label} encoded", breakdown, "low", "film_projector")


def process_audio_job(label: str, dest: Path) -> None:
    src = dest / "source"
    if not src.is_dir():
        log(cfg.ENCLOG, f"no source/ in {dest}")
        return

    n_done = n_skip = 0
    jobs = []
    for f in sorted(src.glob("*.wav")):
        out = dest / "encoded" / f"{f.stem}.{cfg.AUDIO_FORMAT}"
        if out.exists() and out.stat().st_size > 0:
            n_skip += 1
            continue
        log(cfg.ENCLOG, f"  ->   {f.name}  -> encoded/{out.name}")
        jobs.append((f, out))
        n_done += 1
    _run_batched(jobs, lambda j: encode_audio_one(*j))

    if n_done + n_skip == 0:
        log(cfg.ENCLOG, f"WARN  {label} - no tracks found in source/. Check {cfg.LOGDIR}/")
        notify(f"backcrack - {label} nothing found", "No tracks in source/.", "high", "warning")
    else:
        log(cfg.ENCLOG, f"DONE  {label} - {n_done} tracks, {n_skip} already present")
        if cfg.NOTIFY_ENCODES:
            notify(f"backcrack - {label} encoded", f"{n_done} tracks", "low", "film_projector")


def process_job(jobfile: Path) -> None:
    fields = dict(
        line.split("=", 1) for line in jobfile.read_text().splitlines() if "=" in line
    )
    label, dest_s, kind = fields.get("label"), fields.get("dest"), fields.get("kind")
    if not label or not dest_s or not Path(dest_s).is_dir():
        log(cfg.ENCLOG, f"BAD JOB {jobfile}")
        jobfile.unlink(missing_ok=True)
        return

    dest = Path(dest_s)
    log(cfg.ENCLOG, f"ENCODE {label} ({kind})")
    if kind == "audio":
        process_audio_job(label, dest)
    else:
        process_video_job(label, dest)
    (cfg.ENCDONE / label).touch()
    jobfile.unlink(missing_ok=True)
