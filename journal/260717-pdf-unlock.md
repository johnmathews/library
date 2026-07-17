# Password-protected PDF unlock at ingest

Small feature: when a password-protected PDF is uploaded, the app now tries a
short list of known passwords and, on success, unlocks it so it is fully usable
inside the library app (which is behind auth).

## 1. What shipped

- `library.pdf_unlock.unlock_pdf(content, passwords)` — pure, `pikepdf`-based
  (already in the tree via OCRmyPDF, so no new dependency). Tries the empty
  password first, then each configured password; returns decrypted bytes,
  passes non-encrypted/corrupt content through unchanged, or raises
  `PdfLockedError` (which reports only a *count*, never password values).
- `Settings.pdf_unlock_passwords` — `LIBRARY_PDF_UNLOCK_PASSWORDS`,
  comma-separated, **case-sensitive**, default `["2064"]`.
- `library.ingest.ingest_file` — for `application/pdf`, unlocks **before**
  hashing/storing, so the **decrypted PDF is the source of truth** (dedup, OCR,
  thumbnails, viewer, download all see a normal unlocked file). The `received`
  event records `pdf_unlocked`.
- `library.ocr.router` — `EncryptedPdfError`, raised by a cheap `pikepdf` guard
  in `_route_pdf` when a still-encrypted PDF reaches OCR (unknown password). The
  worker's existing `except Exception` turns it into a `failed` document with a
  clear reason — visible and retryable.

## 2. Decisions

- **Store decrypted, not decrypt-on-read** — the app is behind auth, so keeping
  the unlocked PDF as the stored artifact is simplest and makes every surface
  (including *Download original*) work with no per-read password handling.
- **Config setting, not a hardcoded constant** — `2064` is the default but the
  list is env-overridable; passwords are case-sensitive (unlike email senders,
  which are lowercased).
- **Failure = the existing worker `FAILED` path**, not a bespoke ingest-time
  status. Keeps the "worker owns FAILED" invariant; the reason string carries
  the explanation. The encrypted original is preserved, so adding the password
  later and re-processing unlocks it on retry.

## 3. Tests

- `tests/test_pdf_unlock.py` — passthrough, correct/first-match/empty password,
  no-match raises without leaking attempts, corrupt bytes pass through.
- `tests/test_config.py` — default + case-sensitive env split.
- `tests/test_ingest_api.py` — encrypted upload stored decrypted (`pdf_unlocked`
  true, sha of decrypted bytes, reopens without a password); plain PDF records
  false; unknown-password PDF stored as-is without crashing.
- `tests/test_ocr_router.py` — still-encrypted PDF raises `EncryptedPdfError`;
  the guard does not misfire on a born-digital PDF.
- Reusable fixture helper `tests.ocr_fixtures.encrypt_pdf`.

## 4. Docs

`docs/ingestion.md` — new "PDF unlock" section + the unlock step in the flow.
