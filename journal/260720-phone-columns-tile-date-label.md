# Phone-column dashboard preference + labeled tile date

Date: 2026-07-20

## 1. Request

Two small dashboard polish items:

- A phone-width tile-density control: on screens under 641px the dashboard
  grid was hardcoded to a single column, with no way to see more tiles at
  once on a phone.
- The document-date tag on a dashboard tile rendered as a bare value (just
  `12 Mar 2026`), while every other tile date (Added, Due, Expires, Edited)
  already carried a short muted label. The plain date was the odd one out —
  metadata elsewhere on the tile reads as key: value, and the date silently
  didn't.

## 2. Decisions

- **New preference: `phone_columns`.** Values 1/2/3, default **2**. Lives in
  **Settings → Appearance → Phone columns**, next to the existing dock-position
  and document-type-colour controls — this is account appearance, not a
  dashboard field, so it belongs with the other Appearance-tab controls rather
  than the Dashboard tab's field picker.
- **Server-synced, not per-machine.** The existing desktop "tiles per row"
  control is a `localStorage`-only preference (`library:doc-grid-cols`) because
  it's genuinely a per-monitor layout choice. Phone columns is different: a
  phone is usually a single device per person, and we want the choice to
  follow the account (so switching phones, or reinstalling the PWA, doesn't
  silently reset it). Stored as `phone_columns` on the existing `user.preferences`
  JSON blob and read through `auth.phoneColumns`, following the same
  `updateAppearance` PATCH path as dock position and tile preview.
- **No DB migration.** `preferences` is already a JSON column; adding a new
  key is a backend/frontend-only change (clamp to 1/2/3, default 2 when
  absent). Confirmed there's nothing to run beyond the usual test suite.
- **Default flips 1 → 2 for existing users.** Before this preference existed,
  phone width effectively rendered 1 column (a fixed rule with no override).
  Any existing user opening the dashboard on a phone today, with no stored
  preference, will now see 2 columns instead of 1 — a deliberate, one-time
  visual change on top of the DB-migration-free implementation, not a
  regression to work around.
- **Grid var scoping.** Phone columns drives a new `--doc-grid-cols-phone` CSS
  var, distinct from the existing `--doc-grid-cols` (desktop/wide) var. The
  tablet band stays fixed at 2 columns — it wasn't part of the request, and
  giving it its own knob would be scope creep for a phone-specific ask.
- **Date label: muted prefix, no colon, date-only.** The primary document date
  now renders `Date <value>` with the label in the same
  `text-gray-400 dark:text-gray-500` "muted prefix" style already used for
  Added/Due/Expires/Edited, matching their no-colon convention. Amount and
  Sender were deliberately left alone — an amount already self-identifies via
  its currency symbol, and a sender reads fine as a plain name — so this was a
  narrow, single-field fix rather than a general "label everything" pass.

## 3. Verification

- Backend: `pytest tests/test_settings_api.py` covers the `phone_columns`
  clamp/default/round-trip through `PUT /api/settings/appearance`.
- Frontend: store defaulting/resolution (`auth.spec.ts`), the
  `updateAppearance` request body (`settings.spec.ts`), the Settings-view
  radio-group persistence (`SettingsView.spec.ts`), and the dashboard's
  `--doc-grid-cols-phone` var + `Date` label rendering
  (`DocumentListView.spec.ts`) are all covered by tests alongside the code.
- Full backend + frontend suites, `ruff format --check`, `ruff check`, and
  `vue-tsc --noEmit` were run clean as part of closing out this change (see
  the accompanying task report for exact output).
