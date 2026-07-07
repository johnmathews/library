# DOCX ingestion — convert Word documents to Markdown at ingest

Follow-on to the [email attachment drops](260707-email-attachment-drops-and-review-reasons.md)
work: a forwarded `.docx` used to sniff correctly as
`application/vnd.openxmlformats-officedocument.wordprocessingml.document`, fail
the `ALLOWED_MIME_TYPES` gate, and be dropped-but-flagged. Now it is ingested.

## 1. What shipped

Word `.docx` uploads/attachments are converted to Markdown at the single ingest
choke point and flow through the existing extraction → chunk → search → viewer
path. The `.docx` is kept as the content-addressed **original** (source of
truth — downloadable, re-convertible); the Markdown is a **derived** artifact
`converted.md` under `derived_dir(sha256)`, mirroring HEIC's original+derived
split. The document keeps `mime_type = DOCX_MIME`.

## 2. Key decisions

### 2.1 Retain the original vs store-markdown-only

Two designs were on the table: (a) convert and store the Markdown as the content
(discard the `.docx`, like the email HTML-body path), or (b) keep the `.docx` as
the original + a derived Markdown (like HEIC). Chose **(b)** — the original stays
downloadable and re-convertible if the converter improves later. The cost is that
`mime_type = DOCX_MIME` has to be threaded through every MIME-routing surface.

### 2.2 Convert, don't widen the OCR engine set

Per the `library-mime-type-surfaces` project memory, a new ingest MIME must be
threaded through the pipeline or it ingests then raises `UnsupportedOcrInputError`.
Rather than build a bespoke docx OCR engine, the OCR router's docx branch just
reads the derived `converted.md` (engine `docx`), so docx is treated as the
already-first-class Markdown everywhere downstream.

### 2.3 Light conversion path

`mammoth.convert_to_html` → shared `html_to_markdown` (BeautifulSoup strip
`script`/`style` + `markdownify` ATX). Only `mammoth` is new; `markdownify` and
`beautifulsoup4` were already deps (they power the email HTML-body path). The
`_html_to_markdown` helper in `email_ingest` was extracted to
`library.markdown.html.html_to_markdown` and both paths now share it. The docx→PDF→OCR
fallback was explicitly left out of scope (not needed).

## 3. Surfaces threaded (the risk area)

Every place that branched on `text/markdown` had to admit `DOCX_MIME`:

1. `ingest.py` — `ALLOWED_MIME_TYPES` + a HEIC-style transform block writing `converted.md`.
2. `ocr/router.py` — docx branch reads `converted.md`; re-converts the original if it is missing.
3. `markdown/apply.py` — docx joins the born-digital passthrough (verbatim `DocumentPage`, no Anthropic call).
4. `embedding/chunker.py` — extracted `chunker_for_mime()` (used by `jobs.py`) so docx chunks structure-aware like Markdown.

Verified to need **no** docx branch (documented so they aren't re-added):
extraction (reads `ocr_text`; skips gracefully on empty), the vision renderer
(never reached — passthrough handles docx first), thumbnails (skips like text),
and the download route (serves by `mime_type`).

`DOCX_MIME`/`CONVERTED_MARKDOWN_NAME` live in `library.docx` with a **lazy**
`mammoth` import, so importing the constants across the pipeline stays cheap.

## 4. Frontend

A `.docx` has no inline preview (not image/pdf), so the detail view shows the
converted Markdown in the reader and adds a **Download original** affordance
(`hasDownloadableOriginal` = `preview === 'none' && !mime.startsWith('text/')`) so
the retained `.docx` is reachable. Text originals (markdown/plain/notes) are
already shown verbatim, so they get no such link.

## 5. Issues found during development

### 5.1 Shared-backend dedup collision (found by the first full suite run)

`make_docx()` is byte-deterministic, and the integration suite shares one
session-scoped database with no per-test truncation
(`library-test-db-isolation`), so a second test ingesting identical docx bytes
hit sha256 dedup and created no document. Fixed by adding a `marker` param to the
fixture (a unique paragraph → unique sha), matching the existing `make_pdf(marker)`
pattern; every DB-ingesting docx test passes `marker=uuid4().hex`.

### 5.2 Ingest-time conversion failure returned 500 + orphaned the original (code review)

Conversion ran synchronously in the upload request after `store()`, so a corrupt
`.docx` that still sniffed valid but failed mammoth raised a 500 and left an
orphaned original file with no DB row. Made the ingest conversion **best-effort**:
on failure it logs and skips the derived write; the worker's docx branch
re-converts from the original and surfaces a normal `failed` document (visible,
retryable). The email path was already safe (per-attachment skip).

## 6. Tests & verification

- New: `tests/test_docx.py` (conversion unit tests + a `zipfile`-built `.docx`
  fixture in `tests/docx_fixtures.py`, no `python-docx` dep), docx cases in
  `test_ingest_detect`, `test_ocr_router`, `test_chunker`, `test_apply_born_digital`
  (parametrized), `test_ingest_api` (upload + conversion-failure), `test_email_ingest`
  (attachment ingests + body suppression), and frontend `DocumentDetailView.spec.ts`
  (download affordance shown for docx, hidden for text/markdown).
- Backend: 1007 passed, coverage 88% (gate held). Frontend: 885 passed + eslint +
  vue-tsc clean. Ruff check + format clean whole-repo.
