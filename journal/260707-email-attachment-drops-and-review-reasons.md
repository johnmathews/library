# Email attachment drops & more specific review reasons

Investigation started from a real production case: **document 135** came from an
email with three PDF attachments and one PNG. Only the PNG entered the library;
the three PDFs were missing, and the doc was flagged "needs review — extraction
was unsure", which says nothing useful.

## 1. What was actually wrong

### 1.1 Silent attachment drops

The email poller's attachment loop (`_ingest_attachments`) is **exhaustive by
design** — one email is meant to become N documents, one per supported
attachment. There is no "pick one" bug. The three PDFs were dropped by one of
four **silent** paths (empty payload, oversize, unsupported-MIME-after-sniff, or
an `IngestError`), none of which recorded anything durable, notified anyone, or
left a trace outside container logs. `detect_mime` sniffs magic bytes first, so
a PDF whose bytes don't sniff as `application/pdf` is rejected even though PDF is
in the allow-list — the most likely cause for 135 (size limit is 100 MB, so that
path was ruled out).

A second, related defect: the per-message `try/except` only caught `IngestError`
inside the loop, so any *other* exception on attachment #2 of 4 aborted the rest
and left the message to retry forever — another way to lose siblings silently.

### 1.2 Vague review reason

`self_reported_low` emitted a hard-coded "model reported low confidence" and
ignored the model's own one-line `reasoning_note`, which was already captured on
the document (`apply.py`) but never read by `validate()`.

## 2. What changed

### 2.1 Two-pass, loss-proof attachment handling

`_ingest_attachments` now classifies all attachments first (side-effect free),
then ingests the survivors, **stamping each with the email's dropped siblings**
via `ingest_file`'s existing `extra_document` seam. (Code review caught one gap:
when *every* attachment is dropped but the email has a usable body, the body
becomes the sole document — it must be stamped too, or the review reason would be
absent exactly when it matters most. Now handled in the body-fallback branch.) Because `extra` is seeded at
row creation, before the async processing job runs, `validate()` sees the
context with no race. Per-attachment failures (any exception) are caught,
recorded as a skip, and never abort siblings. Drops are counted in
`EmailPollSummary.attachments_dropped`, logged as a per-message WARNING summary,
and pushed to the owner via a new document-less
`dispatch_attachments_dropped_notification` (reuses the `processing_error`
opt-in). An `empty` payload (inline/signature cruft) is logged but deliberately
**not** surfaced, so it can't flag a real document.

### 2.2 Specific review reasons

- `self_reported_low` now threads `reasoning_note` into its message.
- New `email_attachments_dropped` rule ties a document to its dropped siblings.
- New `missing_sender` rule flags an amount-bearing document (bill/receipt) with
  no identified sender.
- Frontend `RULE_TITLES` gains titles for the two new rules.

## 3. Key decision: no schema migration for the all-dropped edge case

`IngestionEvent.document_id` is NOT NULL, so a mail where *every* attachment is
dropped (no document, no usable body) has nowhere in the audit trail. Rather
than migrate for a rare case, that case is covered by the WARNING summary + the
document-less notification (the "flag + notify" the user asked for). A nullable
`document_id` for a first-class "ingestion problems" view is noted as future
work. Per-field / candidate-value confidence ("ambiguous sender") is also
deferred — it needs an `ExtractedMetadata` schema change, not just a new rule.

## 4. Tests

- `tests/test_email_ingest.py`: multiple supported attachments → N documents
  (the previously-untested doc-135 shape); dropped sibling stamped on the
  survivor + notify fired; a per-attachment error no longer aborts siblings;
  body-fallback document also carries the dropped-sibling stamp.
- `tests/test_extraction_validation.py`: `reasoning_note` threading (present /
  blank fallback), `missing_sender` (fires / doesn't), `email_attachments_dropped`.
- `tests/test_notifications.py`: the document-less dropped-attachments dispatch.
- `frontend/.../validationReason.spec.ts`: titles + detail for the new rules.

Full backend suite (988) green; ruff clean whole-repo; frontend spec green.

## 5. Document 135 recovery — re-forward after deploy

Decision: recover by **re-forwarding** the original email to the library dropbox
rather than pulling it from the mailbox directly. Two caveats to be honest about:

1. **Order matters.** Re-forwarding must happen *after* this change is deployed —
   against the current live code it would just reproduce the drop.
2. **Outcome depends on the original reject reason.** If the PDFs were lost to the
   whole-message-abort bug or a transient error, the fix ingests them on the
   re-forward — full recovery. If they genuinely don't content-sniff as
   `application/pdf`, the fix will now *surface* them (review reason on the photo
   document + "Attachments not added" push) instead of dropping them silently, but
   they still won't ingest until we know why the bytes don't sniff — at which
   point the visible reason tells us exactly what to fix next.

Either way the silent-loss failure mode is gone: after deploy, a re-forward can
no longer lose files without telling anyone.
