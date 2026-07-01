# Email body ingestion — when the email *is* the document

## 1. Problem

Emailing the tool a message **with an attachment** ingested fine, but when the
**email body itself** was the invoice/document (no attachment), nothing
happened — no document was created.

## 2. Diagnosis

Not an accidental bug. Body-only ingestion was never implemented: the email-in
poller (`src/library/email_ingest.py`) looped only over `message.attachments`
and moved the message to the processed folder regardless, so a body-only mail
was filed away with zero documents. The limitation was stated in the module
docstring and pinned by `test_body_only_mail_moved_without_ingest`. So the fix
is a small feature, not a one-line patch.

## 3. Decisions

- **3.1 Trigger:** ingest the body **only when the attachment path produced
  zero documents** (no new, no duplicate). An invoice PDF with a "see attached"
  cover note therefore does not also spawn a body document. This kept the
  existing unsupported-attachment and duplicate tests unaffected (both still
  yield an attachment document).
- **3.2 Format:** prefer the **HTML** body, **converted to Markdown** and
  stored as `text/markdown`; fall back to the **plain-text** body
  (`text/plain`) when there is no HTML part. A genuinely empty body still
  creates nothing.
- **3.3 Why Markdown, not `text/html`:** the extraction pipeline (`run_ocr`)
  and markdown passthrough (`apply_markdown`) only handle
  `text/plain`/`text/markdown`; a stored `text/html` document would raise
  `UnsupportedOcrInputError` and never get searchable text — and the viewer
  renders Markdown, not raw HTML, so tables wouldn't render either.
  Converting HTML→Markdown is fully supported end-to-end (passthrough
  extraction, `chunk_markdown`, viewer rendering) with **zero** pipeline
  changes, and preserves tables. Cost: one small MIT dependency
  (`markdownify`, which pulls `beautifulsoup4`).

## 4. Implementation

- **4.1 `src/library/email_ingest.py`:** added `_html_to_markdown` (bs4 strips
  `script`/`style`, then `markdownify`), `_body_candidate` (HTML→Markdown
  preferred, else plain text; size-checked) and `_body_filename` (subject →
  safe `.md`/`.txt` name — the suffix is load-bearing, it's what `detect_mime`
  reads). `poll_mailbox` calls it only when `_ingest_attachments` returned
  `(0, 0)`. Renamed `AttachmentCandidate` → `IngestCandidate` since it now
  carries the body too, and extracted the shared `_event_detail` provenance
  helper.
- **4.2 Dependency:** `uv add markdownify` (MIT; pulls `beautifulsoup4`).
  No change to `ingest.py`/`ALLOWED_MIME_TYPES` — the body is stored as an
  already-supported text type, so `text/html` is never accepted anywhere the
  pipeline can't process it.
- **4.3 Tests:** a pure `_html_to_markdown` test (tables preserved, script/style
  dropped); new poller tests for HTML body (→ `text/markdown`), text-only body
  (→ `text/plain`), body-ignored-when-attachment-present, and empty-body; the
  old drop test was rewritten to assert a document is now created. Body fixtures
  use unique subjects/content to avoid sha256 dedup collisions in the shared
  session-scoped test DB.

## 5. Notes / limitations

- Trigger is "attachments produced zero documents," not "no attachments at
  all" — so a mail whose *only* attachment is an unsupported type now falls back
  to ingesting its body (better than the old silent drop).
- HTML-to-PDF rendering of bodies remains out of scope; the Markdown
  conversion is a lossy-but-faithful text representation, not a visual replica.
