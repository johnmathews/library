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
}

export interface TagOption {
  slug: string
  name: string
  document_count: number
}

/** GET /api/kinds — the seeded kind set, ordered by slug. */
export function listKinds(signal?: AbortSignal): Promise<KindOption[]> {
  return apiFetch<KindOption[]>('/api/kinds', { signal })
}

/** GET /api/senders — all known senders, ordered by name. */
export function listSenders(signal?: AbortSignal): Promise<SenderOption[]> {
  return apiFetch<SenderOption[]>('/api/senders', { signal })
}

/** GET /api/tags — all tags, ordered by name. */
export function listTags(signal?: AbortSignal): Promise<TagOption[]> {
  return apiFetch<TagOption[]>('/api/tags', { signal })
}
