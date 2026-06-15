/**
 * Pure URL-query ⇆ applied-state for the documents list (route `/`).
 *
 * The dashboard keeps all filter state in the URL so back/forward, refresh
 * and shared links round-trip. The list view, the search modal and the
 * dashboard filter bar all read/write the query through these helpers, so
 * the param names and parsing rules live in exactly one place.
 *
 * `tag` repeats in the URL (?tag=a&tag=b) and ANDs — hence `tags: string[]`.
 */
import type { LocationQuery, LocationQueryRaw } from 'vue-router'

export interface AppliedFilters {
  q: string
  kind: string
  senderId: string
  tags: string[]
  language: string
  status: string
  dateFrom: string
  dateTo: string
  page: number
}

/** Collapse a query value to a string; `null`, arrays and `undefined` become `''`. */
function asString(value: LocationQuery[string] | undefined): string {
  return typeof value === 'string' ? value : ''
}

/** A query value may be a string, an array (repeated key), null, or undefined. */
function asStringArray(value: LocationQuery[string] | undefined): string[] {
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === 'string')
  return typeof value === 'string' ? [value] : []
}

/** Parse the route query into the strongly-typed applied state. */
export function parseDocumentQuery(query: LocationQuery): AppliedFilters {
  return {
    q: asString(query.q),
    kind: asString(query.kind),
    senderId: asString(query.sender_id),
    tags: asStringArray(query.tag),
    language: asString(query.language),
    status: asString(query.status),
    dateFrom: asString(query.date_from),
    dateTo: asString(query.date_to),
    page: Math.max(1, Number.parseInt(asString(query.page), 10) || 1),
  }
}

/**
 * Rebuild the URL query from applied state. Empty filters and page 1 are
 * omitted. Pass `page` to override (e.g. reset to 1 when a filter changes).
 */
export function buildDocumentQuery(
  applied: AppliedFilters,
  page: number = applied.page,
): LocationQueryRaw {
  const query: LocationQueryRaw = {}
  if (applied.q) query.q = applied.q
  if (applied.kind) query.kind = applied.kind
  if (applied.senderId) query.sender_id = applied.senderId
  if (applied.tags.length) query.tag = [...applied.tags]
  if (applied.language) query.language = applied.language
  if (applied.status) query.status = applied.status
  if (applied.dateFrom) query.date_from = applied.dateFrom
  if (applied.dateTo) query.date_to = applied.dateTo
  if (page > 1) query.page = String(page)
  return query
}

/** True when any filter (incl. the search text) is applied. */
export function hasActiveFilters(applied: AppliedFilters): boolean {
  return Boolean(
    applied.q ||
      applied.kind ||
      applied.senderId ||
      applied.tags.length ||
      applied.language ||
      applied.status ||
      applied.dateFrom ||
      applied.dateTo,
  )
}
