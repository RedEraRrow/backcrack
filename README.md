# backcrack

Insert discs, get pinged, swap discs. Encoding happens on its own in the
background. Works on CDs, DVDs, and Blu-rays - the disc in the drive tells
the pipeline which path to take.

Python, stdlib only apart from `backbone`, the library the back* tools share
(`watch`'s screen, process and notification helpers). Requires Python 3.10+
(`python3 --version` to check).

## Install

    pip3 install -e .

pip fetches backbone from GitHub. If you're changing backbone too, install
your checkout of it editable first (`pip3 install -e ../backbone`) and pip
leaves it in place.

That puts `backcrack`, `ripd`, `encd`, `sortd`, `watch`, `status`, `swapd`,
`titles`, `diskspeed` and `namer` on your PATH as plain commands: no `./`,
no `.py`, no `python3` in front. It's a live link back to this source tree,
so editing any file here takes effect immediately, no reinstall.

`watch`, `status` and `titles` are common words. If any of those already
means something else in your shell, rename that one entry in
`pyproject.toml`'s `[project.scripts]` and re-run `pip3 install -e .`, or
use the `backcrack <command>` form below, which always works.

Without installing backcrack itself, every command still runs from the
checkout as `python3 -m backcrack <command>` (`python3 -m backcrack watch`),
as long as backbone is installed.

## Before the first disc

`LIBRARY` is the folder finished discs land in, `~/Media/rips` unless you
set it: from `watch`'s settings screen (`s`), in `settings.env`, or as an
environment variable (see [Settings](#settings)).

For a push on every eject, set `NTFY_TOPIC` to a topic name of your own, and
`NTFY_SERVER` too if you don't use the public `https://ntfy.sh`. Anyone who
knows a topic name can read it on the public server, so pick one nobody will
guess. With `NTFY_TOPIC` unset, nothing is sent.

## Morning start

One command:

    backcrack

starts `ripd` (the ripper), `encd` (the encoder) and `sortd` (which files
discs out of `UNSORTED/`) in the background, then opens `watch`, the live
view. Any of them already running is left alone. Their output goes to
`$LIBRARY/.ripstate/<name>.out`. `LAUNCH_RIPD`, `LAUNCH_ENCD`,
`LAUNCH_SORTD` and `LAUNCH_WATCH` (all on by default) turn each part off, to
run it by hand in its own tab instead:

    ripd
    encd       # start it once and leave it; idle is normal
    sortd

Every subcommand also works as `backcrack <command>`, e.g.
`backcrack ripd`, `backcrack namer "Season 4"`.

Then load both drives. Every eject sends an ntfy push. Insert the next discs.

Check progress any time:

    status
    watch      # live view

In `watch`, `q` quits (and offers to stop ripd, encd and sortd with it) and
`s` opens the settings screen - see [Settings](#settings). Both keys, and those of every
list, can be changed under Key bindings on that screen.

## What it detects

- **DVD / Blu-ray** - MakeMKV rips each title to a lossless MKV, HandBrake
  encodes the ones matching a configured duration class - see
  `DURATION_CLASSES` below.
- **Audio CD** - cdparanoia extracts each track losslessly
  (`track01.cdda.wav`...), then each is compressed to `AUDIO_FORMAT` (flac
  by default) as `track01.flac`...

Both land in the same shape: `source/` (lossless rip) and `encoded/`
(compressed, ready to use); video also gets `extras/` for anything shorter
than the main window.

A video disc is known by its volume label. An audio CD has no useful label,
so it is named `AudioCD-<n>tracks-<id>`, where the id is cd-discid's disc id
(or, without cd-discid, a hash of cdparanoia's track table). That name is
what shows in the log and in `UNSORTED/`, and what a `labels.map` line for
the CD has to use.

### Rip mode

`RIP_MODE` picks how a video disc is ripped:

- `titles` (default): MakeMKV rips each title to its own MKV in `source/`.
  Works for DVD and Blu-ray. A rip whose MakeMKV log reports "N titles
  saved, M failed" is marked failed even when makemkvcon exits 0.
- `video_ts`: a full decrypted backup of the disc, menus included, written
  to `VIDEO_TS/` instead of `source/`. DVD only. The partial-rip check
  above doesn't apply; a rip counts as done when makemkvcon exits 0 and
  `VIDEO_TS/` exists.

## Layout produced

    $LIBRARY/<pattern-resolved path>/source/...     lossless rip (VIDEO_TS/ in video_ts mode)
    $LIBRARY/<pattern-resolved path>/encoded/...    compressed, ready to use (default "main" class)
    $LIBRARY/<pattern-resolved path>/extras/...     video only (default "extra" class)
    $LIBRARY/.ripstate/                             logs, queue, progress

For video, `encoded` and `extras` are just the stock `DURATION_CLASSES`
folder names; a class's `folder` field is whatever you set it to. Audio
always goes to `encoded/`.

The path itself is built from `PATTERN_VIDEO` / `PATTERN_AUDIO` by
substituting %tokens%. Default is `Season %season%/Disc %disc%` for video
and `%artist%/%album%` for audio; change these for a movie shelf, a mixed
CD/DVD pile, whatever you're ripping this run. See `docs/pattern-tokens.md`.

Discs whose tokens can't be resolved go to `UNSORTED/<label>/`, and you get
a high-priority push. Add a line to `labels.map` (in your config directory,
see [Settings](#settings)) and, with `sortd` running, it files itself within
a few seconds.

## Before trusting a whole shelf of discs

Once the first disc of a kind has ripped, check what HandBrake saw:

    titles "$LIBRARY/Season 1/Disc 1"

Every title in your target runtime should say `main`; menus, featurettes,
and any "play all" duplicate should say `extra` or `skip`. Adjust
`DURATION_CLASSES` if not - a 22-minute sitcom and a 150-minute film need
very different windows.

`DURATION_CLASSES` is a comma-separated list of
`name:min-max:folder:quality[:dedup]` entries, e.g. the default:

    DURATION_CLASSES=extra:180-1199:extras:22,main:1200-10800:encoded:20

A title's duration must fall in a class's `min-max` (seconds) to match;
first match wins, and anything matching none of them isn't encoded at all.
`folder` is where it lands under a disc's destination, `quality` is
HandBrake's `--quality` (RF) for that class, and the optional `dedup` flag
drops a second title with that exact duration.

`dedup` is off by default, and worth turning on only for a disc that
really does list the same film twice ("Play Movie" plus a separate menu
entry of identical content). It is wrong for TV: episodes on one disc
naturally cluster within seconds of each other - four real titles at 2530,
2530, 2537 and 2533 seconds, two of them bit-for-bit different files - so
matching on duration alone silently drops a distinct episode. Check your own
discs before setting it. Add as many classes as you want: a `commentary` or
`featurette` tier with its own folder and quality, tighter windows for a
mixed sitcom/movie shelf, whatever your discs need.

`MIN_TITLE_S` is the separate rip-time floor (MakeMKV's `--minlength`) below
which a title is never even ripped; it defaults to the shortest configured
class's minimum, so set it explicitly only if you want to rip shorter junk
than you keep.

Then watch one encoded file and scrub a fast camera move frame by frame. If
you see combing, set `DEINTERLACE_ARGS` to HandBrake's deinterlace flags,
e.g. `DEINTERLACE_ARGS=--comb-detect --decomb`. Leave it empty for
progressive sources.

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

Everything is resumable. Stop any daemon with Ctrl-C whenever you like.
(`watch`'s live view is the exception - Ctrl-C is disabled there; press `q`
and it'll ask whether to also stop the daemons.)

- A disc already ripped is ejected immediately instead of redone.
- A failed rip is left in the drive and ripped again, up to `MAX_RETRIES`
  attempts in all (3 by default). After the last one it is ejected and
  `$LIBRARY/.ripstate/gaveup-<label>` is written; re-inserting it then just
  ejects it again. To try it again, delete that file and re-insert the
  disc: it gets a fresh `MAX_RETRIES` attempts.
- A file already encoded is skipped. An encode is written as
  `<name>.part.mkv` and renamed when HandBrake finishes, so an interrupted
  one is redone rather than kept half-written.

Before finishing, check for failures:

    grep FAIL "$LIBRARY/.ripstate/rip.log" "$LIBRARY/.ripstate/encode.log"
    grep -i WARN "$LIBRARY/.ripstate/rip.log"

`WARN` lines are worth reading. A disc routing to a folder that already holds
finished output is ripped alongside it as `<name> (<label>)/` rather than over
it, and says so there - reconcile those by hand.

Every rip also keeps MakeMKV's own output at
`$LIBRARY/.ripstate/logs/<label>.mkv.log`, and every encode HandBrake's at
`$LIBRARY/.ripstate/logs/<file>.hb.log`. That is where a read error, a
retried sector or a title MakeMKV gave up on shows.

## Upgrading ripd mid-run

`swapd` swaps in a new `ripd.py` without interrupting a rip: save the new
version as `backcrack/ripd.py.new` beside `backcrack/ripd.py` and run `swapd`. It waits until no
drive is ripping (giving up after 4 hours), swaps the file in, restarts
`ripd` and exits. `swap.log` in `.ripstate` records what it did.

## Settings

Each setting is read, in order, from an environment variable, then
`settings.env` in your config directory, then the default in
`backcrack/config.py`. So a one-off `LIBRARY=... ripd` overrides
everything, and `settings.env` holds what you want every time.

`s` in `watch` opens a settings screen over most of them and writes your
changes to `settings.env`. The tool paths below aren't on it; set those in
`settings.env` or the environment. The daemons read their settings at
startup, so restart `ripd`, `encd` or `sortd` to pick a change up.

The ones not covered elsewhere in this README:

| Setting | Default | What it does |
|---|---|---|
| `MKVCON`, `HBCLI`, `CDPARANOIA`, `CD_DISCID`, `FLAC`, `FFMPEG` | found on PATH (MakeMKV at `/Applications/MakeMKV.app`) | tool paths |
| `NOTIFY_ENCODES` | `0` | also push when a disc finishes encoding |
| `ACCENT` | `green` | the accent colour: `green`, `red`, `yellow`, `blue`, `magenta`, `cyan` (your terminal's own), `amber`, `coral`, `rose`, `lavender`, `sky`, `mint`, or `#RRGGBB` |
| `WATCH_INTERVAL` | `1` | seconds between `watch` refreshes |
| `SORT_INTERVAL` | `5` | seconds between `sortd` passes |
| `TOTAL_DISCS` | `0` | discs in this run; shows a progress bar and ETA in `watch` (0 hides them) |
| `WINDOW`, `ACTIVE_S`, `FALLBACK_KB` | `20`, `90`, `7340032` | `watch`: seconds behind its MB/s figure, how long an idle rip stays listed, disc size (KB) assumed until the real one is known |
| `READ_MB`, `SKIP_MB`, `STALL_S`, `DISC_BYTES`, `RESULTS` | `600`, `1000`, `60`, `7000000000`, `~/diskspeed.txt` | `diskspeed`: MB read per drive, MB skipped first, seconds without progress before giving up, disc size behind its minutes-per-disc estimate, results file |

An older `INTERVAL` still works as a fallback for both `WATCH_INTERVAL` and
`SORT_INTERVAL`.

`ONESHOT=1 watch` (print one frame and exit), `ONCE=1 sortd` (one pass) and
`DRYRUN=1 sortd` (say what it would do, change nothing) are per-run switches,
read from the environment only.

### Your files live outside the checkout

Everything personal (your saved settings, your disc overrides, your episode
titles) lives in a config directory, not in this repo. The checkout stays
code-only, and nothing of yours needs gitignoring or risks being committed.

| File | What it is |
|---|---|
| `settings.env` | saved settings, one `NAME=value` per line; the `watch` settings screen writes it |
| `labels.map` | manual disc → %token% overrides |
| `episodes.map` | per-season episode titles, for `namer` |

The directory is `$BACKCRACK_CONFIG_DIR` if set, else
`$XDG_CONFIG_HOME/backcrack`, else `~/.config/backcrack`. It is created on
first run. Point `BACKCRACK_CONFIG_DIR` somewhere else to keep separate sets
of overrides for separate shelves.

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
type, which a bare `extras/` is not.

**namer then deletes each disc's `source/` folder, the lossless rip**, once
every encoded episode from that disc has moved into the season. A disc
whose episodes didn't all move (a name collision, or a disc with no
encoded episodes) keeps its `source/`, and namer says which it kept and
which it deleted. Empty disc folders are removed; anything it doesn't
recognise is left where it is.

`episodes.map` lives in your config directory (see [Settings](#settings)) and
is one block per season: a `# Season N` header, then one title per line in
broadcast order. Renaming assumes disc and title order matches
broadcast order, which is the normal convention for a season box set.

A season is refused outright, with nothing touched, if its encoded-file
count doesn't exactly match its title count, or if any encoded file is empty
or still being written. A mismatch means a disc is still ripping, a title
never encoded, or one was wrongly deduped, and guessing would mislabel every
episode after the gap.

namer only understands the default layout: season folders named
`Season N` directly under `LIBRARY`, holding one folder per disc (the
default `PATTERN_VIDEO`, `Season %season%/Disc %disc%`), with episodes in
`encoded/` and extras in `extras/` (the default `DURATION_CLASSES` folder
names). With another pattern or other folder names, don't use it.

## Tools it needs

- **MakeMKV** (`makemkvcon`) - for DVD/Blu-ray. `brew install --cask makemkv`.
- **HandBrakeCLI** - to encode video. `brew install handbrake`.
- **cdparanoia** - for audio CDs. `brew install cdparanoia`.
- **flac** (or ffmpeg) - to compress ripped audio. `brew install flac`.
  With neither, tracks are copied to `encoded/` as `.wav`.
- **cd-discid** - optional, for automatic artist/album lookup on audio CDs.
  `brew install cd-discid`. Without it, every CD goes to `UNSORTED/` until
  you add a `labels.map` line.

A missing tool gets a warning at startup rather than a silent failure:
`ripd` warns about makemkvcon, cdparanoia and cd-discid, and `encd` about
HandBrakeCLI and flac/ffmpeg. The daemon still runs; that disc kind just
won't rip or encode until the tool is installed.

## Storage

Video eats far more space than audio. `namer` deletes a finished TV
season's `source/` folders for you (see above). For anything `namer` doesn't
handle (audio, films, another pattern), delete a disc's `source/` by hand
once you've checked its encoded output.
