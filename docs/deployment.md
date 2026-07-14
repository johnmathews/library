# Deployment

**Status:** active. **Last updated:** 2026-06-11.

How to run Library in production: a single `docker compose up` on any
Docker host. The walkthrough targets the intended home — a Debian LXC on
Proxmox — but nothing below is Proxmox-specific.

## 1.1 What you are deploying

Four long-running containers plus a one-shot migration job, defined in
the repository's single `docker-compose.yml` (it is production-shaped:
restart policies, healthchecks and memory limits are already in it):

| Service | Image | Role |
|---------|-------|------|
| `db` | `pgvector/pgvector:pg17` (pinned) | Metadata, full-text search, **chunk embeddings (pgvector)**, users/sessions, job queue |
| `embedder` | `ghcr.io/huggingface/text-embeddings-inference:cpu-1.7` | Local bge-m3 embeddings for semantic Ask ([ask.md](ask.md)); downloads the model (~2 GB) into `embedder_cache` on first start |
| `migrate` | built from `Dockerfile` | Runs `alembic upgrade head`, exits; the others wait for it |
| `api` | same image | FastAPI: `/api`, `/mcp`, `/healthz`, and the built web app at `/` |
| `worker` | same image | OCR, Claude extraction, thumbnails, embedding, consume-folder watcher, email poller |

> **Naming note — read this if you're looking for the frontend.** There
> is no frontend container, no separate REST container, and no separate
> MCP container. The `api` service (deployed on the home server as
> **`library-webserver`**, following that stack's `paperless-webserver`
> convention) serves all three on one port: the compiled Vue app as
> static files, the REST API under `/api`, and the MCP server under
> `/mcp`. "Webserver" names the role — the HTTP-facing half of the app,
> beside `worker` and `db` — not the protocol list. Rationale in
> [architecture.md](architecture.md) §1.1 and §1.3 below (same-origin
> cookies, no CORS, one versioned artifact).

One image serves api and worker; the frontend is compiled into it (see
1.3). Three named volumes hold all state: `pgdata` (Postgres),
`library_data` (`/data`: content-addressed originals + derived artifacts),
and `embedder_cache` (the downloaded bge-m3 model). Memory limits in the
compose file (api 512m, worker 2g for OCR peaks, db 1g, embedder 6g for
bge-m3 — a large batch OOM-kills it below that) total ~9.5 GB: **size the
LXC at 8 GB RAM or more** (the embedder is the dominant cost). If you don't
need semantic Ask you can omit the `embedder` service and set
`LIBRARY_EMBEDDING_ENABLED=false` to stay at ~4 GB.

## 1.2 Fresh-machine walkthrough (Proxmox LXC)

1. **Container.** Create a Debian 12/13 LXC — 4 vCPU, **6–8 GB RAM**
   (the bge-m3 embedder needs ~3 GB; 4 GB is enough only with
   `LIBRARY_EMBEDDING_ENABLED=false` and no `embedder` service), 32 GB+
   disk (documents live here; size for your archive). Docker-in-LXC
   needs nesting: set **Options → Features → nesting=1** (and
   `keyctl=1` for an unprivileged container). An unprivileged LXC with
   nesting is the recommended shape.
2. **Docker.** Inside the container, install Docker Engine + the compose
   plugin per [docs.docker.com/engine/install/debian](https://docs.docker.com/engine/install/debian/).
3. **Clone and configure:**

   ```console
   git clone https://github.com/johnmathews/library.git /opt/library
   cd /opt/library
   cp .env.example .env
   ```

   Edit `.env`. The minimum for a useful production instance:
   - `LIBRARY_ANTHROPIC_API_KEY` — without it everything works except
     Claude metadata extraction (documents stay searchable by OCR text).
   - Leave `LIBRARY_COOKIE_SECURE=true` and plan for HTTPS (1.5); set it
     to `false` only while testing over plain HTTP.

   Every setting is documented inline in
   [`.env.example`](../.env.example) — that file is the environment
   reference (it mirrors `src/library/config.py`).
4. **Start the stack:**

   ```console
   docker compose up -d --build
   docker compose ps        # wait until api/worker/db report healthy
   ```

   First build takes a few minutes (npm build + Python deps + OCR
   packages). Alternatively, skip building: CI publishes the same image
   to `ghcr.io/johnmathews/library` — add `image:
   ghcr.io/johnmathews/library:latest` to the `migrate`/`api`/`worker`
   services (or a small override file) and `docker compose pull`.
5. **Create the first user** (there is deliberately no signup endpoint), and
   make them an admin so they can reach the admin views and manage other
   users from the UI:

   ```console
   docker compose exec api library user add anna --display-name "Anna" --admin
   # or promote an existing user: library user set-admin anna
   # prompts for a password; `library user --help` for passwd/disable/list
   ```

   The admin role gates global project mutations and the `/admin` views
   (system/architecture/coverage/users); see [admin.md](admin.md). The
   image bakes a CI-generated `coverage-summary.json` and `LIBRARY_GIT_SHA`
   so the System/Coverage views show real build and coverage data.
6. **Log in.** Browse to `http://<lxc-ip>:8000` (with
   `LIBRARY_COOKIE_SECURE=false` while there is no TLS yet) or to your
   proxied HTTPS hostname (1.5), sign in, upload a PDF on `/upload`, and
   watch it reach **Indexed**.
7. **Optional channels** — consume folder (bind-mount your
   Syncthing-synced scan folder over `/data/consume` on the `worker`
   service), email-in (`LIBRARY_EMAIL_*`), paperless-ngx import
   (`docs/migration.md`): all configured via `.env`; see
   [ingestion.md](ingestion.md). Consumed/failed files are archived to
   **siblings of the consume dir** by default
   (`LIBRARY_CONSUMED_DIR`/`LIBRARY_FAILED_DIR`, defaulting to
   `<parent-of-consume-dir>/consumed` and `…/failed`). That default is
   fine when the consume dir lives inside a persistent volume (here:
   `/data/consumed`, `/data/failed`), but if you bind-mount the consume
   dir as its **own mount root** the parent is ephemeral container
   storage — set both vars explicitly to persistent paths.

## 1.3 How the frontend is served

The Docker image builds the Vue SPA in a `node:22-slim` stage and bakes
`frontend/dist` into the runtime image; the **api process serves it**
(no nginx, no separate frontend container):

- `/assets/*` (content-hashed bundles) — `Cache-Control: public,
  max-age=31536000, immutable`.
- Any other non-backend path — the real file if one exists (manifest,
  icons), else `index.html` with `Cache-Control: no-cache`, so deep
  links like `/documents/42` work and deploys take effect on reload.
- `/api`, `/mcp`, `/healthz`, `/docs`, `/openapi.json`, `/redoc` are
  never shadowed; an unknown `/api/...` path still returns a JSON 404.

In **development** none of this applies: run `make dev` (API on :8000)
and `npm run dev` (Vite on :5173, proxying `/api` — see
[frontend.md](frontend.md)). The API only mounts the SPA when
`LIBRARY_FRONTEND_DIST` (default `frontend/dist`) contains an
`index.html`.

## 1.4 Healthchecks

| Service | Check |
|---------|-------|
| `db` | `pg_isready -U library` |
| `api` | Python stdlib `urllib` GET on `http://localhost:8000/healthz` (curl/wget are not in the slim image) |
| `worker` | Python one-liner: the `library` package imports and the database (which carries the job queue) accepts a connection — the worker has no HTTP surface, so DB reachability is the closest truthful "able to take jobs" signal |

`docker compose ps` shows all three as `healthy` when the stack is up.
CI's `compose-smoke` job boots this exact file and asserts healthy +
login on every push.

## 1.5 Reverse proxy (HTTPS)

Session cookies default to `Secure`, so put TLS in front for real use.
Library is one plain HTTP upstream — no websockets, no special paths;
`/mcp` (streamable HTTP) just needs to be passed through like
everything else.

Caddy (automatic certificates):

```caddyfile
library.example.org {
    reverse_proxy lxc-library:8000
}
```

nginx (or what Nginx Proxy Manager generates):

```nginx
server {
    listen 443 ssl;
    server_name library.example.org;
    # ... ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://lxc-library:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Uploads up to LIBRARY_MAX_UPLOAD_BYTES (default 100 MB):
        client_max_body_size 100m;
    }
}
```

In Nginx Proxy Manager: a single proxy host to `lxc-library:8000`, no
websocket support needed; raise the upload size as above in the custom
config. Keep `LIBRARY_COOKIE_SECURE=true` once HTTPS terminates at the
proxy — the cookie flag is about what the *browser* sees.

## 1.6 Backups

This section is the general guidance for a repo-based deploy. The **live
`paperless` instance uses a different, already-automated topology** (file store
on TrueNAS NFS with ZFS snapshots + replication; local `pgdata` captured by the
daily PBS LXC backup) — see §1.7.2 point 3.

Two named volumes hold everything; back up both.

**Database — logical dump (restores anywhere):**

```console
docker compose exec -T db pg_dump -U library -Fc library \
  > /var/backups/library/library-$(date +%F).dump
```

Cron it daily (e.g. `/etc/cron.daily/library-pgdump`) and prune old
dumps. The dump includes the Procrastinate job tables; pending jobs are
re-runnable, so losing in-flight jobs to a restore is harmless.

**Originals — the `/data` volume.** Files are content-addressed and
immutable, which makes any file-level copy safe:

```console
docker run --rm -v library_library_data:/data -v /var/backups/library:/backup \
  alpine tar czf /backup/library-data-$(date +%F).tar.gz -C /data .
```

**Proxmox Backup Server alternative:** vzdump/PBS snapshots of the whole
LXC capture both Docker volumes (under
`/var/lib/docker/volumes/library_{pgdata,library_data}`) consistently
enough for a homelab — Postgres recovers a crash-consistent snapshot via
WAL replay. The belt-and-braces setup is PBS for the machine plus the
nightly `pg_dump` for a database you can restore selectively.

**Restore** (fresh machine): deploy per 1.2 up to step 4, stop api +
worker, then:

```console
docker compose stop api worker
docker compose exec -T db pg_restore -U library -d library --clean --if-exists \
  < library-YYYY-MM-DD.dump
docker run --rm -v library_library_data:/data -v /var/backups/library:/backup \
  alpine tar xzf /backup/library-data-YYYY-MM-DD.tar.gz -C /data
docker compose up -d
```

Derived artifacts (thumbnails, searchable PDFs) are re-derivable; only
`originals/` and the database are irreplaceable.

## 1.7 Upgrades

```console
cd /opt/library
git pull
docker compose up -d --build      # or: docker compose pull && docker compose up -d  (ghcr image)
```

`compose up` recreates only changed containers. The `migrate` service
runs `alembic upgrade head` before api/worker start (they depend on its
successful completion), so schema migrations are automatic. The `db`
image is pinned to a Postgres minor; major-version upgrades are a
deliberate dump/restore, not a pull.

Roll back by checking out the previous tag/commit and `docker compose up
-d --build` again — but note migrations are not automatically reversed;
restore the pre-upgrade `pg_dump` if a downgrade must cross a migration.

### 1.7.1 Upgrading to semantic Ask (pgvector + embedder)

The Ask release swaps the `db` image to `pgvector/pgvector:pg17` and adds the
`embedder` service. Steps (with the gotchas seen on the live `paperless` LXC —
see 1.7.2 for that instance's exact shape):

1. **Back up the DB first** — always, before any DB image change:
   `docker exec <db> pg_dump -U library -Fc library > library-pre-ask.dump`.
2. **Bump RAM** to ~6–8 GB (the embedder needs ~3 GB resident).
3. **Edit the compose**: swap the `db` image; add the `embedder` service; add
   `LIBRARY_EMBEDDING_SERVICE_URL` + a `depends_on: embedder` to api/worker.
4. `docker compose pull && docker compose up -d` — the `migrate` job enables
   the `vector` extension and creates `document_chunks`, `ask_threads`, and
   `ask_turns`; the
   `embedder` downloads bge-m3 (~2 GB) into its cache volume on first start.
5. **Collation check (important).** If the *old* `db` image's glibc differs
   from `pgvector/pgvector:pg17`'s (e.g. a newer `postgres:17` → the pgvector
   image, which trails on an older Debian/glibc), the cluster logs
   `WARNING: database "library" has a collation version mismatch`. Text indexes
   were built under the old sort order — rebuild them:

   ```console
   docker exec <db> psql -U library -d library -c "REINDEX DATABASE library;"
   docker exec <db> psql -U library -d library -c "ALTER DATABASE library REFRESH COLLATION VERSION;"
   ```

   (A *fresh* cluster avoids this entirely — the compose sets
   `POSTGRES_INITDB_ARGS=--locale=C.UTF-8`, a libc-independent collation. This
   only bites when reusing an existing pgdata across a glibc change.)
6. **Embedder OOM guard.** TEI's default `--max-batch-tokens 16384` warmup
   allocation can OOM-kill bge-m3 even at a 4 GB limit. The compose runs it with
   `--max-batch-tokens 2048` and `mem_limit: 6g` — keep those (we embed
   page-sized chunks, so 2048 is plenty). Symptom if it's too low:
   `docker inspect <embedder> --format '{{.State.OOMKilled}}'` is `true` and the
   container restart-loops right after "Warming up model".
7. **Backfill embeddings** for documents indexed before this release:

   ```console
   docker compose exec api library backfill-embeddings
   ```

   Idempotent and throttleable (`--limit N`); the worker embeds on CPU
   (~6 min per ~100 docs). See [ask.md](ask.md) §1.5.
8. **Backfill summaries** for documents indexed before extraction produced a
   summary:

   ```console
   docker compose exec api library backfill-summaries
   ```

   Re-runs extraction (honours user edits + the daily budget) for indexed docs
   with no summary; throttleable (`--limit N`). Needs `LIBRARY_ANTHROPIC_API_KEY`
   and the worker running. See [ingestion.md](ingestion.md) §"Backfill summaries".
9. **Ask answering needs an Anthropic key.** `POST /api/ask` returns `503`
   without `LIBRARY_ANTHROPIC_API_KEY` (only the answer step calls Claude;
   embedding/indexing is local). Retrieval works without it.

### 1.7.2 The live `paperless` LXC instance

The production instance does **not** use this repo's `docker-compose.yml`
(that file is a dev / CI-smoke reference). The live compose at
`/srv/apps/docker-compose.yml` on the `paperless` LXC is **templated from the
`home-server/proxmox-setup` Ansible repo — that repo is the deploy source of
truth for the container wiring, mounts, and memory limits.** This repo owns the
image; proxmox-setup owns how it is run. Concrete details:

1. **Access:** `ssh paperless` (root). Compose at `/srv/apps/docker-compose.yml`
   (back it up before editing — `cp` to a `.bak-<ts>`); env in `/srv/apps/.env`.
   Edit the compose in the proxmox-setup repo and re-apply, rather than editing
   the host copy by hand, so the source of truth stays authoritative.
2. **Services:** `library-db` (pgvector, bind mount `/srv/apps/library/pgdata`),
   `library-embedder` (`mem_limit: 6g`, cache bind mount
   `/srv/apps/library/embedder-cache`), `library-migrate` (one-shot),
   `library-webserver` (`:8010`→8000), `library-worker`. Images are
   `ghcr.io/johnmathews/library:latest`.
3. **Storage & backups (see §1.6).** Library's file store (`/data`) and consume
   folder are bind-mounted from the **TrueNAS `document-store` NFS dataset**
   (`/mnt/nfs/document-store` → `data/`→`/data`, `consume/`→`/consume`), so the
   corpus is not on the 16 GB LXC rootfs. Because `/consume` is its **own mount
   root**, the automatic sibling default for the consume archive dirs would
   resolve to the ephemeral container root (`/consumed`, `/failed`) — the
   `library-worker` service **must** set `LIBRARY_CONSUMED_DIR=/data/consumed`
   and `LIBRARY_FAILED_DIR=/data/failed` explicitly in the proxmox-setup compose
   **before** pulling an image with the sibling-archive change (the startup
   auto-migration moves the archive to wherever these resolve). The database
   (`pgdata`) stays **local** at `/srv/apps/library/pgdata` on the LXC disk. Backup coverage follows from
   this split: the **daily PBS backup of the LXC** captures `pgdata` (local),
   while the file store is covered by **TrueNAS ZFS snapshots + replication** of
   `tank`. No separate `pg_dump` cron is required for disaster recovery.
4. **`.env` is regenerated externally** (Portainer) — durable env overrides go
   in the service's `environment:` block in the compose file, **not** in `.env`.
   So the Anthropic key for Ask must be set on `library-webserver`'s
   `environment:`, not `.env`.
5. **Deploy command** (routine code changes): `cd /srv/apps && docker compose
   up -d --pull always library-migrate library-webserver library-worker`
   (never `stop` the db; always include `library-migrate`). This is automated by
   **`scripts/deploy.sh`** (run `make deploy` from the repo root), which also
   verifies the migrate exit code and `/healthz` — see the focused
   [deploy runbook](runbooks/deploy.md).

## 1.8 Troubleshooting

- **Document stuck in `received`/`ocr`** — check the queue:
  `GET /api/jobs` (or the UI's job data), `docker compose logs worker`.
  Job rows live in the `procrastinate_jobs` table
  (`docker compose exec db psql -U library -c "select id, status,
  task_name, attempts from procrastinate_jobs order by id desc limit
  20"`). A `failed` document carries the error in its detail-page audit
  trail (`ingestion_events`).
- **OCR slow or low quality** — OCR is CPU-bound; give the LXC cores and
  the worker its 2g `mem_limit`. Tune `LIBRARY_OCR_CONFIDENCE_THRESHOLD`
  (lower = fewer expensive neural retries) and
  `LIBRARY_TEXT_LAYER_MIN_CHARS_PER_PAGE`; behaviour and the measured
  benchmark are in [ingestion.md](ingestion.md) and
  [benchmarks/260610-ocr-benchmark.md](benchmarks/260610-ocr-benchmark.md).
- **Extraction skipped** — the document's audit trail says why:
  `missing_api_key` (set `LIBRARY_ANTHROPIC_API_KEY`), `disabled`
  (`LIBRARY_EXTRACTION_ENABLED`), `budget` (raise
  `LIBRARY_EXTRACTION_DAILY_BUDGET_USD` or wait for the next UTC day),
  `input_unusable`/`file_too_large`. Re-run per document with
  `POST /api/documents/{id}/extract` (or the detail page's button).
- **Cannot log in over plain HTTP** — the browser is dropping the
  `Secure` session cookie. Use HTTPS (1.5) or set
  `LIBRARY_COOKIE_SECURE=false` while testing.
- **Consume folder ignores files on a NAS mount** — inotify does not
  cross NFS/SMB: set `LIBRARY_CONSUME_FORCE_POLLING=true`
  ([ingestion.md](ingestion.md), "Syncthing / NAS notes").
- **Healthcheck failing on `worker`** — almost always the database
  connection (the check is exactly "import + connect"): check `db`
  health and `LIBRARY_DATABASE_URL` in `.env`.
