# Library follow-ups run closed

> Purpose
>
> Closes run `manual-20261106T201545Z`, which took the five follow-ups the MCP follow-ups run
> left open (`261106-mcp-followups-run-closed.md` §6). It records what shipped, what was
> found on the way, what was decided against, and the list of fixes a later MCP SDK upgrade
> will need.

**Date:** 2026-11-07
**Branch:** `eng-followups-close`

## 1. What shipped

| Unit | What | Where |
| --- | --- | --- |
| W1 | `scripts/deploy.sh` verifies WUD's deploys by default; `--deploy` is the manual deploy under a host lock, migrating once | #220, #221 (stamp hotfix), #222 (`--no-deps`), this PR |
| W2 | home-server `nfs_client` puts the NFS user in the group the role creates | home-server #341, applied on `documents` |
| W3 | Migration 0042: the twin order lives in the `spend_facts` view as `twin_rank`, and `sum_amount` reads it | #223 |
| W4 | Spike: fastmcp 4 / mcp 2 on a scratch tree | nothing committed; §4 |
| W5 | The SDK upgrade itself | held by John; §3 |
| W6 | Do clients answer a confirm-before-edit question? | research only; §3 |
| W7 | WUD's deploy script takes the same lock and migrates once with `--no-deps` | home-server #342, #343, applied |

All three live checks were done on production after each merge (confirmed):

- **`045b235` (W1):** WUD was the only deployer, the migration ran once, `deploy.sh` verify
  passed, and no stray container was left. `compose run` did recreate `library-db`, which led
  to #222 and home-server #343.
- **Two deploys after #343 (23:38 and 23:51 UTC):** one migrate run container each, and
  `library-db` untouched.
- **`62a8d7d` (W3):** `Running upgrade 0041 -> 0042` once, `library-db` untouched, verify
  green with head `0042`, and a grouped `money_query` answering.

## 2. Found on the way

- **The double migration was two deployers, not compose (confirmed).** The plan assumed
  compose's recreate inside one `up`. A local rehearsal with the production compose shape ran
  the migration once. The host's What's Up Docker auto-deploys every new `:latest`, and its
  log shows it colliding with the manual `deploy.sh` on both 2026-11-06 deploys. The fix
  changed the plan: WUD deploys, the script verifies (`261106-deploy-verifies-wud.md`).
- **Two compose versions recreate each other's containers (strongly supported).** The host
  runs Docker Compose 2.39.2 and the WUD image runs 5.6.1, and `config --hash` differs for
  every service. `--no-deps` on every migrate run keeps either from restarting the database
  (`261107-deploy-migrate-no-deps.md`).
- **`check_docs.py`'s one-day grace hides an unstamped edit until the squash lands (confirmed).**
  #220 changed `docs/deployment.md` without re-stamping it. The PR was green and the
  post-midnight squash red `main` (#221 fixed it). `promote` does not depend on `docs-stamps`,
  so the image shipped anyway.
- **This closing PR's review found two `deploy.sh` defects (confirmed by red tests):**
  - a failed DB read under `set -e` and pipefail ended the script before its own message;
  - verify could remove compose's in-flight rename while WUD was still recreating the worker.

  Verify now waits for the host lock, which WUD holds until its health wait ends, before it
  touches any container.
- **A rehearsal hazard (not observed):** `compose run` attaches stdin by default, and
  `--deploy`'s steps reach the host on `bash -s`'s stdin. `-T </dev/null` keeps it off.
  Showing the failure without the fix did not work, because Docker Desktop failed the pull
  first.

## 3. What is deliberately not done

- **The MCP SDK upgrade (W5), held by John.** mcp 2 refuses MCP request bodies over 4 MiB, and
  fastmcp 4.1.6 passes no `max_request_body_size` through. `ingest_document` would drop from
  100 MiB to about 3.0 MiB decoded (probed: 2.9 MiB → 200, 3.0 MiB → 413), and the model would
  see an HTTP 413, not a tool error. Offered and declined:
  - accept the limit and document it;
  - patch it through a fastmcp internal.

  Revisit when fastmcp exposes the setting. mcp 1 receives security fixes only.
- **Confirm-before-edit through elicitation (W6).** It holds for Claude Code and for ChatGPT
  through multi-round-trip calls on Desktop and Work web. Anthropic's connector build guide
  lists no elicitation for claude.ai, the household's main client (suspected unsupported, by
  absence). A server may not assume the client answers, so the preview and `preview_token`
  flow would stay anyway. Re-check when the connector guide lists elicitation.
- **Pinning one compose version** for the host and the WUD image. `--no-deps` makes it
  harmless for deploys; aligning them changes the WUD image build.
- **Dependabot #183** (mcp 1.30.0) was left open; its `ci-gate` was already failing before this
  run.

## 4. The fixes a fastmcp 4 / mcp 2 upgrade needs (W4, 2026-11-07)

A scratch tree ran fastmcp 4.1.6 and mcp 2.3.0. The suite gave 2512 passed and 4 failed: three
pins red by design, and one rate-limit test that passed 3 of 3 alone. The release notes' "most
FastMCP 3 applications upgrade without code changes" did not hold. Stateless JSON-response mode
is configured as today, and a 2026-07-28 `server/discover` (with the `_meta` envelope and a
matching `Mcp-Method` header) gets 200.

1. **OAuth `/authorize` 500s on every sign-in.** In `auth/oauth_provider.py`
   (`_binding_authorize_route`), mcp 2's `create_auth_routes` wraps each endpoint as an ASGI
   app, which Library called as a `Request → Response` handler.
2. **The 401 loses `resource_metadata`.** fastmcp 4 advertises the URL only when the
   provider's own routes serve it, and Library serves it from the site root
   (`mcp_server.py`, `LibraryMultiAuth.get_routes`).
3. **`get_document_file` fails masked.** Resource URIs must be plain `str`, not `AnyUrl`
   (`_file_block`).
4. **mypy: 21 `call-arg` errors.** camelCase keyword arguments to `ToolAnnotations` and the
   content types need a mechanical snake_case rename; the wire format is unchanged.
5. **Test client changes.** `streamable_http_client` yields two values, not three (6 test
   files). The OAuth SDK test needs an `httpx2` client and an `AuthorizationCodeResult`
   callback, so `httpx2` joins the dev dependencies.
6. **Pins red by design.** `test_newer_protocol_version_is_refused_with_400` becomes the
   2026-07-28 discover test. The rejected-log test keeps its assertion but needs a new trigger
   (a 2026-07-28 body without `_meta`).
7. **Rename 38 camelCase attribute reads in the tests.** The compatibility shims are scheduled
   for removal.
8. **The 4 MiB body limit** (§3).
9. **Stored OAuth clients.** Check that no production client is secret-based with no stored
   secret, because mcp 2 refuses those.

Unaffected: `app.py`, `mcp_rejected_log.py` and `scripts/mcp_smoke.py` (it passed unchanged
against a local server). OpenTelemetry tracing on by default does nothing here, because the
app sets only a MeterProvider.
