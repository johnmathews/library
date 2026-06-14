# 1. Per-user page-canvas tone

**Date:** 2026-06-14

## 1.1 Why

After elevating the dashboard tiles ([260614-dashboard-tile-elevation](260614-dashboard-tile-elevation.md)),
white tiles still sat on a `gray-100` page — only a ~3% tonal step, so the
*surfaces* barely separated even with the new shadow. White can't get lighter,
so the lever is the page canvas. Rather than hard-code a darker tone, made it a
**per-user preference** (default `gray-200`) — fast iteration, and a natural fit
since the app is already multi-user with a JSONB `preferences` blob.

## 1.2 Design

Cloned the existing `dashboard_fields` preference pattern end-to-end. No
migration: `background_tone` is a new key in the same `users.preferences` JSONB.

- **Backend** (`schemas.py`, `api/settings.py`, `api/auth.py`): `BackgroundTone`
  enum (`neutral` default, + `light`/`soft`/`slate`/`sand`/`mist`),
  `DEFAULT_BACKGROUND_TONE`, tolerant resolution (unknown → default, never 422).
  New `UserPreferences` read model (fields + tone) returned by `GET /api/settings`
  and embedded in `UserOut`. New `PUT /api/settings/appearance` writes the tone;
  the existing `PUT /api/settings` still writes fields. Each write merges into the
  blob, preserving the sibling key, so the two Settings tabs save independently.
  `resolve_dashboard_preferences` → `resolve_preferences` (auth `_user_out`).
- **Frontend**: `BACKGROUND_TONES` (value/label/swatch hex) in `api/settings.ts`;
  `backgroundTone` getter on the auth store; `App.vue` watches it and sets
  `<html data-canvas="…">` (immediate, so first paint + reactive repaint on
  change). `main.css` maps `:root[data-canvas='…']` → `--app-canvas` → the body
  background; default `gray-200`, dark mode unchanged. `SettingsView` is now
  tabbed (Dashboard | Appearance); the Appearance tab's swatches apply
  optimistically (instant repaint) and auto-save, reverting on failure.

The token is a name, not a colour — the frontend owns the hex per tone, so the
palette is retunable without touching the backend.

## 1.3 Note: already multi-user

The request mentioned making the app multi-user; it already is — `users` table,
per-user sessions, and per-user JSONB `preferences`. No auth/identity work was
needed; this just adds another per-user preference.

## 1.4 Verification

- Backend: full `pytest` 295/295 (added schema tests for tone resolution/coercion
  and API tests for the appearance round-trip, independence from fields, and auth);
  `ruff check` clean.
- Frontend: full `vitest` 172/172 (added Settings tab-switch, tone-select-saves-
  and-applies, and revert-on-failure tests); `type-check`, `lint`, `build` green.
- Live verification of the Settings → Appearance flow done post-deploy.

## 1.5 Docs

`docs/api.md` §1.10 (settings endpoints incl. new §1.10.3 appearance) and
`docs/frontend.md` (main.css canvas tokens + tabbed SettingsView row).
