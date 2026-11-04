# MCP OAuth authorization server

**Date:** 2026-11-04
**Branch:** `eng-mcp-oauth`

## 1. Context

claude.ai and ChatGPT connect to remote MCP servers only through OAuth 2.1
(dynamic client registration, PKCE S256, RFC 9728 protected-resource metadata,
RFC 8414 authorization-server metadata). Library accepted only static
`library_…` bearer tokens, and the household needs each person to connect as
themselves. The user chose to make Library its own authorization server rather
than use Cloudflare Access Managed OAuth, so identity stays in Library accounts
and Claude Code's static tokens keep working.

## 2. What changed

- **Provider and schema (W2).** `LibraryOAuthProvider` implements FastMCP's
  `OAuthProvider` over five tables (migration 0040): clients, grants (one per
  user and client), pending authorization requests, codes and tokens. Only
  SHA-256 hashes of request ids, codes and tokens are stored. Codes are
  single-use and live 5 minutes; access tokens 1 hour; refresh tokens 30 days
  and rotate, and replaying a rotated refresh token revokes its whole family.
  Access tokens are bound to the resource `{public_base_url}/mcp` and checked
  against grant revocation and `user.is_active`. Their claims carry `user_id`,
  so MCP attribution (ingest uploader) is unchanged.
- **Redirect allow-list.** Registration accepts only the claude.ai and
  chatgpt.com callbacks and loopback `/callback` on any port (Claude Code
  listens on a fresh port per login). `LIBRARY_OAUTH_REDIRECT_ALLOWLIST`
  overrides it; a `*` is allowed only as a final `/*`.
- **Wiring (W3).** With an `https` `LIBRARY_PUBLIC_BASE_URL`, `/mcp` uses
  `LibraryMultiAuth`: OAuth tokens and `library_…` tokens both work. 401s carry
  `resource_metadata`; discovery documents are served at the root well-known
  paths; `/mcp/authorize|token|register|revoke` live under the mount, so a
  Cloudflare Access bypass can stay limited to `/mcp*` and `/.well-known/oauth-*`.
- **Consent API.** `/api/oauth/requests/{id}` (read, approve, deny) and
  `/api/oauth/grants` (list, revoke your own). Approve and deny require the
  browser session and CSRF; a bearer token gets 403, so a script holding a
  `library_…` token cannot approve a connector.
- **Browser binding (review fix).** A review found consent phishing: anyone
  signed in who opened `/oauth/consent?request=<id>` could approve it, so a
  household member could start connecting their own claude.ai and send the
  link to someone else, and get a token for that person if the vendor does
  not tie `state` to its own session. `/mcp/authorize` now sets a
  per-request cookie (`library_oauth_request_<16 hex of the request id's
  hash>`, HttpOnly, SameSite=Lax, Path=/, Secure on https, 10 minutes) whose
  hash is stored on the request; the consent routes need it and otherwise
  answer the same 404 as an unknown request, telling the user to start again
  from their app. One cookie per request rather than one per browser: flows
  share nothing, nothing is read back from the incoming request, and the
  cost is a few short-lived cookies during simultaneous connects. The SDK's
  authorize handler builds its 302 from the URL `authorize()` returns, so the
  provider replaces the `/authorize` route (as FastMCP replaces `/token`)
  with a wrapper that runs the SDK handler and sets the cookie the provider
  left in a context variable; `authorize()` outside that route refuses.
  The column is `NOT NULL` in the amended migration 0040 (unshipped), so an
  unbound request cannot exist.
- **Other review fixes.** The redirect allow-list is re-checked at
  `/authorize` (narrowing it cuts off already-registered clients, with a
  direct 400); approve and deny consume the request with one
  `DELETE … RETURNING`, so a double approval mints one code; a refresh
  asking for fewer scopes narrows only the access token.

## 3. Evidence

- 41 provider tests and 15 wiring tests at first; the review fixes added
  provider tests (binding refusals, fail-closed `authorize()`, allow-list
  narrowing through the SDK handler, a forced approve race, refresh scope)
  and two wiring tests (cookie attributes and per-request clearing, a
  forwarded consent link refused), each seen red against the old code. Fourteen provider guards (single-use
  code, refresh-reuse revocation, resource checks, `is_active`, redirect
  allow-list, loopback port matching, grant ownership, `iss`, code expiry) and
  each wiring test were seen failing with the guarding line removed by hand.
- The MCP SDK's own `OAuthClientProvider` completes the whole flow in-process
  (register, authorize, consent approve, token, tool call), and the ingested
  document's uploader is the approving user.
- Full backend suite green; migration up/down/up cycle green.

## 4. Things learned

- The MCP SDK refuses a non-https issuer, so OAuth stays off with a warning
  when the public URL is plain http (the tests use `https://testserver`).
  Production's `LIBRARY_PUBLIC_BASE_URL` is https, so OAuth turns on at deploy,
  still entirely behind Cloudflare Access until the bypass is added.
- FastMCP derives the resource from the mount and would advertise
  `…/mcp/mcp` or `…/mcp/`; both the provider and `MultiAuth` override
  `_get_resource_url` so it is exactly `{base}/mcp`.
- The SDK's token handler never passes RFC 8707 `resource` to the provider, so
  audience binding happens at `/authorize` and at token verification.
- A client that registered without a scope and then sends one is rejected by
  the SDK before the provider runs; `default_scopes=["library"]` avoids that.
- Two `asyncio.gather`ed approvals did not actually overlap — each finished
  before the other started — so the race test passed against the racy code.
  Holding the request row's lock from a third transaction until both
  approvals are waiting on it made the race deterministic, and the old code
  then minted two codes.

## 5. What is deliberately not done

- The consent page and a Settings list of connected apps (next unit), and rate
  limits on login, `/mcp/token` and `/mcp/register` (the unit after). The
  Access bypass waits for both.
- Client ID Metadata Documents (only FastMCP's `OAuthProxy` supports them);
  both vendors accept DCR.
- Revoking tokens already issued when a used code is replayed (RFC 6749 SHOULD);
  a code is deleted on use, so the replay is refused but cannot trace the earlier
  tokens.
- Tolerating concurrent refreshes: two refreshes racing from one client count
  as reuse and revoke the family, so that client must be approved again.
  Strict reuse detection is the safer default for a family archive.
- `iss` on the SDK's own `/authorize` error redirects (for example
  `invalid_scope`, or a wrong `resource`): the SDK builds them and has no hook,
  so they lack `iss` although the metadata advertises it. The approve and
  deny redirects carry it.
