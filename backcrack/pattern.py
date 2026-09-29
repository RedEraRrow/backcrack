"""pattern.py - dynamic %token% destination patterning.
See docs/pattern-tokens.md for the token vocabulary and worked examples.
"""
import json
import re
import urllib.request
from pathlib import Path
from typing import Optional

from . import config as cfg
from . import disc


def render_pattern(pattern: str, tokens: dict) -> Optional[str]:
    """Substitutes every %key% in pattern for tokens[key]. Returns None if a
    %word% is still standing afterwards - the caller treats that exactly
    like a label that didn't parse: file the disc under UNSORTED/ instead
    of guessing.
    """
    rendered = pattern
    for k, v in tokens.items():
        rendered = rendered.replace(f"%{k}%", v)
    if re.search(r"%[^%]*%", rendered):
        return None
    return rendered


def lookup_override(label: str) -> Optional[dict]:
    """labels.map: LABEL|key=value,key=value,...  Always wins over
    auto-detection - the manual escape hatch for anything the parser or the
    online lookup gets wrong or can't reach.
    """
    if not cfg.LABELS_MAP.exists():
        return None
    for line in cfg.LABELS_MAP.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            continue
        raw_label, rest = line.split("|", 1)
        if raw_label.strip().lower() != label.lower():
            continue
        tokens = {}
        for pair in rest.split(","):
            if "=" in pair:
                k, v = pair.split("=", 1)
                tokens[k.strip()] = v.strip()
        return tokens
    return None


# Pattern-agnostic on purpose: this only extracts season/disc numbers from
# whatever the disc's volume label looks like. It has no idea what show or
# movie it's for - that's PATTERN_VIDEO's job, or labels.map's.
_SEASON_PATTERNS = [
    r"season[^0-9]*(\d+)",
    r"[^a-z0-9]s(\d+)[^a-z0-9]*d\d",
    r"[^a-z0-9]s(\d+)d\d",
    # "Show_S4_Disc1" style: S<n> then a separator. Last resort, so the
    # tighter patterns above still win where they apply.
    r"[^a-z0-9]s(\d+)[^a-z0-9]",
]
_DISC_PATTERNS = [
    r"disc[^0-9]*(\d+)",
    r"disk[^0-9]*(\d+)",
    r"[^a-z0-9]d(\d+)$",
    r"[^a-z0-9]s\d+[^a-z0-9]*d(\d+)",
]


def auto_video_tokens(label: str) -> Optional[dict]:
    low = label.lower()
    season = disc = None
    for pat in _SEASON_PATTERNS:
        m = re.search(pat, low)
        if m:
            season = m.group(1)
            break
    for pat in _DISC_PATTERNS:
        m = re.search(pat, low)
        if m:
            disc = m.group(1)
            break
    if season is None or disc is None:
        return None
    return {"season": str(int(season)), "disc": str(int(disc))}


# ponytail: album/artist only, no per-track title lookup - tracks stay
# track01.flac etc. Add a MusicBrainz recording-list lookup here if you want
# real track names.
def auto_audio_tokens(dev: Optional[str]) -> Optional[dict]:
    fields = disc.cd_discid(dev) if dev else []
    if not fields:
        return None
    discid = fields[0]
    try:
        req = urllib.request.Request(
            f"https://musicbrainz.org/ws/2/discid/{discid}?fmt=json&inc=artist-credits",
            headers={"User-Agent": "backcrack/1.0 (local ripper)"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception:
        return None
    releases = data.get("releases") or []
    if not releases:
        return None
    release = releases[0]
    artist_credit = release.get("artist-credit") or []
    if not artist_credit or not release.get("title"):
        return None
    return {"artist": artist_credit[0].get("name", ""), "album": release["title"]}


def dest_for(kind: str, label: str, dev: Optional[str] = None) -> Path:
    """Tries labels.map first (always wins), then auto-detection for that
    kind. Anything still unresolved lands under UNSORTED/<label> - never a
    guess.
    """
    pattern = cfg.PATTERN_AUDIO if kind == "audio" else cfg.PATTERN_VIDEO

    tokens = lookup_override(label)
    if tokens is None:
        tokens = auto_audio_tokens(dev) if kind == "audio" else auto_video_tokens(label)

    if tokens:
        rel = render_pattern(pattern, tokens)
        if rel is not None:
            return cfg.LIBRARY / rel

    return cfg.LIBRARY / "UNSORTED" / label
