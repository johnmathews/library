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

/** Body of GET /api/documents/{id}. */
export interface DocumentDetail extends DocumentListItem {
  ocr_text: string | null
  ocr_confidence: number | null
  due_date: string | null
  expiry_date: string | null
  source: DocumentSource
  original_filename: string | null
  sha256: string
  extraction: Record<string, unknown> | null
  user_edited_fields: string[]
  events: IngestionEvent[]
}

/** Query parameters of GET /api/documents — all filters AND-compose. */
export interface DocumentFilters {
  q?: string
  kind?: string
  sender_id?: number
  /** Repeatable: every slug must match (AND). */
  tag?: string[]
  language?: DocumentLanguage
  status?: DocumentStatus
  date_from?: string
  date_to?: string
  source?: DocumentSource
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

/** One entry of GET /api/jobs. */
export interface JobInfo {
  id: number
  status: string
  task_name: string
  attempts: number
  scheduled_at: string | null
  document_id: number | null
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

/** GET /api/jobs — recent background jobs, newest first. */
export function listJobs(limit?: number): Promise<JobInfo[]> {
  return apiFetch<JobInfo[]>('/api/jobs', { query: { limit } })
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
