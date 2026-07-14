# Email hold-for-review: decision record

**Date:** 2026-07-14

Decision record for the email hold-for-review feature (whole-email LLM
verdict, `held_emails` queue, `/held-emails` surfaces) plus the poller
hardening that preceded it. This was a full engineering-team cycle —
**evaluate → plan → develop** — run from
`.engineering-team/runs/manual-20260714T000000Z/` (`evaluation-report.md` and
`improvement-plan.md`, work units W1–W20). The feature itself is documented in
`docs/ingestion.md` ("Held for review") and `docs/runbooks/email-triage.md`;
this entry captures only the decisions and why they went the way they did.

## 1. The eight decisions

1. **Held emails are a first-class entity (`held_emails` table, migration
   0026), not a nullable `IngestionEvent.document_id`.** The event table is
   append-only, `selectin`-loaded on every document read, and has no
   lifecycle fields — bolting a document-less, resolvable row onto it would
   have taxed every document read and still needed status columns. The
   pattern was copied from `AuthoredSeriesSuggestion`, the codebase's
   existing pending/dismissed lifecycle.

2. **`held` is a separate concept from `needs_review`.** They answer
   different doubts: `needs_review` marks a *filed document* whose metadata
   is in doubt; a held email has *no document* — its library-worthiness is in
   doubt. `HeldEmail` never touches `Document.review_status`, so the review
   queue is untouched and the two counts stay honest (the dashboard shows
   them side by side, not summed).

3. **The whole-email verdict lives in the existing label pass (v2,
   `email-label-v2`), hoisted to message level, and fail-open is
   schema-enforced.** One call judges the items *and* the email — no second
   spend, and body-only mail is judged because the body became an item of
   its own. `email_verdict: Literal["file","hold"] = "file"` means any skip,
   error, budget stop, or index-mismatched response *cannot* express a hold —
   every failure degrades to today's ingest behaviour, never loss.

4. **Row before move; Message-ID is the authoritative pointer.** The
   `held_emails` row commits first (raising on failure, so the message stays
   put for the next idempotent poll), then the message moves to the Held
   folder — a hold can never lose its pointer. IMAP UIDs are folder-scoped
   and change on move, so "ingest anyway" re-fetches by `Message-ID` header
   search; the stored `imap_uid` is a hint only.

5. **Dismiss is DB-only.** An instant, infallible status flip: no IMAP
   round-trip to fail, the row stays as an audit trail, and the bytes remain
   in the Held folder forever. "Never lose a real document" survives a wrong
   dismiss — the message is still recoverable by hand or by a future
   re-ingest.

6. **Triage-flag visibility is fixed at ingest.** The two email findings
   depend only on `Document.extra`, seeded at creation — so they are computed
   there (pure `email_findings`, run by `ingest_file`). Two alternative
   mechanisms were **rejected**: calling `revalidate_document` at ingest and
   relaxing validating extraction's early returns — both would fire
   `empty_extraction` on pre-pipeline documents and false-flag the whole
   library.

7. **Budget integrity needs no migration.** The label pass's spend event
   anchors on the *first produced document — new or duplicate* — because
   `produced` includes duplicate results, the all-duplicate re-send case has
   an anchor without any schema change. The zero-produced residual (a held
   email) anchors its billing in the held row's `trace["label_usage"]`.

8. **Hold triggers default ON, with one master OFF-switch as the rollback
   lever.** `LIBRARY_EMAIL_HOLD_ENABLED=true` by default because holding is
   strictly safer than the previous silent drop; `=false` restores the
   pre-hold behaviour byte-for-byte. The per-trigger switches
   (`…_HOLD_BELOW_SUBSTANCE`, `…_HOLD_UNKNOWN_SENDERS`) exist for tuning,
   but rollback is a single flag.

## 2. What shipped around the decisions

Hardening first (W1–W7: doc de-drift, ingest-time flags, IMAP socket timeout,
poll overlap guard, budget anchoring, move-before-notify, test package), then
the feature foundation-up (schema → label v2 → poller hold mechanics → resolve
services → REST API → frontend queue + dashboard affordance → `email_held`
push → runbook → these docs). Production enablement (W20) follows: migration
0026, `LIBRARY_EMAIL_LABEL_ENABLED=true`, worker restart — recipe in
`docs/runbooks/email-triage.md` §7.
