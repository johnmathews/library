# 1. Admin role + admin views

**Date:** 2026-06-28
**Branch:** `feat/admin-role-and-views`

## 1.1 Why

Library is multi-user ("named family accounts over one shared library") but had
**no roles** — every authenticated user was equal. There was no way to mark
`john` as privileged, no admin-only operations, and no place to see contextual
information about the running app (version, infra, architecture, test coverage).
This cycle adds a single admin role and an admin-only views surface.

## 1.2 What shipped

Built as the `/engineering-team` evaluate→plan→develop cycle, six work units
across four waves.

- **W1 — role foundation.** `users.is_admin` boolean (migration `0014`,
  default false) + a `require_admin` FastAPI dependency that layers on
  `current_user` (anon → 401, non-admin → 403, admin → allowed). `is_admin`
  surfaced on `GET /api/auth/me`. CLI: `library user set-admin <name>
  [--revoke]` and `user add --admin`; `user list` shows the role. **`john`
  becomes admin via `library user set-admin john`.**
- **W2 — admin API** (`api/admin.py`, gated by `require_admin`):
  `GET /system` (version + git sha, redacted operational config, deployment
  topology, live DB stats), `GET /architecture` (renders
  `docs/{architecture,ingestion}.md`), `GET /coverage` (reads the CI-baked
  summary), and `GET/POST/PATCH /users` (list/create/promote/deactivate) with a
  **last-active-admin lockout guard**.
- **W3 — gating.** `POST/PATCH/DELETE /api/projects` now require admin (projects
  are a global shared taxonomy); `GET` stays open.
- **W4 — coverage pipeline.** `scripts/coverage_summary.py` merges backend +
  frontend coverage into `coverage-summary.json`; CI emits it and the Dockerfile
  bakes it (and `docs/`) into the image with a `LIBRARY_GIT_SHA` build arg.
- **W5 — admin frontend.** auth-store `isAdmin`, `/admin` route + `authGuard`
  redirect, `api/admin.ts`, `AdminView.vue` (System / Architecture / Coverage /
  Users tabs), admin-only sidebar link.
- **W6 — e2e + docs.** Playwright `admin-views` spec (normal user has no link
  and is redirected; admin sees the four tabs); CI gains an `e2e-admin` user.
  New `docs/admin.md` + cross-links.

## 1.3 Decisions

1. **Boolean `is_admin`, not an enum.** YAGNI for a family-scale app; a
   `require_admin` guard checks one bit. Trivially extensible later.
2. **CLI promotion, not config auto-promote.** `library user set-admin john`
   post-deploy — explicit, env-agnostic, no startup side effects.
3. **Gate all project mutations, not just delete.** Projects are shared global
   taxonomy, so create/edit affect everyone, not only delete.
4. **Coverage baked at build, not committed back.** CI writes the summary into
   the image (avoids a CI→repo push loop); the endpoint degrades to
   `available: false` when absent (local dev).
5. **CLI stays operator-level.** `library user*` / `backfill*` already require
   host shell access, so admin gating applies to the web API only.

## 1.4 Gotchas

1. **`SELECT count(*) … FOR UPDATE` is invalid in Postgres** (no FOR UPDATE with
   aggregates). The last-admin guard's naive count is racy under READ COMMITTED:
   two concurrent demotions each see one remaining admin and both commit → zero
   admins. Fixed by serialising admin-role mutations with a transaction-scoped
   **advisory lock** (`pg_advisory_xact_lock`) at the top of `update_user`, so
   the second request sees the first's committed state and 409s.
2. **`docs/` must be in the image** for the Architecture tab — W4 baked the
   coverage file but not docs, so a `COPY docs/ /app/docs/` was added (the
   endpoint reads `settings.docs_dir`, default `docs`).
3. **The admin config view is allowlist-based** (`_SAFE_CONFIG_FIELDS`) — only
   fourteen operational knobs, never secrets/URLs. A denylist would have leaked
   on the next setting added.
4. **`require_admin` ordering matters:** because it depends on `current_user`,
   anonymous requests still get 401 (not a confusing 403), and the same guard
   attached at router-include level covers every admin route without per-route
   ceremony.

## 1.5 Verification

Backend 632 tests green; frontend 406 green + coverage gate (85/85/85/75);
ruff + eslint clean; type-check clean. Code review found the authz surface clean
apart from the concurrency race (fixed, §1.4.1). Doc-freshness audit confirmed
accuracy; added the `/api/admin/*` rows to the api.md summary, a CHANGELOG entry,
and the README docs-index link.
