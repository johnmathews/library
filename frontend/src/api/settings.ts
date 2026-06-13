/** Per-user display preferences (docs/api.md — /api/settings). */
import { apiFetch } from './client'

/**
 * The selectable dashboard tile fields, in canonical render order. This
 * array is the single frontend source of truth for both the settings
 * checkboxes and the order fields appear on a tile.
 */
export const DASHBOARD_FIELDS = [
  { value: 'kind', text: 'Document type' },
  { value: 'sender', text: 'Correspondent' },
  { value: 'tags', text: 'Tags' },
  { value: 'date', text: 'Date' },
  { value: 'language', text: 'Language' },
  { value: 'status', text: 'Status' },
  { value: 'amount', text: 'Amount' },
  { value: 'file_type', text: 'File type' },
] as const

export type DashboardField = (typeof DASHBOARD_FIELDS)[number]['value']

export interface DashboardPreferences {
  dashboard_fields: DashboardField[]
}

/** GET /api/settings — resolved dashboard field preferences. */
export function getSettings(): Promise<DashboardPreferences> {
  return apiFetch<DashboardPreferences>('/api/settings')
}

/** PUT /api/settings — persist the field list; returns the cleaned set. */
export function updateSettings(prefs: DashboardPreferences): Promise<DashboardPreferences> {
  return apiFetch<DashboardPreferences>('/api/settings', { method: 'PUT', body: prefs })
}
