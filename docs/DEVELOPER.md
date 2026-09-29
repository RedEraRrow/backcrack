# Developer notes

## Design principles

- **One daemon per concern, sharing one state directory.** `ripd.py` rips,
  `encd.py` encodes, `sortd.py` reconciles `UNSORTED/`. None of them know
  about the others except through `$LIBRARY/.ripstate` - lock files, a queue
  directory, done markers. Kill and restart any one of them without
  touching the rest.
- **Format is a branch, not a rewrite.** The code dispatches on a disc's
  `kind` (`video` | `audio`) at exactly three points: which rip function
  `ripd.py` calls (`rip.py`), which destination pattern `pattern.dest_for`
  uses, and which encode function `encode.process_job` calls. Everything
  else (locking, retry, eject, notify, queueing, the watch dashboard) is
  format-agnostic and stays that way on purpose. Adding a third disc kind
  should only mean adding a branch at those three points, not touching
  `lib.py`/`watch.py`.
- **Pattern engine has no opinion about your library shape.** `dest_for`
  (`backcrack/pattern.py`) doesn't know what a season or an album is - it
  substitutes whatever tokens it's handed into whatever pattern you
  configured. The auto-detectors (`auto_video_tokens`, `auto_audio_tokens`)
  are the only code that knows what a season number or a MusicBrainz
  release looks like. `namer.py` is the exception: it assumes the default
  video pattern and folder names.
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
  give up loudly rather than loop forever. Ctrl-C anywhere is safe. The one
  deliberate delete, `namer` removing a finished disc's `source/`, only
  happens once that disc's encoded episodes are all in place.
- **State lives in the filesystem, not a database.** Lock files, done
  markers, a queue directory of small text files - the same design as the
  original shell version. It's slower than SQLite and nobody notices,
  because a disc rip takes minutes; readable-with-`cat` state that survives
  a crash mid-write is worth more here than a query language.
- **Shared helpers live in `backcrack/lib.py`**: logging, ntfy, finding and
  starting daemons (`find_daemons`, `spawn_daemon`, `stop_daemons`), the
  process listing, disk free, the pipeline counts. Terminal formatting
  (`human_gb`, `dir_size_kb`, colours, the hint bar) comes from backbone.

## Why Python over shell for this one

`urllib`/`json` replace `curl`/`jq` for the MusicBrainz lookup - one fewer
pair of external tools to have installed. `subprocess.run(..., timeout=N)`
replaces the shell version's hand-rolled `capped()` wrapper around
`drutil`/`dd` calls (there's no `timeout(1)` on macOS). In-process dicts
replace `watch.sh`'s `/tmp/*.d` scratch files for growth-rate history,
since one long-running Python process can just keep that in memory instead
of re-reading it from disk every frame.

## Keys

The daemons have no interactive keys: each runs until Ctrl-C. `watch` has
two:

- `q` quits, then asks whether to stop ripd, encd and sortd too.
- `s` opens the settings screen, which writes `settings.env` and can append
  a `labels.map` line. Nothing else in `watch` writes anywhere.

## Style rules

- No emoji, no decorative colour. `watch.py` uses two colours: backbone's
  `PRIMARY` for anything active, finished or counted, and `RED` for `FAIL`
  lines, whatever `ACCENT` is set to. Everything else is weight and
  brightness (bold, dim, white). Colour switches itself off when not
  writing to a terminal (`NO_COLOR=1`, or piped output).
- Every deliberate simplification gets a comment naming the ceiling and
  what to do about it, not a silent gap. Grep for `ponytail:` in
  `backcrack/pattern.py` for the current one.
