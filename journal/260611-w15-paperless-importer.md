# W15 — paperless-ngx importer

**Date:** 2026-06-11. **Unit:** W15 (improvement plan §1.3.15, decision 9).

## What landed

`library import paperless [--url --token | env] [--dry-run] [--no-extract]
[--limit N]` — a full migration path out of paperless-ngx over its REST
API. Three modules under `src/library/importer/`:

- **client.py** — thin async httpx client, token auth, pinned
  `Accept: application/json; version=9` (2.x and 3.0), `next`-following
  pagination, and `download/?original=true` verified against the MD5
  `original_checksum` from `metadata/` with one retry.
- **mapper.py** — pure payload→`MappedDocument` translation: kind table
  (en+nl document-type names, unmapped → `other` + provenance tag),
  `is_inbox_tag` → `needs-review`, monetary/select/documentlink custom
  fields, `created` (plain date on v9, datetime on older).
- **runner.py** — idempotent batch runner: skip by `paperless_id` or
  sha256, per-document error isolation, download concurrency 4,
  paperless `content` reused as `ocr_text` (engine `paperless-import`,
  straight to `indexed`, no OCR job; extraction deferred as enrichment
  unless `--no-extract`), batch uuid in `extra["paperless"]["batch_id"]`,
  `paperless_imported` event, documentlink remap in a second pass,
  dry-run with mapping summary.

Docs: new `docs/migration.md` (workflow, mapping table, batch escape
hatch, limitations); architecture W15 row → done. Tests:
`tests/test_importer.py`, 21 tests over a `FakePaperless` API behind
`httpx.MockTransport` (no respx dependency needed) plus the real
testcontainers Postgres.

## Session-died handoff

The previous engineer's session died mid-unit, leaving the three
importer modules on disk, unreviewed and never executed; the paperless
settings block, `ingest_file(defer_processing=)`, and the
`Document.paperless_id` column already existed. Review of record for
that code:

- **Two real bugs, both "fresh ORM row" traps:** `_apply_metadata` read
  `document.extra` (None until refreshed — `extra` only has a *server*
  default and sessions run `expire_on_commit=False`) and iterated
  `document.tags` (unloaded collection → implicit lazy load →
  `MissingGreenlet` under the async session). Every single live import
  would have crashed on the first document. Fixed with `extra or {}`
  guards and an explicit `session.refresh(document, ["tags"])`; the
  integration tests now cover the exact path.
- **Added:** resume of interrupted imports (bytes ingested with
  `source=import` but no `extra["paperless"]` → metadata re-applied
  instead of skipped); dry-run duplicate detection (read-only) so
  "would import" is honest on re-runs; the whole CLI command; all tests
  and docs (none existed).
- **Kept as found:** client and mapper (correct on inspection and now
  under test), batching/concurrency structure, the
  `user_edited_fields` protection of migrated values.

## Decisions

- **No Batch API for backfill extraction** (deviation from plan
  decision 3): imported documents queue the standard `extract_document`
  job. One code path, budget-respecting, and at family scale the 50%
  batch discount is cents. Noted in migration.md limitations.
- **httpx.MockTransport over respx** — the client already accepts a
  transport, so no new dev dependency or lockfile change.
- Content duplicates (same bytes already in Library) are linked to the
  paperless id but their metadata is left untouched — the Library copy
  is assumed curated; the link makes future runs skip before download.
