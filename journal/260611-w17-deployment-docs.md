# W17 — Deployment hardening, docs completion, wrap (2026-06-11)

Final work unit. Library v0.1.0 is feature-complete and deployable from
the docs alone.

## What landed

- **Production frontend serving.** The Docker image gained a
  `node:22-slim` stage (`npm ci && npm run build`) and the FastAPI
  process now serves `frontend/dist` itself: `/assets` through a
  `StaticFiles` subclass adding `Cache-Control: public,
  max-age=31536000, immutable` (Vite content-hashes the filenames), and
  a catch-all route that serves real files (manifest, icons) or falls
  back to `index.html` with `no-cache` for client-side routes. Backend
  heads (`api`, `mcp`, `healthz`, `docs`, `redoc`, `openapi.json`) are
  excluded so an unknown `/api/...` path still 404s as JSON. New setting
  `LIBRARY_FRONTEND_DIST` (default `frontend/dist`, resolving to the
  baked-in build at `/app/frontend/dist` in the image); when the
  directory has no `index.html` (dev), nothing is mounted and the Vite
  proxy workflow is unchanged. Tests: `tests/test_spa.py` (9 cases
  incl. cache headers, deep links, JSON 404s, no-dist mode).
- **Design call — why serve the SPA from FastAPI:** one image, one
  process, one port; no nginx container to configure/patch; FastAPI's
  route ordering gives the API absolute precedence; family-scale traffic
  makes a dedicated static server pure overhead. The hashed-immutable +
  index-no-cache split gives correct deploy semantics anyway.
- **Compose hardening.** `restart: unless-stopped` on db/api/worker;
  `postgres` pinned to `17.5-alpine`; memory limits (api 512m, worker 2g
  for OCR peaks, db 1g — commented as tunable, sized for a ~4 GB LXC);
  worker healthcheck (stdlib one-liner: import `library` + connect to
  Postgres via the Procrastinate conninfo — the worker has no HTTP
  surface, DB reachability is the honest "can take jobs" probe);
  `env_file: .env` (`required: false`) on migrate/api/worker plus
  `${VAR:-default}` interpolation so `.env` values win over the inline
  defaults; new fully-commented `.env.example` covering every
  `LIBRARY_*` setting in `config.py`.
- **Docs.** New `docs/deployment.md` (Proxmox-LXC fresh-machine
  walkthrough incl. nesting flag, frontend-serving explanation, reverse
  proxy with Caddy/nginx snippets, backup/restore for both named
  volumes incl. PBS notes, upgrades via migrate service, troubleshooting
  matrix). Sweep fixes: ingestion.md's stale "No authentication yet"
  banner (W8 landed long ago) and the `GET /api/jobs` no-auth note;
  frontend.md now documents both serving modes; architecture.md W17 row
  done + accurate frontend-serving sentence. README rewritten for
  v0.1.0 (feature list, quickstart → deployment.md). `CHANGELOG.md`
  added (Keep a Changelog, v0.1.0, 2026-06-11). **Screenshots skipped**
  deliberately: there is no stable demo dataset to screenshot, and
  fabricated documents would go stale against the real UI immediately.
- **CI.** New `compose-smoke` job: `docker compose up -d --build` on the
  real production compose file, wait for all three healthchecks, assert
  the SPA shell at `/`, create a user, cookie login, `GET
  /api/documents` → items. Runs with `LIBRARY_COOKIE_SECURE=false` (curl
  drops Secure cookies over plain HTTP — production default stays true).

## Local proof

Full stack built and booted locally from the new compose file: api,
worker and db all reached `healthy`; `GET /` returned the SPA shell with
`no-cache`; hashed asset served `immutable`; `/healthz` ok; user
created via `docker compose exec api library user add`; httpx cookie
login + `GET /api/documents` → 200 with `items`; unknown `/api` path →
JSON 404. Torn down with volumes.

## Coverage

`uv run coverage run -m pytest && uv run coverage report` →
**262 passed, TOTAL 90%** (2562 statements, 253 missed) across the full
suite (testcontainers Postgres + real OCR engines included); `htmlcov/`
generated locally (gitignored — `.gitignore` already lists `htmlcov/`).

## Future work

- Restore/undelete endpoint (soft-deleted documents currently need SQL).
- Richer tag editing widget + multi-tag filter UI (API already supports
  ANDed tags).
- Email body (HTML→PDF) ingestion; currently attachments only.
- Automatic retries for failed pipeline jobs (Procrastinate supports
  retry policies; today a failed document is re-queued manually).
- An ops-grade metrics endpoint (Prometheus) if the archive ever needs
  real monitoring.
