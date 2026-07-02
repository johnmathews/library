# Forwarded-invoice recipient + dashboard ordering questions

Two production questions about document 123 (a forwarded Google Cloud invoice,
ingested via email on 2 Jul). Investigated against the live `paperless` LXC
stack (library-db / library-worker).

## 1. "Why can't I see doc 123 on the dashboard?"

Not a bug: it **is** on the first page, at position 5. The dashboard sorts by
`document_date DESC NULLS LAST, created_at DESC, id DESC`
(`search.py:172-175`), i.e. by the date printed **on** the document, not by
ingestion time. Doc 123 is an invoice dated **30 Jun**, forwarded on **2 Jul**;
four documents carry a document date of 1–2 Jul, so 123 sorts below them.

The Pushover deep-link works because it targets `/documents/123` directly,
bypassing the sort. Expectation mismatch ("most recently *added* at top" vs
"most recent document *date* at top"), not a missing document. Left the
ordering as-is (documented behaviour); offered a sort-by-ingestion toggle as a
possible follow-up.

## 2. Empty recipient on a forwarded invoice (real bug)

**Symptom:** the invoice says "Bill to: John Mathews" but `recipient_id` was
`NULL`.

**Root cause (proven, not guessed):**

- Re-ran extraction on 123's OCR against prod's live code + API key: the model
  returns `recipient_name = "John Mathews"` (confidence high). The LLM was
  correct — the failure is downstream.
- Yesterday's constraint (`f653b72`, "don't invent recipients") made inference
  match-only via `match_existing_recipient`. `"John Mathews"` matched nothing:
  user `john` had username `"john"` and an **empty `display_name`** (display-name
  matching is disabled when empty), and existing recipients were `John` /
  `Ritsya` / `Mathews` — none equal to "john mathews". So it was dropped.
- The email-`To:` fallback couldn't save it either: 123 was **forwarded**
  (`email_from: mthwsjc@gmail.com` → correctly resolved `uploader_id=2`), so the
  `To:` header is the library dropbox `itsa.big.pizza+library@gmail.com`, which
  matches no user's `email_forward_addresses`. Both tiers missed → recipient
  stayed unset.

The `f653b72` design leaned on the To: fallback to fill recipients for email,
but that fallback silently doesn't apply to **forwarded** mail (To: = dropbox,
not the user). Doc 123 is the first forwarded invoice to expose the gap.

**Fix (two parts, per user guidance "both: precise + owner fallback"):**

1. **Precise (data).** Set `john.display_name = "John Mathews"`. The existing
   `_match_user` display-name path now resolves "John Mathews" → user john →
   recipient "John" (id 1, already linked). Verified live: `"John Mathews"` and
   `"John"` both resolve to `('John', 2)`, `"Ritsya"` → Ritsya, `"Some Stranger"`
   → `None` (still no junk rows). No code deploy needed for this tier.
2. **Owner fallback (code).** Added a final tier in `_apply_outcome`
   (`extraction/apply.py`): when recipient is still unset after the LLM and the
   To: fallback, and the document has an `uploader_id`, attribute it to that
   owner's linked recipient (`get_or_create_user_recipient`). For personal mail
   you forward to the library, the forwarder (owner) *is* the recipient — even
   when the addressee name matches nobody and To: is the dropbox. Fill-only;
   LLM-matched recipient wins, then To: user, then owner; a manual edit still
   wins over all. Requires deploy to take effect on prod.

Doc 123 itself: set `recipient_id = 1` directly (now shows recipient "John").

## 3. Verification

- New tests (`tests/test_extraction_apply.py`): owner fallback fills from
  `uploader_id` when nothing else resolves; To: user takes precedence over the
  owner fallback; the existing "To: matches nobody → recipient stays null"
  case still holds (no `uploader_id`, so the owner tier can't fire).
- Full backend suite **805 passed**; `ruff check` / `format --check` clean.
- Docs updated (`ingestion.md`: recipient resolution now a three-tier chain —
  LLM → To: user → owner; email-in section notes forwarded mail).
- Live prod verified: 123 recipient = John; display_name = "John Mathews";
  name-match resolution as above.

## 4. Follow-ups (not done here)

- Deploy the owner-fallback code (commit → CI `promote` green → `make deploy`).
  Until then, prod relies on the display_name/precise tier, which already covers
  the "John Mathews" case.
- Optional dashboard sort-by-ingestion-date toggle (question 1).
- The orphaned `Mathews` recipient (id 3, unlinked, 0 docs) is still the pending
  cleanup noted in `260702-larger-tile-text-and-recipient-constraint.md` §3.
