/**
 * Typed API for the saved-views endpoints.
 *
 * A saved view captures the homepage's filter/search state (the output of
 * `buildDocumentQuery`) under a name, so it can be re-applied in one click.
 * A pinned view also surfaces in the sidebar as a custom "dashboard". All
 * endpoints are per-user server-side (session-cookie auth via `apiFetch`).
 */

import { apiFetch } from './client'
import type { LocationQueryRaw } from 'vue-router'

/** The persisted homepage query — exactly what `buildDocumentQuery` returns. */
export type SavedViewFilterState = Record<string, string | string[]>

export interface SavedView {
  id: number
  name: string
  filter_state: SavedViewFilterState
  pinned: boolean
  sort_order: number
  created_at: string
  updated_at: string
}

export interface CreateSavedViewBody {
  name: string
  filter_state: LocationQueryRaw
  pinned?: boolean
}

export interface UpdateSavedViewBody {
  name?: string
  filter_state?: LocationQueryRaw
  pinned?: boolean
}

/** GET /api/saved-views — the caller's own views, ordered by sort_order. */
export function listSavedViews(signal?: AbortSignal): Promise<SavedView[]> {
  return apiFetch<SavedView[]>('/api/saved-views', { signal })
}

/** POST /api/saved-views — create a named view from the current filter state. */
export function createSavedView(body: CreateSavedViewBody): Promise<SavedView> {
  return apiFetch<SavedView>('/api/saved-views', { method: 'POST', body })
}

/** PATCH /api/saved-views/{id} — rename, re-capture filters, or (un)pin. */
export function updateSavedView(id: number, body: UpdateSavedViewBody): Promise<SavedView> {
  return apiFetch<SavedView>(`/api/saved-views/${id}`, { method: 'PATCH', body })
}

/** DELETE /api/saved-views/{id} — remove a view. */
export function deleteSavedView(id: number): Promise<void> {
  return apiFetch<void>(`/api/saved-views/${id}`, { method: 'DELETE' })
}

/**
 * POST /api/saved-views/reorder — persist a new order. `ids` must be EXACTLY
 * the caller's current view ids in the desired order (a mismatch is a 400).
 */
export function reorderSavedViews(ids: number[]): Promise<SavedView[]> {
  return apiFetch<SavedView[]>('/api/saved-views/reorder', { method: 'POST', body: { ids } })
}
