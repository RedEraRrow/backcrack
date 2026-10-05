"""What backcrack needs besides Python (see backbone.deps): the rippers and
encoders it drives, at the paths config resolved. Reported by
`backcrack doctor`; each daemon warns about the ones its own work needs."""
from __future__ import annotations
import os

from backbone.deps import Dep

from backcrack import config as cfg


def _path(value: str):
    """A probe for a configured tool path: found when it exists."""
    return lambda: value if value and os.path.exists(value) else None


def deps() -> list[Dep]:
    return [
        Dep("MakeMKV", _path(cfg.MKVCON), "ripping DVDs and Blu-rays", hints={
            "macos": "brew install --cask makemkv", "other": "https://www.makemkv.com/download/"}),
        Dep("HandBrake", _path(cfg.HBCLI), "encoding video", hints={
            "macos": "brew install handbrake", "debian": "sudo apt install handbrake-cli",
            "other": "https://handbrake.fr/downloads2.php"}),
        Dep("cd-paranoia", _path(cfg.CDPARANOIA), "ripping and looking up audio CDs", hints={
            "macos": "brew install libcdio-paranoia", "debian": "sudo apt install cdparanoia"}),
        Dep("flac", _path(cfg.FLAC), "encoding CD audio", hints={
            "macos": "brew install flac", "debian": "sudo apt install flac"}),
        Dep("ffmpeg", _path(cfg.FFMPEG), "audio formats other than FLAC", hints={
            "macos": "brew install ffmpeg", "debian": "sudo apt install ffmpeg"}),
    ]


def doctor() -> None:
    """backcrack doctor: every tool it drives, found or how to install it."""
    from backbone.deps import report_lines
    print("\n".join(report_lines(deps())))
    print("Paths can be set in settings.env (MKVCON, HBCLI, CDPARANOIA, FLAC, FFMPEG).")
