# MCP OAuth consent page and rate limits

**Date:** 2026-11-04
**Branch:** `eng-mcp-consent`

## 1. Context

The OAuth authorization server (#202) parks each connector's request and sends
the browser to `/oauth/consent`, but no page existed there, and nothing
throttled the endpoints that a Cloudflare Access bypass will expose. These two
units close the gap before the bypass is added.

## 2. What changed

- **Consent page** (`/oauth/consent`, no app chrome, like `/login`). It shows
  the app's name, the host the answer goes back to, the account it connects as
  with a *Not you?* switch, and what the app can do. The host is the thing to
  check: an app chooses its own name at registration, so a client calling
  itself "Claude" proves nothing. Approve and Deny each answer once and
  navigate back to the app. Expired and not-this-browser requests explain
  themselves.
- **Connected apps** tab in Settings: each approved app with when it connected
  and was last used, and an inline-confirmed Disconnect. With seven tabs the
  Settings tab bar overflowed a 375 px phone and dragged the whole page
  sideways (measured 683 px wide in a 343 px column); it now scrolls on its own.
- **Disabling a user** (CLI or Admin) also revokes their OAuth grants and
  tokens and deletes unused codes, so a disabled account cannot keep a
  connector alive.
- **Rate limits**: an in-process sliding window (production runs one uvicorn
  worker). Login: 5 failures per username and 20 per client per 15 minutes,
  failures only, the same 429 whatever the username. `/mcp/token`: 60 per
  client per 15 minutes; `/mcp/register`: 10 per hour.
  `LIBRARY_TRUSTED_CLIENT_IP_HEADER` names the proxy's client-IP header.

## 3. Evidence

- The flow ran end to end in a real browser against a local server: authorize,
  log in, consent, Approve, back to the app's callback with `code`, `state` and
  `iss`, then Disconnect in Settings. Screenshots at 375 px and 1280 px, light
  and dark, covered the normal, expired and not-found states.
- Frontend: consent view, API client, Settings tab, router guard and App
  layout specs, each seen failing first. Backend: revoke-on-disable tests seen
  failing first; rate-limit tests failed with the limiter disabled (written
  after the code, so red was observed by turning it off, not test-first).

- An independent review sent 30 simultaneous wrong passwords and all 30 were
  checked (`Counter({401: 30})`, confirmed): the failure was recorded only
  after the password hash was verified, so every request in flight read the
  old count. Attempts are now counted as they start and uncounted when they
  succeed; the same burst now gets `{401: 5, 429: 25}`. The cost: while in
  flight a correct login holds a slot, so more than five simultaneous logins
  for one user would be refused, which no person does.
- The API now warns at startup when MCP OAuth is on and
  `LIBRARY_TRUSTED_CLIENT_IP_HEADER` is unset.

## 4. Decisions

- The consent page lists only what the server can do today: search, read,
  download and add documents. The edit tools arrive in a later unit, and the
  page must not promise them early.
- A successful login does not reset a username's failure count; failures
  expire on their own. The cost is that anyone can lock a username for 15
  minutes with 5 bad passwords, which is accepted at family scale, with the
  per-client limit as the second line.

## 5. What is deliberately not done

- `LIBRARY_TRUSTED_CLIENT_IP_HEADER` is not set on the host yet. Until it is,
  every request behind cloudflared and Traefik shares one client bucket. The
  host `.env` is templated by the home-server ansible role.
- The Cloudflare Access bypass itself (a dashboard change by the owner).
- The Admin and Vocabulary tab bars have the same phone overflow; untouched.
