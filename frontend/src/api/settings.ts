/** Per-user display preferences (docs/api.md — /api/settings). */
import { apiFetch } from './client'

/**
 * The selectable dashboard tile fields. This array is the single frontend
 * source of truth for the Settings page's checkbox list — the field keys,
 * their display labels, and the checkbox order. It does NOT define the
 * dashboard tile render order: that order is fixed in DocumentListView's
 * template, independent of this list.
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

/**
 * The page-canvas tones a user can pick (Settings → Appearance). The `swatch`
 * hex MUST match the colour assets/main.css maps each `[data-canvas="…"]` to,
 * so the picker preview equals what actually renders. Tones apply to light
 * mode only — dark mode keeps its gray-900 canvas. `neutral` is the default
 * (mirrors the backend's DEFAULT_BACKGROUND_TONE).
 */
export const BACKGROUND_TONES = [
  { value: 'neutral', text: 'Neutral', swatch: '#e5e7eb' },
  { value: 'light', text: 'Light', swatch: '#f3f4f6' },
  { value: 'soft', text: 'Soft', swatch: '#e9ebef' },
  { value: 'slate', text: 'Slate', swatch: '#e9eaf2' },
  { value: 'sand', text: 'Sand', swatch: '#efece6' },
  { value: 'mist', text: 'Mist', swatch: '#e6edf0' },
] as const

export type BackgroundTone = (typeof BACKGROUND_TONES)[number]['value']

export const DEFAULT_BACKGROUND_TONE: BackgroundTone = 'neutral'

/**
 * How a dashboard tile renders the document's first-page thumbnail
 * (Settings → Appearance). `full_width` fills the tile width and crops the
 * lower part of the page; `whole_page` shows the entire first page letterboxed.
 * `full_width` is the default (mirrors the backend's DEFAULT_TILE_PREVIEW).
 */
export const TILE_PREVIEWS = [
  { value: 'full_width', text: 'Full width', hint: 'Fills the tile; crops the lower part of the page.' },
  { value: 'whole_page', text: 'Whole page', hint: 'Shows the entire first page, letterboxed.' },
] as const

export type TilePreview = (typeof TILE_PREVIEWS)[number]['value']

export const DEFAULT_TILE_PREVIEW: TilePreview = 'full_width'

export interface UserPreferences {
  dashboard_fields: DashboardField[]
  // Optional on the client so older payloads (and test fixtures) without the
  // key still type-check; consumers fall back to the defaults.
  background_tone?: BackgroundTone
  tile_preview?: TilePreview
}

/** GET /api/settings — resolved display preferences. */
export function getSettings(): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings')
}

/** PUT /api/settings — persist the dashboard field list; returns the cleaned set. */
export function updateSettings(prefs: { dashboard_fields: DashboardField[] }): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings', { method: 'PUT', body: prefs })
}

/** PUT /api/settings/appearance — persist the page-canvas tone and tile preview. */
export function updateAppearance(
  tone: BackgroundTone,
  tilePreview: TilePreview,
): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings/appearance', {
    method: 'PUT',
    body: { background_tone: tone, tile_preview: tilePreview },
  })
}
