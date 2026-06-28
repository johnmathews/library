/**
 * Typed API for notes — first-class markdown documents authored in-app.
 *
 * A note is a document with `source === 'note'` and `mime_type ===
 * 'text/markdown'`; its body lives in the markdown reader. These endpoints
 * create and edit a note's title/body and expose its version history, each
 * returning the same `DocumentDetail` shape as the document endpoints.
 */

import { apiFetch } from './client'
import type { DocumentDetail } from './documents'

/** One entry of GET /api/notes/{id}/versions (newest-first). */
export interface NoteVersion {
  version_no: number
  title: string | null
  body: string
  created_at: string
}

/** POST /api/notes — create a note; returns the created document detail. */
export function createNote(note: {
  title: string
  body_markdown: string
}): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>('/api/notes', { method: 'POST', body: note })
}

/** PATCH /api/notes/{id} — edit a note's title and/or body; returns the detail. */
export function updateNote(
  id: number,
  patch: { title?: string; body_markdown?: string },
): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/notes/${id}`, { method: 'PATCH', body: patch })
}

/** GET /api/notes/{id}/versions — version history, newest-first. */
export function listNoteVersions(id: number, signal?: AbortSignal): Promise<NoteVersion[]> {
  return apiFetch<NoteVersion[]>(`/api/notes/${id}/versions`, { signal })
}

/** POST /api/notes/{id}/versions/{version_no}/restore — restore a version. */
export function restoreNoteVersion(id: number, versionNo: number): Promise<DocumentDetail> {
  return apiFetch<DocumentDetail>(`/api/notes/${id}/versions/${versionNo}/restore`, {
    method: 'POST',
  })
}
