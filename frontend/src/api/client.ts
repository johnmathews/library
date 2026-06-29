/**
 * Typed fetch wrapper for the Library API.
 *
 * Contract (docs/api.md §1.9): the browser authenticates with the
 * `library_session` httpOnly cookie; state-changing requests must echo the
 * readable `library_csrftoken` cookie in an `X-CSRF-Token` header.
 */

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  /**
   * The parsed JSON error body, when the server returned a JSON object (e.g. a
   * 409 conflict that carries extra fields beyond `detail`). `null` when the
   * body was empty or not JSON. Callers narrow it to a known shape per endpoint.
   */
  readonly body: Record<string, unknown> | null

  constructor(status: number, detail: string, body: Record<string, unknown> | null = null) {
    super(`API error ${status}: ${detail}`)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }
}

export const CSRF_COOKIE = 'library_csrftoken'
export const CSRF_HEADER = 'X-CSRF-Token'
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])

/** Read a cookie value by name; null when absent. */
export function getCookie(name: string): string | null {
  for (const part of document.cookie.split(';')) {
    const eq = part.indexOf('=')
    if (eq === -1) continue
    if (part.slice(0, eq).trim() === name) {
      return decodeURIComponent(part.slice(eq + 1).trim())
    }
  }
  return null
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** JSON-serialised unless it is FormData. */
  body?: unknown
  query?: Record<string, string | number | boolean | undefined>
  signal?: AbortSignal
}

/**
 * Perform an API request. Resolves with the parsed JSON body (or undefined
 * for 204 responses); rejects with `ApiError` carrying the normalised
 * `detail` string for any non-2xx response.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET'
  const headers = new Headers({ Accept: 'application/json' })

  if (!SAFE_METHODS.has(method)) {
    const csrf = getCookie(CSRF_COOKIE)
    if (csrf) headers.set(CSRF_HEADER, csrf)
  }

  let body: BodyInit | undefined
  if (options.body instanceof FormData) {
    body = options.body
  } else if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(options.body)
  }

  let url = path
  if (options.query) {
    const params = new URLSearchParams()
    for (const [key, value] of Object.entries(options.query)) {
      if (value !== undefined) params.set(key, String(value))
    }
    const qs = params.toString()
    if (qs) url += `?${qs}`
  }

  const response = await fetch(url, {
    method,
    headers,
    body,
    credentials: 'same-origin',
    signal: options.signal,
  })

  if (!response.ok) {
    const { detail, body } = await readError(response)
    throw new ApiError(response.status, detail, body)
  }
  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

/**
 * Read an error response once, returning both the normalised `detail` string
 * and the full parsed JSON body (when it was a JSON object) so callers can read
 * endpoint-specific conflict fields off `ApiError.body`.
 */
async function readError(
  response: Response,
): Promise<{ detail: string; body: Record<string, unknown> | null }> {
  let body: Record<string, unknown> | null = null
  try {
    const data: unknown = await response.json()
    if (data && typeof data === 'object') {
      body = data as Record<string, unknown>
      if ('detail' in body) {
        const detail = body.detail
        if (typeof detail === 'string') return { detail, body }
        return { detail: JSON.stringify(detail), body }
      }
    }
  } catch {
    // fall through to status text
  }
  return { detail: response.statusText || `HTTP ${response.status}`, body }
}
