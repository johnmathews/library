import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  DASHBOARD_FIELDS,
  NOTIFICATION_EVENTS,
  TILE_PREVIEWS,
  getEmailTriage,
  getEmailTriageRecentSkips,
  getSettings,
  updateAppearance,
  updateNotifications,
  updateSettings,
} from '../settings'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('settings api', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('GET /api/settings returns the field list', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ dashboard_fields: ['kind'] }))
    vi.stubGlobal('fetch', fetchMock)
    expect(await getSettings()).toEqual({ dashboard_fields: ['kind'] })
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/settings')
  })

  it('PUT /api/settings sends the field list', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ dashboard_fields: ['tags'] }))
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateSettings({ dashboard_fields: ['tags'] })
    expect(result).toEqual({ dashboard_fields: ['tags'] })
    const [, init] = fetchMock.mock.calls[0]!
    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/settings')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({ dashboard_fields: ['tags'] })
  })

  it('PUT /api/settings/appearance sends tone, tile preview, and dock position', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({
          dashboard_fields: ['kind'],
          background_tone: 'slate',
          tile_preview: 'whole_page',
          dock_position: 'bottom-left',
        }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const result = await updateAppearance('slate', 'whole_page', 'bottom-left')
    expect(result.tile_preview).toBe('whole_page')
    expect(result.dock_position).toBe('bottom-left')
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/settings/appearance')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({
      background_tone: 'slate',
      tile_preview: 'whole_page',
      dock_position: 'bottom-left',
    })
  })

  it('TILE_PREVIEWS contains full_width and whole_page in order', () => {
    expect(TILE_PREVIEWS.map((m) => m.value)).toEqual(['full_width', 'whole_page'])
  })

  it('DASHBOARD_FIELDS contains all canonical field values in order (five dates)', () => {
    expect(DASHBOARD_FIELDS.map((f) => f.value)).toEqual([
      'kind', 'sender', 'tags',
      'date', 'due_date', 'expiry_date', 'added_date', 'last_edited',
      'language', 'status', 'amount', 'file_type',
    ])
  })

  it('NOTIFICATION_EVENTS contains the five canonical event keys in order', () => {
    expect(NOTIFICATION_EVENTS.map((e) => e.value)).toEqual([
      'document_success', 'processing_error', 'needs_review', 'duplicate', 'email_held',
    ])
  })

  it('PUT /api/settings/notifications sends the notification body', async () => {
    const readModel = {
      dashboard_fields: ['kind'],
      notifications: {
        enabled: true,
        pushover_app_token_set: true,
        pushover_user_key_set: true,
        pushover_device: null,
        events: ['document_success'],
        email_forward_addresses: ['me@example.com'],
      },
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(readModel))
    vi.stubGlobal('fetch', fetchMock)

    const result = await updateNotifications({
      enabled: true,
      pushover_app_token: 'token-123',
      pushover_user_key: 'user-456',
      pushover_device: null,
      events: ['document_success'],
      email_forward_addresses: ['me@example.com'],
    })

    expect(result).toEqual(readModel)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/settings/notifications')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body)).toEqual({
      enabled: true,
      pushover_app_token: 'token-123',
      pushover_user_key: 'user-456',
      pushover_device: null,
      events: ['document_success'],
      email_forward_addresses: ['me@example.com'],
    })
  })

  it('GET /api/settings/email-triage returns the triage config', async () => {
    const config = {
      email_in_configured: true,
      poll_minutes: 10,
      held_folder: 'Library/Held',
      processed_folder: 'Library/Processed',
      hold: { enabled: true, below_substance: true, unknown_senders: true },
      allowlist: { configured: true, count: 2 },
      noise_filter: {
        enabled: true,
        tiny_image_max_bytes: 4096,
        tiny_image_max_edge_px: 64,
        decoration_max_bytes: 65536,
        decoration_max_edge_px: 384,
      },
      label: {
        enabled: false,
        active: false,
        model: 'claude-haiku-4-5',
        daily_budget_usd: 2,
        body_snippet_chars: 1000,
        prompt_version: 'email-label-v2',
      },
      body_substance: { min_words: 40, min_chars: 240 },
      imap_timeout_seconds: 60,
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(config))
    vi.stubGlobal('fetch', fetchMock)
    expect(await getEmailTriage()).toEqual(config)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/settings/email-triage')
    expect(init.method).toBe('GET')
  })

  it('GET /api/settings/email-triage/recent-skips returns the skip rows', async () => {
    const payload = {
      recent_skips: [
        {
          id: 12,
          message_id: '<m1@example.com>',
          subject: 'Fwd: invoice 42',
          from_address: 'alice@example.com',
          created_at: '2026-07-14T10:00:00Z',
          decisions: [
            {
              kind: 'attachment',
              filename: 'image001.png',
              reason: 'decoration_image',
              detail: 'filename, size and shape signals fired',
            },
          ],
        },
      ],
    }
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(payload))
    vi.stubGlobal('fetch', fetchMock)
    expect(await getEmailTriageRecentSkips()).toEqual(payload)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(String(url)).toBe('/api/settings/email-triage/recent-skips')
    expect(init.method).toBe('GET')
  })
})
