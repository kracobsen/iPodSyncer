# iPodSyncer

macOS CLI for syncing music, podcasts, and audiobooks to an iPod Classic 6G / 6.5G running factory Apple firmware. Born because Tahoe (macOS 26) broke Finder sync for Classic iPods.

**Status:** v0.1 feature-complete. Phases 0–16 of [plans/ipodsyncer-v0.1.md](./plans/ipodsyncer-v0.1.md) have landed: bootstrap, CLI, host doctor, mount/eject, ls, snapshot/restore, single-file add (passthrough + dedupe + artwork + transcode), idempotent music sync, rm + `--prune`, podcast classification + flagged playlist + per-track playback flags, audiobook routing, M3U playlists, on-device doctor, and user config. See [FEASIBILITY.md](./FEASIBILITY.md) for the spec.

## Scope (v0.1)

- **Target:** iPod Classic 6G / 6.5G only, factory firmware, USB.
- **Host:** Apple Silicon Mac (Intel untested), macOS Sequoia 15.x or Tahoe 26.x.
- **Media:** music, podcasts, audiobooks.
- **Input:** local source tree (`music/`, `podcasts/<show>/`, `audiobooks/<author>/*.m4b`).
- **Transcoding:** auto FLAC/Opus/Ogg → AAC ~256k VBR via ffmpeg. Originals untouched.
- **DB:** libgpod (gerion0 fork) handles iTunesDB / iTunesCDB / ArtworkDB + hash58.

Out of scope: other iPod models, iPod Touch, Rockbox, RSS fetching, audiobook assembly from MP3 folders, Apple Music / DRM.

## Requirements

- macOS Sequoia 15.x or Tahoe 26.x (Tahoe is why this exists — it broke Finder sync).
- Apple Silicon (arm64); Intel untested.
- [uv](https://docs.astral.sh/uv/) — drives the venv, deps, and CLI invocations.
- Homebrew — installs the native libs libgpod links against (glib, libplist, sqlite, libxml2, libusb, ffmpeg).
- Full Disk Access granted to the terminal/CLI (System Settings → Privacy & Security → Full Disk Access). Sequoia 15.4.1+ blocks `diskutil mount` for iPod Classic; this tool calls `mount_hfs` directly, which requires FDA.

Install uv if you don't have it:

```
brew install uv
```

## Bootstrap (one command)

```
./scripts/bootstrap.sh
```

The script:

1. `brew install`s native deps: `pkg-config meson ninja swig glib libplist sqlite gdk-pixbuf libxml2 libusb pygobject3 ffmpeg`.
2. Creates a uv-managed venv at `.venv/` using brew's Python (so the meson-built bindings link against the same Python ABI as everything else on the system).
3. `uv sync --inexact` — installs PyPI deps (`typer`, `mutagen`, `Pillow`, `libusb1`) and the project itself in editable mode. `--inexact` keeps subsequent syncs from pruning the gpod bindings dropped in by step 5.
4. Clones `gerion0/libgpod` into `vendor/libgpod/` and patches two upstream issues (libplist pkg-config name; bindings install path on macOS Homebrew Python).
5. Builds with meson and installs the native `libgpod.dylib` into `.venv/lib/` and the `gpod` Python bindings into `.venv/lib/python3.x/site-packages/`.

Idempotent — re-running updates the clone and rebuilds.

### Optional: libfdk_aac for cleaner transcodes

Stock Homebrew's `ffmpeg` ships with the built-in `aac` encoder, which audibly smears transients on dense material (strings, cymbals, loud FLAC masters). `libfdk_aac` is substantially cleaner but non-free, so it lives in a tap:

```
brew uninstall ffmpeg
brew tap homebrew-ffmpeg/ffmpeg
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-fdk-aac
```

`ipodsync` auto-detects `libfdk_aac` at transcode time and uses it (VBR q=5, ~224 kbps avg) when present. `ipodsync doctor` reports availability.

## Running the CLI

Drive everything through `uv run` — uv keeps the venv in sync with `pyproject.toml` + `uv.lock` automatically:

```
uv run ipodsync doctor
uv run ipodsync sync ~/Music/ipod --dry-run
```

Or activate the venv once and use the bare command:

```
source .venv/bin/activate
ipodsync doctor
```

## Commands

All device-touching commands auto-mount the iPod (raw `mount_hfs`, bypassing Finder / `diskutil`) and eject when done. Every mutation snapshots `iTunesDB` / `iTunesCDB` / `ArtworkDB` under `~/Library/Application Support/ipodsync/snapshots/<guid>/<ts>/` first, so a bad run rolls back with `ipodsync restore`.

| Command | What it does |
| --- | --- |
| `ipodsync doctor [--device]` | Host checks: macOS, Python, ffmpeg/ffprobe, libgpod import, FDA, brew/ports, libfdk_aac. With `--device`, also: GUID, free space, expected dirs, DB roundtrip, track counts by kind, snapshot count/size. |
| `ipodsync mount [--dry-run]` | Auto-detect a plugged-in iPod Classic, mount it, print mount point + FireWireGUID. |
| `ipodsync eject` | Unmount cleanly. |
| `ipodsync ls [--kind music\|podcast\|book] [--json]` | Read-only track listing. |
| `ipodsync add <file>` | Add one audio file. Probes codec, transcodes non-native formats to AAC ~256k VBR (cached), extracts embedded / sibling `cover.*` artwork, dedupes by source sha1. `--strict` (global) refuses to transcode. |
| `ipodsync rm [TRACK_IDS...] [--filter KEY=VALUE] [--kind K] [--dry-run] [-y]` | Delete tracks. Positional ids and/or `--filter` on `title`/`artist`/`album`/`genre` (case-insensitive equality). Refuses without a selector. Removes from all playlists, deletes the `F##` file, drops the DB row. |
| `ipodsync sync [<src>] [--dry-run] [--prune]` | Mirror `<src>/music/**`, `<src>/podcasts/<show>/**`, `<src>/audiobooks/<author>/*.{m4b,m4a}`, and `<src>/playlists/*.{m3u,m3u8}` to the iPod. `<src>` falls back to `config.source_dir`. Idempotent. `--prune` also removes on-device tracks no longer in the source, sweeps orphan `F##` files, and deletes ipodsync-owned playlists whose M3U disappeared. |
| `ipodsync snapshot` | Take a DB snapshot without mutating anything. |
| `ipodsync restore [--snapshot TS\|latest] [-y]` | List snapshots or roll one back (takes a pre-restore snapshot first). |
| `ipodsync config init [-f]` | Write a commented `~/.config/ipodsync/config.toml` example. |
| `ipodsync config show` | Print resolved config (file values + defaults). |
| `ipodsync version` | Print package version. |

**Stubs (not yet implemented):** `playlist create|add|rm` (phase 14).

## Configuration

Optional. All keys are optional; with no file present every default applies.

```
ipodsync config init       # writes ~/.config/ipodsync/config.toml (commented)
ipodsync config show       # prints resolved values
```

| Key | Default | Effect |
| --- | --- | --- |
| `source_dir` | unset | Used by `ipodsync sync` when no positional path is given. |
| `strict` | `false` | Refuse transcoding by default (same as passing `--strict`). |
| `log_level` | `"INFO"` | Stdlib logging level — `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `snapshot_retention` | `10` | Pre-write DB snapshots kept per device; older ones are pruned automatically. |

### Sync routing details

- **Music** (`music/<artist>/<album>/...`): `mediatype=0x01`, in MPL, appears under Songs / Albums / Artists.
- **Podcasts** (`podcasts/<show>/...`): `mediatype=0x04` plus per-track `mark_unplayed=0x02`, `flag4=1`, `skip_when_shuffling=1`, `remember_playback_position=1`. Excluded from MPL. Land in a dedicated podcast-flagged playlist, grouped by show folder name (libgpod writes the `mhip` group key from `track.album`, which sync overrides to the show name). Do not appear under Songs / Albums / Artists. The blue-dot unplayed indicator and resume-on-pause both work on 6G firmware.
- **Audiobooks** (`audiobooks/<author>/*.m4b`): `mediatype=0x08`, land under the Books menu (firmware filters Songs/Albums/Artists on that bit). `.m4a` sources are auto-renamed to `.m4b` on copy via a cache symlink (the extension is firmware-load-bearing). Chapterless audiobook inputs log a warning.
- **Playlists** (`playlists/*.m3u`, `*.m3u8`): each file becomes a non-smart playlist named after the basename. Entries are resolved against the source root first, then the M3U's parent dir; absolute paths are honoured. Order is preserved. Missing entries print a warning and are skipped (never abort). Re-running replaces playlist contents in place. Ownership is tracked per-device in `~/Library/Application Support/ipodsync/playlists/<guid>.json` so `--prune` removes only ipodsync-created playlists whose M3U disappeared — manually-created device playlists are left alone.

### Typical flow

```
uv run ipodsync doctor                         # verify host
uv run ipodsync config init                    # optional: pin source_dir, retention, etc.
uv run ipodsync sync ~/Music/ipod --dry-run    # preview
uv run ipodsync sync ~/Music/ipod              # add new tracks
uv run ipodsync sync ~/Music/ipod --prune      # also remove deletions + orphans
uv run ipodsync ls                             # inspect
uv run ipodsync eject
```

If `source_dir` is set in the config, the path can be omitted: `uv run ipodsync sync --prune`.

### Source tree layout

```
<src>/
  music/<artist>/<album>/<tracknum-title>.{mp3,m4a,flac,opus,ogg,wav,aiff}
  podcasts/<show>/<episode>.{mp3,m4a,flac,opus,ogg,wav,aiff}
  audiobooks/<author>/<title>.m4b               # .m4a accepted and renamed
  playlists/<name>.{m3u,m3u8}                   # one playlist per file
```

## Safety

- Every mutating command snapshots `iTunesDB`, `iTunesCDB`, and `ArtworkDB` to `~/Library/Application Support/ipodsync/snapshots/<guid>/<ISO-timestamp>/` before writing.
- `ipodsync restore --snapshot latest` rolls back atomically (and snapshots the pre-restore state first).
- `ipodsync rm` and `sync --prune` both go through the snapshot path.
- Refuses to operate on a device with a `.rockbox/` directory present.

## Development

Dev deps (ruff, mypy) live in the `dev` dependency group and are installed by default with `uv sync`:

```
uv run ruff check
uv run mypy
```

Lockfile (`uv.lock`) is checked in for reproducible installs. Bump deps with `uv add`, `uv lock --upgrade-package <name>`, etc.

## Layout

```
src/ipodsync/
  cli.py                  typer entrypoint
  config.py               ~/.config/ipodsync/config.toml loader
  doctor.py               host + on-device checks
  ls.py                   read-only listing
  add.py                  single-file add
  rm.py                   track deletion
  sync.py                 source-tree mirror
  playlist.py             M3U parser + per-device ownership ledger
  restore.py              snapshot + restore
  device/
    detect.py             iPod detection (diskutil + IOKit)
    mount.py              raw mount_hfs
    ops.py                mount/eject orchestration
    sysinfo.py            SysInfoExtended fetch via libusb
    snapshot.py           pre-write DB backups
    gpod.py               python-gpod facade (writes, podcast/audiobook flags)
  pipeline/
    probe.py              ffprobe wrapper
    transcode.py          ffmpeg AAC transcode (libfdk_aac when available)
    artwork.py            embedded + sibling cover extraction, RGB565 thumbnails
scripts/bootstrap.sh      one-shot toolchain build
plans/ipodsyncer-v0.1.md  vertical-slice phase plan
FEASIBILITY.md            authoritative spec
vendor/libgpod/           cloned by bootstrap.sh (gitignored)
```

## Troubleshooting

- **`diskutil mount` won't touch the iPod (Sequoia 15.4.1+).** Expected — that's why `ipodsync mount` calls `mount_hfs` directly. Make sure the terminal has Full Disk Access.
- **`import gpod` fails.** Re-run `./scripts/bootstrap.sh`. Verify with `uv run python -c "import gpod; print(gpod.version)"`.
- **`uv sync` removed the gpod bindings.** Shouldn't happen — gpod is meson-installed without a `.dist-info`, so uv ignores it. Bootstrap still passes `--inexact` as a safeguard. If gpod ever does disappear, re-run `./scripts/bootstrap.sh`.
- **iPod not detected.** Verify it shows up in `diskutil list` (it will, even when Finder ignores it). FireWireGUID is read via libusb; `ipodsync doctor --device` exercises the full path.
