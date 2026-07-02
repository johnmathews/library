# FX-rate seeding + charts/dashboard/sidebar polish

**Date:** 2026-07-02
**Branch:** `feat-fx-seeding-and-ui-polish`
**Status:** approved, in build.

A small follow-on to the currency-normalize feature (W5 of the 2026-07-02
dashboard/admin cycle), plus five UI polish items batched into the same change.

## Work items

### W1 — FX rate seeding (backend)

The currency-normalize flow flags `fx_rate_missing` but offers no way to seed a
rate. This adds an admin affordance that seeds an `fx_rates` row so cross-currency
conversion resolves. A **single** row is sufficient — `fx.rate_to_base` falls back
to the nearest endpoint for any date. USD is the implicit base (1.0) and is never
seeded.

**New `src/library/fx_api.py`** — live provider client, shaped like
`embedding/client.py` (async, injectable `httpx.AsyncClient`, timeout from `Settings`):
- `async def fetch_rate_to_base(currency, *, settings, client=None) -> Decimal | None`
- Calls `GET {fx_api_url}/USD` (`open.er-api.com/v6/latest/USD`). Response:
  `{"result":"success","rates":{"EUR":0.92,...}}`. `rates[X]` = units of X per 1
  USD, so **`rate_to_base(X) = Decimal(1) / Decimal(str(rates[X]))`**, quantized to
  8 dp (matches `Numeric(18,8)`). Never float.
- Returns `None` when the currency is absent from `rates`; raises `FxApiError` on
  transport failure or `result != "success"`.
- `Settings` gains `fx_api_url: str = "https://open.er-api.com/v6/latest"` and
  `fx_api_timeout_s: float = 10.0`.

**New `src/library/fx_admin.py`** — service (keeps `fx.py` pure conversion):
- `list_fx_status(session)` → per in-use currency (reuses `list_currencies_in_use`):
  `code, document_count, has_rate, rate_to_base?, as_of?, is_base`. USD → `is_base`
  (always convertible, rate 1.0, not seedable).
- `seed_fx_rate(session, currency, rate_to_base, as_of)` → validates via
  `normalize_currency_code`, **rejects USD**, **upserts** on `(currency, as_of)` via
  Postgres `INSERT ... ON CONFLICT DO UPDATE` (atomic, so **no advisory lock**).
- `seed_fx_rate_live(session, currency, *, settings, client)` → `fetch_rate_to_base`
  then `seed_fx_rate` with `as_of = date.today()`.

**Routes** (end of admin.py currency section; auto-gated by `require_admin`):
- `GET /api/admin/fx-rates` → `list[FxRateStatus]`.
- `POST /api/admin/fx-rates` → `{currency, source: "live"|"manual", rate_to_base?, as_of?}`.
  `live` fetches; `manual` uses the supplied `Decimal` (gt=0). USD → 422; bad code →
  422; live fetch fail/unsupported → **502 with a clear detail** (UI falls back to
  manual entry).

**Coverage headroom:** direct main-thread unit tests for `seed_fx_rate` /
`list_fx_status`, plus `fetch_rate_to_base` against an `httpx.MockTransport`
(success / unsupported / error). (TestClient handler lines undercount.)

### W2 — FX seeding (frontend)

- `api/admin.ts`: `listFxRates()`, `seedFxRate({currency, source, rate_to_base?, as_of?})`
  mirroring `listCurrencies` / `normalizeCurrency`.
- AdminView Currencies card gains a **standing "FX rates" subsection**: each in-use
  code with has-rate/no-rate status; a **"Fetch rate"** button (live) and a
  collapsible **manual-entry** fallback form. Existing `currency-fx-warning` points at
  this subsection. testids: `fx-row-{code}`, `fx-fetch-{code}`, `fx-manual-toggle-{code}`,
  `fx-manual-input-{code}`, `fx-seed-submit-{code}` — no `getByLabel` reliance.

### W3 — Move the Fields button

Move `<DashboardFieldsMenu />` from its own row into the right-hand controls group
next to **Sort** and **Tiles per row** (`DocumentListView.vue`), aligned `items-end`.

### W4 — "Needs review" as a collapsed section, not a pill

- Count: `listDocuments({ review_status: 'needs_review', limit: 1 })`, read `total`
  into `reviewCount` (refetched on load / after verify). No new endpoint.
- Restyle `rounded-full` pill → **`rounded-md` block** reading as a collapsed section.
  `count > 0`: **pale red bg + darker red border** + text **"{n} document(s) need
  review"**. Active (filter on): active treatment. `count === 0` & not active:
  **hidden**. Responsive at all widths.

### W5 — Charts defaults + the red bar

- **Force-reset** via storage-key bump: `library:charts-timeframe-v2` default `'12m'`,
  `library:charts-grouping-v2` default `'month'`. Old values ignored → new defaults
  visible immediately; future manual changes still persist.
- **Red bar:** unchanged — it is the "latest document" highlight, shown only ungrouped;
  grouped bars stay uniform blue. (Documented, no code change.)

### W6 — Grouped tooltip: total + per-document breakdown

- Extend `GroupedPoint` with `items: {amount, label}[]`; `groupSeriesPoints` collects
  each contributing document's amount (+ label).
- Tooltip: total+count on the first line; an `afterBody` callback lists each
  document's amount on its own row (currency appended if the series has one),
  **capped at ~12 rows with "+N more"**.

### W7 — Sidebar order

Current: Documents, Upload, New note, Ask, Settings, Jobs, Charts, Projects, Admin.
**New:** Documents, Upload, New note, **Charts**, Ask, Jobs, **Projects, Settings**, Admin.

## Cross-cutting

- **Tests:** backend (fx_api, fx_admin, admin API incl. anon/non-admin gating) +
  frontend (admin.ts, AdminView FX, DocumentListView Fields/needs-review, charts
  composables new defaults+keys, groupSeriesPoints breakdown, tile tooltip, sidebar
  order). Full backend + frontend suites + ruff (whole repo; format new files) +
  type-check/lint.
- **Docs:** `docs/api.md` (fx-rates §), `docs/admin.md` (FX subsection),
  `docs/frontend.md` (charts defaults, needs-review, Fields placement, sidebar),
  config docs for the two new settings; dated journal entry.
- **No schema migration** — `fx_rates` already exists.
- **Deploy note:** backend now makes an outbound HTTPS call to `open.er-api.com`;
  confirm LXC egress during post-deploy verify.
