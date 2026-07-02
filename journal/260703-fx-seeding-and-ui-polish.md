# FX-rate seeding + charts/dashboard/sidebar polish

**Date:** 2026-07-03
**Branch:** `feat-fx-seeding-and-ui-polish`
**Spec:** `docs/superpowers/specs/2026-07-02-fx-seeding-and-ui-polish-design.md`

A small follow-on to the currency-normalize feature (fills the "no way to seed an
FX rate" gap it left) plus five batched UI-polish items. Brainstormed the design
first; built directly (no full engineering-team cycle).

## What shipped

1. **FX rate seeding (W1/W2).** The normalize flow flagged `fx_rate_missing` but
   couldn't fix it. Now:
   - `src/library/fx_api.py` — `fetch_rate_to_base(currency)` calls the **keyless**
     `open.er-api.com` (`GET /USD`), which returns USD→X; inverts to
     `rate_to_base(X) = 1 / rates[X]`, `Decimal`, quantized to 8 dp. `None` when
     unlisted; `FxApiError` on transport/payload failure. Config
     `fx_api_url`/`fx_api_timeout_s`.
   - `src/library/fx_admin.py` — `list_fx_status` (per in-use code: base/seeded/
     missing), `seed_fx_rate` (validate, reject USD, **upsert on
     `(currency, as_of)`** via `ON CONFLICT DO UPDATE` — atomic, no advisory lock),
     `seed_fx_rate_live` (fetch then seed, `as_of=today`).
   - Routes `GET`/`POST /api/admin/fx-rates` (auto-gated by `require_admin`).
     `source: live|manual`; USD/bad-code → 422; provider down or unlisted → **502**
     so the UI falls back to manual entry.
   - Frontend: `api/admin.ts` `listFxRates`/`seedFxRate`; AdminView Currencies card
     gained a standing **FX rates** subsection (`fx-row-*`, `fx-fetch-*`,
     `fx-manual-*`) — Fetch rate (live) + manual fallback that auto-opens on a live
     failure.

2. **Fields button moved (W3).** `DashboardFieldsMenu` now sits in the Sort/Tiles
   controls row (right-aligned, `items-end`), not its own row above the grid.

3. **"Needs review" restyle + count (W4).** From a yellow pill to a `rounded-md`
   collapsed-section block: warning icon, **"{n} document(s) need review"**,
   pale-red bg + darker-red border (active state deepens). Count is a cheap
   `limit:1` total probe refreshed on each list load; the button **hides** when the
   count is 0 and the filter is off. Full-width on mobile.

4. **Charts defaults + red bar (W5).** Storage keys bumped to
   `library:charts-timeframe-v2` / `-grouping-v2` with new defaults **Last 12
   months** + **By month** (a one-time reset that reaches existing machines; later
   manual choices persist). The red bar is just the highlighted latest document,
   shown only ungrouped — documented, unchanged.

5. **Grouped tooltip breakdown (W6).** `GroupedPoint` now carries `items`
   (per-document amount + label); the tooltip shows the bucket total + count, then
   each document's amount on its own row (capped ~12, "+N more"), currency
   appended when the series has one.

6. **Sidebar order (W7).** Documents · Upload · New note · **Charts** · Ask · Jobs
   · **Projects** · **Settings** · Admin.

## Decisions worth remembering

- **Live FX vs manual (D1).** The user picked a **live API** over manual entry,
  then a **keyless provider** (`open.er-api.com`, ~160 currencies) to avoid a
  secret, with **manual as the automatic fallback**. So a code is never left
  permanently unconvertible even if the provider is down or lacks it.
- **`as_of` + upsert (D2).** Today by default, upsert on `(currency, as_of)` — a
  single row suffices (fx.rate_to_base falls back to the nearest date), and
  re-seeding the same day is idempotent. No advisory lock (single-row ON CONFLICT
  is atomic).
- **Charts default reset (D3).** Bumping the storage key is what makes the new
  defaults actually visible to an existing user (localStorage already held the old
  values); a plain default change would have been invisible.

## Gotchas hit / re-confirmed

- **`type="number"` v-model coerces to a number** → `.trim()` blew up in
  `seedFxManual`. Switched the manual input to `type="text" inputmode="decimal"`
  (also keeps the exact Decimal string) and hardened with `String(...)`.
- **jsdom doesn't submit a form on submit-button click** — the AdminView FX tests
  had to `trigger('submit')` on the form, not click the button.
- **Coverage undercount** still applies; added direct main-thread unit tests for
  `seed_fx_rate`/`list_fx_status`/`fetch_rate_to_base` (fx_admin 97%, fx_api 86%).
- **Review-count probe polluted `/api/documents` call assertions** — isolated it in
  the DocumentListView test mock (distinct `review_status=needs_review&limit=1`
  response) and excluded it from the `documentUrls()` helper.
- **Stale node_modules** — `sortablejs` (added last cycle) wasn't installed in the
  frontend; `npm install` fixed it before vitest would run.

## Tests / checks

- Backend: **855 passed** (was 835; +20). New: fx_api unit (invert/quantize/
  base/unlisted/error), fx_admin unit (upsert, reject-USD, list-status, live
  success/unsupported), admin API (list, manual, live mocked, 422s, 502s, anon/
  non-admin gating). Coverage **85%** (at gate).
- Frontend: **655 passed** (was 642; +13). New: charts composable defaults +
  `items` breakdown, tile grouped-tooltip, sidebar order, DocumentListView
  needs-review (count/singular/hidden/toggle) + Fields placement, AdminView FX
  subsection (list, live fetch, manual fallback, validation). type-check + ESLint
  clean.
- ruff check + format clean over the whole repo. No schema migration
  (`fx_rates` already existed).

## Follow-ups (not done)

- The review-count is refreshed on list load, not after verifying a doc on its
  detail page then returning to an identical URL — it self-heals on the next
  filter change / reload. Fine for a single user.
- Live FX seeds one row dated today; no historical backfill. Manual entry can seed
  a past `as_of` via the API but the UI only seeds "today". Add a date field if
  date-accurate historical conversion becomes important.
- Deploy note: the backend now makes an outbound HTTPS call to `open.er-api.com` —
  confirm the LXC has egress when the live "Fetch rate" is first used.
