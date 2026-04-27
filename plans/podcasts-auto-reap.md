# Plan: Podcasts auto-reap

> Source PRD: design captured in conversation 2026-04-27. Continues v0.1 plan
> (`plans/ipodsyncer-v0.1.md`) past phase 16; phase numbering picks up at 17.

## How to use this plan

Same rules as `ipodsyncer-v0.1.md`: one phase per commit, message format
`phase-N: <title>`, no skipping ahead. Each phase is a vertical slice that
cuts through CLI → sync → device layers and is demoable on its own.

## Goal (one-liner)

When a podcast episode plays through to the end on the iPod, the next sync
removes it from the device automatically and remembers not to re-add it from
source.

## Out of scope

- Auto-reap for music or audiobooks (different content models).
- RSS / podcast-feed integration.
- Time-based or partial-play reap (anything other than `playcount >= 1`).
- Mutating files in the user's source tree.
- `add` / `rm` consulting the consumed ledger — reap is sync-only.

---

## Architectural decisions (durable)

These apply to every phase below. Do not revisit without explicit user approval.

- **Source of truth for "played"**: the iPod's iTunesDB. Reap fires when
  `playcount >= 1` on a podcast track that carries our `sha1_hash` userdata
  stamp. Tracks without our stamp (e.g. iTunes-seeded) are never reaped.
- **Consumed ledger location**: `~/Library/Application Support/ipodsync/<guid>/podcasts.json`.
  Per-FireWireGUID, sibling to the existing snapshot dir layout.
- **Ledger schema** (JSON):
  ```json
  {
    "version": 1,
    "entries": {
      "<sha1>": {
        "show": "...",
        "title": "...",
        "source_path": "/abs/path/to/source/file",
        "removed_at": "2026-04-27T12:34:56Z"
      }
    }
  }
  ```
- **Ledger lifecycle**: entries added post-commit when reap succeeds. Entries
  pruned at the start of every sync if their `source_path` no longer exists
  on disk. Otherwise persistent — only the explicit `forget` command removes
  entries while the source is still present.
- **Default behavior**: auto-reap is ON by default. Config key
  `[podcasts] auto_reap_played = true` (in `~/.config/ipodsync/config.toml`)
  flips it. Per-sync override: `sync --keep-played` skips reap for that run
  but still consults the ledger to suppress re-adds.
- **Order of operations inside `sync.run`**:
  1. Walk source.
  2. Load ledger; prune entries whose `source_path` is gone.
  3. Detect/mount device.
  4. `open_readonly` → existing sha1s + playlist memberships + per-sha1
     playcounts for podcast tracks.
  5. Compute `to_reap` (podcast tracks with playcount ≥ 1, only ours).
  6. Plan: `to_add = source ∖ existing ∖ ledger ∖ to_reap`.
  7. `--dry-run` exits here, after printing reap count.
  8. Prepare new items.
  9. `open_readwrite` → snapshot (existing) → reap → prune (existing) →
     add (existing) → playlists (existing). Single commit.
  10. Post-commit: append reaped entries to ledger; save.
- **Re-listen UX**: `ipodsync podcasts forget <pattern>` removes ledger
  entries by `<show>` or `<show>/<episode>` substring; `--all` clears.
  Re-adding happens on the next sync via the existing add path.
- **Reap announce**: commit summary lists reaped titles (first N + count
  if many), not just a number. Snapshot already covers rollback via
  `ipodsync restore`.
- **Module shape**:
  - New deep module: consumed-podcast ledger. JSON I/O, schema versioning,
    contains/record/forget/prune-stale. No device or libgpod dependency.
    The only piece with unit tests in this feature.
  - `device/gpod.py` gains a playcount reader filtered to our-stamped
    podcast tracks.
  - `sync.py`, `cli.py`, `config.py`, `doctor.py` get integration glue.

---

## Phase 17: Read podcast playcounts on device

**User stories**: M17 — verify firmware increments `playcount` on real
hardware before any reap logic depends on it.

### What to build

`device/gpod.py` gains a read-only function that walks all tracks with
`mediatype=PODCAST` carrying our `sha1_hash` userdata stamp and returns
`{sha1 → (playcount, title)}`. `ipodsync ls --kind podcast` surfaces a
`played` column populated from this read. No mutations, no ledger, no
config changes.

### Acceptance criteria

- [ ] On a real iPod with at least one fully-played podcast episode,
      `ls --kind podcast` shows `played > 0` for that episode and `0` for
      the rest
- [ ] `ls --kind podcast --json` includes the `played` field in the
      documented schema
- [ ] iTunes-seeded podcast tracks (no `sha1_hash` userdata) are not
      surfaced by the new reader (or are clearly tagged as "foreign")
- [ ] No new device writes — `ls` remains read-only

---

## Phase 18: Podcast ledger module + `podcasts` CLI

**User stories**: M18 — ship the consumed-ledger primitive and a CLI
surface for inspecting / clearing entries, before any sync code depends
on it.

### What to build

New deep module for the consumed-podcast ledger. Pure logic + filesystem.
Schema-versioned JSON at the path documented in Architectural decisions.
Public surface stays small: load by GUID, contains by sha1, record entry,
forget by pattern (or all), prune entries whose source path is missing,
save. Unit tests cover round-trip, prune semantics, pattern matching, and
schema-version handling.

`cli.py` gains a `podcasts` subcommand group:
- `ipodsync podcasts list` — print ledger entries as a table.
- `ipodsync podcasts forget <pattern>` — remove entries by substring
  match against `<show>` or `<show>/<episode>`.
- `ipodsync podcasts forget --all` — clear the ledger.

Sync is not yet wired. Demoable via a hand-crafted JSON file.

### Acceptance criteria

- [ ] Hand-writing a valid ledger JSON, then running `podcasts list`,
      prints the entries in a stable table format
- [ ] `podcasts forget <show-name>` removes only matching entries; the
      file mutates in place; `list` reflects the change
- [ ] `podcasts forget --all` empties the ledger but keeps the file with
      a valid empty schema
- [ ] Loading a ledger missing the `version` field succeeds (forward-compat)
- [ ] Unit tests pass for: round-trip, prune-missing-sources, forget-pattern,
      forget-all, schema-version-on-save
- [ ] `--help` discovers the new subcommand group

---

## Phase 19: Auto-reap played podcasts during sync

**User stories**: M19 — the headline feature. Sync removes finished
podcasts from the iPod automatically and remembers not to re-add them.

### What to build

Wire phase 17's playcount reader and phase 18's ledger into `sync.run`
per the order-of-operations in Architectural decisions. Reap happens
inside the existing single `open_readwrite` commit so it shares the
snapshot taken there. Ledger is saved only after a successful commit.

Config: add `[podcasts] auto_reap_played` to the loader, default true.
CLI: add `sync --keep-played` to skip the reap step for one run while
still honoring the ledger for suppression of re-adds.

`--dry-run` reports the reap count and lists the titles it would remove
without writing anything.

Commit summary line lists reaped titles (first N + count if many), not
just a number.

### Acceptance criteria

- [ ] On a real iPod: sync, mark an episode played to the end on device,
      eject, sync again → episode is gone from device, ledger has its
      sha1 with `show`, `title`, `source_path`, `removed_at`
- [ ] Same scenario with `sync --keep-played` → episode stays on device,
      ledger unchanged
- [ ] Same scenario with `auto_reap_played = false` in config → episode
      stays on device, ledger unchanged
- [ ] After reap, sync runs again with no source changes → episode is not
      re-added (ledger suppresses)
- [ ] `podcasts forget <show>/<episode>` then sync → episode is re-added
- [ ] iTunes-seeded podcasts with playcount > 0 are NOT reaped (only
      tracks carrying our sha1 stamp are eligible)
- [ ] Source file deleted from disk → next sync's ledger-prune drops the
      stale entry; commit log notes it
- [ ] `--dry-run` prints reap count and titles, exits without writing
- [ ] Snapshot is taken before the reap (existing snapshot path covers it);
      `ipodsync restore` rolls back a botched reap
- [ ] Reaped episode that was a member of a user M3U → existing M3U rebuild
      flags it as `missing`; reap announce mentions the playlist impact
- [ ] Music and audiobooks are unaffected regardless of their playcount

---

## Phase 20: Doctor reporting + README polish

**User stories**: M20 — make the feature discoverable and auditable
without reading source.

### What to build

`doctor --device` adds a "consumed podcasts" line reporting the ledger
file path and entry count. README documents:
- The auto-reap behavior and how to opt out via config or per-sync flag.
- `ipodsync podcasts list` and `forget` for re-listening.
- A troubleshooting note: hard-yanking the iPod can lose the firmware's
  pending playcount writes; safe-eject before unplug.
- A note that restoring a previously-deleted source file from backup may
  cause re-add of an episode the user had finished, because the ledger
  prunes entries whose source path goes missing.

### Acceptance criteria

- [ ] `ipodsync doctor --device` on a device with a non-empty ledger
      reports the entry count and the absolute ledger path
- [ ] `doctor --device` on a device with no ledger reports `0` without
      crashing
- [ ] README has a "Podcasts auto-reap" section linked from the feature
      list, covering the four bullets above
- [ ] README walkthrough output matches a real run
