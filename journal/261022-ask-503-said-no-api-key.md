# Ask said "no API key" when the key was there

**Date:** 2026-10-22

## 1. What happened

Ask stopped answering, and the error read *"Answering is unavailable — no AI API
key is configured. Add one in settings to use Ask."* The key was configured. The
deploy runs Ask on the `subscription` backend, and its Claude OAuth session had
expired. The refresh token came back `invalid_grant`, after which the CLI
rewrote `.credentials.json` with `expiresAt: 0` and no refresh token.

The server reported this correctly. `POST /api/ask` returned a 503 whose
`detail` named the cause and the command to fix it (`docs/llm-backends.md`
§5.2). `AskView.friendlyError` then threw that `detail` away. It mapped **every**
503 to the no-key sentence, a mapping written before the subscription backend
existed. When the subscription backend added a second 503 (2026-08-20), the
server side was updated and this mapping was not.

## 2. The fix

A 503 now shows the server's `detail` verbatim, like every other error status.
The one exception is a 503 with no JSON body. That comes from a proxy or a
restarting container, not from the app, and its status text ("Service
Unavailable") says nothing useful, so it gets a generic "unavailable right
now".

The existing test asserted the hard-coded sentence, so it pinned the bug in
place. Two tests replace it: a subscription-auth 503 must show its reason and
must not mention an API key, and a body-less 503 gets the generic message. Both
failed against the old mapping with the exact text the user saw.

## 3. Not changed

- **The outage itself is operational.** Recovery is to log in again on the host
  (`llm-backends.md` §4). No code change can do that.
- **`/healthz` already reports `claude_credentials`**, but nothing alerts on it.
  The user found the expired login by trying to use Ask, which is the outcome
  §4's health field exists to prevent. Alerting on it is a separate change in
  the monitoring stack.
- **The server's message is operator-facing**, with a `chown` command and a
  container path. That is the right audience on a single-admin deploy. A
  multi-user deploy would want a shorter user-facing line and the detail in the
  log.
