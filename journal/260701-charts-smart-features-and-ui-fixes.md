# Charts smart features and UI fixes

Date: 2026-07-01. Branch: `ui-fixes-sidebar-notes-charts`. Run via the
engineering-team skill (run dir `manual-20260701T084847Z`).

Three requested UI areas, delivered as seven work units. Two of the requested
things already existed and just needed fixing/exposing; the charts "smart"
features were all new.

## 1. Sidebar toggle at all desktop widths (W1)

The narrow/wide desktop toggle already existed (Mosaic-template pattern: a
`sidebar-expanded` body class + a custom Tailwind v4 `@custom-variant`). The bug
was that it was **force-widened at `2xl` (≥1536px) and the toggle button was
hidden there** — so on a large monitor it looked like the feature was missing.

- Removed every `2xl:` force-wide variant (`AppSidebar.vue`): the width class,
  the nav-label `opacity` spans, and the logo `opacity`/`whitespace`. Un-gated
  the toggle button (`hidden lg:inline-flex 2xl:hidden` → `hidden lg:inline-flex`).
- Migrated the persistence key to the app's `library:` convention
  (`library:sidebar-expanded`), reading the legacy bare key once as a fallback.
- The `flex-1` main content reflows automatically off the sidebar's `w-*`; no
  layout math needed. Mobile drawer behaviour untouched (user hasn't tested
  phone yet — explicitly out of scope).

## 2. Notes editor: guidance into the subtitle (W2)

The `/notes/new` edit and preview panes were misaligned because the edit pane
had an explanatory `hint` above the textarea (the preview pane had none), so the
preview sat higher. Moved that guidance into the `PageHeader` `description`
("…The first line becomes the title; Markdown is supported and the preview
updates as you type.") and dropped the `AppTextarea` hint. Both panes now start
with a single label line and align.

## 3. Charts create form (W3)

- **Subtitle/context:** exposed the `description` field in the create form (the
  `AuthoredSeries` model + endpoint already accepted it; only the form lacked it).
- **Currency dropdown:** new `CurrencySelect.vue` + `useCurrencyOptions`
  composable — built-in EUR/GBP/USD plus an inline "Add another…" that appends a
  custom 3-letter code, persisted under `library:currency-options`. Replaced the
  free-text currency input.
- **Currency-mismatch warning:** mechanical (no LLM). `DocumentListItem` already
  carries each doc's `currency`, so when a selected document's currency differs
  from the chosen series currency we show an advisory amber warning. Non-blocking.

## 4. Charts shared time-axis (W4)

`useChartsTimeframe` composable + a `charts-timeframe` dropdown (All / YTD /
Last 12 months / Last 3 years, persisted under `library:charts-timeframe`). The
selected window is computed to `{min, max}` ISO bounds and passed as
`axis-min`/`axis-max` props into every `SeriesChartTile`, which now applies them
to the Chart.js time scale. Display-only — it never changes membership or data;
bounded windows pin `max` to today so all charts share the same right edge.

## 5. Charts smart features — signatures, suggestions, odd-ones-out, auto-continue (W5–W7)

Chosen design (confirmed with the user): **hybrid** matching — mechanical
`(sender_id, kind_id, currency)` **signature** with an LLM used ONLY to phrase an
odd-one-out reason — and **propose-for-review** auto-continue (never silent add).

### 5.1 Backend (W5, W6)

- New `authored_series_suggestions` table (migration `0021`) + model, with a
  `pending`/`dismissed` state. Dismissed rows are tombstones so a rejected
  candidate is never re-proposed.
- `series.py`: `SeriesSignature` + `derive_signature` (dominant triple +
  `dominance` fraction), `load_authored_signature`, `suggest_signature_matches`
  (signature-matching non-members, gated on `dominance ≥ 0.6`), `odd_ones_out`
  (members breaking the signature, first-differing axis).
- `series_match.py`: `generate_reason` (mirrors `series_insight`'s Anthropic
  call, `extraction_model`/haiku, ≤60 tokens) and `propose_authored_matches`
  (records `pending` suggestions on index; **never** inserts a member — the core
  invariant, asserted in tests).
- Job hook: `evaluate_series_autocontinue` deferred alongside
  `generate_series_insight` when a document reaches INDEXED (best-effort).
- Endpoints: `GET …/signature`, `GET …/suggestions`,
  `POST …/suggestions/{id}/accept`, `POST …/suggestions/{id}/dismiss`,
  `GET …/odd-ones-out`. Authored `/charts` entries gained additive
  `signature` / `suggestion_count` / `odd_one_out_count` keys so the dashboard
  renders badges without a follow-up fetch.
- New config: `series_autocontinue_enabled` (True),
  `series_autocontinue_min_dominance` (0.6), `series_suggestion_limit` (20).

### 5.2 Frontend (W7)

`SeriesChartTile` gained two lazily-loaded panels on authored tiles: a violet
**suggestions** panel (Add / Dismiss per row) and an amber **odd-ones-out**
panel (reason + Remove). Odd-ones-out loads only on expand because the reason
sentence triggers a per-member LLM call server-side. Both emit `changed` so the
grid refetches and the badges update.

## 6. Verification

- Backend: `772 passed`, `ruff check` + `ruff format --check` clean.
- Frontend: `534 unit tests passed`, `vue-tsc` type-check clean, `eslint` clean,
  production `vite build` + `check:assets` OK.
- Notable choices: the odd-ones-out reason is computed on demand and not cached
  (acceptable for a small family archive; the frontend fetches it lazily to
  avoid incidental LLM spend). Suggestion matching reuses `extraction_model`
  (haiku), which already has a pricing row — no `MODEL_PRICING_USD_PER_MTOK`
  change needed.

## 7. Follow-ups / not done

- Phone-screen sidebar behaviour untested (user's note); W1 is desktop-only and
  does not regress the mobile drawer.
- The odd-one-out `reason` is uncached; if it becomes a cost concern, persist it
  onto the suggestion row (columns already exist: `reason`/`model`/tokens/`cost_usd`).
