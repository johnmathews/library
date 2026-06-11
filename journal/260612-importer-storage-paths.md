# Importer: carry paperless storage paths over as tags

**Date:** 2026-06-12. Small contained fix to the W15 paperless-ngx
importer.

## Gap

The importer ignored paperless **storage paths** entirely — it never
fetched `/api/storage_paths/`. In this paperless instance every document
has one (e.g. *Family*, *Atlas Consulting Expenses*); they encode the
personal/business split, so dropping them lost a curated dimension on
every import.

The 106 documents already imported in production (batch `b6105c48…`)
were **backfilled by hand** — tags `family` / `atlas-consulting-expenses`
added manually after the fact. This change makes future imports do that
automatically; the production backfill itself is done and needs no
re-run.

## What changed

- **client.py** — `list_storage_paths()`, paginated like the other
  taxonomy endpoints (fields used: `id`, `name`).
- **mapper.py** — `Taxonomies` gains `storage_paths` (defaulted, so
  existing `from_lists` callers are unaffected); `map_document` attaches
  a **plain** tag `slugify(name)` with the original name as display name
  — deliberately *no* `paperless:` prefix, matching the hand-backfilled
  tags — and records the raw name in `extra["paperless"]["storage_path"]`
  (carried as `MappedDocument.storage_path_name`). Null/missing
  `storage_path` writes nothing (no null keys); a stale storage-path id
  (deleted between fetches) is logged and skipped, never crashes the run.
- **runner.py** — `ImportReport.storage_path_counts`, tallied like
  kinds/senders (`(none)` bucket included) and printed as a
  `storage paths:` section in the (dry-run) report.
- **Docs** — `docs/migration.md`: mapping-table row for `storage_path`,
  dry-run example updated, and the §1.6 limitation corrected (storage
  paths *are* migrated now; only the path templates are not).
- **Tests** — `FakePaperless` serves `/api/storage_paths/` and defaults
  `storage_path: null` on documents; new mapper tests (tag + extra,
  null → nothing, stale id → skipped) and extended integration tests
  (live import asserts the `family` tag + extra + counts; dry run
  asserts counts and the report section). 24 importer tests pass.

## Decisions

- **Plain slug, no prefix.** The `paperless:<Type>` prefix exists to
  preserve *unmapped* provenance; storage paths map cleanly onto
  Library's tag model and the production backfill already used plain
  slugs, so prefixing would have forked the taxonomy.
- **`(none)` bucket in the count.** Mirrors kinds/senders exactly, and
  makes a dry run show how many documents would arrive without a
  storage path.
