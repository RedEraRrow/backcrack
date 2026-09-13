# backcrack

Insert discs, get pinged, swap discs. Encoding happens on its own in the
background. Works on CDs, DVDs, and Blu-rays - the disc in the drive tells
the pipeline which path to take.

Python, stdlib only apart from `backbone`, the shared terminal-UI layer the
back* suite draws itself with - installed from its sibling checkout, not from
PyPI. Requires Python 3.9+ (macOS ships one; `python3 --version` to check).

## Install

Installed editable, once, from this directory:

    pip3 install -e .

That puts `ripd`, `encd`, `sortd`, `watch`, `status`, `swapd`, `titles`,
`diskspeed` and `namer` on your PATH as plain commands - no `./`, no `.py`, no `python3`
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

In `watch`, `q` quits (and offers to stop the daemons with it) and `s` opens
the settings screen - see Settings below.

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

    DURATION_CLASSES=extra:180-1199:extras:22,main:1200-10800:encoded:20

A title's duration must fall in a class's `min-max` (seconds) to match;
first match wins, and anything matching none of them isn't encoded at all.
`folder` is where it lands under a disc's destination, `quality` is
HandBrake's `--quality` (RF) for that class, and the optional `dedup` flag
drops a second title with that exact duration.

`dedup` is off by default, and worth turning on only for a disc that
genuinely lists the same film twice ("Play Movie" plus a separate menu entry
of identical content). It is wrong for TV: episodes on one disc naturally
cluster within seconds of each other - four real titles at 2530, 2530, 2537
and 2533 seconds, two of them bit-for-bit different files - so matching on
duration alone silently drops a distinct episode. Check your own discs before
setting it. Add as many classes as you want - a `commentary` or
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

## Encoding throughput

`ENCODE_JOBS` is how many HandBrake jobs run at once, `ENCODER_PRESET` is the
x264 preset, and `ENCODE_THREADS` caps the threads any one job may use.

The defaults (4 jobs, `fast`, threads capped at cores/jobs) were measured on
an M1 Pro with 8 performance cores: `medium` saturates it at 2 concurrent
jobs, while `fast` keeps scaling through 4, for roughly 33% more aggregate
throughput at about 3.6% bigger files at the same RF. Those numbers are
specific to that CPU and preset. Re-benchmark if you change any of it: encode
one short clip alone, then N of them at once, and compare the wall time.

The thread cap matters more than it looks. Jobs in a batch don't finish
together - a 168-minute title far outlasts a 42-minute one - so a job can end
up running alone, and an uncapped x264 then takes every core it can see. That
starved two concurrent MakeMKV rips of scheduling time for over ten minutes
(both sat at about 3% CPU with nothing written). The cap keeps a fixed
ceiling however many siblings are still going, so ripping always has room.

## Resuming

Everything is resumable. Stop either daemon with Ctrl-C whenever you like.
(`watch`'s live view is the exception - Ctrl-C is disabled there; press `q`
and it'll ask whether to also stop the daemons.)

- A disc already ripped is ejected immediately instead of redone.
- A failed rip is *not* marked done, so re-inserting it retries.
- A file already encoded is skipped.

Before finishing, check for failures:

    grep FAIL "$LIBRARY/.ripstate/rip.log"
    grep -i WARN "$LIBRARY/.ripstate/rip.log"

`WARN` lines are worth reading. A disc routing to a folder that already holds
finished output is ripped alongside it as `<name> (<label>)/` rather than over
it, and says so there - reconcile those by hand.

Every rip also keeps MakeMKV's own output at
`$LIBRARY/.ripstate/logs/<label>.mkv.log`. That is where a read error, a
retried sector or a title MakeMKV quietly gave up on shows. A rip whose log
reports "N titles saved, M failed" is marked failed even when makemkvcon
exits 0, so a partial rip never counts as a finished one.

## Settings

Every setting is an environment variable with a default in
`backcrack/config.py`, and a one-off `LIBRARY=... ripd` still overrides
everything.

`s` in `watch` opens a settings screen over the same variables, writing them
to `settings.env` in this directory. That file sits underneath `os.environ` in
precedence and is read by any process started afterwards, so a change survives
past the current shell without editing source. It is local state and is not
tracked by git. `ripd` and `encd` read their config at startup, so restart
them to pick a change up.

The same screen has an entry for adding a `labels.map` line, which is the
manual escape hatch for a disc whose %tokens% couldn't be resolved - pick it
out of `UNSORTED/`, fill in the tokens the active pattern needs, and `sortd`
files it within a few seconds.

## Finishing a season

Once a season's discs are all ripped and encoded, `namer` lays them out the
way Jellyfin and Kodi expect:

    namer                 every season episodes.map covers
    namer "Season 4"      just one

Each disc's encoded titles are renamed to `SxxExx Title.mkv` using the titles
in `episodes.map`, moved up into the season folder itself, and every disc's
extras are merged into one season-level `featurettes/` - a recognised extra
type, which a bare `extras/` is not. Empty disc folders are removed; anything
it doesn't recognise is left where it is.

`episodes.map` is one block per season, a `# Season N` header then one title
per line in broadcast order. Renaming assumes disc and title order matches
broadcast order, which is the normal convention for a season box set.

A season whose encoded-file count doesn't exactly match its title count is
refused outright rather than guessed at. A mismatch means a disc is still
ripping, a title never encoded, or one was wrongly deduped, and guessing would
mislabel every episode after the gap.

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
