# backcrack

Insert discs, get pinged, swap discs. Encoding happens on its own in the
background. Works on CDs, DVDs, and Blu-rays - the disc in the drive tells
the pipeline which path to take.

Python, stdlib only - no third-party packages needed for the pipeline itself.
Requires Python 3.9+ (macOS ships one; `python3 --version` to check).

## Install

Installed editable, once, from this directory:

    pip3 install -e .

That puts `ripd`, `encd`, `sortd`, `watch`, `status`, `swapd`, `titles` and
`diskspeed` on your PATH as plain commands - no `./`, no `.py`, no `python3`
in front. It's a live link back to this source tree, so editing any file
here takes effect immediately, no reinstall.

`watch`, `status` and `titles` are common words - if any of those already
means something else in your shell, rename that one entry in
`pyproject.toml`'s `[project.scripts]` and re-run `pip3 install -e .`.

Without installing, every command still runs directly - `./ripd.py` etc.
work exactly the same either way.

## Morning start

Two terminal tabs.

Tab 1 - the ripper:

    ripd

Tab 2 - the encoder (start it once and leave it; idle is normal):

    encd

Then load both drives. Every eject sends an ntfy push. Insert the next discs.

Check progress any time from a third tab:

    status
    watch      # live view

## What it detects

- **DVD / Blu-ray** - MakeMKV rips each title to a lossless MKV, HandBrake
  encodes the ones matching a configured duration class - see
  `DURATION_CLASSES` in `backcrack/config.py`.
- **Audio CD** - cdparanoia extracts each track losslessly, then gets
  compressed to `AUDIO_FORMAT` (flac by default).

Both land in the same shape: `source/` (lossless rip) and `encoded/`
(compressed, ready to use); video also gets `extras/` for anything shorter
than the main window.

## Layout produced

    $LIBRARY/<pattern-resolved path>/source/...    lossless rip
    $LIBRARY/<pattern-resolved path>/encoded/...    compressed, ready to use (default "main" class)
    $LIBRARY/<pattern-resolved path>/extras/...     video only (default "extra" class)
    $LIBRARY/.ripstate/                             logs, queue, progress

The folder names above are just the stock `DURATION_CLASSES` defaults, not
fixed - a class's `folder` field is whatever you set it to.

The path itself is not fixed - it's built from `PATTERN_VIDEO` /
`PATTERN_AUDIO` in `backcrack/config.py` by substituting %tokens%. Default is
`Season %season%/Disc %disc%` for video and `%artist%/%album%` for audio;
change these for a movie shelf, a mixed CD/DVD pile, whatever you're ripping
this run. See `docs/pattern-tokens.md`.

Discs whose tokens can't be resolved go to `UNSORTED/<label>/`, and you get
a high-priority push. Add a line to `labels.map` and it files itself within
a few seconds (`sortd` watches for exactly this).

## Before trusting a whole shelf of discs

Once the first disc of a kind has ripped, check what HandBrake saw:

    titles "$LIBRARY/Season 1/Disc 1"

Every title in your target runtime should say `main`; menus, featurettes,
and any "play all" duplicate should say `extra` or `skip`. Adjust
`DURATION_CLASSES` (env var, or edit `backcrack/config.py`) if not - a
22-minute sitcom and a 150-minute film need very different windows.

`DURATION_CLASSES` is a comma-separated list of
`name:min-max:folder:quality[:dedup]` entries, e.g. the default:

    DURATION_CLASSES=extra:180-1199:extras:22,main:1200-10800:encoded:20:dedup

A title's duration must fall in a class's `min-max` (seconds) to match;
first match wins, and anything matching none of them isn't encoded at all.
`folder` is where it lands under a disc's destination, `quality` is
HandBrake's `--quality` (RF) for that class, and the optional `dedup` flag
drops a second title with that exact duration - only worth setting on a
class that should fire once (a movie/episode; discs sometimes list the main
feature twice). Add as many classes as you want - a `commentary` or
`featurette` tier with its own folder and quality, tighter windows for a
mixed sitcom/movie shelf, whatever your discs need.

`MIN_TITLE_S` is the separate rip-time floor (MakeMKV's `--minlength`) below
which a title is never even ripped; it defaults to the shortest configured
class's minimum, so set it explicitly only if you want to rip shorter junk
than you keep.

Then watch one encoded file and scrub a fast camera move frame by frame. If
you see combing, `DEINTERLACE_ARGS` needs setting.

For audio, `cdparanoia -Q -d <device>` lists a disc's tracks directly if you
want to sanity-check before ripping.

## Resuming

Everything is resumable. Stop either daemon with Ctrl-C whenever you like.
(`watch`'s live view is the exception - Ctrl-C is disabled there; press `q`
and it'll ask whether to also stop the daemons.)

- A disc already ripped is ejected immediately instead of redone.
- A failed rip is *not* marked done, so re-inserting it retries.
- A file already encoded is skipped.

Before finishing, check for failures:

    grep FAIL "$LIBRARY/.ripstate/rip.log"

## Tools it needs

- **MakeMKV** (`makemkvcon`) and **HandBrakeCLI** - for DVD/Blu-ray.
- **cdparanoia** - for audio CDs. `brew install cdparanoia`.
- **flac** (or ffmpeg) - to compress ripped audio. `brew install flac`.
- **cd-discid** - optional, only for automatic artist/album lookup on audio
  CDs (the lookup itself uses Python's own `urllib`/`json`, no `curl`/`jq`
  needed). Without it, every CD goes to `UNSORTED/` until you add a
  `labels.map` line.

Anything missing gets a warning at startup, not a silent failure - the
daemon still runs, that disc kind just won't rip until it's installed.

## Storage

Video eats far more space than audio. Once you've verified a disc's
encoded output, delete its `source/` folder to reclaim the lossless copy.
