# Configurable Phone Columns + Labeled Tile Date

**Date:** 2026-07-20
**Status:** approved (design)

## Problem

On phones the dashboard tile grid is hard-locked to a single column, and tile
metadata renders the primary document date bare — a screenshot shows a naked
"20 July 2021" with no indication of which date it is. Two changes:

1. Make the phone column count configurable (Settings → Appearance,
   account-synced), defaulting to **2** columns.
2. Label the primary document date on tiles so metadata is always key: value
   (essential for dates, where the type must be clear).

## Part A — Configurable phone columns

### Behaviour
- New account-synced appearance preference `phone_columns`, one of `{1, 2, 3}`,
  default **2**. Unknown / out-of-range values resolve to 2.
- Applies to the phone band only (`< 641px`). The tablet band (641–768px) stays
  fixed at 2 columns; the desktop `--doc-grid-cols` control is unchanged.
- The default flips from 1 → 2 for every existing user without the key set.
  This is intended.

### Backend — `src/library/schemas.py`, `src/library/api/settings.py`
Preferences are stored as a JSON blob on `user.preferences`, so **no DB
migration** is required — the key rides along with the existing appearance PUT.

- `schemas.py`:
  - Add `DEFAULT_PHONE_COLUMNS = 2` and `_ALLOWED_PHONE_COLUMNS = {1, 2, 3}`.
  - Add `_resolve_phone_columns(blob)` mirroring `_resolve_tile_preview`: read
    `blob.get("phone_columns")`, coerce to int, clamp to the allowed set, else
    default 2.
  - `AppearancePreferences.phone_columns: int = DEFAULT_PHONE_COLUMNS` with a
    `mode="before"` field-validator that maps unknown/out-of-range → default.
  - `UserPreferences.phone_columns: int`, populated in `resolve_preferences`
    via `_resolve_phone_columns(blob)`.
- `api/settings.py` `put_appearance`: add
  `"phone_columns": payload.phone_columns` to the persisted preferences blob.

### Frontend
- `api/settings.ts`: add `phone_columns` to the `UserPreferences` /
  `AppearanceUpdate` types; add a `phoneColumns` parameter to `updateAppearance`
  and include `phone_columns` in the request body.
- `stores/auth.ts`: `phoneColumns` computed (default 2), sourced from
  preferences like `tilePreview`.
- `views/SettingsView.vue` Appearance tab: a small segmented 1 / 2 / 3 control,
  optimistic update mirroring the tile-preview control. Thread `phoneColumns`
  through all three `updateAppearance(...)` call sites (tone, tile preview,
  dock position) so each preserves the current value.
- `views/DocumentListView.vue`: bind a `--doc-grid-cols-phone` CSS var on the
  `.app-doc-grid` element from `auth.phoneColumns`.
- `assets/utility-patterns.css`: the base `.app-doc-grid` rule (phone band,
  `< 641px`) becomes
  `grid-template-columns: repeat(var(--doc-grid-cols-phone, 2), minmax(0, 1fr));`
  (was a hard `1fr`). Tablet and desktop bands unchanged. Update the
  "phone/tablet bands deliberately ignore the override" comment to reflect that
  the phone band now honours a phone-specific var.

The desktop toolbar column select stays `hidden lg:flex` and per-machine
(localStorage) — untouched. The phone control is a distinct, account-synced
setting.

## Part B — Labeled document date (key: value)

`views/DocumentListView.vue` — the `field === 'date'` branch renders
`item.document_date` bare. Give it the same muted-prefix treatment the secondary
dates (`TILE_DATE_FIELDS`: Added / Due / Expires / Edited) already use:

```
<span class="text-gray-400 dark:text-gray-500">Date</span> 20 July 2021
```

- Muted "Date" prefix, no colon — visually consistent with the existing
  secondary-date prefixes.
- **Only** the document date changes. Amount stays bare (the currency symbol
  self-identifies it); sender stays a plain name.
- The preview-box metadata facsimile (`metadataRows`, already `{ label: 'Date',
  ... }`) is unaffected.

## Testing
- **Backend** (`tests/` settings API): `put_appearance` accepts a valid
  `phone_columns`; out-of-range clamps to 2; a missing key resolves to 2.
- **Frontend unit**:
  - auth-store `phoneColumns` default + resolution.
  - `updateAppearance` request body includes `phone_columns`.
  - `SettingsView` control persists the choice.
  - `DocumentListView` renders the `Date` prefix on the document date and
    applies `--doc-grid-cols-phone` from the store.
- Review existing mobile e2e specs for any hard "1 column" assumption; update if
  present.

## Docs & journal
- Update `docs/frontend.md` (dashboard tile fields + appearance settings) and
  the settings/preferences doc.
- New `journal/260720-*.md` entry.

## Non-goals
- No change to the desktop column control or tablet band.
- No labels added to amount or sender.
- No DB migration.
