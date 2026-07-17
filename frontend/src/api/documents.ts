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
export type DocumentSource = 'upload' | 'consume' | 'email' | 'api' | 'mcp' | 'import' | 'note'
export type ReviewStatus = 'verified' | 'needs_review' | 'unreviewed'

export interface KindRef {
  slug: string
  name: string
}

export interface SenderRef {
  id: number
  name: string
}

export interface RecipientRef {
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

export interface MatterRef {
  slug: string
  name: string
}

export interface IngestionEvent {
  event: string
  detail: Record<string, unknown>
  created_at: string
}

/**
 * Compact validation finding carried on list rows (and the base of the fuller
 * detail `ValidationFinding`). Explains *why* a document needs review.
 */
export interface ValidationFindingSummary {
  rule: string
  /**
   * Storage field name (e.g. `document_date`, `amount_total`), or null for
   * document-level rules (`ocr_confidence_gate`, `empty_extraction`, …).
   */
  field: string | null
  message: string
}

/** One row of GET /api/documents. */
export interface DocumentListItem {
  id: number
  title: string | null
  summary: string | null
  kind: KindRef | null
  sender: SenderRef | null
  recipient: RecipientRef | null
  tags: TagRef[]
  projects: ProjectRef[]
  matters: MatterRef[]
  document_date: string | null
  due_date: string | null
  expiry_date: string | null
  language: DocumentLanguage
  status: DocumentStatus
  mime_type: string
  page_count: number | null
  /** Library ingest time ("Added date"). Always present. */
  created_at: string
  /** Last metadata edit ("Last edited"). Always present. */
  updated_at: string
  has_searchable_pdf: boolean
  has_thumbnail: boolean
  amount_total: string | null
  currency: string | null
  review_status: ReviewStatus
  /**
   * Compact reasons a document needs review (populated only when
   * review_status is `needs_review`; `[]` otherwise). See {@link resolveReviewReason}.
   */
  review_findings: ValidationFindingSummary[]
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

/**
 * One row of GET /api/documents/deleted: a normal list item plus the
 * soft-delete lifecycle fields. `deleted_at`/`purge_at` are ISO datetimes;
 * `days_remaining` counts down to the automatic permanent purge.
 */
export interface DeletedDocumentItem extends DocumentListItem {
  deleted_at: string
  purge_at: string
  days_remaining: number
}

/** Paginated body of GET /api/documents/deleted (adds the retention window). */
export interface DeletedDocumentListResponse {
  items: DeletedDocumentItem[]
  total: number
  limit: number
  offset: number
  retention_days: number
}

/** One finding from the extraction validation step (detail: adds severity). */
export interface ValidationFinding extends ValidationFindingSummary {
  severity: 'warn' | 'error'
}

/** Structured output from the validation step attached to a document. */
export interface ValidationResult {
  findings: ValidationFinding[]
}

/** One comment on a document (backend `CommentOut`). */
export interface DocumentComment {
  id: number
  document_id: number
  author_id: number | null
  body: string
  created_at: string
}

/** Body of GET /api/documents/{id}. */
export interface DocumentDetail extends DocumentListItem {
  /** Human-readable topic phrases extracted for general/reference docs. */
  topics: string[]
  /**
   * When the document was soft-deleted, or null if live. Non-null only when
   * fetched with `includeDeleted` (the Recently-Deleted read path).
   */
  deleted_at: string | null
  ocr_text: string | null
  ocr_confidence: number | null
  due_date: string | null
  expiry_date: string | null
  /** Last time the document was edited; bumps on any change, including tags/projects. */
  updated_at: string
  source: DocumentSource
  original_filename: string | null
  sha256: string
  extraction: Record<string, unknown> | null
  validation: ValidationResult | null
  user_edited_fields: string[]
  events: IngestionEvent[]
  comments: DocumentComment[]
}

/** Query parameters of GET /api/documents — all filters AND-compose. */
export interface DocumentFilters {
  q?: string
  kind?: string
  sender_id?: number
  recipient_id?: number
  /** Repeatable: a document in any of these project slugs matches (OR). */
  project?: string[]
  /** Repeatable: a document in any of these matter slugs matches (OR). */
  matter?: string[]
  /** Repeatable: every slug must match (AND). */
  tag?: string[]
  language?: DocumentLanguage
  status?: DocumentStatus
  date_from?: string
  date_to?: string
  source?: DocumentSource
  review_status?: ReviewStatus
  /** Non-search order field; ignored by the backend when `q` is set. */
  sort?: 'document_date' | 'added_date'
  /** Direction for `sort`; ignored when `q` is set. */
  direction?: 'asc' | 'desc'
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
  /** Recipient name; upserted case-insensitively. */
  recipient?: string | null
  tags?: string[]
  /**
   * Full-replacement list of project names-or-slugs (`[]` clears). Unknown
   * names are upserted by the backend, so free text creates a new project.
   */
  projects?: string[]
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
 * `query` option) because `tag`, `project` and `matter` repeat: ?tag=a&tag=b
 * ANDs both; ?project=a&project=b and ?matter=a&matter=b OR both.
 */
export function documentQueryString(filters: DocumentFilters): string {
  const params = new URLSearchParams()
  const { tag, project, matter, ...scalars } = filters
  for (const [key, value] of Object.entries(scalars)) {
    if (value !== undefined && value !== '') params.set(key, String(value))
  }
  for (const slug of tag ?? []) params.append('tag', slug)
  for (const slug of project ?? []) params.append('project', slug)
  for (const slug of matter ?? []) params.append('matter', slug)
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

/**
 * GET /api/documents/{id} — full detail.
 *
 * `includeDeleted` opts into fetching a soft-deleted document (returns it with
 * `deleted_at` set instead of 404ing) so the Recently-Deleted view can open a
 * trashed document read-only. Off by default, matching every other read path.
 */
export function getDocument(
  id: number,
  opts: { includeDeleted?: boolean; signal?: AbortSignal } = {},
): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/documents/${id}`, {
    query: opts.includeDeleted ? { include_deleted: true } : undefined,
    signal: opts.signal,
  })
}

/** PATCH /api/documents/{id} — partial metadata edit; returns the new detail. */
export function updateDocument(id: number, patch: DocumentUpdate): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/documents/${id}`, { method: 'PATCH', body: patch })
}

/** DELETE /api/documents/{id} — soft delete (204). */
export function deleteDocument(id: number): Promise<void> {
  return apiFetch<void>(`/api/documents/${id}`, { method: 'DELETE' })
}

/**
 * GET /api/documents/deleted — the "Recently Deleted" holding area: soft-deleted
 * documents awaiting permanent purge, each carrying its deletion lifecycle.
 */
export function listDeletedDocuments(
  params: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<DeletedDocumentListResponse> {
  return apiFetch<DeletedDocumentListResponse>('/api/documents/deleted', {
    query: { limit: params.limit, offset: params.offset },
    signal,
  })
}

/** POST /api/documents/{id}/restore — undo a soft delete; returns the detail. */
export function restoreDocument(id: number): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/documents/${id}/restore`, { method: 'POST' })
}

/**
 * DELETE /api/documents/{id}/permanent — hard-delete a soft-deleted document
 * (204). Only valid for a document currently in the trash; the row and its
 * files are removed for good. 404s a live or unknown document.
 */
export function permanentlyDeleteDocument(id: number): Promise<void> {
  return apiFetch<void>(`/api/documents/${id}/permanent`, { method: 'DELETE' })
}

/** POST /api/documents/{id}/extract — queue metadata re-extraction (202). */
export function requestExtraction(id: number): Promise<ExtractionQueued> {
  return apiFetch<ExtractionQueued>(`/api/documents/${id}/extract`, { method: 'POST' })
}

/** POST /api/documents/{id}/verify — mark document as verified; returns updated detail. */
export function verifyDocument(id: number): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/documents/${id}/verify`, { method: 'POST' })
}

/** GET /api/documents/{id}/comments — a document's comments. */
export function listComments(documentId: number, signal?: AbortSignal): Promise<DocumentComment[]> {
  return apiFetch<DocumentComment[]>(`/api/documents/${documentId}/comments`, { signal })
}

/** POST /api/documents/{id}/comments — add a comment (201); returns the created comment. */
export function createComment(documentId: number, body: string): Promise<DocumentComment> {
  return apiFetch<DocumentComment>(`/api/documents/${documentId}/comments`, {
    method: 'POST',
    body: { body },
  })
}

/** PATCH /api/documents/{id}/comments/{cid} — edit a comment's body; returns the updated comment. */
export function updateComment(
  documentId: number,
  commentId: number,
  body: string,
): Promise<DocumentComment> {
  return apiFetch<DocumentComment>(`/api/documents/${documentId}/comments/${commentId}`, {
    method: 'PATCH',
    body: { body },
  })
}

/** DELETE /api/documents/{id}/comments/{cid} — remove a comment (204). */
export function deleteComment(documentId: number, commentId: number): Promise<void> {
  return apiFetch<void>(`/api/documents/${documentId}/comments/${commentId}`, {
    method: 'DELETE',
  })
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

/**
 * GET /api/documents/{id}/markdown — assembled per-page markdown.
 *
 * `includeDeleted` renders a soft-deleted document's text (for the read-only
 * Recently-Deleted detail view); off by default, matching `getDocument`.
 */
export function fetchDocumentMarkdown(
  id: number,
  opts: { includeDeleted?: boolean } = {},
): Promise<DocumentMarkdownResponse> {
  return apiFetch<DocumentMarkdownResponse>(`/api/documents/${id}/markdown`, {
    query: opts.includeDeleted ? { include_deleted: true } : undefined,
  })
}

/**
 * Options for the file URL helpers. `inline: true` appends
 * `?disposition=inline` so the browser renders the file (detail-page
 * iframe/img previews); the default is the backend's
 * `Content-Disposition: attachment`, which downloads — an attachment
 * response inside an iframe/img shows nothing and triggers a download.
 * `includeDeleted: true` appends `include_deleted=true` so a soft-deleted
 * document's preview/download still resolves in the read-only trash view.
 */
export interface FileUrlOptions {
  inline?: boolean
  includeDeleted?: boolean
}

function fileUrl(path: string, options?: FileUrlOptions): string {
  const params = new URLSearchParams()
  if (options?.inline) params.set('disposition', 'inline')
  if (options?.includeDeleted) params.set('include_deleted', 'true')
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

/** URL of a document's first-page thumbnail (404 until generated). */
export function thumbnailUrl(id: number, options?: FileUrlOptions): string {
  return fileUrl(`/api/documents/${id}/thumbnail`, options)
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
  /** Present only for authored (user-curated) series (W14). When set the tile
   *  edits this series via the authored endpoints (PATCH) rather than the
   *  emergent meta-override endpoint. */
  authored_id?: number | null
  currency: string | null
  other_currencies: string[]
  cadence: 'monthly' | 'quarterly' | 'yearly' | 'irregular'
  count: number
  document_ids: number[]
  /** User title override (SeriesMetaOverride); absent unless set. The chart tile
   *  prefers it over the derived `sender · cadence series` heading. */
  title?: string | null
  /** Cached LLM prose summary, or a user description override when set. */
  description?: string | null
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
  /** Authored series only: the dominant (sender, kind, currency) signature of the
   *  current membership, or null for an empty series. Drives the smart features. */
  signature?: SeriesSignature | null
  /** Authored series only: how many non-member documents match the signature and
   *  are awaiting review (propose-for-review auto-continue). */
  suggestion_count?: number
  /** Authored series only: how many current members break the signature. */
  odd_one_out_count?: number
}

/** The mechanical identity of an authored series (backend `SeriesSignature`). */
export interface SeriesSignature {
  sender_id: number | null
  kind_id: number | null
  currency: string | null
  member_count: number
  dominant_count: number
  dominance: number
}

/** GET /api/documents/{id}/series — recurring-series stats + comparison. */
export function fetchDocumentSeries(id: number, signal?: AbortSignal): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/documents/${id}/series`, { signal })
}

/** Result of a series-membership toggle (POST/DELETE …/members). */
export interface SeriesMemberResult {
  state: 'pinned' | 'excluded' | 'cleared'
  sender_id: number
  kind_id: number
  currency: string | null
  document_id: number
}

/**
 * POST /api/series/{senderId}/{kindId}/members — add a document to a series
 * (clears an existing exclude, else pins). `currency` is the series bucket.
 */
export function addSeriesMember(
  senderId: number,
  kindId: number,
  documentId: number,
  currency?: string | null,
): Promise<SeriesMemberResult> {
  return apiFetch<SeriesMemberResult>(`/api/series/${senderId}/${kindId}/members`, {
    method: 'POST',
    body: { document_id: documentId },
    query: { currency: currency ?? undefined },
  })
}

/**
 * DELETE /api/series/{senderId}/{kindId}/members/{documentId} — remove a
 * document from a series (clears an existing pin, else excludes).
 */
export function removeSeriesMember(
  senderId: number,
  kindId: number,
  documentId: number,
  currency?: string | null,
): Promise<SeriesMemberResult> {
  return apiFetch<SeriesMemberResult>(
    `/api/series/${senderId}/${kindId}/members/${documentId}`,
    { method: 'DELETE', query: { currency: currency ?? undefined } },
  )
}

/** A near-threshold emergent bucket: a `(sender, kind, currency)` group with
 *  `2 ≤ docs < series_min_documents`. Not yet a chart; one more matching
 *  document promotes it. The `/charts` view can reveal these on demand and
 *  "promote" one into an authored series right away. */
export interface CandidateSeries {
  sender_id: number
  sender: string
  kind_id: number
  /** The document kind's slug (e.g. `invoice`), as on a charted series entry. */
  kind: string
  currency: string | null
  /** How many amount-bearing documents the bucket has so far (≥ 2, < needed). */
  count: number
  /** The threshold (`series_min_documents`) the bucket must reach to chart. */
  needed: number
  document_ids: number[]
}

/** Body of GET /api/charts — every eligible series plus near-threshold candidates. */
export interface ChartsResponse {
  series: DocumentSeries[]
  /** Emergent buckets one or more documents short of charting (`2 ≤ docs < min`). */
  candidates: CandidateSeries[]
}

/** GET /api/charts — all chartable (sender, kind) series. */
export function fetchCharts(signal?: AbortSignal): Promise<ChartsResponse> {
  return apiFetch<ChartsResponse>('/api/charts', { signal })
}

/**
 * The stable, URL-safe id for a series identity: `{sender}-{kind}-{currency}`,
 * with `none` for the NULL-currency bucket. Mirrors the backend's
 * `encode_series_id`, so it round-trips through `/api/charts/{seriesId}`.
 */
export function seriesId(s: Pick<DocumentSeries, 'sender_id' | 'kind_id' | 'currency'>): string {
  return `${s.sender_id}-${s.kind_id}-${s.currency ?? 'none'}`
}

/** GET /api/charts/{seriesId} — one series by its stable id (single-chart page). */
export function fetchChart(id: string, signal?: AbortSignal): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/charts/${id}`, { signal })
}

/** Body of PUT /api/charts/{seriesId}/meta (omit a field to leave it unchanged). */
export interface SeriesMetaUpdate {
  title?: string | null
  description?: string | null
}

/**
 * PUT /api/charts/{seriesId}/meta — override a series' title and/or description.
 * Returns the refreshed single-series body.
 */
export function updateSeriesMeta(id: string, body: SeriesMetaUpdate): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/charts/${id}/meta`, { method: 'PUT', body })
}

/** The stable, URL-safe id for an authored (user-curated) series: `a-{id}`. */
export function authoredSeriesId(id: number): string {
  return `a-${id}`
}

/** Body of POST /api/charts/authored. */
export interface AuthoredSeriesCreate {
  name: string
  currency?: string | null
  description?: string | null
  document_ids?: number[]
}

/**
 * POST /api/charts/authored — create an authored (manual) series, optionally
 * seeding its membership. Returns the series summarised like one /api/charts entry.
 */
export function createAuthoredSeries(body: AuthoredSeriesCreate): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>('/api/charts/authored', { method: 'POST', body })
}

/** Body of PATCH /api/charts/authored/{id} (omit a field to leave it unchanged). */
export interface AuthoredSeriesUpdate {
  name?: string
  description?: string | null
}

/** PATCH /api/charts/authored/{id} — rename / re-describe an authored series. */
export function updateAuthoredSeries(
  id: number,
  body: AuthoredSeriesUpdate,
): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/charts/authored/${id}`, { method: 'PATCH', body })
}

/** DELETE /api/charts/authored/{id} — delete an authored series (204). */
export function deleteAuthoredSeries(id: number): Promise<void> {
  return apiFetch<void>(`/api/charts/authored/${id}`, { method: 'DELETE' })
}

/** POST /api/charts/authored/{id}/members — add a document (idempotent). */
export function addAuthoredMember(id: number, documentId: number): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/charts/authored/${id}/members`, {
    method: 'POST',
    body: { document_id: documentId },
  })
}

/** DELETE /api/charts/authored/{id}/members/{documentId} — remove a document. */
export function removeAuthoredMember(id: number, documentId: number): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/charts/authored/${id}/members/${documentId}`, {
    method: 'DELETE',
  })
}

// --- Authored-series smart features: suggestions & odd-ones-out --------------

/** One candidate document proposed for an authored series (GET …/suggestions). */
export interface SeriesSuggestion {
  id: number
  title: string | null
  sender: string | null
  kind: string | null
  currency: string | null
  document_date: string | null
  amount: string
}

/** A member that breaks the signature, with a one-sentence reason (…/odd-ones-out). */
export interface SeriesOddOneOut extends SeriesSuggestion {
  /** The first differing axis: 'sender' | 'kind' | 'currency'. */
  axis: string
  /** LLM-generated rationale, or null when extraction is disabled. */
  reason: string | null
}

/** GET /api/charts/authored/{id}/suggestions — docs matching the signature. */
export function fetchAuthoredSuggestions(
  id: number,
  signal?: AbortSignal,
): Promise<{ suggestions: SeriesSuggestion[]; count: number }> {
  return apiFetch(`/api/charts/authored/${id}/suggestions`, { signal })
}

/** POST …/suggestions/{documentId}/accept — add the doc; returns refreshed series. */
export function acceptAuthoredSuggestion(id: number, documentId: number): Promise<DocumentSeries> {
  return apiFetch<DocumentSeries>(`/api/charts/authored/${id}/suggestions/${documentId}/accept`, {
    method: 'POST',
  })
}

/** POST …/suggestions/{documentId}/dismiss — tombstone; returns remaining count. */
export function dismissAuthoredSuggestion(
  id: number,
  documentId: number,
): Promise<{ count: number }> {
  return apiFetch(`/api/charts/authored/${id}/suggestions/${documentId}/dismiss`, {
    method: 'POST',
  })
}

/** GET …/odd-ones-out — members that break the signature (lazy: may trigger LLM). */
export function fetchAuthoredOddOnesOut(
  id: number,
  signal?: AbortSignal,
): Promise<{ members: SeriesOddOneOut[] }> {
  return apiFetch(`/api/charts/authored/${id}/odd-ones-out`, { signal })
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
