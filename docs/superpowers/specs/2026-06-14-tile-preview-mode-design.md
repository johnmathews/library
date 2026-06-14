# Document tile preview mode — design

**Date:** 2026-06-14
**Status:** shipped 2026-06-14 (see `journal/260614-tile-preview-mode.md` and
`journal/260614-dashboard-mobile-ux-polish.md`).

## Problem

Dashboard document tiles render the first page of each document. Pages are A4
(tall and narrow), and the current thumbnail box is landscape `aspect-[4/3]`
with `object-contain`, so the page is letterboxed with wasted empty space to the
left and right of the preview.

## Goal

Let each user choose, in **Settings → Appearance**, how the first-page preview
fills its tile:

- **Full width** (new default) — the image fills the full width of the tile,
  top-aligned, with the lower part of the page cropped off (`object-cover` +
  `object-top`). For an A4 page in a 4/3 box this shows roughly the top half.
- **Whole page** — the current behavior: the entire first page is shown,
  letterboxed inside the box (`object-contain`).

The thumbnail box keeps its existing `aspect-[4/3]` dimensions in **both** modes,
so tile and grid row heights stay consistent. Only the image's object-fit /
position changes.

## Approach

Add a new enum-style per-user appearance preference `tile_preview`, following the
exact pattern established by the existing `background_tone` (page-canvas tone)
setting. The preference is stored in the user's JSONB `preferences` column and
flows backend → API → store → Settings UI, and is consumed directly on the tile
image (no global `data-*` attribute or shared CSS needed, since the effect is
scoped to the dashboard tiles).

### Values

| value         | meaning                          | image classes                |
| ------------- | -------------------------------- | ---------------------------- |
| `full_width`  | fill tile width, crop bottom     | `object-cover object-top`    |
| `whole_page`  | show whole page, letterboxed     | `object-contain`             |

**Default:** `full_width`. Users who have never set the preference (including all
existing users on upgrade) see full-width crop. Unknown/missing values resolve to
the default.

## Changes by layer

### 1. Backend schemas — `src/library/schemas.py`

- Add `TilePreview(StrEnum)` with `FULL_WIDTH = "full_width"` and
  `WHOLE_PAGE = "whole_page"`.
- Add `DEFAULT_TILE_PREVIEW: Final[TilePreview] = TilePreview.FULL_WIDTH`.
- Add `_resolve_tile_preview(blob)` with the same fallback shape as
  `_resolve_background_tone`.
- Add `tile_preview: TilePreview` to the `UserPreferences` response model and
  populate it in `resolve_preferences()`.

### 2. Backend API — `src/library/api/settings.py`

- Extend the `AppearancePreferences` request model to carry both
  `background_tone` and `tile_preview` (with a before-validator that falls back
  to the default on unknown values, mirroring the tone field).
- In `PUT /settings/appearance`, merge both keys into `user.preferences`:
  ```python
  user.preferences = {
      **(user.preferences or {}),
      "background_tone": payload.background_tone.value,
      "tile_preview": payload.tile_preview.value,
  }
  ```
- `GET /settings` already returns the full `UserPreferences`, so no change beyond
  the schema addition.

Reusing the single `/settings/appearance` endpoint (rather than adding a new
route) keeps both Appearance-tab settings on one save path.

### 3. Frontend settings API — `frontend/src/api/settings.ts`

- Export `TILE_PREVIEWS` options array: `{ value, text }` pairs for
  `full_width` ("Full width") and `whole_page` ("Whole page").
- Export `type TilePreview = (typeof TILE_PREVIEWS)[number]['value']` and
  `DEFAULT_TILE_PREVIEW: TilePreview = 'full_width'`.
- Add `tile_preview?: TilePreview` to the `UserPreferences` interface.
- Update `updateAppearance()` to send both fields:
  ```ts
  export function updateAppearance(
    tone: BackgroundTone,
    tilePreview: TilePreview,
  ): Promise<UserPreferences> {
    return apiFetch<UserPreferences>('/api/settings/appearance', {
      method: 'PUT',
      body: { background_tone: tone, tile_preview: tilePreview },
    })
  }
  ```

### 4. Frontend store — `frontend/src/stores/auth.ts`

- Add `tilePreview` computed:
  ```ts
  const tilePreview = computed<TilePreview>(
    () => user.value?.preferences?.tile_preview ?? DEFAULT_TILE_PREVIEW,
  )
  ```
- Expose it from the store return.

### 5. Settings UI — `frontend/src/views/SettingsView.vue`

- Add a second `<fieldset>` in the Appearance tab (`tab === 'appearance'`),
  below the page-background swatches, titled e.g. "Document previews".
- Render a two-option radio group from `TILE_PREVIEWS` (reuse the existing
  `role="radiogroup"` / `role="radio"` markup pattern).
- Add `selectedTilePreview` ref initialised from `auth.tilePreview` and a
  `selectTilePreview(value)` handler that mirrors `selectTone`:
  optimistic `auth.applyPreferences(...)`, call `updateAppearance(tone, value)`,
  reconcile from the response, roll back + show an error message on failure.
- Because `updateAppearance` now takes both fields, `selectTone` and
  `selectTilePreview` each pass the current value of the other
  (`selectedTone.value` / `selectedTilePreview.value`).

### 6. Consumption — `frontend/src/views/DocumentListView.vue`

- Import the auth store (if not already) and read `auth.tilePreview`.
- Bind the thumbnail `<img>` fit classes reactively:
  - `full_width` → `object-cover object-top`
  - `whole_page` → `object-contain`
- Keep the container `aspect-[4/3]` and the `<img>` `w-full` unchanged. The
  fallback `<span>` (no thumbnail) is unaffected.

## Testing

**Backend** (`pytest`):

- `tile_preview` round-trips through `PUT /settings/appearance` and `GET /settings`.
- Missing `tile_preview` resolves to `full_width` (default / backward-compat).
- Unknown `tile_preview` string resolves to the default.
- Setting `tile_preview` does not clobber `background_tone` and vice versa.

**Frontend** (existing component/unit test setup):

- Selecting "Whole page" in the Appearance tab calls `updateAppearance` with
  `whole_page` and updates the store; failure rolls back and surfaces an error.
- `DocumentListView` applies `object-cover object-top` when `tilePreview` is
  `full_width` and `object-contain` when `whole_page`.

## Out of scope

- Per-document override of the preview mode.
- Changing tile aspect ratio, grid columns, or thumbnail generation.
- Any change to how thumbnails are produced on ingestion.

## Docs to update on implementation

- `docs/frontend.md` — note the new Appearance setting and tile preview modes.
- `docs/api.md` — `tile_preview` field on `UserPreferences` / `/settings/appearance`.
- A `/journal` entry per the project journal convention.
