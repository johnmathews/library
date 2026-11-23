import type { LocationQueryRaw } from 'vue-router'
import type { DocumentListItem } from '@/api/documents'
import type { DashboardField } from '@/api/settings'

/**
 * What every non-tile dashboard layout (rows, table, list) is given. The view
 * owns fetching, infinite scroll and live status; a layout only arranges.
 */
export interface DocumentLayoutProps {
  items: DocumentListItem[]
  /** The owner's chosen fields, in order (`dashboard_fields`). */
  fields: readonly DashboardField[]
  compactDates: boolean
  /** Forwarded on each document link so the detail page steps through this set (#140). */
  detailQuery: LocationQueryRaw
  /** True while a search is applied, when a document's snippet says why it matched. */
  searching: boolean
  /** The document type's colour per document id, or null for none. */
  accents: Map<number, string | null>
}
