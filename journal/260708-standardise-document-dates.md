# Standardise document date names + full field parity

Made the five document dates named and selectable consistently across every
surface, after an audit found two inconsistencies.

## Audit findings

The `Document` model has **five** user-facing dates (the user's mental model had
four — it missed `updated_at`):

| Field | Label | Nullable |
|---|---|---|
| `document_date` | Document date | yes |
| `due_date` | Due date | yes |
| `expiry_date` | Expiry date | yes |
| `created_at` | Added date | no |
| `updated_at` | Last edited | no |

Inconsistencies:
1. `created_at` was labelled **"Ingested"** in the detail hero but **"Added
   date"** in the sort control — same field, two names.
2. **Edit-layout** (detail hero) already distinguished all five dates, but
   **edit-fields** (dashboard tiles) collapsed them into a single generic
   "Date" and couldn't add due/expiry/added/last-edited.

## Changes (decisions: "Added date" everywhere; Last edited is a 5th date; full backend+frontend parity)

- **Rename** `created_at`'s hero label "Ingested" → "Added date"
  (`useDocumentLayout.ts`), matching the sort control and tile picker. (The
  History-timeline "Ingested" milestone is a different concept — the ingest
  event — and was left alone.)
- **Dashboard tile fields** now mirror the hero's five dates:
  - Backend `DashboardField` enum gains `due_date`, `expiry_date`, `added_date`,
    `last_edited` (the legacy `date` value is kept = document date, for
    back-compat with saved prefs; the tolerant validator drops nothing).
  - `DocumentListItem` schema + `_list_item_fields` now return `due_date`,
    `expiry_date`, `updated_at` (moved up from `DocumentDetail`, which inherits
    them) so tiles can render any date.
  - Frontend `DASHBOARD_FIELDS` catalog gains the four fields (relabelled the
    old "Date" → "Document date"); the tile renders each extra date with a short
    muted prefix (Due / Expires / Added / Edited) so several dates stay
    unambiguous. `created_at`/`updated_at` are datetimes → sliced to the date
    portion before `formatDate`.

## Verification

Backend full suite 1035 passed; frontend 892 passed; `vue-tsc`, `eslint`, `ruff
check`/`format` clean. Updated `docs/frontend.md` and `docs/api.md` (list
response now documents all five dates; dashboard-field catalog table; detail
section trimmed). No backend default-sort change — the API default stays
`document_date` (shared with MCP); only labels/fields changed.
