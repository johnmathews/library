# Migrating from paperless-ngx

**Status:** active. **Last updated:** 2026-06-11.

`library import paperless` copies every document out of a running
paperless-ngx instance (2.x or 3.0) into Library over the paperless REST
API — originals bit-for-bit, metadata mapped onto Library's model. The
import is idempotent and resumable: run it as often as you like; each
paperless document lands exactly once. Code: `src/library/importer/`.

## 1.1 Prerequisites

- A reachable paperless-ngx instance and an **API token** for a user who
  can see all documents: paperless UI → profile (top right) → *My
  Profile* → API Auth Token (or `Authorization: Token …` from
  `POST /api/token/` with username/password).
- The Library database migrated (`alembic upgrade head`) and the
  configured `LIBRARY_DATA_DIR` writable — imported originals are stored
  exactly like uploads.
- The Library **worker running** if you want the queued follow-up jobs
  (thumbnails, Claude extraction, OCR for documents paperless had no
  text for) to execute during/after the import. The import itself does
  not need the worker.

Credentials are given as flags or environment variables:

| Flag | Env var |
|---|---|
| `--url` | `LIBRARY_PAPERLESS_URL` |
| `--token` | `LIBRARY_PAPERLESS_TOKEN` |

The client pins `Accept: application/json; version=9`, which both
paperless-ngx 2.x and 3.0 understand.

## 1.2 Workflow: dry-run first

Always start with a dry run. It fetches and maps everything (taxonomies
plus every document page) but downloads nothing and writes nothing — the
database is only read, to detect already-imported documents — and prints
the mapping summary: how many documents would import, and the kind /
sender / storage-path / tag distributions so you can sanity-check the
mapping before committing.

```console
$ library import paperless --url http://paperless.lan:8000 --token <token> --dry-run
paperless import (dry run — nothing was written)
  documents seen:     1843
  would import:       1840
  skipped (duplicate): 0
  skipped (trashed):   3
  failed:             0
  kinds:
    invoice: 612
    receipt: 401
    ...
  storage paths:
    Family: 1102
    Atlas Consulting Expenses: 741
    ...
```

Then a small careful live run, then the real one:

```console
$ library import paperless --url http://paperless.lan:8000 --token <token> --limit 10
$ library import paperless --url http://paperless.lan:8000 --token <token>
```

Other flags: `--no-extract` skips queueing Claude extraction for the
imported documents (you can re-run extraction later per document);
`--limit N` only considers the first N documents. The command exits
non-zero if any document failed, with the per-document reasons listed.

## 1.3 What is transferred, exactly

Per document the importer fetches `metadata/` (for the original's MD5
checksum) and `download/?original=true` (the bit-exact original file),
verifies the MD5 — one retry on mismatch, then the document is recorded
as failed and the run continues — and ingests the bytes through the
standard ingestion path (content-addressed by SHA-256, deduplicated).
Downloads run with small concurrency (4); trashed documents
(`deleted_at` set) are skipped.

### 1.3.1 Mapping table

| paperless | Library |
|---|---|
| original file | original under `/data/originals/` (sha256-addressed), `source=import` |
| `title` | `title` |
| `created` | `document_date` |
| `content` (paperless OCR text) | `ocr_text` (audit event engine `paperless-import`); document is immediately searchable, no re-OCR |
| `correspondent` | `sender` (created by name, case-insensitive match) |
| `document_type` | `kind` via a name table (English + Dutch synonyms, e.g. *Factuur* → `invoice`); unmapped types → `other` plus a `paperless:<type>` tag |
| `tags` | tags (slugified, display name kept); any tag with `is_inbox_tag` adds the `needs-review` tag |
| `storage_path` | a plain tag — `slugify(name)`, **no prefix** (e.g. *Atlas Consulting Expenses* → `atlas-consulting-expenses`), display name kept; raw name also in `extra["paperless"]["storage_path"]`. Null storage path → no tag, no key; a stale storage-path id is logged and skipped |
| custom field, monetary (`"EUR123.45"`) | first one → `amount_total` + `currency` (bare numbers use the field's `default_currency`); raw value also kept in `extra` |
| custom field, select | option **label** (resolved from `extra_data.select_options`) in `extra["paperless"]["custom_fields"]` |
| custom field, documentlink | `extra["paperless"]["linked_documents"]`, remapped to Library document ids in a second pass after all documents exist |
| other custom fields | `extra["paperless"]["custom_fields"]` verbatim |
| `added`, `archive_serial_number`, notes | `extra["paperless"]` (`added`, `asn`, `notes`) |
| trashed (`deleted_at` set) | skipped |

Every field migrated from paperless (title, date, kind, sender, amount)
is listed in `extra["user_edited_fields"]`, so later Claude extraction
fills gaps but never overwrites curated paperless values.

### 1.3.2 Follow-up processing

Documents arriving **with** paperless OCR text go straight to status
`indexed` and queue only Claude extraction (skippable with
`--no-extract`) and a thumbnail job. Documents **without** text (rare —
e.g. paperless never finished processing them) go through Library's
normal pipeline: OCR, extraction, thumbnail.

## 1.4 Idempotency and re-runs

Two keys make re-runs safe:

1. **paperless id** — each imported document stores its
   `paperless_id` (unique column). A re-run skips these before
   downloading anything.
2. **content hash** — if the same bytes already exist in Library (e.g.
   you had also uploaded the file manually), no second document is
   created; the existing document is linked to the paperless id so the
   next run skips it by key 1. Its existing metadata is left untouched.

A run that died midway is simply re-run: completed documents are
skipped, failed/unprocessed ones are imported, and a document caught
exactly between ingest and metadata (ingested bytes with `source=import`
but no `extra["paperless"]`) is detected and finished rather than
skipped.

## 1.5 Batch escape hatch

Every run gets a batch uuid, printed in the final report and stored on
each imported document in `extra["paperless"]["batch_id"]` (plus a
`paperless_imported` ingestion event). If a batch turns out wrong,
identify it:

```sql
SELECT id FROM documents
WHERE extra->'paperless'->>'batch_id' = '<batch-id>';
```

and delete those documents through the API (soft delete) or SQL. The
report also lists every failed document with its paperless id and
reason, so a partial batch can be reconciled against the source
instance, which stays untouched throughout (the importer only ever
reads from paperless).

## 1.6 Limitations

- **Originals only.** paperless's archived (OCR-layered) PDF variants
  are not imported; Library re-derives searchable PDFs itself when it
  OCRs, and reuses paperless's text otherwise.
- **Extraction uses the regular API**, not the Anthropic Batch API the
  original plan suggested for the backfill discount — the standard
  per-document `extract_document` job keeps one code path and respects
  the daily budget; at family scale the difference is cents.
- paperless **owners/permissions, saved views and workflows** are not
  migrated (Library has no equivalents). Storage paths *are* carried
  over — as plain tags plus `extra["paperless"]["storage_path"]` (see
  the mapping table) — but the path *templates* themselves are not:
  Library's content-addressed storage has no configurable layout.
- ASN and notes survive only inside `extra["paperless"]`, not as
  first-class fields.
- A paperless document whose bytes match a **soft-deleted** Library
  document is recorded as a failure (Library refuses to resurrect
  deleted content); restore or purge the deleted document first.
- Custom fields beyond the first monetary one are preserved in `extra`
  but not promoted to typed columns.
