# Dynamic patterning

`PATTERN_VIDEO` and `PATTERN_AUDIO` are destination templates, set like any
other setting (see the README's Settings section). Every `%token%` in them
gets substituted once the disc's tokens are known; anything left unresolved
sends the disc to `UNSORTED/` instead of guessing. One scheme, applied
everywhere - the same pattern runs whether a token came from auto-detection
or from a `labels.map` override.

## Where tokens come from

1. **labels.map** - always wins. `LABEL|key=value,key=value`. `LABEL` is
   matched case-insensitively. A matching line replaces auto-detection
   outright rather than adding to it, so it has to supply every token the
   pattern uses.
2. **Auto-detection**, only if no override matched:
   - Video: `season` and `disc`, parsed out of the disc's volume label
     (`backcrack/pattern.py:auto_video_tokens`). Purely pattern-matching on the
     label text - it has no idea what show or movie it's for.
   - Audio: `artist` and `album`, looked up from MusicBrainz using the
     disc's table of contents from `cd-discid` (its MusicBrainz disc id, with a
     near match when there's no exact one). No per-track title lookup - ripped tracks
     stay `track01.flac` etc.

If neither source gives every token the pattern needs, the disc goes to
`UNSORTED/<label>/` and you get an ntfy push asking for a `labels.map` line.

The label is the volume label for a video disc. An audio CD is labelled
`AudioCD-<n>tracks-<id>` (see the README's "What it detects"), which stays
the same whichever drive the CD is in.

## Token vocabulary

Use whatever a pattern references - there's no fixed list, `render_pattern`
just substitutes `%key%` for whatever the tokens dict was given. In practice:

| token     | kind  | source                            |
|-----------|-------|------------------------------------|
| `season`  | video | auto-detected, or labels.map       |
| `disc`    | video | auto-detected, or labels.map       |
| `artist`  | audio | MusicBrainz lookup, or labels.map  |
| `album`   | audio | MusicBrainz lookup, or labels.map  |
| `title`   | any   | labels.map only (no auto-detector) |

A movie shelf, for example, has no season/disc structure to auto-detect at
all - set `PATTERN_VIDEO=%title%` and give every disc a `labels.map`
line with `title=...`. The auto-detector always fails for it, which is
correct: every disc goes to `UNSORTED/` once, you name it, `sortd` files
it.

## Worked examples

```
# settings.env  (in ~/.config/backcrack, not the checkout)
PATTERN_VIDEO=Season %season%/Disc %disc%
PATTERN_AUDIO=%artist%/%album%
```

```
# labels.map  (same directory)
HOUSE_S4_DISC2|season=4,disc=2
AudioCD-14tracks-b50d9e0e|artist=Pixies,album=Doolittle
```

Renders to:

```
$LIBRARY/Season 4/Disc 2/...
$LIBRARY/Pixies/Doolittle/...
```

## Adding a token the auto-detectors don't know

Auto-detection only ever produces `season`/`disc` (video) or
`artist`/`album` (audio) - see `backcrack/pattern.py`. Anything else (a track
number, a disc's condition, a `%format%` tag) has to come through
`labels.map` until someone writes a detector for it. That's a deliberate
ceiling, not an oversight - keep the auto-detectors narrow and let the
manual override carry everything else, the same way an unparseable label
always has.
