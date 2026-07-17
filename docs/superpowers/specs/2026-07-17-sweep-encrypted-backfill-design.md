# Backfill: unlock existing encrypted PDFs (`library sweep-encrypted`)

## 1. Purpose

A one-off maintenance command to unlock PDF documents that were ingested and
**failed** before the [ingest-time PDF unlock](../../ingestion.md#pdf-unlock-librarypdf_unlock)
feature existed. It tries the configured `pdf_unlock_passwords` (plus the empty
password) against each encrypted PDF and, on success, unlocks it **in place** so
the same document — id, uploader, dates, history — becomes a normal usable
document. Mirrors the `sweep-junk` CLI: dry-run by default, `--apply --ids` to
act.

## 2. Key facts that shape the design

- **Encrypted ⟹ failed at OCR.** pypdfium2 cannot read an encrypted PDF's text,
  so an encrypted PDF always fails at the OCR stage and has **no** downstream
  rows (`ocr_text`, `DocumentPage`, `DocumentChunk`, embeddings). In-place reset
  is therefore clean — nothing stale to clear.
- **`advance_pipeline` treats `failed` as terminal** — reprocessing requires
  resetting `status → received` and re-deferring `process_document`.
- **Content-addressed storage.** Decrypting changes the bytes → new `sha256` →
  new stored path. `sha256` is globally unique in `documents`, so the apply path
  must guard against a collision with an existing (incl. soft-deleted) row.
- **`unlock_pdf` already classifies by return identity** (`src/library/pdf_unlock.py`):
  returns the *same* object → not encrypted / unreadable; returns *new bytes* →
  was encrypted, now unlocked; raises `PdfLockedError` → encrypted, no known
  password. No new detection code is needed.

## 3. Command

`library sweep-encrypted` (typer command in `src/library/cli.py`).

### 3.1 Candidate set

Non-deleted `application/pdf` documents with `status='failed'`. For each, read
the stored original (`storage.path_for`) and call
`unlock_pdf(content, settings.pdf_unlock_passwords)`:

| `unlock_pdf` result        | Classification            |
|----------------------------|---------------------------|
| returns same object        | not encrypted / unreadable → **skip** |
| raises `PdfLockedError`     | encrypted, **locked** (report only) |
| returns new bytes           | encrypted, **unlockable** ✅ |

### 3.2 Dry run (default, read-only)

Prints one line per encrypted doc (`id · filename · unlockable|locked`), then
totals: *"N failed PDFs scanned, E encrypted, U unlockable."* Nothing is
written. Ends with the pre-filled `library sweep-encrypted --apply --ids <U ids>`
command. Passwords are never printed.

### 3.3 Apply (`--apply --ids`)

`--apply` requires an explicit `--ids` list and refuses any id outside the
current *unlockable* candidate set (batch refused before any write, like
`sweep-junk`). Per id:

1. `decrypted = unlock_pdf(original_bytes, passwords)`; `new_sha = sha256(decrypted)`.
2. **Collision guard:** if a `Document` with `new_sha` already exists → skip and
   report (the decrypted content is already in the library / matches a
   soft-deleted row). Prevents the unique-constraint violation.
3. `store(decrypted)` → set `document.sha256 = new_sha`, `status = received`,
   clear `ocr_text`/`ocr_confidence`/`page_count`/`searchable_pdf` → record a
   `pdf_unlocked_backfill` `IngestionEvent` `{old_sha, new_sha}` (never the
   password) → **commit**.
4. `remove(old_sha)` (delete the encrypted original + its derived dir) → defer
   `process_document(id)`.

**Ordering** — store → commit → remove-old → defer — guarantees no crash leaves
a row pointing at a missing file (a mid-way crash only orphans a harmless file).

## 4. Reuse

`unlock_pdf`, `storage.store` / `remove` / `path_for`, `jobs.process_document`,
and the `sweep-junk` CLI scaffolding (dry-run/apply/`--ids` refusal). No new
decryption, storage, or pipeline logic.

## 5. Testing

- **Dry-run classification** — a failed encrypted PDF (default password) is
  reported `unlockable`; one with an unknown password is `locked`; a failed
  *non*-encrypted PDF is not listed.
- **Apply** — an unlockable doc: `sha256` changes to the decrypted hash, the
  stored file reopens without a password, a `pdf_unlocked_backfill` event is
  written, `status` is back to `received`, a `process_document` job is queued,
  and the old original is removed.
- **Collision** — when the decrypted content already exists as another doc, the
  id is skipped and reported; the original row is untouched.
- **Refusal** — `--apply` with an id outside the unlockable set refuses the
  whole batch.

## 6. Running on prod

The `library` CLI runs inside the deployed container against the live DB and
`/data`. The exact `docker compose exec` invocation on the `paperless` host is
confirmed before running; the dry run is executed first and its output reviewed
before any `--apply`.

## 7. Out of scope

Re-ingest-as-new-document semantics, brute-forcing beyond the configured list,
non-PDF formats, and unlocking documents that are not `status='failed'`
(encrypted PDFs cannot reach any other terminal state).
