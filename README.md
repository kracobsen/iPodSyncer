# iPodSyncer

macOS CLI for syncing music, podcasts, and audiobooks to an iPod Classic 6G running factory Apple firmware.

Status: design phase. See [FEASIBILITY.md](./FEASIBILITY.md) and [plans/](./plans/).

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
