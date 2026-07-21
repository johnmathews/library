/** Per-user display preferences (docs/api.md — /api/settings). */
import { apiFetch } from './client'

/**
 * The catalog of dashboard tile fields: the field keys and their display
 * labels. This is the frontend source of truth for the Fields picker and the
 * Settings checkbox list. The *stored* per-user list (`dashboard_fields`)
 * determines BOTH which fields show AND the order they render in on the card
 * (DocumentListView drives its meta row from that ordered list). The order of
 * this catalog is only the default catalog order used to seed the picker.
 */
export const DASHBOARD_FIELDS = [
  { value: 'kind', text: 'Document type' },
  { value: 'sender', text: 'Correspondent' },
  { value: 'tags', text: 'Tags' },
  // The five document dates, in the same order as the detail-page hero. `date`
  // keeps its legacy value (= the document's own date) for back-compat with
  // saved preferences; the others map to due_date / expiry_date / created_at
  // (added) / updated_at (last edited).
  { value: 'date', text: 'Date on document' },
  { value: 'due_date', text: 'Due date' },
  { value: 'expiry_date', text: 'Expiry date' },
  { value: 'added_date', text: 'Date added to library' },
  { value: 'last_edited', text: 'Last edited' },
  { value: 'language', text: 'Language' },
  { value: 'status', text: 'Status' },
  { value: 'amount', text: 'Amount' },
  { value: 'file_type', text: 'File type' },
] as const

export type DashboardField = (typeof DASHBOARD_FIELDS)[number]['value']

/**
 * The default enabled dashboard fields, in render order. Mirrors the backend's
 * DEFAULT_DASHBOARD_FIELDS (schemas.py) — amount and file_type are off by
 * default. Used by the "Reset to defaults" action.
 */
export const DEFAULT_DASHBOARD_FIELDS: DashboardField[] = [
  'kind',
  'sender',
  'tags',
  'date',
  'language',
  'status',
]

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

/**
 * The screen positions the floating action dock can occupy (Settings →
 * Appearance). `top-right` is the default (mirrors the backend's
 * DEFAULT_DOCK_POSITION).
 */
export const DOCK_POSITIONS = [
  'top-left',
  'top-middle',
  'top-right',
  'bottom-left',
  'bottom-right',
] as const

export type DockPosition = (typeof DOCK_POSITIONS)[number]

export const DEFAULT_DOCK_POSITION: DockPosition = 'top-right'

/** Allowed dashboard column counts on phones (< 641px). Default is 2. */
export const PHONE_COLUMNS_OPTIONS = [1, 2, 3] as const
export const DEFAULT_PHONE_COLUMNS = 2

/** Whether to hide each tile's description on phones (< 641px). Default: show it. */
export const DEFAULT_HIDE_SUMMARY_MOBILE = false

/**
 * The built-in default tile-border colour for each document kind, by slug. Only
 * the kinds that meaningfully occur are coloured; every other kind (incl.
 * `other`) has no entry and renders with the tile's neutral default border.
 *
 * These hues are the reference source of truth for the "Default"/reset action
 * and were validated colourblind-safe (worst adjacent ΔE ≈ 24–38, well above
 * the ≥12 target). The backend stores only per-user *overrides*; these defaults
 * live frontend-side so the palette can be retuned without a data migration.
 */
export const DEFAULT_KIND_COLORS: Record<string, string> = {
  invoice: '#56b1f3', // sky
  receipt: '#34bd68', // green
  letter: '#755ff8', // violet
  warranty: '#dfad2b', // amber
  contract: '#fa4949', // red
}

/**
 * One-click colour presets shown beside the picker in Settings. A spread of
 * distinct, legible hues; the user can still pick any colour via the native
 * picker. Not required to be mutually colourblind-safe — they are conveniences,
 * not an auto-assigned series.
 */
export const SUGGESTED_COLORS = [
  { name: 'Blue', hex: '#56b1f3' },
  { name: 'Green', hex: '#34bd68' },
  { name: 'Violet', hex: '#755ff8' },
  { name: 'Amber', hex: '#dfad2b' },
  { name: 'Red', hex: '#fa4949' },
  { name: 'Orange', hex: '#eb6834' },
  { name: 'Teal', hex: '#14b8a6' },
  { name: 'Pink', hex: '#e87ba4' },
  { name: 'Slate', hex: '#6b7280' },
] as const

/** The grey shown in the picker for a kind with no colour (a "no accent" stand-in). */
export const NEUTRAL_KIND_COLOR = '#cbd0d8'

/**
 * The notification events a user can subscribe to (Settings → Notifications).
 * This ordered list is the single frontend source of truth for the checkbox
 * list — the event keys and their labels. The keys mirror the backend's
 * canonical event set.
 */
export const NOTIFICATION_EVENTS = [
  { value: 'document_success', label: 'Document processed successfully' },
  { value: 'processing_error', label: 'Processing failed' },
  { value: 'needs_review', label: 'Needs review (low confidence)' },
  { value: 'duplicate', label: 'Duplicate detected' },
  { value: 'email_held', label: 'Email held for review' },
] as const

export type NotificationEvent = (typeof NOTIFICATION_EVENTS)[number]['value']

/**
 * The Pushover/forwarding notification preferences read model (returned by
 * GET /api/settings and embedded in /api/auth/me). Secrets are never returned;
 * the `*_set` booleans report whether a value is stored.
 */
export interface NotificationPreferences {
  enabled: boolean
  pushover_app_token_set: boolean
  pushover_user_key_set: boolean
  pushover_device: string | null
  events: string[]
  email_forward_addresses: string[]
}

/**
 * The PUT /api/settings/notifications write body. Omit (or send "") for a
 * `pushover_*` secret to keep the stored value unchanged.
 */
export interface NotificationUpdate {
  enabled: boolean
  pushover_app_token?: string | null
  pushover_user_key?: string | null
  pushover_device?: string | null
  events: string[]
  email_forward_addresses: string[]
}

export interface UserPreferences {
  dashboard_fields: DashboardField[]
  // Optional on the client so older payloads (and test fixtures) without the
  // key still type-check; consumers fall back to the defaults.
  background_tone?: BackgroundTone
  tile_preview?: TilePreview
  dock_position?: DockPosition
  // Sparse per-kind border-colour overrides (slug → '#rrggbb'). Absent kinds
  // fall back to DEFAULT_KIND_COLORS; an empty map means "all defaults".
  kind_colors?: Record<string, string>
  notifications?: NotificationPreferences
  phone_columns?: number
  hide_summary_mobile?: boolean
}

/** GET /api/settings — resolved display preferences. */
export function getSettings(): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings')
}

/** PUT /api/settings — persist the dashboard field list; returns the cleaned set. */
export function updateSettings(prefs: { dashboard_fields: DashboardField[] }): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings', { method: 'PUT', body: prefs })
}

/**
 * PUT /api/settings/appearance — persist tone, tile preview, dock position,
 * phone columns, and the mobile "hide tile description" flag.
 */
export function updateAppearance(
  tone: BackgroundTone,
  tilePreview: TilePreview,
  dockPosition: DockPosition,
  phoneColumns: number,
  hideSummaryMobile: boolean,
): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings/appearance', {
    method: 'PUT',
    body: {
      background_tone: tone,
      tile_preview: tilePreview,
      dock_position: dockPosition,
      phone_columns: phoneColumns,
      hide_summary_mobile: hideSummaryMobile,
    },
  })
}

/**
 * PUT /api/settings/kind-colors — replace the per-kind border-colour overrides.
 * Send the full override map; `{}` resets every kind to its built-in default.
 * Malformed entries are dropped server-side; the cleaned map is returned.
 */
export function updateKindColors(
  kindColors: Record<string, string>,
): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings/kind-colors', {
    method: 'PUT',
    body: { kind_colors: kindColors },
  })
}

/** PUT /api/settings/notifications — persist Pushover + forwarding settings. */
export function updateNotifications(payload: NotificationUpdate): Promise<UserPreferences> {
  return apiFetch<UserPreferences>('/api/settings/notifications', {
    method: 'PUT',
    body: payload,
  })
}

// --- Email triage (read-only, instance-wide) ---------------------------------

/** The hold-for-review switches (`enabled` is the master switch). */
export interface EmailTriageHold {
  enabled: boolean
  below_substance: boolean
  unknown_senders: boolean
}

/** Sender allowlist state — the count only; the addresses are never returned. */
export interface EmailTriageAllowlist {
  configured: boolean
  count: number
}

/** The deterministic noise gate and its tiny-image/decoration thresholds. */
export interface EmailTriageNoiseFilter {
  enabled: boolean
  tiny_image_max_bytes: number
  tiny_image_max_edge_px: number
  /** Decoration-image signal ceilings (>= 2 of filename/size/shape must fire). */
  decoration_max_bytes: number
  decoration_max_edge_px: number
}

/** The optional per-email LLM label pass. `active` = enabled AND API key present. */
export interface EmailTriageLabel {
  enabled: boolean
  active: boolean
  model: string
  daily_budget_usd: number
  body_snippet_chars: number
  prompt_version: string
}

/** The body-substance gate thresholds (fixed in code, not configuration). */
export interface EmailTriageBodySubstance {
  min_words: number
  min_chars: number
}

/**
 * The effective email-in triage configuration (GET /api/settings/email-triage).
 * Instance-wide and read-only — secret-free by construction (no credentials,
 * no host, no allowlist addresses). See docs/ingestion.md, "Email item
 * selection" / "Held for review" for the semantics of each gate.
 */
export interface EmailTriageConfig {
  email_in_configured: boolean
  poll_minutes: number
  held_folder: string
  processed_folder: string
  hold: EmailTriageHold
  allowlist: EmailTriageAllowlist
  noise_filter: EmailTriageNoiseFilter
  label: EmailTriageLabel
  body_substance: EmailTriageBodySubstance
  imap_timeout_seconds: number
}

/** GET /api/settings/email-triage — the live triage pipeline configuration. */
export function getEmailTriage(): Promise<EmailTriageConfig> {
  return apiFetch<EmailTriageConfig>('/api/settings/email-triage')
}

/** One skipped item from a stored per-email selection trace (compact shape). */
export interface EmailTriageSkipDecision {
  /** `attachment` or `body`. */
  kind: string
  filename: string | null
  /** The stable skip code (e.g. `decoration_image`, `tiny_image`). */
  reason: string | null
  /** The human sentence behind the reason (e.g. which signals fired). */
  detail: string | null
}

/** One email whose selection quietly filtered or dropped at least one item. */
export interface EmailTriageRecentSkip {
  id: number
  message_id: string | null
  subject: string | null
  from_address: string | null
  created_at: string
  /** Only the skipped decisions — ingested siblings are not echoed. */
  decisions: EmailTriageSkipDecision[]
}

/** Body of GET /api/settings/email-triage/recent-skips (newest first). */
export interface EmailTriageRecentSkips {
  recent_skips: EmailTriageRecentSkip[]
}

/**
 * GET /api/settings/email-triage/recent-skips — the last 20 emails with a
 * skipped item, newest first. The durable answer to "did the pipeline just
 * eat my attachment?" without grepping server logs.
 */
export function getEmailTriageRecentSkips(): Promise<EmailTriageRecentSkips> {
  return apiFetch<EmailTriageRecentSkips>('/api/settings/email-triage/recent-skips')
}
