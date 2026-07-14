/**
 * Typed API for held emails (docs: hold-for-review queue).
 *
 * An email the poller judged not library-worthy sits in `held_emails` (its
 * message safe in the IMAP Held folder) until a human resolves it: "ingest
 * anyway" queues the override task, "dismiss" flips the row DB-only. Types
 * mirror the backend Pydantic schemas in src/library/schemas.py
 * (HeldEmailItem / HeldEmailDetail / HeldEmailListResponse /
 * HeldEmailIngestQueuedResponse). Dates travel as ISO strings.
 */

import { apiFetch } from './client'

export type HeldEmailStatus = 'held' | 'ingested' | 'dismissed'
/** `?status=` filter values; `all` disables the predicate. */
export type HeldEmailListStatus = HeldEmailStatus | 'all'

/**
 * Known hold triggers: `llm_hold` (the labeller's judgement), plus the
 * deterministic gates `below_substance`, `nothing_ingested`, `sender_unknown`.
 * Typed as string because the backend may grow new verdicts; render unknown
 * ones neutrally.
 */
export type HeldEmailVerdict = string

/** One row of GET /api/held-emails. */
export interface HeldEmailItem {
  id: number
  /** The RFC 5322 Message-ID — the authoritative IMAP pointer. */
  message_id: string | null
  sender: string | null
  subject: string | null
  /** The message's Date: header, if parseable. */
  received_at: string | null
  /** When the poller held the email. */
  created_at: string
  verdict: HeldEmailVerdict
  /** Human-readable trigger detail (e.g. the LLM's reason). */
  reason: string | null
  status: HeldEmailStatus
  owner_id: number | null
  /** The resolved owner's display label; null when no owner matched. */
  owner: string | null
  resolved_at: string | null
  /** Documents created by an ingest-anyway resolution; empty otherwise. */
  document_ids: number[]
  /** Most recent failed-resolution error (e.g. message not found on re-fetch). */
  last_error: string | null
}

/**
 * Body of GET /api/held-emails/{id}: the list item plus the decision trace the
 * poller snapshotted at hold time (the `email_selection` event shape —
 * email_from/email_subject/email_message_id + `items[]` with
 * kind/filename/mime/size/stage/verdict/reason — plus `label_usage` when the
 * LLM pass billed). Typed loosely; render defensively.
 */
export interface HeldEmailDetail extends HeldEmailItem {
  trace: Record<string, unknown>
}

/** Paginated body of GET /api/held-emails. */
export interface HeldEmailListResponse {
  items: HeldEmailItem[]
  total: number
  limit: number
  offset: number
}

/** 202 body of POST /api/held-emails/{id}/ingest. */
export interface HeldEmailIngestQueued {
  queued: boolean
  /** The Procrastinate job id (see GET /api/jobs). */
  job_id: number
}

/** The list API 422s on limit > 100; the client caps rather than erroring. */
export const HELD_EMAILS_MAX_LIMIT = 100

export interface HeldEmailListParams {
  status?: HeldEmailListStatus
  limit?: number
  offset?: number
}

/** GET /api/held-emails — the hold queue (default `status=held`), newest first. */
export function listHeldEmails(
  params: HeldEmailListParams = {},
  signal?: AbortSignal,
): Promise<HeldEmailListResponse> {
  return apiFetch<HeldEmailListResponse>('/api/held-emails', {
    query: {
      status: params.status ?? 'held',
      limit: Math.min(params.limit ?? 25, HELD_EMAILS_MAX_LIMIT),
      offset: params.offset ?? 0,
    },
    signal,
  })
}

/** GET /api/held-emails/{id} — one held email with its full decision trace. */
export function getHeldEmail(id: number, signal?: AbortSignal): Promise<HeldEmailDetail> {
  return apiFetch<HeldEmailDetail>(`/api/held-emails/${id}`, { signal })
}

/**
 * POST /api/held-emails/{id}/ingest — queue the ingest-anyway override task
 * (202). The row resolves asynchronously; poll GET detail for
 * status/document_ids/last_error. 409 when already resolved.
 */
export function ingestHeldEmail(id: number): Promise<HeldEmailIngestQueued> {
  return apiFetch<HeldEmailIngestQueued>(`/api/held-emails/${id}/ingest`, { method: 'POST' })
}

/**
 * POST /api/held-emails/{id}/dismiss — flip the row to dismissed (DB-only;
 * the message stays in the Held folder). Returns the updated detail; 409 when
 * already resolved.
 */
export function dismissHeldEmail(id: number): Promise<HeldEmailDetail> {
  return apiFetch<HeldEmailDetail>(`/api/held-emails/${id}/dismiss`, { method: 'POST' })
}
