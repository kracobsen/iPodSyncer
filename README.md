# iPodSyncer

macOS CLI for syncing music, podcasts, and audiobooks to an iPod Classic 6G running factory Apple firmware.

Status: in progress — phases 0–11 of [plans/ipodsyncer-v0.1.md](./plans/ipodsyncer-v0.1.md) landed. Music sync, artwork, transcode, prune, and podcast classification + flagged playlist work on-device. Per-track podcast playback flags (phase 12) + audiobooks / playlists still pending. See [FEASIBILITY.md](./FEASIBILITY.md) for the spec.

## Scope (v0.1)

- **Target:** iPod Classic 6G / 6.5G only, factory firmware, USB (Apple Silicon Mac on Sequoia).
- **Media:** music, podcasts, audiobooks.
- **Input:** local source tree (`music/`, `podcasts/<show>/`, `audiobooks/<author>/*.m4b`).
- **Transcoding:** auto FLAC/Opus/Ogg → AAC ~256k VBR via ffmpeg. Originals untouched.
- **DB:** libgpod (gerion0 fork) handles iTunesDB / iTunesCDB / ArtworkDB + hash58.

Out of scope: other iPod models, iPod Touch, Rockbox, RSS fetching, audiobook assembly from MP3 folders, Apple Music / DRM.

## Requirements

- macOS Sequoia 15.x (Tahoe 26.x also works for sync; Tahoe broke Finder sync, which is why this tool exists)
- Apple Silicon (arm64); Intel untested
- Python 3.12+
- Homebrew (primary supported path) or MacPorts
- Full Disk Access granted to the terminal/CLI (System Settings → Privacy & Security → Full Disk Access)

## Bootstrap (phase 0)

One command builds `gerion0/libgpod` + the `python-gpod` bindings via Homebrew, clones the source into `vendor/libgpod/`, and installs into the Homebrew Python's site-packages:

```
./scripts/bootstrap.sh
```

The script installs these Homebrew formulae: `pkg-config meson ninja swig glib libplist sqlite gdk-pixbuf libxml2 pygobject3 ffmpeg`, plus `mutagen` into brew's Python. `PKG_CONFIG_PATH` is extended to include the keg-only `sqlite` and `libxml2` pkgconfig dirs.

Verify (the script does this automatically at the end):

```
.venv/bin/python -c "import gpod; print(gpod.version)"
```

The script drops you with a project venv at `.venv/` containing both the native `libgpod.dylib` (under `.venv/lib/`) and the Python bindings (`gpod` module in `.venv/lib/python3.14/site-packages/`). Activate it for day-to-day work:

```
source .venv/bin/activate
```

### MacPorts alternative

`sudo port install libgpod +python312` provides the bindings without a source build. The rest of the tool still runs from a brew-installed Python; adjust `PATH` / `PYTHONPATH` accordingly. Not covered by `bootstrap.sh`.

## Commands

All device-touching commands auto-mount the iPod (raw `mount_hfs`, bypassing Finder / `diskutil`) and eject when done. Every mutation snapshots `iTunesDB` / `iTunesCDB` / `ArtworkDB` under `~/Library/Application Support/ipodsync/snapshots/<guid>/<ts>/` first, so a bad run rolls back with `ipodsync restore`.

Implemented:

- `ipodsync doctor` — host checks (macOS, Python, ffmpeg/ffprobe, libgpod import, FDA, brew/ports). Exits non-zero on failure with the fix command.
- `ipodsync mount [--dry-run]` — auto-detects a plugged-in iPod Classic, mounts it, prints mount point + FireWireGUID.
- `ipodsync eject` — unmount cleanly.
- `ipodsync ls [--kind music|podcast|book] [--json]` — read-only track listing.
- `ipodsync add <file>` — add one audio file. Probes codec, transcodes non-native formats to AAC ~256k VBR (cached), extracts embedded / sibling `cover.*` artwork, dedupes by source sha1. `--strict` (global) refuses to transcode.
- `ipodsync rm [TRACK_IDS...] [--filter KEY=VALUE] [--kind K] [--dry-run] [-y]` — delete tracks. Positional ids and/or `--filter` on `title`/`artist`/`album`/`genre` (case-insensitive equality). Refuses without a selector. Removes from all playlists, deletes the `F##` file, drops the DB row.
- `ipodsync sync <src> [--dry-run] [--prune]` — mirror `<src>/music/**` and `<src>/podcasts/<show>/**` to the iPod. Podcasts land in a dedicated podcast-flagged playlist, grouped by show folder name (libgpod writes mhip groups on `track.album`, which sync overrides to the show name). Podcast tracks do not appear under Songs / Albums / Artists. Idempotent: a second run on an unchanged tree is a no-op. `--prune` also removes on-device tracks no longer in the source and sweeps orphan `F##` files that aren't referenced by any DB track.
- `ipodsync snapshot` — take a DB snapshot without mutating anything.
- `ipodsync restore [--snapshot TS|latest] [-y]` — list snapshots or roll one back (takes a pre-restore snapshot first).
- `ipodsync version`

Stubbed (not yet implemented): `playlist create|add|rm`.

### Typical flow

```
ipodsync doctor                         # verify host
ipodsync sync ~/Music/ipod --dry-run    # preview
ipodsync sync ~/Music/ipod              # add new tracks
ipodsync sync ~/Music/ipod --prune      # also remove deletions + orphans
ipodsync ls                             # inspect
ipodsync eject
```

### Source tree layout

```
<src>/
  music/<artist>/<album>/<tracknum-title>.{mp3,m4a,flac,opus,ogg,wav,aiff}
  podcasts/<show>/<episode>.{mp3,m4a,flac,opus,ogg,wav,aiff}
  audiobooks/<author>/<title>.m4b                # pending (phase 13)
  playlists/<name>.m3u                           # pending (phase 14)
```
