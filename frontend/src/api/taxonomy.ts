/**
 * Typed API for the taxonomy list endpoints (docs/api.md §1.8.2):
 * kinds, senders, and tags with document counts. These feed the list
 * view's filter options and the detail view's edit inputs.
 */

import { apiFetch } from './client'

export interface KindOption {
  slug: string
  name: string
  document_count: number
}

export interface SenderOption {
  id: number
  name: string
  document_count: number
  colour: string | null
}

export interface RecipientOption {
  id: number
  name: string
  document_count: number
}

export interface TagOption {
  slug: string
  name: string
  document_count: number
}

/** A kind as returned by POST /api/kinds (created or deduped existing). */
export interface CreatedKind {
  slug: string
  name: string
}

/** GET /api/kinds — all known kinds (seeded set + any created), ordered by slug. */
export function listKinds(signal?: AbortSignal): Promise<KindOption[]> {
  return apiFetch<KindOption[]>('/api/kinds', { signal })
}

/**
 * POST /api/kinds — create a document kind from a display name.
 *
 * The backend slugifies + sentence-cases the name, dedupes an exact match
 * (returning the existing kind), and rejects a near-duplicate with a 409 whose
 * body carries `existing_slug`/`existing_name` (read off `ApiError.body`).
 */
export function createKind(name: string): Promise<CreatedKind> {
  return apiFetch<CreatedKind>('/api/kinds', { method: 'POST', body: { name } })
}

/** GET /api/senders — all known senders, ordered by name. */
export function listSenders(signal?: AbortSignal): Promise<SenderOption[]> {
  return apiFetch<SenderOption[]>('/api/senders', { signal })
}

/** GET /api/recipients — all known recipients, ordered by name. */
export function listRecipients(signal?: AbortSignal): Promise<RecipientOption[]> {
  return apiFetch<RecipientOption[]>('/api/recipients', { signal })
}

/** GET /api/tags — all tags, ordered by name. */
export function listTags(signal?: AbortSignal): Promise<TagOption[]> {
  return apiFetch<TagOption[]>('/api/tags', { signal })
}

/**
 * PATCH /api/senders/{id} — set or clear a sender's chart colour, and nothing
 * else: a sender's name comes from ingested documents and renaming one is an
 * admin taxonomy operation with its own merge semantics. `null` clears the
 * override; not calling this leaves it alone (see `setValueColour`).
 */
export function setSenderColour(id: number, colour: string | null): Promise<SenderOption> {
  return apiFetch<SenderOption>(`/api/senders/${id}`, { method: 'PATCH', body: { colour } })
}
