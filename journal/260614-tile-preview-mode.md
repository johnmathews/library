# Per-user document tile preview mode

## Problem

Dashboard document tiles preview each document's first page. Pages are A4
(tall, narrow) but the tile thumbnail box is landscape `aspect-[4/3]` with
`object-contain`, so the page is letterboxed with wasted empty space to the
left and right of the preview.

## Decision

Add a per-user **tile preview** appearance setting (Settings → Appearance),
following the existing `background_tone` (page-canvas tone) pattern exactly —
enum stored in the user's JSONB `preferences`, resolved/persisted through the
same `PUT /api/settings/appearance` endpoint.

Two modes:

- `full_width` — **new default** — image fills the tile width, top-aligned,
  lower part of the page cropped (`object-cover object-top`). For an A4 page in
  a 4/3 box this shows roughly the top half.
- `whole_page` — the previous behavior: whole first page letterboxed
  (`object-contain`).

Key choices:

- **Box height unchanged.** Both modes keep `aspect-[4/3]`; only the image's
  object-fit/position changes, so grid row heights stay consistent.
- **Default flips to `full_width`** for everyone (including existing users) —
  unknown/absent values resolve to it. The user wanted the cropped look as the
  baseline; whole-page is opt-in.
- **Reused the one appearance endpoint** rather than adding a route. The PUT
  now carries both `background_tone` and `tile_preview`; `tile_preview` has a
  body default so older clients sending only the tone still succeed. The
  frontend `updateAppearance(tone, tilePreview)` gained a second arg, so each
  selector (`selectTone`, `selectTilePreview`) passes the other's current value.

## Touched layers

- Backend: `schemas.py` (`TilePreview` enum, default, resolver, `UserPreferences`
  field, `AppearancePreferences` field + tolerant validator), `api/settings.py`
  (persist both keys).
- Frontend: `api/settings.ts` (`TILE_PREVIEWS`, type, default, `updateAppearance`
  signature), `stores/auth.ts` (`tilePreview` computed), `views/SettingsView.vue`
  ("Document previews" fieldset + optimistic handler), `views/DocumentListView.vue`
  (`thumbnailFitClass` bound to the thumbnail `<img>`).
- Tests at every layer (pytest + vitest); docs `api.md` §1.10 and `frontend.md`.

Spec/plan: `docs/superpowers/specs/2026-06-14-tile-preview-mode-design.md`,
`docs/superpowers/plans/2026-06-14-tile-preview-mode.md`.
