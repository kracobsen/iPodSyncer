# iPodSyncer — Feasibility Report

**Goal.** A macOS CLI that syncs music, podcasts and audiobooks to an **iPod Classic 6G** running factory Apple firmware, on a modern (Apple Silicon, macOS Sequoia) Mac.

**Date.** 2026-04-19. All statements about macOS/Apple behaviour are current as of Sequoia 15.x / Tahoe 26.x.

**Bottom line.** Feasible with bounded engineering effort. The Classic 6G is the cleanest target in the whole iPod lineup: USB Mass Storage, well-documented iTunesDB + iTunesCDB, and a DB signature (hash58) that is fully reverse-engineered and implemented in libgpod. **Do not rely on Apple's own Finder/Music.app plumbing** — it is actively regressing. Build on libgpod via a maintained fork, mount via raw `mount` instead of `diskutil`, target Sequoia (Tahoe broke Finder-sync for iPod Classic, and also removed FireWire — not our problem, but useful context).

Scope decisions made during the interview (2026-04-19) are summarised in §2 and drive every downstream recommendation.

---

## 1. Why this is worth doing

Apple has effectively abandoned first-party iPod Classic sync. Finder-based sync regressed in Sequoia 15.4.1, was partially restored in 15.5, and regressed again in **Tahoe 26.0** where clicking "Sync Settings" does nothing and newly-added media silently fails to appear ([Apple Community – Tahoe not showing Sync Settings](https://discussions.apple.com/thread/256154568), [Apple Community – iPod Classic won't sync after Tahoe 26.0](https://discussions.apple.com/thread/256147797)). Books.app never had a path to classic iPods; Podcasts.app's bridge through Finder is unreliable ([MacRumors thread](https://forums.macrumors.com/threads/app-to-replace-itunes-for-syncing-podcasts-and-imported-audio-to-ipod-classic.2395120/)). Third-party apps either (a) target iOS only (iMazing extracts from classic iPods but will not sync to them — see [iMazing FAQ](https://imazing.com/faq?s=ipod)), or (b) are Windows-only (CopyTrans). A modern macOS-native CLI fills a real gap.

## 2. Scope (locked)

- **Target device:** iPod Classic 6G (and by accident 6.5G / "7G Thin" — same DB format, same hash). All other iPod models **out of scope**.
- **Media types:** music, podcasts, audiobooks. No video, no photos, no contacts/calendars.
- **Transcoding:** auto-transcode non-iPod-supported formats (FLAC/Opus/Ogg/…) → AAC ~256 kbps VBR via ffmpeg. Originals untouched; converted copies cached on disk. `--strict` flag rejects unsupported input. Target codecs the iPod Classic actually plays: MP3, AAC (.m4a), Apple Lossless (.m4a), AIFF, WAV, Audible `.aa`/`.aax` (DRM — out of scope for the tool).
- **Pipeline architecture:** ordered, composable stages; each item flows through probe → transcode → tag-normalize → artwork → cache-key → device-write. New stages (e.g., speed-up for podcasts/audiobooks, chapter regeneration) can be inserted without restructuring. See §6.
- **Podcast source:** a local folder tree only (`~/podcasts/<show>/<episode>.{mp3,m4a}`). No RSS fetching, no Podcasts.app integration. User is expected to use `gPodder` / `podget` / similar separately.
- **Audiobook source:** pre-built `.m4b` only (one file per book, chapter markers embedded). No MP3-folder concat. User is expected to build `.m4b`s with `m4b-tool` or similar separately.
- **Language:** Python 3.12+. Distributed via `pipx`. libgpod dependency installed via MacPorts or Homebrew tap.
- **Rockbox:** not supported. If the tool detects `/iPod_Control` is absent or a `.rockbox/` directory is present, it refuses and prints a message.
- **iPod Touch:** out of scope (iOS, closed sync stack).

This scope is narrow by design. Everything else stays on a "future" list in §8.

## 3. How the sync path works for Classic 6G

The device in factory firmware exposes itself as **USB Mass Storage** (HFS+ if Mac-formatted, FAT32 if Windows-formatted). Sync is file I/O, not a wire protocol. The work:

1. Mount the device (Sequoia quirk below).
2. Copy audio files into `/iPod_Control/Music/F00`…`F49`, randomized 4-letter filenames, preserving the extension. A single flat pool — **no** separate Podcasts/Audiobooks folders on click-wheel iPods. Extension is load-bearing: `.m4b` vs `.m4a` changes firmware behaviour even with identical bytes ([Engadget: iPod file structure](https://www.engadget.com/2005-10-21-terminal-tips-the-ipods-file-structure.html)).
3. Build `/iPod_Control/iTunes/iTunesDB` (binary tree of `mhbd`/`mhsd`/`mhit`/`mhod` records — [wikiPodLinux ITunesDB](http://www.ipodlinux.org/ITunesDB/)). The 6G also wants the compressed `iTunesCDB` sibling.
4. Render artwork into per-model RGB565 `.ithmb` files and write `/iPod_Control/Artwork/ArtworkDB`.
5. Compute the **hash58** signature on the DB using the device's FireWireGUID as salt. libgpod handles this; [mono/ipod-sharp Hash58.cs](https://github.com/mono/ipod-sharp/blob/master/src/Hash58.cs) is a clean reference.
6. `sync`, eject.

**Sequoia mount quirk.** On 15.4.1+ `diskutil mount` is blocked for iPod Classic, but low-level `mount_hfs` (or `mount_msdos` for Windows-formatted devices) still works ([MacRumors thread](https://forums.macrumors.com/threads/i-found-a-way-to-still-use-my-modded-ipod-classic-past-macos-sequoia-15-4-1.2463657/)). The CLI invokes `mount` directly and requires **Full Disk Access** granted in System Settings → Privacy.

## 4. iTunesDB specifics for music / podcasts / audiobooks

Track classification is a single `mediatype` bitfield on the `mhit` track record ([libgpod `itdb.h`](https://github.com/gtkpod/libgpod/blob/master/src/itdb.h)):

| Media | mediatype |
|---|---|
| Music | `0x01` |
| Podcast | `0x04` |
| Audiobook | `0x08` |
| Video podcast | `0x06` |

**Podcasts.** Require a dedicated second master-like playlist (`mhyp`) with `podcastflag = 1`. **Exactly one** such playlist may exist — duplicates nuke the menu. Podcast tracks go only in that playlist (never the MPL, or they double-show under Songs). Show/episode grouping uses `mhip` items with `groupflag`/`groupid`/`groupref`, keyed off the track's Album tag as the show name. The type-3 MHSD must sit between type-1 and type-2 MHSDs inside the mhbd or the Podcasts menu never appears. libgpod exposes `itdb_playlist_set_podcasts()` and `itdb_playlist_is_podcasts()`.

**Audiobooks.** No playlist flag. The Audiobooks top-level menu appears automatically as soon as any track has `mediatype = 0x08` or an `.m4b`/`.aa` extension. Per-track flags `skip_when_shuffling`, `remember_playback_position`, `bookmark_time`, `mark_unplayed`, `flag4` are force-enabled by firmware on `.m4b`/`.aa` regardless of what the DB says — so writing the correct extension matters as much as the mediatype bit.

**Artwork.** Classic 6G has a color screen. Thumbnails are pre-rendered to model-specific pixel dimensions, stored as RGB565 little-endian in `F1_1.ithmb` etc. libgpod has the size tables and the `itdb_track_set_thumbnails()` API. Podcasts usually carry per-episode art — treat identically to album art.

**Known gotchas:**
- `.m4b` vs `.m4a` — get the extension right.
- MPL membership — podcasts must NOT be in the master playlist.
- Exactly one podcast-flagged playlist.
- MHSD ordering (type-3 between type-1 and type-2).
- Type-100 sort MHOD accounting under dbversion 0x0d — easy to miscount and produce a silently-unreadable DB. **Do not roll a writer at the mhit/mhod level; let libgpod do it.**
- Mac-epoch timestamps (seconds since 1904-01-01).
- 5G iPod Video corrupts `.m4b` longer than ~4h — does not apply to 6G (not in scope anyway).
- Apple Music / FairPlay-protected tracks cannot sync; refuse with a clear error.

## 5. Library choice

**libgpod via a maintained fork.** Upstream 0.8.3 is from 2013; three practical forks:

- **[gerion0/libgpod](https://github.com/gerion0/libgpod)** — Meson build, Py3 bindings, HAL removed. **Cleanest base for Apple Silicon Python work. Primary choice.**
- **[strawberrymusicplayer/strawberry-libgpod](https://github.com/strawberrymusicplayer/strawberry-libgpod)** — CMake, currently shipped inside Strawberry releases; best-tested "user-facing active" fork. Fallback.
- **[fadingred/libgpod](https://github.com/fadingred/libgpod)** — historical reference with the iTunesCDB/SQLite docs in-tree.

Prior art worth reading end-to-end:
- **[whatdoineed2do/gpod-utils](https://github.com/whatdoineed2do/gpod-utils)** — CLI built on libgpod with `gpod-ls` / `gpod-cp` (auto-transcodes via ffmpeg) / `gpod-rm` / playlist generation. Directly analogous to what we're building.
- **[TheRealSavi/iOpenPod](https://github.com/TheRealSavi/iOpenPod)** — pure-Python iTunesDB writer including podcasts/audiobooks/artwork. Not our primary path (libgpod is more battle-tested for 6G), but a useful reference for the Python data-model layer.

**Packaging on Apple Silicon.** No Homebrew formula in core. **MacPorts has `libgpod`** and builds on arm64 ([port](https://ports.macports.org/port/libgpod/)) — the only package-manager route. Otherwise build gerion0's fork from source. Our `pipx` install should document MacPorts as the default dep.

**Hashing for 6G** is pure hash58, fully solved in libgpod. No HashInfo-seed-from-iTunes problem (that's a Nano 5G / iOS concern — not us).

## 6. Architecture

### Command shape

```
ipodsync mount                              # mount iPod (bypasses diskutil)
ipodsync ls [--kind music|podcast|book] [--json]
ipodsync add <path>... [--kind ...] [--playlist NAME]
ipodsync rm <track-id|filter>
ipodsync sync <source-dir>                  # one-way mirror, idempotent
ipodsync playlist (create|add|rm) ...
ipodsync doctor                             # check mount, FDA, free space, DB sanity
ipodsync eject
```

Target source-tree layout for `sync`:

```
<source-dir>/
  music/<artist>/<album>/<tracknum-title>.{mp3,m4a,flac,…}
  podcasts/<show>/<episode>.{mp3,m4a}
  audiobooks/<author>/<title>.m4b
```

### Pipeline

Each file flows through an ordered sequence of stages. A stage is a pure-ish function `(Item, Context) -> Item` that can add/modify metadata on the `Item` or produce a new intermediate file in a cache dir. Stages can be skipped via cache key. Stage order for v0.1:

```
1.  classify         detect kind from path + probe (music/podcast/audiobook)
2.  probe            ffprobe → codec, bitrate, duration, existing tags, chapters
3.  transcode        if codec ∉ {mp3, aac-lc-m4a, alac-m4a, aiff, wav}:
                     → AAC 256k VBR in .m4a (or .m4b if kind=audiobook)
                     cached by content-hash(source) + stage-version
4.  tag-normalize    write/fix tags; enforce .m4b extension for audiobooks;
                     set mediatype per kind; podcast show = folder name = Album
5.  artwork          extract embedded or sibling cover.jpg/png;
                     resize to 6G thumbnail spec (320×320 full, 140×140 list);
                     cached
6.  cache-key        fingerprint = hash(final-file, artwork, tags, mediatype);
                     compare against device state — skip if unchanged
7.  device-write     copy into F## folder, add Itdb_Track to libgpod model
8.  db-commit        after all items: libgpod writes iTunesDB + iTunesCDB +
                     ArtworkDB + hash58
```

Future stages slot in cleanly without restructuring:

- **`speedup`** (between `transcode` and `tag-normalize`, opt-in per kind): `ffmpeg -filter:a "atempo=1.25"` for podcasts, `atempo=1.15` for audiobooks. Chapter markers preserved via `ffmpeg -map_chapters 0` — verify per book; `m4b-tool` is a fallback.
- **`chapter-regen`** (for audiobooks missing chapters): derive from silence detection or from filename patterns.
- **`loudnorm`** (EBU R128 normalization) before transcode.
- **`tag-enrich`** (MusicBrainz lookup) between probe and tag-normalize.

Each stage declares inputs/outputs and a `version: int`. The cache key incorporates stage versions so changing a stage's behaviour invalidates affected cache entries without a wipe.

### Modules

```
ipodsync/
  cli.py                 click/typer entrypoints
  device/
    mount.py             raw mount wrapper + FDA detection
    gpod.py              libgpod (python-gpod) facade
    hash58.py            only needed if we bypass libgpod's writer (we won't)
  pipeline/
    base.py              Stage protocol, Item dataclass, Context, cache
    classify.py
    probe.py             ffprobe wrapper
    transcode.py         ffmpeg wrapper; cache in ~/Library/Caches/ipodsync
    tags.py              mutagen; kind-specific normalization
    artwork.py           Pillow; per-model size table
    writer.py            calls gpod.py for device-write + db-commit
  model/
    item.py              Item, Kind enum, TagSet
    layout.py            source-tree parsing (music/ podcasts/ audiobooks/)
  config.py              ~/.config/ipodsync/config.toml
```

Dependencies: `typer`, `mutagen`, `Pillow`, `click` (transitive), `python-gpod` (from gerion0 fork). Shelled out: `ffmpeg`, `ffprobe`, `mount`. No network.

### Safety & idempotency

- `sync <src>` is a one-way mirror from src to device. Files on device absent from src are either kept (default) or removed (`--prune`).
- Every write is staged — DB edits happen in memory via libgpod, written atomically at `db-commit`. If any stage fails mid-run, the DB is not clobbered.
- A pre-sync snapshot of `iTunesDB` + `iTunesCDB` + `ArtworkDB` is saved to `~/Library/Application Support/ipodsync/snapshots/<device-guid>/<timestamp>/` so a bad sync can be reverted by copying files back.
- `doctor` verifies: mount, Full Disk Access, free space, DB parse round-trip, libgpod/ffmpeg versions.

## 7. Risks and open technical unknowns

- **macOS may tighten USB-MSC access to classic iPods.** Tahoe dropped FireWire; Sequoia already blocks `diskutil mount` for iPods. The raw-mount workaround could stop working. Mitigation: `mount.py` is small and isolated; swap to a FUSE-style plugin if needed.
- **Artwork size table per exact 6G sub-revision.** 6G and 6.5G use the same sizes (320×320 full, 140×140 list) but confirm against libgpod's table at implementation time.
- **python-gpod arm64 build.** Needs `meson`, `glib`, `libplist`, `libxml2`, `sqlite3` via MacPorts (or Homebrew with `PKG_CONFIG_PATH` pointed at `/opt/homebrew`). Document exact steps in README.
- **ffmpeg atempo chapter preservation** for the future speedup stage — verified-working for m4b with `-map_chapters 0`, but edge cases around nested chapters exist. Not a v0.1 concern.
- **DB version 0x0d type-100 sort MHOD accounting** — a real footgun if we ever drop libgpod and write the DB directly. We won't in v0.1.

## 8. Future work (explicitly out of v0.1)

- `speedup` pipeline stage (1.15× / 1.25× / 1.5×).
- `chapter-regen` for audiobooks missing chapters.
- `loudnorm` normalization.
- MusicBrainz tag enrichment.
- Read-back: pull playcounts and bookmarks off the iPod into a local file (mirror resume points across devices). libgpod reads Play Counts — cheap.
- Daemon mode: watch source dir + auto-sync on device connect.
- Other iPod models (Mini, Photo, Video, Nano 1–4, Shuffle, Nano 5–7). Each one is a separate writer profile; pipeline stages 1–6 are shared.

## 9. Milestones

- **M0 — spike (1 week).** Build gerion0/libgpod + python-gpod on arm64. Read a test 6G's DB via python, list tracks. Confirm Sequoia mount path. Write `doctor` and `mount`/`eject`.
- **M1 — music-only sync (1 week).** `sync` command, classify+probe+transcode+tags+artwork+writer+db-commit. Music only. Source-tree layout fixed. Atomic snapshot on every run.
- **M2 — podcasts (3–5 days).** Podcast-flagged playlist, show/episode grouping, extra mhod fields. On-device UI verification under Apple firmware.
- **M3 — audiobooks (2–3 days).** mediatype=0x08, `.m4b` extension enforcement, bookmark/skip-shuffle verification.
- **M4 — prune + doctor + polish (3–5 days).** `--prune`, better error messages, `doctor` deep checks, `pipx` packaging.

Total: ~3–4 focused weeks for v0.1.

---

## Appendix A — Key sources

### macOS state
- Apple Support: [Use Finder to sync](https://support.apple.com/en-us/102471) · [Sync audiobooks](https://support.apple.com/guide/mac-help/sync-audiobooks-to-your-device-mchlf764c5a4/mac) · [Sync podcasts](https://support.apple.com/guide/mac-help/sync-podcasts-to-your-device-mchlc60ece64/mac)
- Apple Community: [Tahoe won't sync iPod Classic](https://discussions.apple.com/thread/256147797) · [Sequoia 15.4.1 regression](https://discussions.apple.com/thread/255978644) · [Manual management gone](https://discussions.apple.com/thread/252010664)
- MacRumors: [Modded iPod Classic past Sequoia 15.4.1](https://forums.macrumors.com/threads/i-found-a-way-to-still-use-my-modded-ipod-classic-past-macos-sequoia-15-4-1.2463657/) · [Podcast sync workarounds](https://forums.macrumors.com/threads/app-to-replace-itunes-for-syncing-podcasts-and-imported-audio-to-ipod-classic.2395120/)
- [iMazing FAQ (iPod)](https://imazing.com/faq?s=ipod) · [CopyTrans compatibility (Windows-only)](https://www.copytrans.net/support/ios-and-ipod-models-compatibility-summary/)

### Libraries
- libgpod forks: [gerion0](https://github.com/gerion0/libgpod) · [strawberrymusicplayer](https://github.com/strawberrymusicplayer/strawberry-libgpod) · [fadingred](https://github.com/fadingred/libgpod) · [gtkpod upstream](https://sourceforge.net/projects/gtkpod/)
- [whatdoineed2do/gpod-utils](https://github.com/whatdoineed2do/gpod-utils) — CLI prior art on libgpod
- [TheRealSavi/iOpenPod](https://github.com/TheRealSavi/iOpenPod) — pure-Python writer reference
- [MacPorts libgpod](https://ports.macports.org/port/libgpod/)

### iTunesDB / media kinds
- libgpod [`itdb.h`](https://github.com/gtkpod/libgpod/blob/master/src/itdb.h) · [Tracks API](https://tmz.fedorapeople.org/docs/libgpod/libgpod-Tracks.html) · [iTunesDB structure API](https://tmz.fedorapeople.org/docs/libgpod/libgpod-The-Itdb-iTunesDB-structure.html)
- [wikiPodLinux — ITunesDB](http://www.ipodlinux.org/ITunesDB/) · [ITunesDB File](http://www.ipodlinux.org/ITunesDB/iTunesDB_File.html)
- [Linux Journal — Learning the iTunesDB File Format](https://www.linuxjournal.com/article/6334)
- [libgpod-ondevice `itdb_itunesdb.c`](https://github.com/H2CO3/libgpod-ondevice/blob/master/src/itdb_itunesdb.c) — byte-offset parser
- [Engadget: iPod file structure (F00/F01/…)](https://www.engadget.com/2005-10-21-terminal-tips-the-ipods-file-structure.html)

### Hashing
- [mono/ipod-sharp Hash58.cs](https://github.com/mono/ipod-sharp/blob/master/src/Hash58.cs) — hash58 reference implementation
- [libgpod README.sqlite](https://github.com/fadingred/libgpod/blob/master/README.sqlite) — context for newer hashes (not needed for 6G)

## Appendix B — Scope decisions log (2026-04-19)

1. Target: iPod Classic 6G only.
2. Transcode: auto, AAC ~256k VBR, originals untouched, cached. Pipeline architecture, stages composable.
3. Podcast source: local folder only.
4. Audiobook source: pre-built `.m4b` only.
5. Language: Python.
6. Rockbox: not supported; refuse if detected.
