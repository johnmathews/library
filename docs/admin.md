# Admin role & admin views

The library is a multi-user "named family accounts over one shared library"
(see [architecture.md](architecture.md) §1.5). On top of that, a single boolean
role — **admin** — gates a handful of cross-cutting operations and an
admin-only views surface. Everything below admin is an ordinary authenticated
user.

## 1.1 The role

`users.is_admin` (BOOLEAN, default `false`; migration `0014`). Surfaced on
`GET /api/auth/me` so the frontend can show/hide admin affordances. There is no
finer-grained RBAC — it is deliberately one bit.

Enforcement is the `require_admin` FastAPI dependency
(`src/library/auth/deps.py`). It layers on `current_user`, so:

- anonymous → **401**,
- authenticated non-admin → **403**,
- admin → allowed.

### 1.1.1 Making a user an admin

Admins are created from the host (there is no self-service signup):

```bash
# promote an existing user (this is how `john` becomes admin in production)
library user set-admin john
# revoke
library user set-admin john --revoke
# or create one directly
library user add alice --admin
```

`library user list` shows each account's role and active state.

## 1.2 What admin gates

### 1.2.1 Project mutations

Projects are a **global, shared** taxonomy, so changing them affects everyone.
`POST`/`PATCH`/`DELETE /api/projects*` require admin; `GET /api/projects*`
stays open to all authenticated users (they still filter and assign documents).
Ordinary per-user or recoverable actions (document soft-delete, notes, uploads,
settings, ask threads) remain open to all authenticated users — only globally
shared, destructive, or administrative operations are gated.

### 1.2.2 The admin API (`/api/admin/*`)

All under `require_admin`:

| Endpoint | Returns |
|---|---|
| `GET /api/admin/system` | app version + git sha, redacted operational config, deployment topology, live DB stats (documents by status, users, job-queue depth, total extraction spend) |
| `GET /api/admin/architecture` | `docs/architecture.md` + `docs/ingestion.md` as markdown (rendered client-side) |
| `GET /api/admin/coverage` | backend + frontend coverage % vs gate, plus per-file detail (file counts, lowest-covered files), from the CI-baked summary (§1.4) |
| `GET /api/admin/users` | every user (role + active state, no secrets) |
| `POST /api/admin/users` | create a user (optionally admin); also links a recipient (§1.2.4) |
| `PATCH /api/admin/users/{id}` | promote/demote and activate/deactivate |
| `DELETE /api/admin/users/{id}` | delete a user (guarded: not yourself, not the last admin) (§1.2.4) |
| `POST /api/admin/recipients` | create a recipient (dedupes case-insensitively) (§1.2.3) |
| `PATCH /api/admin/recipients/{id}` | rename a recipient, or merge it into another (§1.2.3) |
| `DELETE /api/admin/recipients/{id}` | delete a recipient, reassigning its documents first (§1.2.3) |
| `POST` / `PATCH` / `DELETE /api/admin/senders[/{id}]` | senders: create / rename-or-merge / reassign-then-delete — identical contract to recipients (§1.2.3) |
| `PATCH` / `DELETE /api/admin/kinds/{slug}` | kinds (slug-keyed): rename the display name only (no merge), or reassign-then-delete by slug (§1.2.3) |
| `GET /api/admin/currencies` | distinct currency codes in use, with document counts (§1.2.5) |
| `POST /api/admin/currencies/normalize` | rename a currency code across the whole store, series-aware (§1.2.5) |
| `GET /api/admin/fx-rates` | FX-rate seeding status per in-use currency (§1.2.6) |
| `POST /api/admin/fx-rates` | seed an FX rate (live fetch or manual entry) so conversion resolves (§1.2.6) |

The system config view only exposes a curated, secret-free subset of settings —
never API keys, passwords, or internal URLs. All reference-entity mutations
(senders/kinds/recipients) and currency normalisation serialise on transaction-scoped
advisory locks (kinds/senders/recipients share one; currency uses its own) so
concurrent admin edits can't interleave.

**Last-admin protection:** `PATCH` refuses (409) any change that would leave
zero active admins, so an admin cannot lock everyone out. Deactivating a user
also revokes their sessions and API tokens (same as `library user disable`).

### 1.2.3 Recipient (metadata) management

Recipients are a global, shared taxonomy, so curating them (fixing typos,
folding duplicates, removing dead entries) is admin-only. Two endpoints back the
**Metadata** admin tab (§1.3); the services live in `library/taxonomy.py` and own
their own transaction (a single commit per call). Document counts here exclude
soft-deleted documents (matching `GET /api/recipients`), but every reassignment
moves **all** rows — soft-deleted included — so nothing is left orphaned by the
`recipient_id` FK's `ON DELETE SET NULL`.

**Rename / merge** — `PATCH /api/admin/recipients/{id}` with `{name, merge?}`:

- The name is trimmed; a blank name is rejected (`400`).
- The collision check is **case-insensitive and excludes the recipient itself**,
  so a pure casing change (e.g. `john` → `John`) renames in place rather than
  reporting a self-collision.
- If the new name matches **another** recipient and `merge` is not set, the call
  returns `409` whose flat (top-level) body carries the target's `target_id`,
  `target_name`, and `target_document_count` so the UI can warn before merging.
- Re-sending with `merge=true` reassigns this recipient's documents onto the
  target and deletes this recipient (the target row is returned).

**Reassign-then-delete** — `DELETE /api/admin/recipients/{id}` with an optional
`reassign_to` query param that is **three-state**:

- **omitted** — a recipient with zero documents is deleted outright; an in-use
  recipient returns `409` (`document_count`) rather than silently nulling FKs.
- **`reassign_to=<id>`** — move this recipient's documents to that recipient,
  then delete. Targeting itself is `400`; an unknown target is `404`.
- **`reassign_to=`** (empty / `null`) — clear the recipient on its documents
  (set to NULL), then delete.

These mutations move the FK directly; they bypass per-document
`extra["user_edited_fields"]` locks (the recipient row is being deleted, so its
documents must move regardless). After any mutation the frontend refreshes the
shared taxonomy cache so other views' recipient dropdowns/filters update.

**Senders and kinds** get the same treatment from the same Metadata tab.
**Senders** (`POST/PATCH/DELETE /api/admin/senders`) are id-keyed and behave
exactly like recipients (create/dedupe, rename-or-merge, reassign-then-delete;
`Document.sender_id` is `ON DELETE SET NULL`). **Kinds**
(`PATCH/DELETE /api/admin/kinds/{slug}`) are keyed by their stable, unique
`slug`: rename edits the **display name only** (the slug never changes, and a
name collision with another kind is refused — there is no kind merge), and
delete reassigns documents to another kind **by slug**. Create for kinds stays on
the public `POST /api/kinds`. Deleting any reference row never deletes documents
— it only nulls their pointer.

### 1.2.4 Users ↔ recipients (auto-link, dual-name matching, delete)

Each user is paired with a **recipient** (the "who a document is addressed to"
lookup, §1.2.3) via `recipients.user_id` (nullable FK → `users.id`,
`ON DELETE SET NULL`, migration `0020`). This lets one recipient row stand in
for a user under either of their names.

- **Auto-create on user create.** `POST /api/admin/users` (and `library user
  add`) upsert a recipient named by the user's **display name** — falling back
  to the **username** when the display name is empty — and link it via
  `user_id`. If a recipient with that name already exists *unlinked*, it is
  adopted (its `user_id` is set) rather than duplicated.
- **Dual-name matching at ingestion.** When extraction resolves a document's
  recipient (`upsert_recipient`), a name matching a user's **username OR display
  name** (case-insensitive) resolves to that user's linked recipient (created or
  adopted as needed). So a document addressed to `john` and one addressed to
  `John Smith` both land on the same recipient when that user has username
  `john` / display name `John Smith`. Any name that matches no user upserts a
  plain recipient by case-insensitive name, exactly as before.

**Delete a user** — `DELETE /api/admin/users/{id}`:

- Deleting **yourself** is rejected (`400`) — an admin cannot remove their own
  account out from under their session.
- Deleting the **last active admin** is rejected (`409`), mirroring the
  last-admin `PATCH` guard, so the deployment can never be left without an
  admin. The guard is serialised by the same advisory lock as role changes.
- A user's linked recipient **survives** the delete — the `ON DELETE SET NULL`
  FK just unlinks it (`user_id` → NULL), so documents addressed to that person
  stay addressed. Deletion of the user row itself is irreversible (sessions and
  API tokens cascade away with it).

### 1.2.5 Currency normalisation (series-aware)

Currency is a free-text `CHAR(3)` code, not a reference table — but it is part
of **series identity** (a series is one `(sender, kind, currency)` group), so it
can't just be CRUDed. The Metadata tab's **Currencies** card lists the distinct
codes in use (`GET /api/admin/currencies`) and offers a **normalise** action
(`POST /api/admin/currencies/normalize`, `{from_code, to_code}`) that renames a
code everywhere at once, in one transaction under a dedicated advisory lock:

- **Plain rewrite** — `documents`, `authored_series`, and
  `authored_series_suggestions` (`signature_currency`).
- **Cache merge** — `series_insights` is a recomputable cache; a `from_code` row
  that would collide with an existing `to_code` bucket is dropped (the survivor
  is kept and regenerates on next indexing), the rest are moved.
- **Refuse on user data** — `series_membership_overrides` and
  `series_meta_overrides` hold user-authored pins/titles. If the rename would
  collide there, the whole operation is **refused** (`409`, listing the
  conflicts) and nothing changes — no user data is ever dropped.
- **`fx_rates` untouched** — FX rate rows are never merged across codes; if the
  target has no rate row the result flags `fx_rate_missing` so the admin knows FX
  conversion for it is unavailable until a rate is seeded.

Codes are validated as `^[A-Z]{3}$` (upper-cased first); a no-op rename (same
code) is a `400`. The admin UI shows a confirm step before running, and surfaces
the per-table result, the FX warning, or the conflict list. See
docs/api.md §1.18.6 for the exact response shapes.

### 1.2.6 FX rates (seeding conversion)

Cross-currency series convert through a stored USD rate (`fx_rates`, base = USD;
`rate_to_base` is the value of one unit in USD). A **single** row per currency is
enough — `library.fx` falls back to the nearest-date rate for any document. The
Currencies card's **FX rates** subsection (`GET /api/admin/fx-rates`) lists every
in-use code with its status: **base** (USD, always 1.0, never seeded), a seeded
**rate + as-of date** (with a *Refresh*), or **No rate** with two ways to seed
one (`POST /api/admin/fx-rates`, upsert on `(currency, as_of)`, today by default):

- **Fetch rate** (`source: "live"`) — pulls the current rate from a keyless
  provider (`open.er-api.com`, config `fx_api_url`/`fx_api_timeout_s`), inverting
  its USD→X rate to USD-per-unit.
- **Enter manually** (`source: "manual"`) — type the USD-per-unit rate. This is
  also the automatic fallback: if the live fetch fails or the provider doesn't
  list the code (a `502`), the manual form opens with the error.

USD is refused (it is the implicit base). See docs/api.md §1.18.7 for shapes.

## 1.3 The admin views (frontend)

A single `/admin` route (`AdminView.vue`), reachable from an admin-only sidebar
link, with five tabs backed by the endpoints above: **System**, **Architecture**
(markdown → sanitised HTML via the shared marked + DOMPurify pipeline),
**Coverage**, **Users** (promote/demote/activate + create + delete, with inline
last-admin error handling; each row offers a two-step **Delete** confirm except
your own, which shows "You" instead), and **Metadata** (recipient management, §1.2.3) —
each recipient row has an inline **Rename** (revealing a merge prompt on a `409`
collision) and an inline **Delete** (with a reassign-target picker for in-use
recipients). The router's `authGuard` redirects non-admins away from
`meta.adminOnly` routes, so the page is unreachable without the role even by deep
link.

## 1.4 Test-coverage pipeline

Coverage figures only exist as CI artifacts, not at runtime, so CI bakes them
into the image:

1. The backend and frontend jobs produce `coverage.json` and
   `frontend/coverage/coverage-summary.json`.
2. The image build job runs `scripts/coverage_summary.py` to merge them into a
   single `coverage-summary.json`, written into the build context. Each side
   (`backend`, `frontend`) carries its headline `pct` and `threshold` plus
   per-file detail: `files_total`, `files_below_gate`, and `worst_files` (the
   up-to-`MAX_WORST_FILES` lowest-covered files, ascending, each `{path, pct}`).
   The summary also carries `test_types` — the four kinds of test the CI
   pipeline runs (`backend`/`frontend` unit suites with line coverage, plus
   `e2e` (Playwright) and `compose-smoke`, which are pass/fail gates with no line
   coverage; `has_coverage` distinguishes them). The top level also has
   `generated_at` and `git_sha`. Older summaries without `test_types` or per-file
   detail still validate (those fields default to empty/null).
3. The `Dockerfile` `COPY`s it to `/app/coverage-summary.json` (the default
   `LIBRARY_COVERAGE_SUMMARY_PATH`) and sets `LIBRARY_GIT_SHA` from a build arg.
4. `GET /api/admin/coverage` reads that file; when absent (local dev) it reports
   `available: false`. The Coverage view renders one card per CI **test type**
   (in pipeline order): the two unit suites show the headline %, a gate
   **Pass / Below gate** badge, file counts and the lowest-covered files; `e2e`
   and `compose-smoke` show what they exercise and a **CI gate** badge (no line
   coverage). A footer records when/which build produced the numbers.

## 1.5 Configuration

| Setting (env `LIBRARY_*`) | Default | Purpose |
|---|---|---|
| `git_sha` | unset | build commit, shown in the System view (set by the image build) |
| `coverage_summary_path` | `coverage-summary.json` | where `GET /api/admin/coverage` reads from |
| `docs_dir` | `docs` | where the Architecture view reads markdown from |
