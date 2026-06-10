# 1. Evaluation report — Library (greenfield baseline)

**Date:** 2026-06-10. **Run:** manual-20260610-154616.

## 1.1 Executive summary

Library is a greenfield project: a self-hosted personal/family document
archive replacing paperless-ngx, with a Vue 3 frontend (GOV.UK design
north star), FastAPI backend, PostgreSQL, an OCR + Claude-metadata
pipeline, and REST + MCP interfaces. There is no code yet — this report
is the requirements and risk baseline that Phase 2 plans against. All
inception decisions are recorded in
`discussions/260610-project-inception-decisions.md` (same run directory).

## 1.2 Test suite results

Not applicable — no code exists. The working directory contains only
`journal/260610-project-inception.md` and this run's `.engineering-team/`
artifacts. No linter, no tests, no CI. For a greenfield build these are
not findings; they become work units (scaffold unit establishes ruff,
pytest+coverage, vitest, and GitHub Actions from day one, per John's
global standards).

## 1.3 Requirements baseline (what Phase 2 must plan for)

1. Ingest scans, photos and digital documents (PDF/JPG/PNG/HEIC, …).
   **Primary flow:** iOS Notes scans exported into a watched consume
   directory. Also: web upload, iPhone camera capture (PWA), email-in
   (IMAP), REST API, MCP.
2. Digitise: routed OCR pipeline (direct text-layer extraction;
   OCRmyPDF/Tesseract `nld+eng` for scans; OpenCV+RapidOCR fallback for
   raw photos; confidence-gated retry).
3. Enrich: Claude API extracts structured metadata — kind, sender,
   topic/purpose, title, summary, document date, amounts, expiry,
   language (documents are mixed Dutch/English), tags.
4. Store: originals immutable and content-addressed on the filesystem;
   metadata + extracted text in Postgres (FTS in `dutch` + `english`,
   JSONB per-kind fields; pgvector later).
5. Serve: Vue 3 web app usable on desktop, iPhone and iPad, styled on
   GOV.UK design principles; full REST API; MCP server for other tools.
6. Auth: named family accounts over one shared library (session/JWT).
7. Migrate: importer for documents *and* metadata from John's existing
   paperless-ngx instance.
8. Operate: Docker Compose (api, worker, db) targeting a Proxmox LXC;
   GitHub Actions building and pushing to `ghcr.io/johnmathews/library`;
   public GitHub repo `library`.
9. Project furniture: `/docs` (complete, current), `/journal` (dated
   entries), tests alongside all code (pytest + coverage), Python 3.13
   via uv.

## 1.4 Risks and open questions for planning

- **R1 — GOV.UK assets licensing:** govuk-frontend code is MIT, but the
  GDS Transport font and crown imagery are restricted to government
  services. The design system port must substitute a permissive
  typeface while keeping the design language. (Planning research will
  confirm current licensing details.)
- **R2 — Scope width:** five ingestion channels + auth + pipeline + MCP
  + importer is a wide v1. Plan must sequence a usable core first
  (upload → OCR → metadata → search → view) and keep email-in and the
  importer as later, independent units.
- **R3 — OCR quality is empirical:** benchmarks justify the routed
  pipeline, but real iOS Notes exports must be benchmarked on the
  target hardware before the pipeline is frozen. Needs ~10 sample
  documents from John.
- **R4 — Claude API cost/failure handling:** extraction must be
  idempotent, retryable, and re-runnable when prompts improve;
  documents must remain usable (searchable by OCR text) when
  extraction fails or is deferred.
- **R5 — Task queue choice:** FastAPI has no built-in background
  worker suitable for OCR-length jobs; the queue/worker choice (e.g.
  arq/dramatiq/celery) needs a current-state check during planning.
- **R6 — MCP SDK churn:** the MCP Python ecosystem moves quickly;
  verify current SDK and transport recommendations before planning the
  MCP unit.
- **R7 — paperless-ngx export format:** the importer depends on
  paperless-ngx's current API/export shapes; verify before planning.

## 1.5 Assessment dimensions

Not scored — there is no code to rate. The dimensions become acceptance
bars for development: every unit ships with tests, docs, and working
deployment, so the first real evaluation scores against a complete
baseline.
