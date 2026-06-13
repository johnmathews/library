# Dashboard metadata preferences

**Date:** 2026-06-13. Per-user control over which metadata fields appear on
the dashboard tiles.

Spec: `docs/superpowers/specs/2026-06-13-dashboard-metadata-preferences-design.md`
Plan: `docs/superpowers/plans/2026-06-13-dashboard-metadata-preferences.md`

## What landed

End-to-end feature in 9 tasks before this journal entry:

1. **Database** — `preferences JSONB` column on the `users` table (Alembic
   migration). One column holds all future per-user settings; no separate
   table.
2. **Schemas** — `DashboardField` (8-value `StrEnum`), `DEFAULT_DASHBOARD_FIELDS`
   (the 6-field default), `DashboardPreferences` (tolerant validator: drops
   unknown keys, deduplicates, preserves order), `resolve_dashboard_preferences`
   (absent key → defaults; explicit empty list → empty), and `UserOut` now
   includes `preferences`.
3. **Settings API** — `GET /api/settings` returns the resolved preference set;
   `PUT /api/settings` persists and returns the cleaned set. Both live in
   `src/library/api/settings.py`.
4. **Auth/me** — login response and `GET /api/auth/me` now include
   `preferences`, so the frontend is hydrated on sign-in without a second
   round-trip.
5. **List amount/currency** — `amount_total` and `currency` promoted from
   detail-only fields to `DocumentListItem`, so the tile can display financial
   totals without fetching the full document.
6. **FE client** — `src/api/settings.ts`: `DASHBOARD_FIELDS` (canonical
   ordered list, single source of truth for checkboxes and tile order),
   `getSettings`, `updateSettings`.
7. **Auth store** — `dashboardFields` computed + `applyPreferences`
   (`src/stores/auth.ts`). Hydrated from `GET /api/auth/me`; updated on save
   so the dashboard reflects changes without a reload.
8. **Settings page** — `/settings` (`SettingsView.vue`): GOV.UK "select all
   that apply" checkboxes, save → `PUT /api/settings` → success banner;
   error → `GovErrorSummary`. "Settings" link added to the service navigation
   (order: Documents · Search · Upload · Settings).
9. **Tile rendering** — `DocumentListView` checks `shows(field)` against
   `auth.dashboardFields` for each of the 8 fields, rendering in fixed
   canonical order. Tags capped at 4 chips + `+N` overflow span.

## Decisions

**JSONB column vs. a separate preferences table.** A separate table would be
over-engineered for what is likely to stay a handful of keys per user. JSONB
on `users` is YAGNI-correct and keeps the schema flat. If preferences grow
beyond a single JSONB column the migration path is straightforward.

**Tolerant validator (never 422).** `DashboardPreferences._clean` silently
drops unknown/garbage values and deduplicates. The alternative — strict 422
on unknown keys — would break the dashboard if a field was renamed in a
deploy or if a user hand-edited the database. The tolerant approach means the
worst case is "that preference key is ignored", not "dashboard is broken".

**Absent key vs. explicit empty list.** Two distinct states are meaningful:
- `preferences` blob has no `dashboard_fields` key → user has never saved;
  return the defaults (so a fresh account gets a sensible tile out of the box).
- `dashboard_fields` key is present but `[]` → user explicitly cleared all
  fields; honour it (tiles show only title + thumbnail).
`resolve_dashboard_preferences` encodes this: the key's *presence*, not just
its value, determines which branch runs.

**Default set = today's tile + tags.** `kind`, `sender`, `tags`, `date`,
`language`, `status` — the fields that were hard-coded on the original tile —
were kept as the default. `amount` and `file_type` are off by default because
they are null/redundant on the majority of documents in the target collection.

**`DASHBOARD_FIELDS` as the single FE source of truth for the Settings page.**
The canonical array is declared once in `src/api/settings.ts` and drives the
checkbox list in `SettingsView` (field keys, display labels, and checkbox
order). `DocumentListView` imports only the `DashboardField` *type* from that
module — not the array. The tile render order is a separate fixed sequence
hardcoded in `DocumentListView`'s template (kind → language → status →
file_type, then sender → date → amount, then tags). The user's saved set
governs *which* fields appear; the template order governs *how* they appear,
so two users with the same selection see tiles in the same layout.

## Surface now visible for the first time

This feature surfaces the metadata the importer has been carrying since the
paperless-ngx migration — including paperless storage-path tags like `family`
and `atlas-consulting-expenses` (added to the importer 2026-06-12) — on the
dashboard tiles. Previously those tags existed in the database but were only
visible on the detail page; now they appear on tiles if the user enables the
`tags` field.

## GOV.UK pattern notes

The **settings page** (checkboxes, save button, success banner, error
summary) is squarely on-pattern: `GovCheckboxes`, `GovButton`,
`GovNotificationBanner`, and `GovErrorSummary` are all documented GOV.UK
components.

The **tile/card grid** (`app-doc-grid` / `app-doc-card`, §1.2.6 of
`docs/frontend.md`) is a bespoke extension — GOV.UK has no card component.
The new per-field rendering keeps it restrained by using only GOV.UK
primitives (`GovTag`, `govuk-body-s`, govuk colours and spacing) and never
inventing new visual patterns.
