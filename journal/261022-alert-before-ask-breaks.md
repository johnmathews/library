# Alerting before Ask's login breaks

**Date:** 2026-10-22

## 1. What & why

Ask stopped answering on 2026-10-22 because the Claude subscription's refresh
token came back `invalid_grant` (`261022-ask-503-said-no-api-key.md`). The user
found out by asking a question. They asked for an alert that fires when Ask
would stop working and that says where the fix is.

The logs showed why nothing could have warned them. The webserver had been up
since 11 October with no Ask call and no token refresh. Refresh ran only before
an SDK query, so the refresh token went unspent for 11 days. `token_health`
reads "expired access token + refresh token present" as healthy, which is
correct until something spends the token. The token died in that window, or was
revoked, and nothing could notice until the user's question spent it.

## 2. Decisions

- **Refresh on a timer, not only on demand.** `oauth.keep_fresh` runs the
  existing `ensure_valid_token` every 15 minutes in the webserver. It spends
  the token only when it is due, so the cadence is one refresh per access-token
  lifetime (~8h), not one per tick. A rejection now surfaces within hours, and
  the token stays in use in case idleness is what kills it. That second effect
  is a hypothesis: this outage cannot tell idle expiry from revocation.
- **The webserver, not the worker.** The rejection flag that turns health
  unhealthy lives in process memory. A refresh in another process would spend
  the token and leave this process's health reading healthy.
- **A gauge, not a blackbox probe on `/healthz`.** Blackbox cannot read a JSON
  field. `library_claude_credentials_healthy` is an observable gauge, evaluated
  on scrape from `app.claude_credential_status`. That function now also backs
  `/healthz`, so the two cannot drift.
- **`degraded` pages.** It means no refresh token. The login lapses when the
  current access token does, and only a human can prevent that.
- **A missing series pages.** If the webserver isn't scraped, Ask is down or
  unobservable, and either way it needs a look.
- **A second rule on `subscription_auth` errors.** It catches an auth failure
  that the file-based gauge cannot see, and it fires on the failed question
  itself.

## 3. Verification

The scheduled-refresh tests reproduce the outage: an expired token, a
refresh token the stub rejects, no Ask call, and health must turn unhealthy.
Removing the loop's error guard turns its test red. The gauge is asserted
through a real scrape at `1`, `0` and absent, and once through the app's
lifespan against credentials on disk.

The Grafana rules and their notification text are recorded in the home-server
repo, where the alerting doc lives.
