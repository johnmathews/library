# Project inception

## 1.1 What happened

Kicked off **Library**, a self-hosted personal/family document archive
intended as a better, faster, more flexible replacement for paperless-ngx.
Two rounds of discussion with John settled the requirements and the
contentious technical choices. No code yet — decisions only.

## 1.2 Decisions

- **Stack:** Vue 3 frontend (GOV.UK Design System as design north star,
  must work on desktop/iPhone/iPad), FastAPI backend (Python 3.13, uv),
  PostgreSQL, MCP server + full REST API. Docker Compose deployment into a
  Proxmox LXC — containers are John's default deployment unit.
- **Database — Postgres over SQLite/MySQL** (John pushed back, rationale
  given): SQLite's single-writer model doesn't fit multi-container
  API+worker+MCP writes and FTS5 has no Dutch stemmer; MySQL's FTS lacks
  ranking quality and a Dutch stemmer, JSON support is weaker than JSONB,
  and there's no pgvector equivalent. Postgres gives Dutch+English FTS,
  JSONB metadata, and pgvector for later semantic search in one container.
- **OCR — researched, Tesseract alone rejected** (John asked "is Tesseract
  really good enough?" — answer: only partially). Routed pipeline: direct
  text extraction for born-digital PDFs; OCRmyPDF/Tesseract `nld+eng` with
  deskew/clean/oversample for scans; OpenCV perspective-correction +
  RapidOCR (PP-OCRv5 latin, CPU) for phone photos; confidence-gated retry.
  Full research with sources in
  `.engineering-team/runs/manual-20260610-154616/discussions/260610-project-inception-decisions.md`.
- **Metadata extraction:** Claude API over the OCR text (hybrid local/cloud).
- **Auth:** named family accounts, one shared library, session/JWT.
- **Ingestion v1:** web upload, watched consume folder (Syncthing-friendly),
  iPhone camera capture (PWA), email-in (IMAP), REST + MCP.
- **Migration:** import documents *and* metadata from John's existing
  paperless-ngx.
- **Repo:** public GitHub repo `library`; CI pushes images to
  `ghcr.io/johnmathews/library`.
- **Primary input clarified:** iOS Notes scans exported to the watched
  consume directory. Notes pre-flattens/enhances images, so these hit the
  Tesseract scan path; the neural photo path is fallback only.
- **Language:** documents are mixed Dutch/English — affects OCR language
  models, FTS stemming configs, and a per-document language metadata field.

## 1.3 Next

Switch to the engineering-team build workflow: planning phase (architecture
blueprint, data model, work-unit breakdown), scaffold repo, create GitHub
repo, CI, then development starting with the core path
(upload → OCR → metadata → search → view).
