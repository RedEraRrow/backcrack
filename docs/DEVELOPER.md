# Developer notes

## Design principles

- **One daemon per concern, sharing one state directory.** `ripd.py` rips,
  `encd.py` encodes, `sortd.py` reconciles `UNSORTED/`. None of them know
  about the others except through `$LIBRARY/.ripstate` - lock files, a queue
  directory, done markers. Kill and restart any one of them without
  touching the rest.
- **Format is a branch, not a rewrite.** `ripd.py`/`encd.py` dispatch on a
  `kind` field (`video` | `audio`) at exactly two points - which rip
  function to call, which encode function to call (`rip.py`, `encode.py`).
  Everything else (locking, retry, eject, notify, queueing, the watch
  dashboard) is format-agnostic and stays that way on purpose. Adding a
  third disc kind should only mean adding a third branch at those two
  points, not touching `lib.py`/`watch.py`.
- **Pattern engine has no opinion about your library shape.** `dest_for`
  (`backcrack/pattern.py`) doesn't know what a season or an album is - it
  substitutes whatever tokens it's handed into whatever pattern you
  configured. The auto-detectors (`auto_video_tokens`, `auto_audio_tokens`)
  are the only code that knows what a season number or a MusicBrainz
  release looks like.
- **Four independent guards before touching a disc's folder** (`sort.py`).
  A previous version of this kind of reconciler moved a folder out from
  under an active rip because it trusted one `ps` check. Nothing here
  relies on a single signal.
- **Unresolvable always means UNSORTED, never a guess.** An unparseable
  video label, a CD with no lookup match, a pattern token nobody supplied -
  all land in the same place with the same manual escape hatch
  (`labels.map`). Silently guessing wrong is worse than asking once.
- **Everything resumable, nothing destructive by default.** A done marker
  means never redo; a fail counter means retry up to `MAX_RETRIES`, then
  give up loudly rather than loop forever. Ctrl-C anywhere is safe.
- **State lives in the filesystem, not a database.** Lock files, done
  markers, a queue directory of small text files - the same design as the
  original shell version. It's slower than SQLite and nobody notices,
  because a disc rip takes minutes; readable-with-`cat` state that survives
  a crash mid-write is worth more here than a query language.

## Why Python over shell for this one

`urllib`/`json` replace `curl`/`jq` for the MusicBrainz lookup - one fewer
pair of external tools to have installed. `subprocess.run(..., timeout=N)`
replaces the shell version's hand-rolled `capped()` wrapper around
`drutil`/`dd` calls (there's no `timeout(1)` on macOS). In-process dicts
replace `watch.sh`'s `/tmp/*.d` scratch files for growth-rate history,
since one long-running Python process can just keep that in memory instead
of re-reading it from disk every frame.

## Key convention

No interactive keys at all - every entrypoint either runs to completion or
loops until Ctrl-C. `watch.py` is read-only; closing it never touches a
running daemon.

## Style rules

- No emoji, no decorative color. The ANSI color in `watch.py` is semantic
  only (teal = ripping, amber = encoding, green = done, red = failed) and
  disables itself when not writing to a terminal (`NO_COLOR=1`, or piped
  output) - it's a signal, not decoration.
- Every deliberate simplification gets a comment naming the ceiling and
  what to do about it, not a silent gap. Grep for `ponytail:` in
  `backcrack/pattern.py` for the current one.
