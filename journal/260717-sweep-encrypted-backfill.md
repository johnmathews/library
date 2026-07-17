# Backfill: unlock existing encrypted PDFs

Follow-on to the ingest-time [PDF unlock](../docs/ingestion.md#pdf-unlock-librarypdf_unlock):
a CLI backfill that unlocks encrypted PDFs already filed before that feature
existed.

## 1. What shipped

`library sweep-encrypted` (in `src/library/cli.py`), mirroring `sweep-junk`:

- **Dry run (default):** lists non-deleted `application/pdf` documents stuck in
  `failed` whose stored original is encrypted, classified `unlockable` vs
  `locked` against `LIBRARY_PDF_UNLOCK_PASSWORDS`, with totals and a
  ready-to-paste `--apply --ids …` line. Read-only; passwords never printed.
- **`--apply --ids`:** unlocks in place — stores the decrypted PDF as the new
  content-addressed original, points the row at the new `sha256`, resets it to
  `received` (clears stale OCR fields), records a `pdf_unlocked_backfill` event
  `{old_sha256, new_sha256}`, removes the old encrypted original, re-queues
  `process_document`. Refuses non-candidate ids (whole batch); skips + reports
  `sha256` collisions instead of merging.

## 2. Why it works out cleanly

- **Encrypted ⟹ failed-at-OCR**, so candidates have no downstream rows
  (ocr_text/pages/chunks/embeddings) — the in-place reset is clean.
- **`advance_pipeline` treats `failed` as terminal**, so the reset to `received`
  + re-queue is what re-runs the pipeline.
- **`unlock_pdf` output is deterministic** (verified), so the same locked PDF
  always yields the same decrypted `sha256` — repeat unlocks dedupe, and the
  collision guard is meaningful.
- **Ordering** store → commit → remove-old → defer means no crash leaves a row
  pointing at a missing file.

Reuses `unlock_pdf`, `storage.store/remove/path_for`, `jobs.process_document`,
and the `sweep-junk` scaffolding — no new decryption/storage/pipeline logic.

## 3. Tests

`tests/test_cli.py`: dry-run classification (unlockable/locked/not-listed),
apply (sha changes to decrypted hash, file reopens without a password, event
written, status reset, `process_document` queued, old file removed), refusal of
a non-unlockable id, and collision skip. Full backend suite green.

## 4. Design doc

`docs/superpowers/specs/2026-07-17-sweep-encrypted-backfill-design.md`.

## 5. Operating it

Run inside the deployed container against the live DB + `/data`; **dry run
first**, review the numbers, then `--apply` the reviewed ids with the worker
running.

## 6. First prod run (2026-07-17)

Ran on the `paperless` host via
`docker compose exec -T library-webserver library sweep-encrypted`:

- Dry run: **3 failed PDFs scanned, 3 encrypted, 3 unlockable** — ids 11
  (`Certificaat.pdf`), 30 (`Polis.pdf`), 182 (`Certificaat.pdf`), all opened by
  the default `2064`.
- `--apply --ids 11,30,182`: all three unlocked in place and re-queued; the
  worker reprocessed them `failed → received → … → indexed`. Final OCR text
  3162 / 1788 / 3162 chars; each carries a `pdf_unlocked_backfill` event with
  its old→new sha256. Three previously-dead documents are now searchable.

## 7. Wrap-up doc addition

Added a "password-protected PDF failed" entry to `docs/deployment.md`'s
troubleshooting list pointing operators at `LIBRARY_PDF_UNLOCK_PASSWORDS` and
`library sweep-encrypted`.
