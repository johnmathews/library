# 1. Library — project inception decisions

**Date:** 2026-06-10
**Status:** agreed with John; source of truth for the initial build.

## 1.1 What Library is

A self-hosted, personal/family "omni library": ingests scans, photos and digital
documents (PDF, JPG, PNG, …) — invoices, certificates, utility bills, parking
tickets, receipts, warranties, manuals — digitises them via OCR, and enriches
each document with structured metadata (kind, sender/correspondent, topic,
purpose, dates, amounts, expiry, tags, title, summary). A better, more
performant and flexible replacement for paperless-ngx.

## 1.2 Stack (set by John)

- Frontend: Vue 3, responsive — must work well on desktop, iPhone and iPad.
- API server: FastAPI (Python 3.13, uv, pytest + coverage).
- MCP server exposing the library to other tools, plus a full REST API.
- Database: PostgreSQL (lead engineer's call — see 1.4).
- Design north star: **www.gov.uk** — copy its design and design principles
  (GOV.UK Design System: content-first, high legibility, strong typography,
  accessible, minimal decoration), extending as necessary.

## 1.3 Decisions made 2026-06-10 (asked and answered)

| # | Question | Decision |
|---|----------|----------|
| 1 | OCR & metadata engine | **Hybrid:** local OCR (Tesseract/OCRmyPDF, free, private) + **Claude API** for structured metadata extraction from the OCR text/image. |
| 2 | Users & auth | **Family accounts, shared library.** A handful of named accounts, one shared library, session/JWT auth; safe behind a reverse proxy. |
| 3 | Ingestion channels (v1) | **All of:** web upload, watched consume folder (pairs with Syncthing/scanner), iPhone/iPad camera capture (PWA flow), email-in (IMAP poll), plus REST API & MCP ingestion. |
| 4 | Migration | **Yes — import documents *and* metadata** from John's existing paperless-ngx instance (via its API/export). |
| 5 | GitHub repo | **Public**, named `library`, under John's account. CI on GitHub Actions; Docker images pushed to `ghcr.io/johnmathews/library`. |

## 1.4 Lead-engineer defaults (overridable, not yet user-confirmed)

- **PostgreSQL** with full-text search; pgvector for semantic search as a
  later phase. SQLite rejected: multi-user writes, FTS quality, growth.
- **Deployment:** Docker Compose (app, db, worker), designed to run in an
  LXC on John's Proxmox home server.
- **Background work** (OCR, metadata, email poll, consume folder) via a task
  queue / worker process, not in-request.
- Originals stored immutably on the filesystem (content-addressed), DB holds
  metadata + extracted text; documents never enter the git repo.
- Scale assumption: low tens of thousands of documents, family-scale traffic.

## 1.5 Addendum (2026-06-10, after John's pushback round)

### 1.5.1 Database — PostgreSQL confirmed, rationale

- **SQLite rejected:** single-writer model conflicts with our multi-process,
  multi-container architecture (API + background worker + MCP server all
  writing); sharing a DB file across containers over a volume is fragile;
  FTS5 has no Dutch stemmer; no mature vector story.
- **MySQL rejected:** its full-text search has weak ranking and no Dutch
  stemmer; JSON support is inferior to JSONB+GIN; no pgvector equivalent.
  It offers nothing this project needs that Postgres lacks.
- **Postgres wins on:** built-in FTS with *both* `dutch` and `english`
  stemming configs, JSONB for flexible per-kind metadata, pgvector for later
  semantic search, true concurrent writers. One container, no separate
  search engine needed in v1.

### 1.5.2 OCR — Tesseract alone is NOT good enough; routed pipeline adopted

Web-researched June 2026 (benchmarks, paperless-ngx community direction):
Tesseract 5 gets >99% char accuracy on clean PDFs and 95–99% on flatbed
scans, but collapses on phone photos (CER 18–30%+). The community pattern
(and ours) is a **pluggable engine interface with routing by input type**:

1. Born-digital PDF with text layer → extract text directly (pypdfium2/
   pdfplumber), no OCR at all.
2. Scans / image-only PDFs → **OCRmyPDF + Tesseract** `-l nld+eng`
   (tessdata_best, `--rotate-pages --deskew --clean --oversample 300
   --skip-text`) → also yields the searchable-PDF artifact.
3. Phone photos (JPEG/HEIC) → **OpenCV preprocessing** (page contour
   detection, 4-point perspective transform, CLAHE/Sauvola) → **RapidOCR**
   (PP-OCRv5 `latin` model via ONNX Runtime, CPU-friendly ~300 MB, Apache
   2.0). One model covers Dutch+English simultaneously.
4. Quality gate: if Tesseract mean word confidence is low, retry via the
   neural path.
5. Rejected: EasyOCR (stagnant), Surya (restrictive model weights license),
   docTR (no Dutch), VLM OCR (needs GPU).
6. Early work unit: benchmark both paths on ~10 representative real
   documents on the target LXC before freezing the pipeline.

### 1.5.3 Primary input path (clarified by John 2026-06-10)

The **primary** ingestion flow is: iOS Notes document scanner → exported
file moved into the watched **consume directory**. iOS Notes already does
perspective correction, cropping, flattening and contrast enhancement, so
these arrive as clean image PDFs — Tesseract's sweet spot (route 2), NOT
the raw-photo path. The OpenCV+RapidOCR photo path (route 3) is the
fallback for unprocessed camera shots and the confidence-gate retry. The
OCR benchmark work unit must use real iOS Notes scan exports as its main
test set.

### 1.5.4 Language

Documents are mixed Dutch and English, sometimes within one document.
Tesseract runs `nld+eng`; PP-OCRv5's latin model is language-agnostic.
Postgres FTS indexes both `dutch` and `english` configs; detected language
stored as a metadata field (Claude extraction step reports it).

## 1.6 Required project furniture (from John's global standards)

- `/docs` directory, complete and current; superseded docs archived to
  `docs/archive/` with a Status header.
- `/journal` directory, entries named `yymmdd-descriptive-name.md`.
- Tests created alongside all code; pytest + coverage.
- GitHub Actions workflow building and pushing the Docker image to
  `ghcr.io/johnmathews/library` on push to `main`, authenticated with
  `GITHUB_TOKEN`.
