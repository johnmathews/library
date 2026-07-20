# Document-detail UX fixes: verify-button gating, panel merge, date labels, id-based prev/next

Date: 2026-07-20

A batch of small `/documents/:id` (and dashboard) UX corrections reported by the
user, plus one dashboard CSS fix.

## 1. Matters row scrollbar (dashboard)

`DocumentFilterBar.vue`'s business-matter pill row used `overflow-x-auto`, which
draws a persistent horizontal scrollbar on desktop Edge/Chrome. Added the
existing house `no-scrollbar` utility (`assets/utility-patterns.css`) to that
row — the bar is hidden in every engine while the row stays scrollable.

## 2. "Mark verified" only when there is something to verify

The button (sidebar + review-queue "Verify & next") was gated on
`review_status !== 'verified'`, so it also showed on **`unreviewed`** documents
where nothing is flagged. Re-gated to `review_status === 'needs_review'`.

Consequence (intended): `verified` is now strictly the resolution of a flagged
document — a clean (`unreviewed`) doc can no longer be manually marked verified.

Also surfaced the flagged **attribute** as a friendly field-label chip
(`[data-testid="reason-field"]`) in the "Why this needs review" panel, mapped
from the finding's storage field in `utils/validationReason.ts` (matched to the
real field names emitted by `extraction/validation.py`, incl. `sender_id` /
`recipient_id` / `currency`).

## 3. Merge the Classification panel into Content

The metadata section tiles were Content / **Classification** (kind + language) /
Sender-&-dates / Financial / System. A two-field Classification tile read as
over-fragmented, so kind + language moved into **Content** (kind placed high,
after the summary) and the `metadata-classification` card was removed.

- `DocumentMetadataEditor.vue`: `fieldGroups` content now carries kind +
  language; `MetadataSection` drops `classification`; the Content tile fetches
  `listKinds()`.
- `useDocumentLayout.ts`: `metadata-classification` removed from
  `METADATA_CARD_IDS`; a saved layout still holding it is dropped on read by
  `reconcileCardColumns` (no migration needed).
- `DocumentDetailView.vue`: removed the classification `cardPresent` branch and
  its `<DocumentMetadataEditor section="classification">`.

## 4. Disambiguate the two dates

`document_date` (date printed on the document) and `created_at` (ingest time)
were labelled "Document date" and "Added date" — easy to confuse. Relabelled
app-wide to **"Date on document"** and **"Date added to library"**: metadata
editor row, hero field labels (`useDocumentLayout` `HERO_FIELD_LABELS`), the
dashboard sort control, `api/settings.ts` `DASHBOARD_FIELDS`, and the
review-reason field chip. Compact tile prefixes ("Date" / "Added") left as-is.

## 5. Previous/Next document by id (was list-sort order)

`useDocumentNeighbors` stepped through the list's remembered sort; under the
default `added_date desc` view that sent "Next →" to an *older*, lower id. Now
navigates **by document id** (Next → N+1, Previous → N-1) regardless of sort.
There is no backend `id` sort, so it scans `added_date desc` (effectively
id-descending) and computes the nearest ids either side **numerically** —
correct even on an `added_date` tie — stopping as soon as it crosses below the
current id.

## 6. Verification

- `npx vue-tsc --noEmit` — clean.
- `npx eslint src e2e` — clean.
- `npx vitest run` — 1015/1015 pass (86 files), incl. rewritten
  `useDocumentNeighbors` + `DocumentDetailView` specs and a new "hidden on
  unreviewed" case.
- `npm run build` — succeeds.
- `e2e/review-quality.spec.ts` updated: asserts the button is hidden on an
  unreviewed doc, then seeds a `needs_review` doc (currency-without-amount) to
  exercise click-to-verify.
- Docs updated: `docs/frontend.md` (neighbours, date labels, tile merge, verify
  gating, field chip) and `docs/api.md` (date-label references).

Not yet observed against the live deployment (doc 172 is in prod); the flows are
covered by @vue/test-utils mounts and the e2e spec.
