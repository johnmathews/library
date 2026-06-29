# 1. Admin role & admin views

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
| `POST /api/admin/users` | create a user (optionally admin) |
| `PATCH /api/admin/users/{id}` | promote/demote and activate/deactivate |

The system config view only exposes a curated, secret-free subset of settings —
never API keys, passwords, or internal URLs.

**Last-admin protection:** `PATCH` refuses (409) any change that would leave
zero active admins, so an admin cannot lock everyone out. Deactivating a user
also revokes their sessions and API tokens (same as `library user disable`).

## 1.3 The admin views (frontend)

A single `/admin` route (`AdminView.vue`), reachable from an admin-only sidebar
link, with four tabs backed by the endpoints above: **System**, **Architecture**
(markdown → sanitised HTML via the shared marked + DOMPurify pipeline),
**Coverage**, and **Users** (promote/demote/activate + create, with inline
last-admin error handling). The router's `authGuard` redirects non-admins away
from `meta.adminOnly` routes, so the page is unreachable without the role even
by deep link.

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
   The top level also has `generated_at` and `git_sha`. Older totals-only
   summaries still validate (the per-file fields default to null/empty).
3. The `Dockerfile` `COPY`s it to `/app/coverage-summary.json` (the default
   `LIBRARY_COVERAGE_SUMMARY_PATH`) and sets `LIBRARY_GIT_SHA` from a build arg.
4. `GET /api/admin/coverage` reads that file; when absent (local dev) it reports
   `available: false`. The Coverage view renders one card per side — the
   headline %, a gate **Pass / Below gate** badge, the file counts, and the
   lowest-covered files — plus a footer with when/which build produced them.

## 1.5 Configuration

| Setting (env `LIBRARY_*`) | Default | Purpose |
|---|---|---|
| `git_sha` | unset | build commit, shown in the System view (set by the image build) |
| `coverage_summary_path` | `coverage-summary.json` | where `GET /api/admin/coverage` reads from |
| `docs_dir` | `docs` | where the Architecture view reads markdown from |
