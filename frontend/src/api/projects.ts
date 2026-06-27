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
}

/** GET /api/projects — all projects, with document counts. */
export function listProjects(signal?: AbortSignal): Promise<ProjectOption[]> {
  return apiFetch<ProjectOption[]>('/api/projects', { signal })
}

/** POST /api/projects — create a project by name; returns the created project. */
export function createProject(name: string): Promise<ProjectOption> {
  return apiFetch<ProjectOption>('/api/projects', { method: 'POST', body: { name } })
}
