/**
 * Typed API for the natural-language Ask endpoint (see docs/ask.md).
 *
 * Types mirror the backend Pydantic models in src/library/api/ask.py.
 * `cost_usd` is a JSON number (the answer's estimated model cost).
 */

import { apiFetch } from './client'

/** One source document the answer is grounded in. */
export interface AskCitation {
  document_id: number
  title: string | null
  page_number: number | null
}

/** Body of POST /api/ask. */
export interface AskResponse {
  answer: string
  citations: AskCitation[]
  used_tools: string[]
  cost_usd: number
  thread_id: number
}

/** Summary row returned by GET /api/ask/threads. */
export interface ThreadSummary {
  id: number
  title: string
  created_at: string
  updated_at: string
  turn_count: number
  total_cost_usd: number
}

/** A single question/answer turn within a thread. */
export interface TurnView {
  id: number
  query: string
  answer: string
  citations: AskCitation[]
  used_tools: string[]
  cost_usd: number
  created_at: string
}

/** Full thread returned by GET /api/ask/threads/{id}. */
export interface ThreadDetail {
  id: number
  title: string
  turns: TurnView[]
}

/**
 * POST /api/ask — ask a natural-language question about the archive and get
 * a cited answer. `question` must be 1..1000 chars (validated server-side;
 * 422 on violation). Pass `threadId` to continue an existing conversation
 * thread. Rejects with `ApiError` carrying `.detail` for the
 * 503 "no API key configured" case and any other non-2xx response.
 */
/** A base64 image attached to a question (no `data:` prefix). */
export interface AskImage {
  media_type: 'image/png' | 'image/jpeg' | 'image/gif' | 'image/webp'
  data: string
}

export function askQuestion(
  question: string,
  threadId?: number,
  signal?: AbortSignal,
  images?: AskImage[],
): Promise<AskResponse> {
  const body: { question: string; thread_id?: number; images?: AskImage[] } = { question }
  if (threadId !== undefined) body.thread_id = threadId
  if (images && images.length) body.images = images
  return apiFetch<AskResponse>('/api/ask', { method: 'POST', body, signal })
}

/** GET /api/ask/threads — list all conversation threads. */
export function listThreads(): Promise<ThreadSummary[]> {
  return apiFetch<ThreadSummary[]>('/api/ask/threads')
}

/** GET /api/ask/threads/{id} — fetch a thread with all its turns. */
export function getThread(id: number): Promise<ThreadDetail> {
  return apiFetch<ThreadDetail>(`/api/ask/threads/${id}`)
}

/**
 * PATCH /api/ask/threads/{id} — rename a thread. `title` must be 1..120 chars
 * (validated server-side; 422 on violation, 404 if the thread isn't yours).
 * Resolves with the updated summary row.
 */
export function renameThread(id: number, title: string): Promise<ThreadSummary> {
  return apiFetch<ThreadSummary>(`/api/ask/threads/${id}`, { method: 'PATCH', body: { title } })
}

/** DELETE /api/ask/threads/{id} — permanently delete a thread. */
export function deleteThread(id: number): Promise<void> {
  return apiFetch<void>(`/api/ask/threads/${id}`, { method: 'DELETE' })
}
