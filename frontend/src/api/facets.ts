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
  /** A stored override; null means the client derives a palette slot from `key`. */
  colour: string | null
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

/** A row of GET /api/facets/counts — money-scoped (see `LabelCount`). */
export interface FacetValueCount {
  facet_key: string
  value_key: string
  /** Documents with an amount, canonical, not deleted. NOT what delete enforces. */
  documents: number
  first_date: string | null
  last_date: string | null
}

/** A row of GET /api/facets/label-counts — what merge moves and delete blocks on. */
export interface LabelCount {
  facet_key: string
  value_key: string
  labelled: number
}

export interface FacetSuggestion {
  id: number
  facet: string
  suggested_label: string
  reason: string | null
  document_id: number
}

/** GET /api/facets/counts — documents *with money* per value, for chart proposals. */
export async function fetchFacetCounts(): Promise<FacetValueCount[]> {
  const body = await apiFetch<{ counts: FacetValueCount[] }>('/api/facets/counts')
  return body.counts
}

/** GET /api/facets/label-counts — documents *carrying* each value. */
export async function fetchLabelCounts(): Promise<LabelCount[]> {
  const body = await apiFetch<{ counts: LabelCount[] }>('/api/facets/label-counts')
  return body.counts
}

/** POST /api/facets — 409 when the key exists. */
export function createFacet(key: string, label: string, ordinal = 0): Promise<{ key: string }> {
  return apiFetch<{ key: string }>('/api/facets', { method: 'POST', body: { key, label, ordinal } })
}

/** POST /api/facets/{facet}/values — 404 unknown facet, 409 duplicate key. */
export function createValue(
  facetKey: string,
  key: string,
  label: string,
): Promise<{ key: string }> {
  return apiFetch<{ key: string }>(`/api/facets/${encodeURIComponent(facetKey)}/values`, {
    method: 'POST',
    body: { key, label },
  })
}

/**
 * PATCH .../values/{value} with ONLY `label`.
 *
 * Deliberately one field per call. The route tells "clear it" from "leave it
 * alone" by whether the key is present in the body, so a single patch function
 * taking an object would clear a colour every time a caller spread a form.
 * "Leave the colour alone" is expressed by calling this function rather than
 * `setValueColour`.
 */
export function renameValue(
  facetKey: string,
  valueKey: string,
  label: string,
): Promise<FacetValueRef> {
  return apiFetch<FacetValueRef>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}`,
    { method: 'PATCH', body: { label } },
  )
}

/** PATCH .../values/{value} with ONLY `colour`. `null` clears it (see `renameValue`). */
export function setValueColour(
  facetKey: string,
  valueKey: string,
  colour: string | null,
): Promise<FacetValueRef> {
  return apiFetch<FacetValueRef>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}`,
    { method: 'PATCH', body: { colour } },
  )
}

/** POST .../aliases — idempotent server-side (ON CONFLICT DO NOTHING). */
export function addAlias(
  facetKey: string,
  valueKey: string,
  alias: string,
): Promise<{ alias: string }> {
  return apiFetch<{ alias: string }>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}/aliases`,
    { method: 'POST', body: { alias } },
  )
}

/**
 * POST .../merge. With `dryRun` it writes nothing and returns the number of
 * labels the real merge would move — and 404s on an unknown target exactly as
 * the real merge would, so a preview fails on anything the merge would fail on.
 */
export function mergeValue(
  facetKey: string,
  valueKey: string,
  into: string,
  dryRun: boolean,
): Promise<{ moved: number }> {
  return apiFetch<{ moved: number }>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}/merge`,
    { method: 'POST', body: { into, dry_run: dryRun } },
  )
}

/** DELETE .../values/{value} — 204, or 409 whose `detail` names the document count. */
export function deleteValue(facetKey: string, valueKey: string): Promise<void> {
  return apiFetch<void>(
    `/api/facets/${encodeURIComponent(facetKey)}/values/${encodeURIComponent(valueKey)}`,
    { method: 'DELETE' },
  )
}

/** GET /api/facet-suggestions — up to 100 pending, oldest first (server-capped). */
export async function listSuggestions(): Promise<FacetSuggestion[]> {
  const body = await apiFetch<{ suggestions: FacetSuggestion[] }>('/api/facet-suggestions')
  return body.suggestions
}

/** POST /api/facet-suggestions/{id}/accept — creates the value AND labels the document. */
export function acceptSuggestion(id: number): Promise<{ facet: string; value: string }> {
  return apiFetch<{ facet: string; value: string }>(`/api/facet-suggestions/${id}/accept`, {
    method: 'POST',
  })
}

/** POST /api/facet-suggestions/{id}/dismiss — rejects without creating anything. */
export function dismissSuggestion(id: number): Promise<{ state: string }> {
  return apiFetch<{ state: string }>(`/api/facet-suggestions/${id}/dismiss`, { method: 'POST' })
}
