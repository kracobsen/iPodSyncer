# Plan: iPodSyncer v0.1

> Source PRD: [../FEASIBILITY.md](../FEASIBILITY.md)
> Scope decisions log: FEASIBILITY.md Appendix B
> Created: 2026-04-19

## How to use this plan (for a fresh context)

This plan is designed to be resumed from a clean conversation. When starting a new session:

1. Read [../FEASIBILITY.md](../FEASIBILITY.md) in full — it is the authoritative spec.
2. Read this plan.
3. Run `git log --oneline` to see which phases have landed.
4. Pick the lowest-numbered unfinished phase.
5. Work one phase at a time. Each phase commits independently with a message like `phase-N: <title>`.
6. **Do not skip phases out of order** — each slice assumes prior slices are in place.

Phases are **vertical slices**: each cuts through every layer that exists at that point (CLI → pipeline → libgpod → device) and is independently demoable. Prefer shipping a thin phase over widening one.

---

## Goal (one-liner)

macOS Python CLI that syncs music, podcasts, and audiobooks from a local source tree to an **iPod Classic 6G** on factory firmware, using libgpod (gerion0 fork) for iTunesDB + hash58, and ffmpeg for transcode.

## Out of scope for v0.1

Other iPod models, iPod Touch, Rockbox, RSS fetching, audiobook assembly from MP3 folders, Apple Music / DRM, speed-up / loudnorm / MusicBrainz enrichment (those are future pipeline stages).

---

## Architectural decisions (durable)

These apply to every phase. Do not revisit without explicit user approval.

- **Language / runtime**: Python 3.12+. Distributed via `pipx`.
- **DB library**: libgpod via `python-gpod`, built from the [gerion0 fork](https://github.com/gerion0/libgpod). MacPorts is the documented default for the native deps; Homebrew acceptable if user has it. Do **not** hand-roll iTunesDB writes at the mhit/mhod level in v0.1.
- **Hashing**: hash58 only (Classic 6G/6.5G). libgpod handles it.
- **Mount strategy**: invoke raw `mount_hfs` / `mount_msdos` directly, not `diskutil mount` (Sequoia 15.4.1+ blocks `diskutil` for iPod Classic). CLI requires Full Disk Access.
- **Source-tree layout** (input to `sync`):
  ```
  <source-dir>/
    music/<artist>/<album>/<tracknum-title>.{mp3,m4a,flac,opus,ogg,wav,aiff}
    podcasts/<show>/<episode>.{mp3,m4a}
    audiobooks/<author>/<title>.m4b
    playlists/<name>.m3u
  ```
- **On-iPod layout**: all audio lives flat in `/iPod_Control/Music/F00`…`F49`, randomized 4-letter filenames, extension preserved. Classification is 100% iTunesDB-driven, not folder-driven. `.m4b` vs `.m4a` extension is load-bearing for audiobooks.
- **Cache dir**: `~/Library/Caches/ipodsync/` (transcode outputs, probe results, artwork thumbnails). Keyed by content-hash + stage-version.
- **Snapshot dir**: `~/Library/Application Support/ipodsync/snapshots/<device-guid>/<ISO-timestamp>/` (pre-sync copies of `iTunesDB`, `iTunesCDB`, `ArtworkDB`).
- **Config**: `~/.config/ipodsync/config.toml`.
- **Pipeline contract**: a stage is `(Item, Context) -> Item`. Each stage declares `version: int`. Cache keys incorporate stage versions so bumping a stage invalidates only its downstream cache entries. Stage order for v0.1: `classify → probe → transcode → tag-normalize → artwork → cache-key → device-write → db-commit`.
- **Media kinds** (from libgpod's `itdb.h`):
  - Music: `mediatype=0x01`
  - Podcast: `mediatype=0x04`, in a dedicated `podcastflag=1` playlist, **not** in MPL
  - Audiobook: `mediatype=0x08`, extension **must** be `.m4b` (or `.aa`)
- **Transcoding target**: AAC ~256 kbps VBR (`ffmpeg -c:a aac -q:a 1.4` equivalent; validate during phase 8). Originals left untouched; converted copies cached.
- **Supported input codecs on iPod Classic 6G** (passthrough): MP3, AAC (.m4a), Apple Lossless (.m4a), AIFF, WAV. Everything else gets transcoded. Audible `.aa/.aax` passthrough DRM is out of scope — refuse.
- **Safety**: every mutating command snapshots the DBs before writing; `ipodsync restore` rolls back to the most recent (or specified) snapshot.
- **Rockbox refusal**: if `.rockbox/` directory exists on device, abort with a clear message.
- **Commit style**: one commit per phase, branch `main`, message format `phase-N: <title>`.

### Module layout (durable)

```
ipodsyncer/
  src/ipodsync/
    cli.py                  typer entrypoint
    config.py               TOML loader
    device/
      detect.py             find iPod via IOKit / diskutil list
      mount.py              raw mount/unmount
      gpod.py               thin facade over python-gpod
      snapshot.py           pre-sync backup/restore of DBs
    pipeline/
      base.py               Stage protocol, Item, Context, Cache
      classify.py
      probe.py              ffprobe wrapper
      transcode.py          ffmpeg wrapper
      tags.py               mutagen-based tag normalization
      artwork.py            Pillow; per-model thumbnail sizes
      writer.py             device-write stage → gpod.py
    model/
      item.py               Item, Kind enum
      layout.py             parse source tree
      playlist.py           M3U parsing → iPod playlists
  tests/
  pyproject.toml
  README.md
  FEASIBILITY.md
  plans/
```

---

## Phase 0: Build libgpod on arm64 (spike)

**User stories**: M0 — risk-retire the dependency stack before writing any app code.

### What to build

Document and automate the build of `gerion0/libgpod` + `python-gpod` bindings on Apple Silicon macOS. Produce a script (`scripts/bootstrap.sh` or similar) that a fresh machine can run. Decide MacPorts vs Homebrew vs source build, pick one as the documented default.

### Acceptance criteria

- [ ] `python -c "import gpod; print(gpod.__version__)"` works in a fresh venv
- [ ] `python -c "import gpod; gpod.Database('/nonexistent/path')"` raises the expected error (not an import error)
- [ ] Bootstrap script in repo; README lists prereqs and the single command to run
- [ ] Document which native deps are needed (glib, libplist, libxml2, sqlite3) and where `PKG_CONFIG_PATH` must point
- [ ] Works on a test device (Apple Silicon Mac, macOS Sequoia)

---

## Phase 1: Project skeleton + CLI entrypoint

**User stories**: foundation for all subsequent phases.

### What to build

`pyproject.toml` with `typer`, `mutagen`, `Pillow`, `python-gpod` deps and a `[project.scripts]` entry for `ipodsync`. A bare CLI with `--help`, `version`, and a stub for every top-level command listed in FEASIBILITY §6 (`mount`, `ls`, `add`, `rm`, `sync`, `playlist`, `doctor`, `eject`). Stubs print "not implemented".

### Acceptance criteria

- [ ] `pipx install .` (or `pip install -e .`) installs the binary
- [ ] `ipodsync --help` lists all top-level commands
- [ ] `ipodsync version` prints the package version
- [ ] Every command stub exits cleanly with a "not implemented" message
- [ ] Basic `ruff`/`mypy` config in `pyproject.toml`; `ruff check` passes

---

## Phase 2: `doctor` — host-only checks

**User stories**: users can verify their Mac is ready before plugging in a device.

### What to build

`ipodsync doctor` runs a series of non-device checks and prints a status table: macOS version, Python version, ffmpeg/ffprobe presence + version, libgpod importability, Full Disk Access likely granted (heuristic: can we read `~/Library/Mail`?), MacPorts/Homebrew present. Exits non-zero if any required check fails.

### Acceptance criteria

- [ ] Shows clear OK / WARN / FAIL per check
- [ ] Exits 0 when all required checks pass
- [ ] Tells user exactly how to fix each failing check (e.g. "install ffmpeg: `brew install ffmpeg`")
- [ ] Runs in <1s

---

## Phase 3: `mount` / `eject`

**User stories**: user can mount and unmount an iPod Classic without fighting Finder.

### What to build

`ipodsync mount` auto-detects a plugged-in iPod Classic (via `diskutil list` parsing or IOKit), mounts it using raw `mount_hfs` (or `mount_msdos` for FAT32) to a known path, and prints the mount point + device FireWireGUID. `ipodsync eject` syncs and unmounts. Prints useful errors on permission failures (FDA).

### Acceptance criteria

- [ ] `mount` succeeds on a real iPod Classic 6G on Sequoia 15.x
- [ ] `mount --dry-run` shows what it would do without doing it
- [ ] `eject` unmounts cleanly
- [ ] Detects and refuses to proceed if `.rockbox/` is present on the device
- [ ] Prints FireWireGUID (needed downstream for hash58)

---

## Phase 4: `ls` — read-only track listing

**User stories**: user can verify the tool can read an existing iPod's library.

### What to build

`ipodsync ls [--kind music|podcast|book] [--json]` mounts (if not already), opens the iTunesDB via `python-gpod`, prints a table of tracks (title, artist, album, kind, size, duration). Read-only — no DB writes. Ends by unmounting cleanly.

### Acceptance criteria

- [ ] On a real iPod with tracks seeded by iTunes, lists all tracks correctly
- [ ] `--kind` filter works (classify by mediatype bits)
- [ ] `--json` emits a stable JSON schema (document it in a docstring)
- [ ] Does not modify the device's filesystem in any way
- [ ] Handles an iPod with zero tracks gracefully

---

## Phase 5: Atomic snapshot + `restore`

**User stories**: a user who runs a bad sync can revert in one command.

### What to build

A `snapshot()` helper that, before any DB-mutating operation, copies `iTunesDB`, `iTunesCDB`, and `ArtworkDB` (if present) to `~/Library/Application Support/ipodsync/snapshots/<guid>/<ISO-timestamp>/`. A `ipodsync restore [--snapshot TS]` command that lists snapshots and rolls back on confirmation. Snapshot creation is wired into a no-op command at this phase (e.g. a `--snapshot-only` flag on a dummy command) since no mutating commands exist yet.

### Acceptance criteria

- [ ] Snapshots land in the correct path, ISO-8601 timestamped
- [ ] `ipodsync restore` lists available snapshots
- [ ] `ipodsync restore --snapshot TS` copies files back atomically (stage then rename)
- [ ] Snapshot retention policy documented (keep last N, default 10)
- [ ] Test: artificially corrupt a DB, restore, verify bitwise match of the original

---

## Phase 6: `add` — one music file

**User stories**: user can add a single music file to the iPod.

### What to build

`ipodsync add <file.mp3|file.m4a>` copies the file into the iPod's `F##` pool with a randomized filename, creates an `Itdb_Track` via libgpod with `mediatype=0x01`, adds it to the master playlist, writes `iTunesDB` + `iTunesCDB` + hash58. No transcoding, no artwork, no pipeline yet. Hooks into phase 5's snapshot. On-device verification: the track appears under Songs after eject and plays.

### Acceptance criteria

- [ ] Adding a clean MP3 results in a playable track on the iPod
- [ ] Adding an `.m4a` AAC works identically
- [ ] Unsupported input codecs are refused with a clear error
- [ ] Snapshot is taken before the write
- [ ] Re-running `add` on the same file does not duplicate (content-hash dedupe)
- [ ] Integration test: add → eject → re-mount → `ls` shows the track

---

## Phase 7: Artwork stage

**User stories**: album art appears on the iPod for added tracks.

### What to build

A pipeline stage that extracts embedded cover art (via mutagen) or a sibling `cover.jpg`/`cover.png`, resizes to the Classic 6G thumbnail dimensions (320×320 full, 140×140 list — confirm exact sizes against libgpod's model table), encodes as RGB565 LE, and writes into `ArtworkDB` + `F1_*.ithmb` via libgpod's `itdb_track_set_thumbnails`. Wired into `add`.

### Acceptance criteria

- [ ] Adding a tagged MP3 with embedded art produces visible art on the iPod
- [ ] Adding a bare MP3 with a sibling `cover.jpg` works
- [ ] Missing art falls back gracefully (no error, no art on device)
- [ ] Artwork cache hits on re-runs (no re-resize)
- [ ] Extracted art survives `eject` → re-mount → `ls` round-trip

---

## Phase 8: Transcode stage

**User stories**: user can hand the tool FLAC/Opus/Ogg and have it play on the iPod.

### What to build

Pipeline stage that runs `ffprobe` to detect codec, then shells out to `ffmpeg` to transcode anything outside the passthrough set (MP3, AAC-in-M4A, ALAC-in-M4A, AIFF, WAV) to AAC ~256 kbps VBR in `.m4a` (or `.m4b` when `kind=audiobook`, forced later in phase 13). Output cached at `~/Library/Caches/ipodsync/transcode/<content-hash>-<stage-version>.m4a`. Preserves tags via `-map_metadata 0`. Wired into `add`.

### Acceptance criteria

- [ ] FLAC → AAC transcode produces a playable `.m4a` on the iPod
- [ ] Opus and Ogg Vorbis inputs also work
- [ ] Second run of `add` on same source file hits the cache (no re-transcode)
- [ ] Changing the transcode stage's `version` busts its cache
- [ ] Tags survive transcode
- [ ] `--strict` global flag refuses to transcode, fails instead

---

## Phase 9: `sync <src>` — music-only, idempotent

**User stories**: user can point the tool at a folder tree and have music mirrored to the iPod.

### What to build

`ipodsync sync <source-dir>` walks `<source-dir>/music/` per the layout in the architectural decisions, builds an `Item` per file, runs every item through the full pipeline (classify → probe → transcode → tags → artwork → cache-key → writer), commits the DB once at the end. Idempotent: a second run with no source changes is a no-op. No prune yet.

### Acceptance criteria

- [ ] Point at a music library of 100+ tracks, mirror completes end-to-end
- [ ] Second run changes nothing on device (DB bitwise stable, no re-copied files)
- [ ] Adding one new file to source + re-running syncs only that file
- [ ] Progress output (count, current file, ETA) shown
- [ ] Snapshot taken before the commit
- [ ] `--dry-run` prints the plan without writing

---

## Phase 10: `rm` + `sync --prune`

**User stories**: removing a file from the source tree removes it from the iPod on next sync.

### What to build

`ipodsync rm <track-id|--filter>` removes one or more tracks from the DB + deletes the underlying file from `F##`. `sync --prune` additionally removes any on-device tracks absent from the source. Both hook into snapshot.

### Acceptance criteria

- [ ] `rm` by track id removes track from `ls` output and deletes file
- [ ] `rm --filter "artist=Foo"` removes matching tracks
- [ ] `sync --prune` removes on-device tracks no longer in source
- [ ] `sync` without `--prune` leaves on-device extras alone
- [ ] Snapshot taken before deletion
- [ ] Orphan file cleanup: no dangling files in `F##` after `rm`

---

## Phase 11: Podcast classification + flagged playlist

**User stories**: files under `podcasts/<show>/` appear in the iPod's Podcasts menu, grouped by show.

### What to build

`classify` stage routes `podcasts/<show>/<episode>` items to `kind=podcast`. Writer sets `mediatype=0x04`, **excludes** these tracks from the master playlist, and puts them in a dedicated second master-like playlist with `podcastflag=1`. Show/episode grouping uses `mhip` items with `groupflag`/`groupid`/`groupref`, keyed on the show name (folder name). Exactly one podcast-flagged playlist is enforced. Verify MHSD type-3 ordering is correct via libgpod's writer (should be automatic).

### Acceptance criteria

- [ ] Syncing a tree with `podcasts/<show>/` produces a Podcasts menu on the iPod
- [ ] Each show shows as a group with its episodes nested
- [ ] Podcast tracks do NOT appear under Songs/Albums/Artists
- [ ] Exactly one podcast-flagged playlist exists after any number of syncs
- [ ] Episodes keep correct order (by filename / tracknumber tag)

---

## Phase 12: Podcast playback flags + podcast URL fields

**User stories**: podcast playback resumes at the right position and is excluded from shuffle.

### What to build

Set per-track flags on podcast items: `skip_when_shuffling=1`, `remember_playback_position=1`, `mark_unplayed=0x02` (unplayed → 0x01 when played — firmware handles the transition), `flag4=0x01` (or `0x02` for sub-info page). If the source file has podcast URL / RSS metadata (uncommon for hand-downloaded files), write `podcasturl` / `podcastrss` / `time_released`. `chapterdata` passthrough if present in m4a chapters.

### Acceptance criteria

- [ ] Shuffle Songs on iPod does not surface podcast episodes
- [ ] Pausing mid-episode and coming back resumes at the right second
- [ ] Blue-dot unplayed indicator shows for new episodes
- [ ] No regressions on music playback

---

## Phase 13: Audiobooks

**User stories**: files under `audiobooks/<author>/<title>.m4b` appear in the iPod's Audiobooks menu with working bookmarks.

### What to build

`classify` routes `audiobooks/**` items to `kind=audiobook`. Writer enforces `.m4b` extension on the destination (rename if source was `.m4a`), sets `mediatype=0x08`. No playlist flag needed — the menu surfaces automatically from mediatype + extension. `probe` stage warns if no chapter markers are present (expected per FEASIBILITY §2 — source files pre-built, but we warn). Transcode stage, when invoked on audiobook-kind items, outputs `.m4b` and preserves chapters via `ffmpeg -map_chapters 0`.

### Acceptance criteria

- [ ] Syncing `audiobooks/<author>/<title>.m4b` populates the Audiobooks menu
- [ ] Bookmark-on-pause works; resume jumps back to the right spot
- [ ] Audiobook tracks are excluded from Shuffle Songs
- [ ] A file accidentally named `.m4a` in the audiobooks tree is renamed to `.m4b`
- [ ] Chapters are navigable on the iPod (forward/back one chapter)
- [ ] Warns on chapterless input

---

## Phase 14: Playlists from M3U

**User stories**: M3U files under `<src>/playlists/` create corresponding playlists on the iPod.

### What to build

Parse `<src>/playlists/*.m3u` (and `.m3u8`), resolve each entry to an `Item` already in the sync set (match by relative path under the source tree), create a libgpod playlist with the file's basename as the name. Order is preserved. Missing references skipped with a warning.

### Acceptance criteria

- [ ] A `playlists/road-trip.m3u` creates a "road-trip" playlist on the iPod
- [ ] Track order in the playlist matches the M3U
- [ ] Deleting the M3U + `sync --prune` removes the playlist
- [ ] Playlists containing only podcasts or only audiobooks still work
- [ ] Missing track references print a clear warning, don't abort

---

## Phase 15: `doctor` deep checks (on-device)

**User stories**: user can run one command that reports whether the iPod is healthy and ready.

### What to build

Extend `doctor` with a `--device` mode that (when an iPod is mounted) also reports: FireWireGUID, free space / total space, iTunesDB parse round-trip (read with libgpod, re-serialize, verify size-class sanity), presence of expected dirs (`iPod_Control/Music/F00..F49`, `iPod_Control/iTunes`, `iPod_Control/Artwork`), count of tracks per kind, snapshot count + total snapshot size.

### Acceptance criteria

- [ ] `ipodsync doctor --device` works when iPod is mounted, fails gracefully otherwise
- [ ] Reports free space in human units
- [ ] Flags a missing `iPod_Control/Artwork` as WARN (not FAIL)
- [ ] Flags a Rockbox install as FAIL with explanation
- [ ] Reports track counts by kind matching `ls` output

---

## Phase 16: Packaging + config + install docs

**User stories**: a fresh user can install and run the tool in under 10 minutes.

### What to build

`~/.config/ipodsync/config.toml` loader (source dir, strict mode default, log level, snapshot retention count). `ipodsync config init` writes a commented example. `pyproject.toml` polished for `pipx install .` from a git clone. README includes: install steps, first-run walkthrough, FDA grant, troubleshooting for the Sequoia mount quirk. `scripts/bootstrap.sh` installs MacPorts deps + the package.

### Acceptance criteria

- [ ] Fresh Apple Silicon Mac + README + bootstrap script → working `ipodsync doctor` in ≤10 min
- [ ] `pipx install git+file:///...` from a clean clone works
- [ ] `ipodsync config init` creates a commented TOML at the right path
- [ ] README walkthrough matches real output
- [ ] Troubleshooting section covers: FDA, Sequoia mount, libgpod import failure, ffmpeg missing
