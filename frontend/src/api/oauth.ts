/**
 * OAuth consent and connected apps (docs/api.md §1.9.4).
 *
 * An MCP client (claude.ai, ChatGPT, Claude Code) sends the browser to
 * `/oauth/consent?request=<id>`; the consent page reads the request and
 * approves or denies it. Settings → Connected apps lists and revokes the
 * resulting grants. Every route answers 404 when OAuth is off on the server.
 */
import { apiFetch } from './client'

/** GET /api/oauth/requests/{id}: what the consent page shows. */
export interface OAuthRequest {
  client_id: string
  /** As the client registered it; may be absent. */
  client_name: string | null
  /** Where the answer is sent (the client's callback). */
  redirect_uri: string
  /** The host of `redirect_uri`, for display. */
  redirect_host: string
  scopes: string[]
  expires_at: string
  /** True once past `expires_at`; approving then answers 410. */
  expired: boolean
}

/** POST …/approve|deny: send the browser here next. */
export interface OAuthRedirect {
  redirect_to: string
}

/** One row of GET /api/oauth/grants: an app connected over OAuth. */
export interface OAuthGrant {
  id: number
  client_id: string
  client_name: string | null
  created_at: string
  last_used_at: string | null
}

function requestPath(requestId: string): string {
  return `/api/oauth/requests/${encodeURIComponent(requestId)}`
}

/** GET /api/oauth/requests/{id}. 404 unknown, answered, other browser, or OAuth off. */
export function getAuthorizationRequest(requestId: string): Promise<OAuthRequest> {
  return apiFetch<OAuthRequest>(requestPath(requestId))
}

/** POST /api/oauth/requests/{id}/approve. 410 when the request expired. */
export function approveAuthorizationRequest(requestId: string): Promise<OAuthRedirect> {
  return apiFetch<OAuthRedirect>(`${requestPath(requestId)}/approve`, { method: 'POST' })
}

/** POST /api/oauth/requests/{id}/deny: the redirect carries `error=access_denied`. */
export function denyAuthorizationRequest(requestId: string): Promise<OAuthRedirect> {
  return apiFetch<OAuthRedirect>(`${requestPath(requestId)}/deny`, { method: 'POST' })
}

/** GET /api/oauth/grants: your connected apps, newest first. */
export function listOAuthGrants(): Promise<OAuthGrant[]> {
  return apiFetch<OAuthGrant[]>('/api/oauth/grants')
}

/** DELETE /api/oauth/grants/{id}: disconnect an app; its tokens stop working at once. */
export function revokeOAuthGrant(grantId: number): Promise<void> {
  return apiFetch<void>(`/api/oauth/grants/${grantId}`, { method: 'DELETE' })
}
