/**
 * Typed API for the controlled facet vocabulary (docs/facets.md).
 *
 * A facet is a named dimension; a document carries at most one value per facet.
 * Values are a closed set: the API rejects a label naming a value that does not
 * exist rather than creating it.
 */

import { apiFetch } from './client'

export interface FacetValueRef {
  key: string
  label: string
  parent_id: number | null
  aliases: string[]
}

export interface FacetRef {
  key: string
  label: string
  ordinal: number
  values: FacetValueRef[]
}

/** GET /api/facets — the full controlled vocabulary, ordinal-ordered. */
export async function fetchFacets(): Promise<FacetRef[]> {
  const body = await apiFetch<{ facets: FacetRef[] }>('/api/facets')
  return body.facets
}

/** GET /api/documents/{id}/labels — this document's facet_key -> value_key map. */
export async function fetchDocumentLabels(id: number): Promise<Record<string, string>> {
  const body = await apiFetch<{ labels: Record<string, string> }>(`/api/documents/${id}/labels`)
  return body.labels
}

/**
 * PUT /api/documents/{id}/labels — set/clear labels. A `null` value clears that
 * facet; a value key outside the vocabulary is rejected with a 422 rather than
 * created. Returns the resulting full label map.
 */
export async function updateDocumentLabels(
  id: number,
  labels: Record<string, string | null>,
): Promise<Record<string, string>> {
  const body = await apiFetch<{ labels: Record<string, string> }>(
    `/api/documents/${id}/labels`,
    { method: 'PUT', body: { labels } },
  )
  return body.labels
}

/** `facet=key:value` query pairs for a document-list request; AND-composes. */
export function facetQueryParams(selection: Record<string, string>): [string, string][] {
  return Object.entries(selection).map(([key, value]) => ['facet', `${key}:${value}`])
}
