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
}

/**
 * POST /api/ask — ask a natural-language question about the archive and get
 * a cited answer. `question` must be 1..1000 chars (validated server-side;
 * 422 on violation). Rejects with `ApiError` carrying `.detail` for the
 * 503 "no API key configured" case and any other non-2xx response.
 */
export function askQuestion(question: string, signal?: AbortSignal): Promise<AskResponse> {
  return apiFetch<AskResponse>('/api/ask', { method: 'POST', body: { question }, signal })
}
