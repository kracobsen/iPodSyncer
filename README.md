# iPodSyncer

macOS CLI for syncing music, podcasts, and audiobooks to an iPod Classic 6G / 6.5G running factory Apple firmware. Built because Tahoe broke Finder sync.

**Scope:** Classic 6G/6.5G only · USB · factory firmware · Apple Silicon Mac · macOS Sequoia 15.x or Tahoe 26.x.

## Install

Requires [uv](https://docs.astral.sh/uv/), Homebrew, and Full Disk Access for your terminal (System Settings → Privacy & Security → Full Disk Access — Sequoia 15.4+ blocks `mount_hfs` without it).

```sh
brew install uv
./scripts/bootstrap.sh    # installs native deps, builds libgpod, sets up .venv
```

Optional, for cleaner transcodes — replaces stock ffmpeg with one that has `libfdk_aac`:

```sh
brew uninstall ffmpeg
brew tap homebrew-ffmpeg/ffmpeg
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-fdk-aac
```

`ipodsync` auto-detects it; `doctor` reports availability.

## Quick start

```sh
uv run ipodsync doctor                          # verify host + device
uv run ipodsync sync ~/Music/ipod --dry-run     # preview
uv run ipodsync sync ~/Music/ipod               # add new tracks
uv run ipodsync sync ~/Music/ipod --prune       # also remove deletions + orphans
uv run ipodsync ls
uv run ipodsync eject
```

Or activate once: `source .venv/bin/activate && ipodsync …`

## Source tree layout

```
<src>/
  music/<artist>/<album>/<tracknum-title>.{mp3,m4a,flac,opus,ogg,wav,aiff}
  podcasts/<show>/<episode>.{mp3,m4a,flac,opus,ogg,wav,aiff}
  audiobooks/<author>/<title>.m4b           # .m4a accepted, renamed on copy
  playlists/<name>.{m3u,m3u8}               # one playlist per file
```

- **Music** lands under Songs / Albums / Artists.
- **Podcasts** land under the Podcasts menu (blue-dot, resume-on-pause), grouped by show folder. Excluded from the main library.
- **Audiobooks** land under Books. Chapter atoms preserved.
- **Playlists** are non-smart, named after the basename. Order preserved. Missing entries warn and skip. Re-running replaces contents.

Non-native codecs (FLAC/Opus/Vorbis/…) auto-transcode to AAC ~256k VBR. Originals untouched. Cached at `~/Library/Caches/ipodsync/transcode/`.

## Commands

| Command | What it does |
| --- | --- |
| `doctor [--device]` | Host checks: macOS, ffmpeg, libgpod, FDA, libfdk_aac. With `--device`: GUID, free space, DB roundtrip, track counts. |
| `mount` / `eject` | Manual mount/unmount. Other commands auto-mount. |
| `ls [--kind music\|podcast\|book] [--json]` | Read-only listing. |
| `add <file>` | Add one file. Probes, transcodes if needed, dedupes by sha1, extracts cover art. |
| `rm [IDS…] [--filter KEY=VALUE] [--kind K] [--dry-run] [-y]` | Delete tracks. `--filter` keys: `title`/`artist`/`album`/`genre` (exact, case-insensitive). Refuses without a selector. |
| `sync [<src>] [--dry-run] [--prune]` | Mirror source tree to device. Idempotent. `<src>` falls back to `config.source_dir`. `--prune` removes on-device tracks not in the source, orphan F## files, and ipodsync-owned playlists whose M3U disappeared. |
| `config init [-f]` / `config show` | Write or print the config file. |
| `version` | Print package version. |

Global `--strict`: refuse to transcode (errors instead of re-encoding non-native codecs).

## Configuration

All keys optional. `~/.config/ipodsync/config.toml`:

| Key | Default | Effect |
| --- | --- | --- |
| `source_dir` | unset | Default `<src>` for `sync`. |
| `strict` | `false` | Same as `--strict`. |
| `log_level` | `"INFO"` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |

```sh
uv run ipodsync config init      # writes a commented example
uv run ipodsync config show      # prints resolved values
```

## Safety

Refuses to operate on a device with `.rockbox/`. Recovery relies on your local source tree: re-running `sync` rebuilds the iPod from `<src>/`. Orphan F## files left by an interrupted write are cleaned up by the next `sync --prune`.

## Troubleshooting

- **`diskutil mount` won't touch the iPod (Sequoia 15.4+).** Expected — `ipodsync mount` calls `mount_hfs` directly. Make sure FDA is granted.
- **`import gpod` fails.** Re-run `./scripts/bootstrap.sh`. Verify with `uv run python -c "import gpod; print(gpod.version)"`.
- **iPod not detected.** Check `diskutil list` (it shows up even when Finder ignores it). `ipodsync doctor --device` exercises the full path.

See [FEASIBILITY.md](./FEASIBILITY.md) for the spec, [plans/ipodsyncer-v0.1.md](./plans/ipodsyncer-v0.1.md) for phase history, and [FIX_PROPOSAL.md](./FIX_PROPOSAL.md) for the current code-sweep backlog.
