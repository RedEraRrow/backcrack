"""watch's Settings screen (s): every setting in its section, its value beside
it, changed in place and saved to settings.env. The daemons read settings when
they start, so a change reaches them once they're restarted."""
import re
from pathlib import Path

from backbone import keys, prompt, ui
from backbone.log import configure as log_setup, log_path

from backcrack import config as cfg
from backcrack.common import find_daemons

# (section, [(setting, kind, label, note)]). kind is "path", "text", "int",
# "bool", "classes", "accent", or a dict of a fixed set of values → labels.
# note says what to type, above the field.
SECTIONS = [
    ("Library", [
        ("LIBRARY", "path", "Library folder", ""),
        ("PATTERN_VIDEO", "text", "Video folder pattern", "a path of %tokens%, like Season %season%/Disc %disc%"),
        ("PATTERN_AUDIO", "text", "Audio folder pattern", "a path of %tokens%, like %artist%/%album%"),
    ]),
    ("Ripping", [
        ("RIP_MODE", {"titles": "Each title to its own MKV", "video_ts": "Whole DVD backup, menus too"},
         "Video discs", ""),
        ("MIN_TITLE_S", "int", "Shortest title ripped", "seconds"),
        ("MAX_RETRIES", "int", "Tries before giving up", "attempts at a disc before it's ejected for good"),
    ]),
    ("Encoding", [
        ("DURATION_CLASSES", "classes", "Duration classes",
         "name:min-max:folder:quality[:dedup], comma-separated, in seconds"),
        ("ENCODE_JOBS", "int", "Encodes at once", ""),
        ("ENCODE_THREADS", "int", "Threads per encode", ""),
        ("VIDEO_ENCODER", "text", "Video encoder", "HandBrake's --encoder, like x264"),
        ("ENCODER_PRESET", "text", "Encoder preset", "HandBrake's --encoder-preset, like fast"),
        ("DEINTERLACE_ARGS", "text", "Deinterlace flags", "HandBrake's, like --comb-detect --decomb; blank for none"),
        ("AUDIO_FORMAT", "text", "Audio format", "flac, or anything ffmpeg can write"),
    ]),
    ("Notifications", [
        ("NTFY_TOPIC", "text", "ntfy topic", "blank sends nothing"),
        ("NTFY_SERVER", "text", "ntfy server", ""),
        ("NOTIFY_ENCODES", "bool", "Push when a disc is encoded", ""),
    ]),
    ("Bare backcrack starts", [
        ("LAUNCH_RIPD", "bool", "ripd", ""),
        ("LAUNCH_ENCD", "bool", "encd", ""),
        ("LAUNCH_SORTD", "bool", "sortd", ""),
        ("LAUNCH_WATCH", "bool", "watch", ""),
    ]),
    ("Appearance", [
        ("ACCENT", "accent", "Accent colour", ""),
    ]),
    ("Diagnostics", [
        ("DEBUG", "bool", "Diagnostics log", ""),
    ]),
]
_ROWS = {name: (kind, label, note) for _section, rows in SECTIONS for name, kind, label, note in rows}
_TOGGLES = {name for name, (kind, _l, _n) in _ROWS.items() if kind == "bool"}
_LEAST = {"MIN_TITLE_S": 0}            # every other number is at least 1
_WATCH_ONLY = {"ACCENT", "LAUNCH_RIPD", "LAUNCH_ENCD", "LAUNCH_SORTD", "LAUNCH_WATCH"}
# The key groups of the screens backcrack opens: watch, and the lists, searches
# and questions of this screen. Key bindings lists only these.
_KEY_SCOPES = ["watch", "global", "list", "search", "confirm"]


def saved_value(name: str) -> str:
    """A setting as settings.env holds it."""
    v = getattr(cfg, name)
    if isinstance(v, bool):
        return "1" if v else "0"
    if name == "DURATION_CLASSES":
        # Back to _parse_duration_classes()'s own spec grammar.
        return ",".join(f"{c.name}:{c.min_s}-{c.max_s}:{c.folder}:{c.quality}" + (":dedup" if c.dedup else "")
                        for c in v)
    if isinstance(v, list):
        return " ".join(v)
    return str(v)


def shown_value(name: str):
    """A setting as its row shows it."""
    kind = _ROWS[name][0]
    v = getattr(cfg, name)
    if kind == "bool":
        return prompt.state_glyph(v)
    if kind == "accent":
        return prompt.accent_swatch(v) + ["  " + ui.accent_label(v)]
    if isinstance(kind, dict):
        return kind.get(v, v)
    if kind == "path":
        return str(v).replace(str(Path.home()), "~", 1)
    if kind == "classes":
        return ", ".join(f"{c.name} {c.min_s // 60}-{c.max_s // 60} min" for c in v) or "none"
    if name == "MIN_TITLE_S":
        return f"{v}s"
    return saved_value(name) or "none"


def classes_problem(spec: str):
    """Why `spec` can't be DURATION_CLASSES, or None."""
    try:
        classes = cfg._parse_duration_classes(spec)
    except ValueError:
        return "each one is name:min-max:folder:quality, with whole seconds"
    if not classes:
        return "at least one class"
    bad = next((c.name for c in classes if c.min_s > c.max_s), None)
    if bad:
        return f"{bad}'s min is over its max"
    bad = next((c.name for c in classes if not c.quality.replace(".", "", 1).isdigit()), None)
    return f"{bad}'s quality isn't a number" if bad else None


def apply(name: str, new: str) -> None:
    """Save a setting and use it in this running watch at once."""
    cfg.save_setting(name, new)
    kind = _ROWS[name][0]
    if name == "LIBRARY":
        cfg.use_library(Path(new).expanduser())
    elif name == "DEINTERLACE_ARGS":
        cfg.DEINTERLACE_ARGS = new.split()
    elif name == "DURATION_CLASSES":
        cfg.DURATION_CLASSES = cfg._parse_duration_classes(new)
    elif kind == "bool":
        setattr(cfg, name, new == "1")
    elif kind == "int":
        setattr(cfg, name, int(new))
    else:
        setattr(cfg, name, new)
    if name == "ACCENT":
        ui.set_accent(new)
    elif name == "DEBUG":
        log_setup(cfg.DEBUG)


def _saved_note(name: str) -> str:
    """What the toast adds: where the log goes, or that running daemons need restarting."""
    if name == "DEBUG" and cfg.DEBUG:
        return f", writing to {log_path()}"
    running = [d for d in ("ripd", "encd", "sortd") if find_daemons(d)]
    if running and name not in _WATCH_ONLY:
        return f" · restart {', '.join(running)} to use it"
    return ""


def _edit(name: str) -> None:
    """Change one setting the way its kind is changed."""
    kind, label, note = _ROWS[name]
    question = f"{label} · {note}" if note else label     # the note gives way first when narrow
    if kind == "bool":
        new = "0" if getattr(cfg, name) else "1"
    elif kind == "accent":
        prompt.pick_accent(label, cfg.ACCENT, lambda v: apply(name, v))
        return
    elif isinstance(kind, dict):
        new = prompt.pick_option("", kind, getattr(cfg, name), label)
    elif kind == "path":
        new = prompt.path(question, default=saved_value(name))
    elif kind == "int":
        least = _LEAST.get(name, 1)
        new = prompt.text(question, default=saved_value(name), allow=lambda t: t == "" or t.isdigit(),
                          check=lambda t: None if t.isdigit() and int(t) >= least else f"a whole number, {least} or more")
    elif kind == "classes":
        new = prompt.text(question, default=saved_value(name), check=classes_problem)
    else:
        new = prompt.text(question, default=saved_value(name))
    if new is None or new == saved_value(name):
        return
    apply(name, new.strip())
    ui.show_status(f"{label}: {shown_value(name)}{_saved_note(name)}")


def _unsorted() -> list:
    unsorted = cfg.LIBRARY / "UNSORTED"
    return sorted(p.name for p in unsorted.iterdir() if p.is_dir()) if unsorted.is_dir() else []


def _add_label_entry() -> None:
    """labels.map's manual escape hatch, from inside watch instead of a text
    editor: pick (or type) a disc label, then fill in whatever %tokens% the
    active pattern needs. See pattern.py's lookup_override()."""
    pending = _unsorted()
    manual = "(type a label)"
    label = prompt.select("", pending + [manual], header=prompt.PanelTitle("Which disc?")) if pending else manual
    if label is None:
        return
    if label == manual:
        label = prompt.text("Disc label, exactly as the log has it:")
    if not label:
        return

    audio = prompt.confirm("Is it an audio CD rather than a video disc?", default=False)
    pattern = cfg.PATTERN_AUDIO if audio else cfg.PATTERN_VIDEO
    pairs = []
    for tok in re.findall(r"%([a-zA-Z0-9_]+)%", pattern):
        val = prompt.text(f"{tok}:")
        if val:
            pairs.append(f"{tok}={val}")
    if not pairs:
        return
    with open(cfg.LABELS_MAP, "a") as f:
        f.write(f"{label}|{','.join(pairs)}\n")
    ui.show_status(f"Added {label} to labels.map; sortd files it within {cfg.SORT_INTERVAL}s")


def open_settings() -> None:
    """The Settings screen, until backed out of."""
    cursor = 0
    while True:
        choices = []
        for section, rows in SECTIONS:
            choices.append(prompt.separator(section))
            for name, _kind, label, _note in rows:
                choices.append(prompt.Choice(title=label, value=name, cells=[label, shown_value(name)]))
            if section == "Library":
                n = len(_unsorted())
                choices.append(prompt.Choice(title="Add a labels.map line…", value="__label__",
                                             cells=["Add a labels.map line…", f"{n} unsorted" if n else "none unsorted"]))
            if section == "Appearance":
                changed = sum(keys.changed(a.id) for a in keys.actions())
                choices.append(prompt.Choice(title="[?] help toggle", value="__help__",
                                             cells=["[?] help toggle", prompt.state_glyph(prompt.help_toggle_shown())]))
                choices.append(prompt.Choice(title="Key bindings…", value="__keys__",
                                             cells=["Key bindings…", f"{changed} changed" if changed else "default"]))

        choice = prompt.select("", choices=choices, columns=prompt.SETTINGS_COLUMNS,
                               header=prompt.PanelTitle("Settings"),
                               index=cursor, extra_hints={"list.toggle": "toggle"},
                               **prompt.space_toggles(_TOGGLES | {"__help__"}))
        if not choice:
            return
        if isinstance(choice, tuple):              # space on an on/off row
            choice = choice[1]
        cursor = prompt.index_of(choices, choice, cursor)

        if choice == "__label__":
            _add_label_entry()
        elif choice == "__help__":
            prompt.set_help_toggle_shown(not prompt.help_toggle_shown())
        elif choice == "__keys__":
            prompt.keys_editor(_KEY_SCOPES)
        else:
            _edit(choice)
