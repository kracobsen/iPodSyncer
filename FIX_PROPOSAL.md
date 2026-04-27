# Fix proposal — iPodSyncer code sweep

Findings from a multi-agent review of every source file under `src/ipodsync/`. Items are spot-checked before inclusion. Each entry: **severity · location · problem · fix**.

Severity legend: **CRIT** = silent data loss or device corruption · **BUG** = wrong behaviour · **SMELL** = correctness-adjacent / fragile · **NIT** = cleanup.

---

## 1. Safety & data integrity (fix first)

### 1.1 CRIT · `device/snapshot.py:140-145` — restore is per-file, not transactional
`restore()` renames the three DB files (`iTunesDB`, `iTunesCDB`, `ArtworkDB`) one at a time. A power loss / crash between renames leaves the triplet mixed (e.g. iTunesDB rolled back, iTunesCDB at the new state) — on 6G this is firmware-visible corruption. README line 147 promises "rolls back atomically".
**Fix:** stage all three to `.tmp-ipodsync` siblings first, then rename in sequence; on any rename failure, revert renames already performed. Update the README claim or — preferred — make it true.

### 1.2 CRIT · `device/gpod.py:215` — `db.remove(..., quiet=True)` swallows libgpod errors
The single track-removal choke point silently ignores libgpod failures. A failure to delete the F## file leaves a dangling DB pointer (or vice versa).
**Fix:** `quiet=False`; surface as `DbWriteError` and let the caller's snapshot path handle rollback.

### 1.3 CRIT · `add.py` / `sync.py` — orphan F## files on commit failure
`add_music_track` (gpod.py:372) calls `itdb_cp_track_to_ipod` (writes the F## file to the iPod) **before** `db.close()` commits the DB. If the rwtx exits with `committed=False` (gpod.py:137-139), iTunesDB is discarded — but the F## bytes are real on the device. Snapshots only cover the DB triplet. README ("Every mutation snapshots…") overstates the safety promise.
**Fix:** on rollback (`open_readwrite`'s `committed=False` branch), unlink the F## files copied during this transaction. Tracking which files were copied requires returning them from `add_music_track`. Alternative: always run `_sweep_orphans` after a failed commit, regardless of `--prune`.

### 1.4 CRIT · `device/gpod.py:120-139` — `open_readwrite` has no preflight
The mutation choke point doesn't snapshot, doesn't check Rockbox, doesn't verify GUID readability. Every caller (`add.py`, `rm.py`, `sync.py`, `restore.py`) does an ad-hoc preamble; any new caller can forget. The `restore.py:_prepare` helper already exists but is local.
**Fix:** lift `_prepare` into `device/ops.py` as `device_session(mount_point, *, mutating: bool)` — a context manager that handles detect → mount → Rockbox refusal → GUID → snapshot → `open_readwrite` → unmount. Make `open_readwrite` private; export `device_session` as the only mutation entry point.

### 1.5 BUG · `playlist.py:115-119` — non-atomic ledger write
`save_ledger` does `p.write_text(...)` directly. A crash mid-write produces corrupt JSON; `load_ledger` (line 106) then catches `JSONDecodeError` and silently returns an empty set. Effect: `--prune` refuses to delete any ipodsync-owned playlist (it thinks it owns nothing); next run silently re-creates them and overwrites the corrupt file.
**Fix:** write to `<path>.tmp`, `os.replace`. Mirror `snapshot._atomic_copy`.

### 1.6 BUG · `playlist.py:106` — corrupt ledger is silently downgraded to empty
Same root cause as 1.5: even after the atomic-write fix, an externally-corrupted ledger swallows ownership. Empty-set fallback is wrong; it should fail loudly or quarantine.
**Fix:** on `JSONDecodeError`, rename the file to `<guid>.json.corrupt-<ts>` and emit a WARN.

### 1.7 BUG · `device/snapshot.py:62-105` — `create()` not atomic across `_prune`
After a successful new snapshot, `_prune` runs. If `_prune` raises (permission, race), the caller sees `SnapshotError` and may abort the write — but the new snapshot is already on disk.
**Fix:** swallow `_prune` errors and log a WARN; the new snapshot is already safe.

### 1.8 BUG · `device/ops.py:33-58` — Rockbox refusal leaves the iPod mounted
`run_mount` calls `mount_mod.mount(...)`, then checks for `.rockbox/`, returns 3 on hit — without unmounting. README Safety section says we refuse to operate; the device stays mounted under the managed path.
**Fix:** unmount before returning the refusal exit code.

### 1.9 BUG · `restore.py:147-153` — unhandled `SnapshotError`
The pre-restore snapshot at line 148 is not wrapped in `try/except snap.SnapshotError`. `add.py:213-218` is. If the safety net itself fails, the user sees a raw traceback.
**Fix:** mirror add.py's wrapping.

---

## 2. Correctness bugs

### 2.1 BUG · `pipeline/transcode.py:143` — volumedetect regex doesn't match `-inf`
On a silent or empty source, ffmpeg emits `max_volume: -inf dB`. The regex `(-?\d+(?:\.\d+)?)` requires digits → no match → `TranscodeError` → the entire `add` fails on an otherwise-valid file.
**Fix:** extend regex to accept `-?(?:\d+(?:\.\d+)?|inf|nan)`; treat `-inf` / `nan` as "well below threshold" and return e.g. `-120.0`.

### 2.2 BUG · `pipeline/transcode.py:101-102` — cache key omits encoder identity
`cache_path` keys on `sha1 + VERSION`. The output bytes depend on whether `libfdk_aac` is present. Swap ffmpeg builds (e.g. uninstall the homebrew-ffmpeg tap) → cache hits return the previous encoder's output silently.
**Fix:** include `"fdk"` / `"native"` in the cache filename.

### 2.3 BUG · `pipeline/transcode.py` peak cache — threshold is part of the *decision* but not the *cache key*
The cached `<sha1>-peak.txt` survives `_PEAK_THRESHOLD_DB` changes; tightening the threshold has no effect on already-measured files.
**Fix:** include the threshold (or a peak-stage version int) in the cache filename, or in the file's payload alongside the measurement.

### 2.4 BUG · `playlist.py:56` — UTF-8 BOM leaks into first entry
`m3u.read_text(encoding="utf-8")` does not strip BOM. Most M3U8 exporters (Foobar, etc.) write BOMs. The first entry then becomes `﻿music/foo.mp3`, fails `cand_src.exists()`, falls through to `cand_m3u`, fails again → "unresolved entry".
**Fix:** use `encoding="utf-8-sig"`.

### 2.5 BUG · `sync.py:350` — duplicate sha1 across kinds is silently classified by walk order
`_walk_source` returns music ∪ podcasts ∪ audiobooks (line 167). Two source files with the same sha1 (e.g. one in `music/`, one in `podcasts/<show>/`) collapse into a single plan; whichever walk hit first wins. Future re-runs see the sha on device and never reconsider classification.
**Fix:** detect cross-kind sha1 collisions and either fail loudly or document the precedence in README + log line.

### 2.6 BUG · `sync.py:350` — intra-`to_add` sha1 dedupe missing
`to_add` excludes existing on-device sha1s but allows duplicate sha1s within a single sync run. `add_music_track` accepts duplicates → two on-device tracks pointing at the same content. Subsequent runs can't fix it (sha now exists) → permanent until manual `rm`.
**Fix:** dedupe `to_add` by sha1, log the dropped duplicates.

### 2.7 BUG · `sync.py:471-488` — every M3U is rebuilt on every commit, regardless of content
The `m3u_will_change` flag at line 395 only gates the early-exit; once past, every M3U has its prior playlist deleted and recreated. Adding one music track unrelated to playlists reshuffles every playlist's persistent ID.
**Fix:** rebuild only M3Us whose `desired_pl_members[name] != existing_pl_members.get(name, [])`.

### 2.8 BUG · `sync.py:485` — reserved playlist names not guarded
A user-provided `Podcasts.m3u` calls `find_user_playlist_struct(db, "Podcasts")` (gpod.py:441-446), which returns `None` because the existing playlist has the podcast flag set. `create_user_playlist_struct` then makes a *second* "Podcasts" playlist.
**Fix:** reject reserved names (`"Podcasts"`, MPL name) at parse time with a clear warning. Better: detect via `itdb_playlist_by_name` rather than the user-playlist filter.

### 2.9 BUG · `sync.py:378` — dry-run masks scan failures in exit code
The "real run" returns 5 when `scan_failures` is non-empty. The dry-run early-exit (line 378, line 404) does not.
**Fix:** dry-run should also return 5 when scan_failures.

### 2.10 BUG · `add.py:222-251` — `added_track is not None else 0` is dead code
By the time we reach line 253, `added_track` is always non-None (the `None` path returned early). The conditional is misleading.
**Fix:** drop the conditional.

### 2.11 BUG · `rm.py:174-182` — "no track(s) with id" misleads when filter excludes
A user passing `1 2 3 --filter artist=foo` where ids 1 and 2 exist but only 3 matches the filter sees "no track(s) with id: 1, 2". Conflates "id not found" with "id excluded by filter".
**Fix:** validate `id_set` against the *unfiltered* track list first; emit a separate WARN for ids that exist but were filtered out.

### 2.12 BUG · `rm.py:62` — case-insensitive filter is ASCII-only
`.lower()` doesn't normalize Unicode. `--filter title=café` (NFC) won't match a tag stored as NFD.
**Fix:** `unicodedata.normalize("NFC", x).casefold()` on both sides.

### 2.13 BUG · `device/sysinfo.py:42` vs `:75` — GUID case inconsistency
`_read_guid_from_sysinfo` returns `m.group(1)` verbatim (regex accepts `[0-9A-Fa-f]+`). `_read_guid_from_ioreg` returns uppercase. Two paths can produce GUIDs that differ only in case → snapshot dirs at `~/Library/Application Support/ipodsync/snapshots/<guid>/` shard.
**Fix:** normalize at the boundary: `f"0x{m.group(1)[2:].upper()}"` in `_read_guid_from_sysinfo`.

### 2.14 BUG · `device/detect.py:64-78` — partition fallback may pick firmware partition
`_pick_data_partition` falls back to "largest partition with a volume name" when the `Content` heuristic fails. On a 6G with an unnamed data partition this could in principle pick the firmware partition.
**Fix:** require unambiguous HFS/FAT `Content` and refuse otherwise; surface a clear "could not identify data partition" error.

### 2.15 BUG · `device/mount.py:67-85` — staging dir leaks on mount failure
If `subprocess.run` raises, `mnt.mkdir(...)` from line 77 stays on disk — accumulates per failed attempt under `MOUNT_ROOT`.
**Fix:** wrap mkdir+run; `mnt.rmdir()` on failure (best-effort).

### 2.16 BUG · `device/mount.py:80` — `mount_hfs` stderr discarded
`subprocess.run(cmd, check=True)` doesn't capture stderr. The actual reason ("Resource busy", "Operation not permitted", "sudo: a password is required") is lost — the user sees only the command line.
**Fix:** `capture_output=True`; surface `e.stderr` in `MountError`.

### 2.17 BUG · `pipeline/artwork.py:103` — miss sentinel persists across parse failures
`_miss_path(sha1).touch()` runs even when mutagen raised on a malformed file (the exception was swallowed). The miss is now permanent across the v=1 cache lifetime, even if the file is later fixed.
**Fix:** only write the miss sentinel on a definitively-no-art path; on parse failure, log + skip without sentinel-ing.

### 2.18 BUG · `pipeline/artwork.py:23-29` — sibling cover lookup is case-sensitive
On case-sensitive APFS volumes (rare but exists), `Cover.jpg` / `COVER.JPG` (Windows-exported libraries) are silently ignored.
**Fix:** glob `[Cc]over.*` / `[Ff]older.*` and check suffix in `{.jpg, .jpeg, .png}`.

### 2.19 BUG · `cli.py:42-57` — root callback fails on corrupt config even for `version` / `--help`
`config_mod.get()` runs unconditionally; a malformed `~/.config/ipodsync/config.toml` breaks every command, including offline ones.
**Fix:** lazy-load config inside subcommands that need it. Or wrap the load in `try/except ConfigError` and log a WARN, falling back to defaults for offline subcommands.

### 2.20 BUG · `doctor.py:264-265` — fix message points at the wrong remediation
`_check_guid` FAILs with "ensure SysInfoExtended is present at iPod_Control/Device/SysInfoExtended (run `scripts/bootstrap.sh`)". `bootstrap.sh` builds libgpod with `-Dsysinfo-ng=enabled`; it does not fetch SysInfoExtended onto the device. The fetcher lives inside libgpod and runs at first DB write.
**Fix:** rewrite the message: "Connect the iPod and run a write op (e.g. `ipodsync sync --dry-run` won't trigger it; first real `add` or `sync` will). If GUID still can't be read, check that the device shows up in `ioreg -a -l -r -c IOUSBHostDevice`."

### 2.21 BUG · `doctor.py:312-325` — `_check_db_roundtrip` opens DB without snapshot
A read-only `gpod.Database` open is *believed* not to mutate, but libgpod has a history of touching sidecar files. README's Safety section promises every device-touching mutation snapshots first; doctor should comply for defence in depth.
**Fix:** take a snapshot before any DB open in `--device` mode, even readonly.

### 2.22 BUG · `doctor.py:346-347` — `_dir_size` follows broken symlinks
`f.stat().st_size` raises `FileNotFoundError` on a dangling symlink. Audiobook `.m4a → .m4b` cache symlinks (per project memory) shouldn't appear under snapshots, but defence in depth.
**Fix:** `f.lstat().st_size` or wrap.

### 2.23 BUG · `config.py:62` — `snapshot_retention` accepts 0 / negative silently
`snapshot_retention = 0` deletes the snapshot you just took (interaction with snapshot.py:104). Negative is silently accepted.
**Fix:** clamp `max(1, int(raw.get("snapshot_retention", 10)))`; reject non-int with a useful `ConfigError`.

---

## 3. Architecture & smells

### 3.1 SMELL · `sync.py:run()` — 270+ lines, 6+ phases, 4-deep try/finally
Phases conflated: validate → walk → scan → device-open → plan → prepare → snapshot → commit → ledger → cleanup. Untestable as a unit.
**Fix:** extract `_resolve_device(...)`, `_compute_plan(...)`, `_commit(db, prepared, prune_targets, m3us, ...)`. The `_commit` block at lines 427-497 is the most obvious split.

### 3.2 SMELL · `device/gpod.py` — 533 lines, seven concerns
Read facade, hashing/userdata, removal, music/podcast/audiobook add, podcast playlist mgmt, user playlists, artwork. `db._itdb` is poked in 12 places, leaking the SWIG handle.
**Fix:** split into `gpod/_session.py` (open/close + preflight), `gpod/_read.py`, `gpod/_write.py`, `gpod/_playlists.py`, `gpod/_artwork.py`. Wrap `db._itdb` in a `Database` dataclass that owns the SWIG handle.

### 3.3 SMELL · mutation preamble duplicated four times
`add.py:200-223`, `rm.py:152-212`, `sync.py:330-427`, `restore.py:50-150` all repeat: detect → mount → Rockbox check → GUID → snapshot → open. Same code, four sites.
**Fix:** subsumed by 1.4. The `device_session(mutating=True)` context manager replaces all four preambles.

### 3.4 SMELL · `device/gpod.py:60-74` vs `:333-335` — mediatype constants split
`kind_from_mediatype` uses raw hex (`0x01`/`0x04`/`0x08`); `add_music_track` uses `gpod.ITDB_MEDIATYPE_*`. Drift risk if libgpod ever changes a value.
**Fix:** one source of truth — module-level constants resolved from `gpod.ITDB_MEDIATYPE_*` once.

### 3.5 SMELL · `transcode.py` peak-detection cost-benefit
A passthrough source whose peak is loud gets re-encoded through the limiter. The limiter pass requires a full decode anyway. The volumedetect pre-pass does a *separate* full decode just to measure. Two decodes for one limiter trip.
**Fix:** either (a) skip the measurement and unconditionally limit suspect codecs, or (b) measure during the encode itself with `astats=metadata=1`. Option b is cleanest.

### 3.6 SMELL · `transcode.py:194` — unconditional `-map_chapters 0`
Music inputs occasionally carry spurious chapters (CUE-derived FLACs). Maps them through to the AAC output for no benefit.
**Fix:** skip `-map_chapters` when `probe_result.chapter_count == 0`.

### 3.7 SMELL · `playlist.py:51-92` — entry-resolution priority is unusual
"Source root first, then M3U parent dir" is the opposite of what most M3U writers (Foobar, Plex) emit. README is honest about it but users will trip over it.
**Fix:** flip the order (M3U parent first), or make it config-driven (`playlists.resolve_against = "m3u" | "source"`). At minimum, name the order in the warning text.

### 3.8 SMELL · `rm.py:213-216` — accesses private `w._track.id`
The first tuple element of `iter_track_wrappers` is already a `TrackInfo` with a public `id`.
**Fix:** unpack `for ti, w, _ in ... if ti.id in ids_to_remove`.

### 3.9 SMELL · `doctor.py:99-112` — imports `_has_libfdk_aac` (private symbol)
Crosses an underscore boundary.
**Fix:** promote to `transcode.has_libfdk_aac()`.

### 3.10 SMELL · `restore.py:124` vs `log` — stdout vs stderr mixing is intentional but undocumented
Snapshot list goes to stdout (for piping); status messages stay on stderr. No comment explains it.
**Fix:** one-line comment.

### 3.11 SMELL · `ls.py:106-115` — JSON schema built via `**asdict(t)`
New `TrackInfo` fields leak into the documented schema silently.
**Fix:** explicit dict construction in `_emit_json` with a whitelist; keeps the public schema stable.

---

## 4. Drift between code and README

### 4.1 README:95 — "Stubs (not yet implemented): `playlist create|add|rm` (phase 14)"
There are no playlist subcommand stubs in `cli.py`. Phase 14 was the M3U sync path, not a CLI subcommand.
**Fix:** delete the line.

### 4.2 README:78,147 — "rolls back atomically" / "Every mutation snapshots first"
Both overstated, per items 1.1 and 1.3.
**Fix:** narrow to "the iTunesDB triplet is snapshotted before mutation; restore replays the triplet (per-file rename, not transactional). Orphan F## files from a failed write are recovered by `sync --prune`."

### 4.3 README:87 — "case-insensitive equality" for `rm --filter`
True but lossy: ASCII-only fold (item 2.12), and *exact* match means no substring matching. Users expect `--filter title=love` to find "Love Will Tear Us Apart". It won't.
**Fix:** clarify "exact (case-insensitive) match"; consider adding substring/glob in v0.2.

### 4.4 README:117 — `.m4a` rename is via "cache symlink"
The audiobook routing claim is accurate, but the symlink lives in the transcode/artwork cache rather than on the iPod. README doesn't say where; readers may think the iPod gets a symlink (it can't — HFS+ on the iPod is read-by-firmware).
**Fix:** one clarifying sentence.

---

## 5. NITs (mention, don't fix proactively)

- `add.py:188-198` — `we_mounted = True` set after `mount()` returns; tiny `KeyboardInterrupt` window.
- `add.py:148` — `content_hash` runs before Rockbox guard; minor wasted work.
- `sync.py:78` — `_Plan.source_size` is computed but never read.
- `sync.py:355` — `prune_blocked` is misleadingly named (it's "extras left alone").
- `pipeline/probe.py:59,60-64` — `float("N/A")` / `int("N/A")` raise outside the try/except in some edge paths; rare.
- `cli.py:114` — `track_ids: list[int]` annotation lies (runtime is `None` when nothing passed).
- `ls.py:165` — eager `read_firewire_guid` call even when GUID isn't displayed.
- `device/snapshot.py:117` — `.partial` cleanup hidden from `list_snapshots`.
- `device/sysinfo.py:62-64` — bare `except Exception:` on `plistlib.loads`.
- `device/sysinfo.py:73` — `len(hits) != 1` doesn't dedup duplicate IOKit entries.

---

## Suggested ordering

**Round 1 (safety net):** 1.1, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9.
**Round 2 (correctness):** 2.1, 2.2, 2.4, 2.5, 2.6, 2.7, 2.8, 2.13, 2.16, 2.19, 2.20.
**Round 3 (architecture):** 3.1, 3.2, 3.3 (these are big; sequence matters — do 3.3 first, it cleans up most of 3.1 and 3.2).
**Round 4 (polish):** README drift (§4), remaining smells, NITs.

The architecture round is where the existing scaffolding pays back: a single `device_session` context manager mechanically removes four duplicated preambles and is the right place to enforce snapshot + Rockbox + GUID preflight (1.4). Do it before further bug-hunting in `sync.py`.
