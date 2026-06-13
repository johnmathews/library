# Per-user dashboard metadata preferences

**Date:** 2026-06-13. **Status:** approved design, pre-implementation.

## Goal

Let each user choose which document metadata fields appear on the dashboard
tiles (`DocumentListView.vue`, route `/`). Preferences are per-user,
server-persisted (follow the user across devices), and edited on a new
in-app **Settings** page. This is the first per-user preference in the app.

## Selectable fields

Eight fields are toggleable; **title and thumbnail are always shown**.

| Key | Tile rendering | Source today |
| --- | --- | --- |
| `kind` | `GovTag` blue (e.g. *Invoice*) | list API |
| `sender` | secondary text (correspondent) | list API |
| `tags` | `GovTag` grey chips, `+N` overflow | list API (fetched, not yet rendered) |
| `date` | `document_date`, formatted | list API |
| `language` | `GovTag` grey (hidden when `unknown`) | list API |
| `status` | `GovTag` red/yellow (non-`indexed` only) | list API |
| `amount` | `amount_total` + `currency` | **detail only — must be added to list API** |
| `file_type` | PDF / Image / Text / File | derived from `mime_type` (list API) |

**Default set** (new user, or `dashboard_fields` key absent):
`{kind, sender, tags, date, language, status}` — today's tile plus tags.

Field render order on the tile is fixed and canonical (tags row: kind,
language, status, file_type; then sender; then date; then amount; then tag
chips). Not user-reorderable (YAGNI).

## Storage (decision: Option A)

Add a single JSONB column to `users`:

```
preferences JSONB NOT NULL DEFAULT '{}'
```

Shape: `{"dashboard_fields": ["kind", "sender", "tags", "date", "language", "status"]}`.

Rationale: settings are small, single-owner, and read as one blob; one
migration, no new table, extensible to future preferences. Rejected: a
`user_preferences` table (overkill — YAGNI) and `localStorage` (fails the
cross-device, in-settings requirement).

## Backend

- **Migration** `0003_user_preferences`: add the `preferences` column
  (`server_default='{}'`, not null).
- **Schema** `DashboardPreferences` (Pydantic) in `schemas.py`:
  - `dashboard_fields: list[FieldKey]` where `FieldKey` is a `StrEnum` of the
    eight keys.
  - A validator **drops unknown keys** and **de-duplicates** (so a future
    field rename or hand-edited row can't 500 the dashboard); an absent or
    `{}` preferences blob resolves to the default set.
  - Resolution helper `resolve_dashboard_fields(user) -> list[FieldKey]`.
- **Endpoints** (`src/library/api/auth.py`, or a new `settings.py` router
  mounted alongside — implementer's call):
  - `GET /api/settings` → `{ "dashboard_fields": [...] }`, defaults filled.
  - `PUT /api/settings` → validate body, persist, return resolved prefs.
- **`GET /api/auth/me`**: extend `UserOut` with `preferences: DashboardPreferences`
  so the dashboard has prefs on first paint (no extra round-trip). `_user_out`
  (`auth.py:38`) populates it from the resolver.
- **List API**: add `amount_total: Decimal | None` and `currency: str | None`
  to `DocumentListItem` (`schemas.py:54`) and the list serializer in
  `api/documents.py`. Decimal serialises to a JSON string (existing
  convention, see `schemas.py` header).

## Frontend

- **Route** `/settings` (`router/index.ts`) + a **Settings** link in the
  navbar (`GovServiceNavigation`, in `App.vue`).
- **`SettingsView.vue`**: one `GovCheckboxes` group "Dashboard tile fields"
  ("Select all that apply"), `GovButton` "Save changes", success via
  `GovNotificationBanner`. Save failure → inline `GovErrorMessage`, prefs
  left unchanged.
- **Preferences store** (Pinia): holds `dashboardFields`, hydrated from
  `/auth/me` on startup (extend `stores/auth.ts` or a new `preferences.ts`),
  updated on save. API helpers in a new `api/settings.ts`.
- **`DocumentListView.vue`**: render each metadata field conditionally on the
  store's set, in the fixed order above. Add tag chips (reuse `GovTag` grey,
  cap visible chips with a `+N` indicator), amount, and file type. Existing
  per-field guards (language `!== unknown`, status `!== indexed`) still apply
  *within* a toggled-on field.
- **`api/documents.ts`**: add `amount_total`/`currency` to `DocumentListItem`.

## Error handling

- Invalid/corrupt stored preferences → server coerces to defaults, never 500.
- `PUT` with unknown keys → silently dropped by the validator (200, returns
  the cleaned set), so the client always learns the effective state.
- Save network/HTTP failure → inline error, store unchanged.

## Testing

- **Backend**: `DashboardPreferences` validation (unknown keys dropped, dedupe,
  default fill); `GET`/`PUT /api/settings` round-trip; `/auth/me` includes
  resolved prefs; list API includes `amount_total`/`currency`.
- **Frontend**: settings save flow (check/uncheck → save → banner); dashboard
  renders exactly the toggled-on fields; tag overflow `+N`.
- **e2e** (Playwright): set a preference in Settings → return to dashboard →
  tile reflects the change.

## Docs to update

- `docs/api.md`: `/api/settings`, `/auth/me` preferences field, new list-item
  fields.
- `docs/frontend.md`: Settings view + preferences store.
- Journal entry under `journal/`.

## Out of scope (YAGNI)

Per-field ordering / drag-reorder, separate per-view configs (detail, search),
column-density controls. Tile-field selection only.
