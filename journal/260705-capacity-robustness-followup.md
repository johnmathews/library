# Capacity & robustness follow-up (engineering-team run)

**Date:** 2026-07-05. **Branch:** `worktree-eng-capacity-robustness-followup`.
Run dir: `.engineering-team/runs/manual-20260704T194051Z/`.

An engineering-team plan → develop → wrap-up cycle acting on the 2026-07-04
capacity/robustness evaluation (`evaluation-report.md`). The evaluation asked
whether Library is ready for intensive use (~100 docs/month over 5 years, 2–5
household users). Its verdict: compute/search/concurrency have years of
headroom; the only hard ceiling was **storage**, plus two robustness gaps.

## 1. Findings that were already resolved out-of-band

Verified live before planning, so no work units:

1. **Rootfs disk exhaustion (CRITICAL) — resolved.** The file store was moved
   off the 16 GB LXC rootfs onto a dedicated TrueNAS dataset `tank/document-store`
   (NFS-mounted `/mnt/nfs/document-store`), done in the `home-server/proxmox-setup`
   Ansible repo (commit `d28fef4`). Confirmed via live Prometheus
   `node_filesystem` (the new NFS mount is present, 5.7 TB free) and the
   proxmox-setup journals.
2. **No automated DB backups (HIGH) — resolved.** `library-db` pgdata is local
   at `/srv/apps/library/pgdata`, captured by the daily PBS backup of CT 117 (21
   snapshots, verified via PBS). The NFS document store is covered by recursive
   daily ZFS snapshots of `tank` + replication to `backup/tank`. This is stronger
   than the report's suggested `pg_dump` cron (automated + offsite). Caveat
   accepted: PBS captures a crash-consistent live Postgres, not a logical dump.

## 2. What shipped (W1–W5)

All five planned units, green together: **backend 931 tests**, **frontend 745
unit tests**, ruff + eslint + vue-tsc clean.

### 2.1 W1 — Worker-crash recovery (HIGH)

A hard-killed worker (OOM/SIGKILL/**redeploy mid-stage** — the common trigger)
never runs `advance_pipeline`'s failure handler, so its in-flight
`process_document` job stays `doing` and the document is stranded in a
non-terminal status with no re-queue. Added a periodic `sweep_stalled_jobs`
task (`jobs.py`) that calls `job_manager.get_stalled_jobs(task_name=…,
seconds_since_heartbeat=…)` → `retry_job`; the pipeline resumes idempotently.
Config: `stalled_job_sweep_minutes` (default 5, `0` disables),
`stalled_job_heartbeat_seconds` (default 60, above Procrastinate's ~10 s
heartbeat so a live worker mid-OCR is never swept).

**Design note:** the report offered "a retry policy *or* a sweeper." A
task-level `retry=` policy was deliberately rejected — it recovers nothing here:
a hard kill never marks the job failed (so retry never fires), and a clean
exception already marks the document `FAILED`/terminal (so a retried job
no-ops). The sweeper is the correct and sufficient fix.

### 2.2 W2 — Deploy-config alignment (MEDIUM)

The `library` repo's reference `docker-compose.yml` was stale vs the real prod
source (proxmox-setup): embedder `mem_limit` 3g→6g + `--max-batch-tokens 2048`
(bge-m3 OOM-kills below 6g on a large batch). Documented the real live topology
in `docs/deployment.md` §1.6/§1.7.2 (document-store NFS mount, local pgdata,
proxmox-setup as deploy source of truth). Config/docs only — prod already ran 6g.

### 2.3 W3 — Make the HNSW index usable (MEDIUM)

`_vector_candidates` (`search.py`) used `SELECT DISTINCT ON (document_id) …
ORDER BY document_id, distance`, which forces a sort by `document_id` first and
so sequential-scans every chunk (the HNSW ANN index can't accelerate it).
Rewrote it to the canonical pgvector shape: an index-friendly prefetch
(`ORDER BY embedding <=> q LIMIT pool * VECTOR_CANDIDATE_FANOUT`) then a
`DISTINCT ON` collapse to one row per document over that small set.

**Key discovery via `EXPLAIN`:** the JOIN to `documents` (present only to apply
filter conditions) *itself* defeats the ANN index scan. So the prefetch now
**joins only when filter conditions exist** — the common unfiltered Ask/search
path stays index-accelerated; a filtered search accepts the join (the filter
narrows the rows the sort touches). Verified with `EXPLAIN` on a 1,500-chunk
seeded set: the unfiltered shape yields `Index Scan using
ix_document_chunks_embedding`; the old `DISTINCT ON` shape does not even when
`enable_seqscan=off`. Negligible today (tens of ms at the current corpus);
the fix matters before ~10k docs.

### 2.4 W4 — Tunable worker concurrency (LOW)

`run_worker`/`run_worker_async` (`worker.py`) now pass
`concurrency=worker_concurrency` (new setting, default 1 — kept serial so
raising it is a deliberate, RAM-aware choice on the 8 GB LXC).

### 2.5 W5 — Budget-skip visibility + daily auto-backfill (LOW)

Budget exhaustion (`extraction_skipped`/`markdown_skipped`, `reason: budget`)
left documents `indexed` but silently without LLM metadata. New
`library.budget_backfill` finds documents whose *latest* extraction/markdown
event is a budget skip (a later success clears it). Surfaced as
`stats.documents_budget_skipped` on the admin **System** view (amber when > 0).
Added an opt-in daily task `backfill_budget_skipped` (03:17 UTC, after the
budget resets) that re-enqueues `extract_document` + `markdown_document` for
those documents — gated by `budget_backfill_enabled` (default **off**, since it
spends).

## 3. Review fixes (two confirmed correctness bugs caught in wrap-up)

The `/done` code-review pass (adversarial, against the shipped diff + Procrastinate
3.8.1 source) found two real defects that tests had missed. Both fixed and
verified before merge:

1. **W1 sweeper went blind after a worker-row prune.** `get_stalled_jobs` only
   sees a stranded job while its dead worker's row still exists in
   `procrastinate_workers`. Procrastinate prunes those rows at the next worker
   startup (`stalled_worker_timeout`, default **30 s**) — so for the exact
   redeploy/host-crash cases W1 targets (restart gap ≥30 s), the replacement
   worker deleted the crashed worker's row *before* the sweep could run, hiding
   the orphaned `doing` job forever. Fix: pass `stalled_worker_timeout=
   stalled_worker_prune_seconds` (default **24 h**) to `run_worker`, so dead rows
   survive until the sweep re-enqueues their jobs. Confirmed against the
   installed `select_stalled_jobs_by_heartbeat` / `procrastinate_prune_stalled_workers_v1`
   SQL (the prune only DELETEs the row; it does not requeue).

2. **W3's HNSW fast path was dead code.** `filter_conditions()` always returns
   the soft-delete base condition `[deleted_at IS NULL]`, so `if conditions:`
   was *always* true and the join-to-`documents` (which defeats the ANN index)
   fired on **every** search — the index optimization never actually happened.
   Fix: split the always-on soft-delete base from user-supplied filters
   (`has_user_filters = len(conditions) > 1`). Unfiltered searches now prefetch
   over `document_chunks` alone (HNSW-accelerated) and apply the soft-delete
   exclusion in the collapse over the ~k prefetched rows; filtered searches keep
   the join-before-limit (correct for restrictive filters). Re-verified with
   `EXPLAIN` on the *real composed* unfiltered query (Index Scan using
   `ix_document_chunks_embedding`), plus a new regression test that a
   soft-deleted nearest document is excluded from unfiltered results.

## 4. Accepted as-is (no work unit)

**Finding #8 — document text stored 3×** (`ocr_text`, `document_pages.markdown`,
`document_chunks.text`). Each serves a distinct consumer (FTS, the "understood"
layer, embedding-aligned slices); de-duplicating would couple FTS + chunking +
detail view for ~50 KB/doc against a DB that stays under 1 GB at the 5-year
horizon. Blast radius ≫ benefit.

## 5. Notes for next time

- Prod deploy config lives in **`home-server/proxmox-setup`** (Ansible →
  `/srv/apps/docker-compose.yml`), *not* this repo's compose. The repo compose
  is a dev/CI-smoke reference; keep the two from drifting (this run closed one
  such drift: embedder 3g).
- `_chunks_per_document` (`search.py`) was left as-is: it's already bounded by
  `document_id.in_(document_ids)` (≤ top_k docs), so it never corpus-scans and
  gains nothing from an HNSW rewrite.
