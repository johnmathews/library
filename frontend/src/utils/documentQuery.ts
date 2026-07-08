/**
 * Pure URL-query ⇆ applied-state for the documents list (route `/`).
 *
 * The dashboard keeps all filter state in the URL so back/forward, refresh
 * and shared links round-trip. The list view, the search modal and the
 * dashboard filter bar all read/write the query through these helpers, so
 * the param names and parsing rules live in exactly one place.
 *
 * `tag` repeats in the URL (?tag=a&tag=b) and ANDs — hence `tags: string[]`.
 * `project` also repeats (?project=a&project=b) but ORs (documents in any) —
 * hence `projects: string[]`.
 */
import type { LocationQuery, LocationQueryRaw } from 'vue-router'

export const SORT_FIELDS = ['document_date', 'added_date'] as const
export const SORT_DIRECTIONS = ['asc', 'desc'] as const
export type SortField = (typeof SORT_FIELDS)[number]
export type SortDirection = (typeof SORT_DIRECTIONS)[number]
export const DEFAULT_SORT: SortField = 'added_date'
export const DEFAULT_SORT_DIRECTION: SortDirection = 'desc'

/** A remembered sort choice (field + direction), persisted per-user so a bare
 *  dashboard URL reproduces the last selection. See `parseDocumentQuery`. */
export interface SortPreference {
  sort: SortField
  dir: SortDirection
}

export interface AppliedFilters {
  q: string
  kind: string
  senderId: string
  recipientId: string
  projects: string[]
  tags: string[]
  language: string
  status: string
  dateFrom: string
  dateTo: string
  review: string
  // Sort is not a "filter" (excluded from hasActiveFilters); it round-trips
  // through the URL like one. Unknown values fall back to the defaults.
  sort: SortField
  dir: SortDirection
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

/** Coerce a query value to one of `allowed`, else `fallback`. */
function asEnum<T extends string>(
  value: LocationQuery[string] | undefined,
  allowed: readonly T[],
  fallback: T,
): T {
  const s = asString(value)
  return (allowed as readonly string[]).includes(s) ? (s as T) : fallback
}

/**
 * Parse the route query into the strongly-typed applied state.
 *
 * `sortDefaults` is the caller's remembered sort preference: when the URL
 * carries no `sort`/`dir` param, we fall back to it (validated) rather than the
 * hard constants, so a bare `/` reproduces the user's last choice. The
 * `DEFAULT_SORT`/`DEFAULT_SORT_DIRECTION` constants remain the ultimate fallback
 * for an unset or garbage preference.
 */
export function parseDocumentQuery(
  query: LocationQuery,
  sortDefaults?: Partial<SortPreference>,
): AppliedFilters {
  const sortFallback = asEnum(sortDefaults?.sort, SORT_FIELDS, DEFAULT_SORT)
  const dirFallback = asEnum(sortDefaults?.dir, SORT_DIRECTIONS, DEFAULT_SORT_DIRECTION)
  return {
    q: asString(query.q),
    kind: asString(query.kind),
    senderId: asString(query.sender_id),
    recipientId: asString(query.recipient_id),
    projects: asStringArray(query.project),
    tags: asStringArray(query.tag),
    language: asString(query.language),
    status: asString(query.status),
    dateFrom: asString(query.date_from),
    dateTo: asString(query.date_to),
    review: asString(query.review),
    sort: asEnum(query.sort, SORT_FIELDS, sortFallback),
    dir: asEnum(query.dir, SORT_DIRECTIONS, dirFallback),
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
  if (applied.recipientId) query.recipient_id = applied.recipientId
  if (applied.projects.length) query.project = [...applied.projects]
  if (applied.tags.length) query.tag = [...applied.tags]
  if (applied.language) query.language = applied.language
  if (applied.status) query.status = applied.status
  if (applied.dateFrom) query.date_from = applied.dateFrom
  if (applied.dateTo) query.date_to = applied.dateTo
  if (applied.review) query.review = applied.review
  // Sort params are omitted at their defaults so the canonical URL stays clean.
  if (applied.sort !== DEFAULT_SORT) query.sort = applied.sort
  if (applied.dir !== DEFAULT_SORT_DIRECTION) query.dir = applied.dir
  if (page > 1) query.page = String(page)
  return query
}

/** True when any filter (incl. the search text) is applied. */
export function hasActiveFilters(applied: AppliedFilters): boolean {
  return Boolean(
    applied.q ||
      applied.kind ||
      applied.senderId ||
      applied.recipientId ||
      applied.projects.length ||
      applied.tags.length ||
      applied.language ||
      applied.status ||
      applied.dateFrom ||
      applied.dateTo ||
      applied.review,
  )
}
