# Per-user document-type tile colours

**Date:** 2026-07-03

## 1. What shipped

Users can now colour the homepage document tiles' **border** by document kind,
editable in Settings → Appearance → "Document type colours": a native colour
picker per kind, one-click suggested swatches, a per-kind **Default** reset, and
a global **Reset all**. Colours are per-user; unset kinds fall back to a
built-in palette (5 common kinds coloured, the rest neutral).

## 2. How the request evolved

Started as "colour the tile borders by document type" with a fixed palette.
Mid-design the ask expanded to a **per-user, editable** picker with suggestions
and a reset. The original palette work wasn't wasted — it became the built-in
**default** (the reset target and suggested swatches).

## 3. Key decisions

### 3.1 Data-driven palette, and neutral "other"

Pulled live kind frequencies from prod (`library-db`): of 121 docs, `other` is
48% and `invoice` 39%; only `letter`/`receipt`/`warranty` (5 each) and
`contract` (1) otherwise occur; 9 seeded kinds are empty. The decisive call:
**`other` stays neutral** — colouring the 48% catch-all would flood the page and
drown the signal. The five meaningful kinds get distinct hues
(invoice→sky, receipt→green, letter→violet, warranty→amber, contract→red),
validated colourblind-safe via the dataviz validator (worst adjacent ΔE ≈ 24–38,
well above the ≥12 target).

### 3.2 Sparse hex overrides in the existing JSONB blob

Stored as `kind_colors: {slug: "#rrggbb"}` inside the existing
`users.preferences` JSONB — **no migration** (prefs are schemaless). Only
overrides are stored; absence = built-in default; `{}` = reset all.

### 3.3 Hex, departing from the `background_tone` token precedent

The codebase deliberately stores *named tokens* (frontend owns the hex) for
`background_tone`. A colour **picker** needs arbitrary colour, so this is the
first hex storage/validation in the project. Kept the spirit by having the
**defaults** live frontend-side (`DEFAULT_KIND_COLORS`) so the built-in palette
still retunes without a data migration; the backend resolver is tolerant (drops
non-`#rrggbb`, caps at 64), matching `dashboard_fields`.

### 3.4 Rendering

Coloured tiles get `.app-doc-card--accented` + a `--card-accent` CSS var;
neutral tiles are untouched (keep the gray base + violet hover). A single stored
hex is adapted per surface with `color-mix` (darkened on light, lightened for
the near-black dark surface) and shifts on hover.

## 4. Gotcha

Adding a mount-time `GET /api/kinds` to `SettingsView` broke 5 existing tests:
they stub `fetch` with a single-use `Response`, and the mount read consumed the
body before the test's own interaction could read it. Fixed by routing
`/api/kinds` to its own fresh response in the spec's fetch stub.

## 5. Verification

Backend 867 passed + ruff clean; frontend 669 unit passed + type-check + lint
clean. New tests: schema resolver tolerance, the `PUT /api/settings/kind-colors`
endpoint, the `kindColor` resolver, tile accent rendering (default / neutral /
override), and the settings section (list order, save, per-kind + global reset).
