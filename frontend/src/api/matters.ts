/**
 * Typed API for the business-matter list endpoints.
 *
 * Matters group documents across kinds/senders by the business concern they
 * belong to (e.g. "Acme merger"). They mirror projects: these feed the list
 * view's filter facet and button row, and the detail view's edit input.
 */

import { apiFetch } from './client'

export interface MatterOption {
  slug: string
  name: string
  document_count: number
  /** Present on GET /api/matters; optional so the taxonomy cache and test
   * fixtures can keep constructing the lean {slug, name, document_count}. */
  hint?: string | null
  archived?: boolean
}

/** GET /api/matters — all matters, with document counts. */
export function listMatters(
  includeArchived = false,
  signal?: AbortSignal,
): Promise<MatterOption[]> {
  const qs = includeArchived ? '?include_archived=true' : ''
  return apiFetch<MatterOption[]>(`/api/matters${qs}`, { signal })
}

/** POST /api/matters — create a matter by name (admin only). */
export function createMatter(name: string, hint?: string): Promise<MatterOption> {
  return apiFetch<MatterOption>('/api/matters', {
    method: 'POST',
    body: { name, hint: hint || null },
  })
}

/** PATCH /api/matters/{slug} — rename, edit hint, or (un)archive (admin only). */
export function updateMatter(
  slug: string,
  patch: { name?: string; hint?: string | null; archived?: boolean },
): Promise<MatterOption> {
  return apiFetch<MatterOption>(`/api/matters/${slug}`, { method: 'PATCH', body: patch })
}

/** DELETE /api/matters/{slug} — hard delete; memberships cascade (admin only). */
export function deleteMatter(slug: string): Promise<void> {
  return apiFetch<void>(`/api/matters/${slug}`, { method: 'DELETE' })
}
