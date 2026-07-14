# Consume archive dirs become siblings of the consume folder

**Date:** 2026-07-14

## What landed

The consume watcher's archive locations moved out of the consume dir:
successful files now go to `LIBRARY_CONSUMED_DIR` (`YYYY/MM` subtree)
and rejected files to `LIBRARY_FAILED_DIR` — two new settings that
default to **siblings of the consume dir**
(`<parent-of-consume-dir>/consumed` and `<parent-of-consume-dir>/failed`)
whenever `LIBRARY_CONSUME_DIR` is set, and stay `None` when it is not.
Both dirs are created at worker startup so unwritable targets fail
loudly once, not per-file. Archive moves are EXDEV-safe (`os.replace`
fast path, copy-based fallback across mounts), the scanner skips
candidates by configured-path check instead of by directory name, and a
one-time startup migration moves any legacy `{consume_dir}/consumed` /
`{consume_dir}/failed` trees into the configured locations
(collision-safe merge via the suffixing logic), then removes the
emptied legacy dirs. Re-deploys after migration are no-ops.
Docs: ingestion.md (flow diagram, ignored-names bullet, "Archive
layout", Syncthing/NAS notes, settings table), deployment.md (§1.2
step 7 bind-mount guidance, §1.7.2 prod mapping), docker-compose.yml
worker comment, CHANGELOG.md.

## Decisions

- **The W12 back-sync rationale is deliberately reversed (2026-07-14).**
  W12 (260610) placed `consumed/YYYY/MM` *inside* the consume dir
  precisely so Syncthing would sync the archive back to the device that
  dropped the file. That behavior is now dropped on purpose: the
  cleaner layout wins — `/consume` holds only pending items, and the
  archive no longer round-trips through every synced device.
- **Sibling defaults, not hardcoded paths.** With only
  `LIBRARY_CONSUME_DIR=/x/consume` set, the archive lands at
  `/x/consumed` and `/x/failed`. In the dev/CI compose
  (`LIBRARY_CONSUME_DIR=/data/consume` inside the `library_data`
  volume) the defaults resolve to `/data/consumed` and `/data/failed` —
  persistent and correct, no compose env changes needed (overrides flow
  via `env_file: .env`).
- **Prod ephemeral-root trap.** In production the consume dir is its
  own NFS bind mount at `/consume`, so the sibling default resolves to
  the **ephemeral container root** (`/consumed`, `/failed`) — and the
  startup auto-migration would move the archive there. The
  proxmox-setup compose must set `LIBRARY_CONSUMED_DIR=/data/consumed`
  and `LIBRARY_FAILED_DIR=/data/failed` on `library-worker` **before**
  pulling the image with this change; after the first startup, verify
  the migration log line and that `/data/consumed/YYYY/MM` holds the
  relocated archive.
- **Auto-migration over a manual runbook step.** Legacy trees are moved
  once at worker startup rather than asking the operator to `mv` them:
  it is idempotent (no-op when the legacy dirs are absent or when an
  override points the configured dir at the legacy location) and
  collision-safe, and the data move is manually reversible if ever
  needed.
- **Two hardening fixes from wrap-up code review.** (1) The migration's
  cleanup no longer assumes legacy trees contain only files and plain
  dirs — non-regular entries (symlinks etc.) are left in place with a
  warning instead of crashing the watcher on every startup. (2) The
  cross-device (EXDEV) move fallback follows the `storage.py`
  atomic-write pattern — copy to a temp name in the target dir, then
  `os.replace` — so a crash mid-copy can never leave a truncated file
  under the final archive name (a plain `shutil.move` could, and the
  collision suffixing would then have shadowed the good retry copy
  forever).

## Tests

Ten new tests in `tests/test_consume.py` (suite 1093 → 1103, all
green): sibling defaults derived from `consume_dir` plus explicit env
overrides, archive landing in sibling `consumed/YYYY/MM` and `failed/`,
the inside-the-consume-dir override skip (re-ingest loop regression),
the EXDEV fallback, the legacy-tree migration (relocation, collision
suffixing, no-op cases), a symlink in the legacy tree (migration
survives, warns, leaves it in place), and a failed cross-device copy
(no partial file under the final name, source kept for retry).

Docs: ingestion.md, deployment.md, docker-compose.yml (comment),
CHANGELOG.md.
