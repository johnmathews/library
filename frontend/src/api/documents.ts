/**
 * Typed API for documents and jobs (docs/api.md §1.2–1.8).
 *
 * Types mirror the backend Pydantic schemas in src/library/schemas.py.
 * Dates and datetimes travel as ISO strings; `amount_total` is a decimal
 * string to preserve precision.
 */

import { ApiError, apiFetch, getCookie, CSRF_COOKIE, CSRF_HEADER } from './client'

export type DocumentLanguage = 'nld' | 'eng' | 'mixed' | 'unknown'
export type DocumentStatus = 'received' | 'ocr' | 'extract' | 'indexed' | 'failed'
export type DocumentSource = 'upload' | 'consume' | 'email' | 'api' | 'mcp' | 'import'
export type ReviewStatus = 'verified' | 'needs_review' | 'unreviewed'

export interface KindRef {
  slug: string
  name: string
}

export interface SenderRef {
  id: number
  name: string
}

export interface TagRef {
  slug: string
  name: string
}

export interface ProjectRef {
  slug: string
  name: string
}

export interface IngestionEvent {
  event: string
  detail: Record<string, unknown>
  created_at: string
}

/** One row of GET /api/documents. */
export interface DocumentListItem {
  id: number
  title: string | null
  summary: string | null
  kind: KindRef | null
  sender: SenderRef | null
  tags: TagRef[]
  projects: ProjectRef[]
  document_date: string | null
  language: DocumentLanguage
  status: DocumentStatus
  mime_type: string
  page_count: number | null
  created_at: string
  has_searchable_pdf: boolean
  has_thumbnail: boolean
  amount_total: string | null
  currency: string | null
  review_status: ReviewStatus
  /**
   * Only non-null with `?q=`. ts_headline fragments with <b>/</b> markers
   * over raw (NOT HTML-escaped) OCR text — render via `renderSnippet`,
   * never as-is.
   */
  snippet?: string | null
  rank?: number | null
}

/** Paginated body of GET /api/documents. */
export interface DocumentListResponse {
  items: DocumentListItem[]
  total: number
  limit: number
  offset: number
}

/** One finding from the extraction validation step. */
export interface ValidationFinding {
  rule: string
  /**
   * Storage field name, e.g. `amount_total`, `currency`, `kind_id`, `sender_id`.
   * Null for document-level rules (e.g. `ocr_confidence_gate`, `empty_extraction`,
   * `self_reported_low`) that are not tied to a single field.
   */
  field: string | null
  severity: 'warn' | 'error'
  message: string
}

/** Structured output from the validation step attached to a document. */
export interface ValidationResult {
  findings: ValidationFinding[]
}

/** Body of GET /api/documents/{id}. */
export interface DocumentDetail extends DocumentListItem {
  /** Human-readable topic phrases extracted for general/reference docs. */
  topics: string[]
  ocr_text: string | null
  ocr_confidence: number | null
  due_date: string | null
  expiry_date: string | null
  source: DocumentSource
  original_filename: string | null
  sha256: string
  extraction: Record<string, unknown> | null
  validation: ValidationResult | null
  user_edited_fields: string[]
  events: IngestionEvent[]
}

/** Query parameters of GET /api/documents — all filters AND-compose. */
export interface DocumentFilters {
  q?: string
  kind?: string
  sender_id?: number
  /** Single project slug; AND-composes with the other filters. */
  project?: string
  /** Repeatable: every slug must match (AND). */
  tag?: string[]
  language?: DocumentLanguage
  status?: DocumentStatus
  date_from?: string
  date_to?: string
  source?: DocumentSource
  review_status?: ReviewStatus
  limit?: number
  offset?: number
}

/**
 * PATCH /api/documents/{id} body. Only fields present in the object are
 * changed; `null` clears nullable fields; `tags` is a full-replacement
 * slug list (`[]` clears). Edit one field per request — the backend
 * locks every field it sees against re-extraction (docs/api.md §1.5).
 */
export interface DocumentUpdate {
  title?: string | null
  summary?: string | null
  document_date?: string | null
  kind_slug?: string | null
  /** Sender name; upserted case-insensitively. */
  sender?: string | null
  tags?: string[]
  /**
   * Full-replacement list of project names-or-slugs (`[]` clears). Unknown
   * names are upserted by the backend, so free text creates a new project.
   */
  projects?: string[]
  /** Full-replacement list of human-readable topic phrases (`[]` clears). */
  topics?: string[]
  language?: DocumentLanguage
  /** Decimal as string to preserve precision. */
  amount_total?: string | null
  currency?: string | null
  due_date?: string | null
  expiry_date?: string | null
}

/** 202 body of POST /api/documents/{id}/extract. */
export interface ExtractionQueued {
  queued: boolean
  job_id: number
}

/** Body of POST /api/documents (201 created, 200 duplicate). */
export interface UploadResult {
  id: number
  sha256: string
  status: DocumentStatus
  duplicate: boolean
}

/**
 * One entry of GET /api/jobs: a Procrastinate job enriched with the pipeline
 * state of the document it processes. The `document_*` / cost fields are null
 * for jobs without a document (e.g. the periodic email poll) or whose document
 * has since been deleted.
 */
export interface JobInfo {
  id: number
  status: string
  task_name: string
  attempts: number
  scheduled_at: string | null
  started_at: string | null
  finished_at: string | null
  document_id: number | null
  active: boolean
  document_title: string | null
  document_status: string | null
  error: string | null
  cost_usd: number | null
  tokens: number | null
}

export const DOCUMENT_LANGUAGES: readonly { value: DocumentLanguage; text: string }[] = [
  { value: 'nld', text: 'Dutch' },
  { value: 'eng', text: 'English' },
  { value: 'mixed', text: 'Mixed' },
  { value: 'unknown', text: 'Unknown' },
] as const

export const DOCUMENT_STATUSES: readonly { value: DocumentStatus; text: string }[] = [
  { value: 'received', text: 'Received' },
  { value: 'ocr', text: 'OCR' },
  { value: 'extract', text: 'Extracting' },
  { value: 'indexed', text: 'Indexed' },
  { value: 'failed', text: 'Failed' },
] as const

/**
 * Serialise filters to a query string. Built by hand (not apiFetch's
 * `query` option) because `tag` repeats: ?tag=a&tag=b ANDs both.
 */
export function documentQueryString(filters: DocumentFilters): string {
  const params = new URLSearchParams()
  const { tag, ...scalars } = filters
  for (const [key, value] of Object.entries(scalars)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  for (const slug of tag ?? []) params.append('tag', slug)
  return params.toString()
}

/** GET /api/documents — list, filter, full-text search. */
export function listDocuments(
  filters: DocumentFilters = {},
  signal?: AbortSignal,
): Promise<DocumentListResponse> {
  const qs = documentQueryString(filters)
  return apiFetch<DocumentListResponse>(`/api/documents${qs ? `?${qs}` : ''}`, { signal })
}

/** GET /api/documents/{id} — full detail. */
export function getDocument(id: number, signal?: AbortSignal): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/documents/${id}`, { signal })
}

/** PATCH /api/documents/{id} — partial metadata edit; returns the new detail. */
export function updateDocument(id: number, patch: DocumentUpdate): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/documents/${id}`, { method: 'PATCH', body: patch })
}

/** DELETE /api/documents/{id} — soft delete (204). */
export function deleteDocument(id: number): Promise<void> {
  return apiFetch<void>(`/api/documents/${id}`, { method: 'DELETE' })
}

/** POST /api/documents/{id}/extract — queue metadata re-extraction (202). */
export function requestExtraction(id: number): Promise<ExtractionQueued> {
  return apiFetch<ExtractionQueued>(`/api/documents/${id}/extract`, { method: 'POST' })
}

/** POST /api/documents/{id}/verify — mark document as verified; returns updated detail. */
export function verifyDocument(id: number): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/documents/${id}/verify`, { method: 'POST' })
}

/** Options for {@link listJobs}. All optional; an empty call is the default view. */
export interface ListJobsOptions {
  limit?: number
  /** Include document-less system/periodic rows (the email poll) that succeeded. */
  includeSystem?: boolean
  /**
   * History mode: every job for this one document (uncollapsed), newest first,
   * so a document's full processing history can be traced. System rows are not
   * relevant in this mode.
   */
  documentId?: number
  /** Fully-qualified task name to filter to (implies system rows are shown). */
  taskName?: string
}

/**
 * GET /api/jobs — recent background jobs, newest first. Document-less
 * system/periodic jobs (the email poll) are hidden unless they failed or are
 * running; pass `includeSystem` to list them all. `documentId` switches to
 * uncollapsed history mode; `taskName` filters to one task type.
 */
export function listJobs(options: ListJobsOptions = {}): Promise<JobInfo[]> {
  const { limit, includeSystem = false, documentId, taskName } = options
  return apiFetch<JobInfo[]>('/api/jobs', {
    query: {
      limit,
      include_system: includeSystem || undefined,
      document_id: documentId,
      task_name: taskName,
    },
  })
}

/** GET /api/jobs/task-names — distinct task names, for the task-type filter. */
export function listJobTaskNames(): Promise<string[]> {
  return apiFetch<string[]>('/api/jobs/task-names')
}

/** One page returned by GET /api/documents/{id}/markdown. */
export interface DocumentMarkdownPage {
  page_number: number
  markdown: string
}

/** Body of GET /api/documents/{id}/markdown. */
export interface DocumentMarkdownResponse {
  page_count: number
  pages: DocumentMarkdownPage[]
}

/** GET /api/documents/{id}/markdown — assembled per-page markdown. */
export function fetchDocumentMarkdown(id: number): Promise<DocumentMarkdownResponse> {
  return apiFetch<DocumentMarkdownResponse>(`/api/documents/${id}/markdown`)
}

/** URL of a document's first-page thumbnail (404 until generated). */
export function thumbnailUrl(id: number): string {
  return `/api/documents/${id}/thumbnail`
}

/**
 * Options for the file URL helpers. `inline: true` appends
 * `?disposition=inline` so the browser renders the file (detail-page
 * iframe/img previews); the default is the backend's
 * `Content-Disposition: attachment`, which downloads — an attachment
 * response inside an iframe/img shows nothing and triggers a download.
 */
export interface FileUrlOptions {
  inline?: boolean
}

function fileUrl(path: string, options?: FileUrlOptions): string {
  return options?.inline ? `${path}?disposition=inline` : path
}

/** URL of the stored original (attachment by default; see FileUrlOptions). */
export function originalUrl(id: number, options?: FileUrlOptions): string {
  return fileUrl(`/api/documents/${id}/original`, options)
}

/** URL of the OCR searchable PDF (404 when the document has none). */
export function searchablePdfUrl(id: number, options?: FileUrlOptions): string {
  return fileUrl(`/api/documents/${id}/searchable.pdf`, options)
}

/**
 * POST /api/documents — multipart upload via XMLHttpRequest so the caller
 * gets upload progress (fetch has no upload progress events).
 *
 * Resolves for 201 (new) and 200 (`duplicate: true`, the body points at
 * the existing document). Rejects with `ApiError` for everything else —
 * 409 deleted duplicate, 413 too large, 415 unsupported type — and with
 * `ApiError(0, …)` on network failure.
 */
export function uploadDocument(
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<UploadResult> {
  return new Promise<UploadResult>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/documents')
    xhr.setRequestHeader('Accept', 'application/json')
    const csrf = getCookie(CSRF_COOKIE)
    if (csrf) xhr.setRequestHeader(CSRF_HEADER, csrf)

    xhr.upload.addEventListener('progress', (event: ProgressEvent) => {
      if (event.lengthComputable && event.total > 0) onProgress?.(event.loaded / event.total)
    })
    xhr.addEventListener('load', () => {
      if (xhr.status === 200 || xhr.status === 201) {
        onProgress?.(1)
        resolve(JSON.parse(xhr.responseText) as UploadResult)
        return
      }
      reject(new ApiError(xhr.status, parseDetail(xhr.responseText, xhr.status)))
    })
    xhr.addEventListener('error', () => reject(new ApiError(0, 'network error')))
    xhr.addEventListener('abort', () => reject(new ApiError(0, 'upload aborted')))

    const form = new FormData()
    form.append('file', file, file.name)
    xhr.send(form)
  })
}

/** One point on a series trend; carries enough metadata for a citation link. */
export interface SeriesPoint {
  date: string
  amount: string
  document_id: number
  title?: string | null
}

/** Body of GET /api/documents/{id}/series (optional blocks omitted when N/A). */
export interface DocumentSeries {
  status: 'ok' | 'insufficient'
  sender: string | null
  kind: string | null
  sender_id?: number | null
  kind_id?: number | null
  currency: string | null
  other_currencies: string[]
  cadence: 'monthly' | 'quarterly' | 'yearly' | 'irregular'
  count: number
  document_ids: number[]
  /** Cached LLM prose summary of the series; absent until precomputed. */
  description?: string
  mean?: string
  median?: string
  stdev?: string
  min?: string
  max?: string
  reference?: {
    value: string
    delta: string
    vs_median_pct: string
    z_score: number | null
    verdict: 'higher' | 'typical' | 'lower'
  }
  trend?: { direction: 'rising' | 'falling' | 'flat'; change_pct: string }
  year_over_year?: { prior_value: string; change_pct: string; document_id: number }
  points?: SeriesPoint[]
}

/** GET /api/documents/{id}/series — recurring-series stats + comparison. */
export function fetchDocumentSeries(id: number, signal?: AbortSignal): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/documents/${id}/series`, { signal })
}

/** Body of GET /api/charts — every eligible series, summarised for charting. */
export interface ChartsResponse {
  series: DocumentSeries[]
}

/** GET /api/charts — all chartable (sender, kind) series. */
export function fetchCharts(signal?: AbortSignal): Promise<ChartsResponse> {
  return apiFetch<ChartsResponse>('/api/charts', { signal })
}

function parseDetail(text: string, status: number): string {
  try {
    const data: unknown = JSON.parse(text)
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail
      return typeof detail === 'string' ? detail : JSON.stringify(detail)
    }
  } catch {
    // not JSON
  }
  return `HTTP ${status}`
}
