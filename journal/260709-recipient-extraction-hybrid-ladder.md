# Strengthening recipient / sender / date / kind extraction

**Date:** 2026-07-09

## Why

The `recipient` field on documents was often blank. An engineering-team review
found two independent root causes, and fixing only one wouldn't have helped:

1. **The prompt didn't ask for it well.** `recipient_name` guidance never told
   the model that a salutation ("Dear/Beste/Geachte/T.a.v. …") is the recipient
   signal; `sender_name` never mentioned the sign-off/signer. Worst of all,
   `due_date` and `expiry_date` had **zero** guidance — the words "due" and
   "expiry" appeared nowhere in the system prompt — so the model guessed and
   frequently conflated them (badly in Dutch, where "vervaldatum" = due but
   "verloopt/geldig tot" = expiry).
2. **Two ingest paths made recipient structurally unfillable.** Paperless-import
   and consume-folder documents were created with `uploader_id = NULL`, so the
   uploader fallback that fills recipient for web/email docs could never fire for
   them, and the old resolver *dropped* any addressee name that didn't match a
   known household member. The migrated paperless corpus was the dominant blank
   bucket.

## Decisions (with the user)

- **Hybrid recipient model, as a priority ladder.** The recipient named in the
  document is the overriding signal: (1) document-stated name — matched to a
  known person, else a plain recipient is *created* from it when the extraction
  is high-confidence; (2) email `To:` known user; (3) uploader/owner. This
  deliberately **reverses** the old "inference must never invent a recipient"
  rule, gated on confidence so garbled OCR can't seed junk rows.
- **Salutation = recipient, sign-off = sender.** The user's original phrasing put
  the sign-off under recipient; corrected — a signer is the sender.
- **Backfill existing + going forward.** Bumping `PROMPT_VERSION` makes the older
  corpus stale so `library backfill` re-extracts it under the new prompt.
- **Email = earliest known recipient in a forwarded chain**, not the outer
  wrapper (which is the library dropbox).

## What shipped (7 work units)

1. **Prompt** (`extractor.py`) — contrasting document/due/expiry date definitions
   with English+Dutch cue words and a "never copy the same date into both" rule;
   salutation→recipient and sign-off→sender guidance; a `kind_slug`
   disambiguation rubric. `PROMPT_VERSION` → `2026-07-09.1`.
2. **Schema** (`schema.py`) — per-field `Field(description=…)` on the focus fields
   (Anthropic structured-output best practice); additive `addressee_raw` /
   `signer_raw` verbatim-capture fields. Removed the now-unused `OptionalIsoDate`
   alias (dates inline their `Annotated[... Field(description=)]`).
3. **Email hint** (`extractor.py`) — when the email envelope resolves to a known
   user, a one-line "addressed to <name>" context block is appended to the
   prompt (resolved display name, never a raw address); the document's own
   recipient still wins.
4. **Hybrid recipient** (`apply.py`) — the priority ladder above, with the
   confidence gate; `resolve_recipient_hint` resolves the envelope for W3;
   provenance now stores `addressee_raw`/`signer_raw`. Docstrings/comments that
   asserted "never invent" were rewritten.
5. **Deterministic validation** (`validation.py`) — pure-stdlib cross-checks:
   `due_expiry_grounding` (bilingual cue words; catches a mislabeled
   "vervaldatum"), `missing_recipient` (salutation/addressee present but no
   recipient recorded), a signed-letter `missing_sender` (beyond the amount-only
   rule), and a document-date-independent floor on due/expiry. Also added the
   previously-missing `expiry_date < document_date` test.
6. **Context fallbacks + backfill** (`email_ingest.py`, `consume.py`,
   `importer/runner.py`, `cli.py`, `config.py`, `ingest.py`) —
   `_forwarded_to_addresses` parses the original `To:`/`Aan:` out of a forwarded
   body and lists it first, so the earliest known recipient wins; a new
   `import_default_owner` setting (`LIBRARY_IMPORT_DEFAULT_OWNER`) attributes
   consume/import documents to an owner so rung 3 can fire; `library backfill
   --kinds letter,invoice,receipt` scopes a cost-controlled recipient backfill.
7. **Docs** — `docs/ingestion.md` (recipient ladder, schema table, prompt,
   backfill, consume/env), `docs/api.md` (recipient PATCH row was inaccurate),
   `docs/architecture.md` (recipient ladder), `docs/roadmap.md`, `.env.example`.

## Deferred

- **Consume-folder scanned-file metadata** (PDF `/Author`, EXIF) as a recipient
  signal. PDF author metadata is unreliable for scans and low value now that
  rung 1 extracts the document's own salutation. Revisit only if a concrete,
  reliable metadata source appears.

## Verification

Full backend suite green (**1075 passed**), coverage **88%**, whole-repo ruff
check + format clean. New tests cover: schema descriptions + raw fields; prompt
due/expiry/salutation/sign-off guards; the email hint; the hybrid ladder
(high-confidence create, low-confidence drop, 3-way precedence, addressee_raw
fallback); every new validation rule; forwarded-To parsing; the consume
default-owner; and `backfill --kinds`.

## Follow-up for operators

To fix the *existing* blank recipients, after this deploys run (worker up):
`library backfill --kinds letter,invoice,receipt --dry-run` to see counts, then
without `--dry-run`. Optionally set `LIBRARY_IMPORT_DEFAULT_OWNER` so the
paperless/consume corpus can fall back to an owner when a document names nobody.
