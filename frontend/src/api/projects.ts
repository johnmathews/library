/**
 * Typed API for the projects list endpoints.
 *
 * Projects group documents across kinds/senders (e.g. "House purchase").
 * These feed the list view's filter facet and the detail view's edit input.
 */

import { apiFetch } from './client'

export interface ProjectOption {
  slug: string
  name: string
  document_count: number
  /** Present on GET /api/projects; optional so the taxonomy cache and test
   * fixtures can keep constructing the lean {slug, name, document_count}. */
  description?: string | null
  archived?: boolean
}

/** GET /api/projects — all projects, with document counts. */
export function listProjects(
  includeArchived = false,
  signal?: AbortSignal,
): Promise<ProjectOption[]> {
  const qs = includeArchived ? '?include_archived=true' : ''
  return apiFetch<ProjectOption[]>(`/api/projects${qs}`, { signal })
}

/** POST /api/projects — create a project by name (admin only). */
export function createProject(name: string, description?: string): Promise<ProjectOption> {
  return apiFetch<ProjectOption>('/api/projects', {
    method: 'POST',
    body: { name, description: description || null },
  })
}

/** PATCH /api/projects/{slug} — rename, edit description, or (un)archive (admin only). */
export function updateProject(
  slug: string,
  patch: { name?: string; description?: string | null; archived?: boolean },
): Promise<ProjectOption> {
  return apiFetch<ProjectOption>(`/api/projects/${slug}`, { method: 'PATCH', body: patch })
}

/** DELETE /api/projects/{slug} — hard delete; memberships cascade (admin only). */
export function deleteProject(slug: string): Promise<void> {
  return apiFetch<void>(`/api/projects/${slug}`, { method: 'DELETE' })
}
