# Dependabot alerts cleared

> Purpose
>
> Records run `manual-20261107T093056Z`, which took `main` from 114 open Dependabot alerts
> (10 critical, 45 high) to 4 (all medium, all mcp, held on purpose). It covers what shipped,
> what was found on the way, what stays open and why, and the grouping that should stop
> alerts piling up again.

**Date:** 2026-11-07
**Branch:** `eng-dependabot-w6-grouping`

## 1. What shipped

| Unit | What | Alerts closed | Where |
| --- | --- | --- | --- |
| W1 | Pillow 12.3.0 → 13.0.0 | 41 (5 critical, 23 high) | #225 |
| W2 | PyJWT, Authlib, joserfc, anyio, urllib3, dnspython, fpdf2, Mako, OpenTelemetry, soupsieve (held at 2.9) | 38 (3 critical, 13 high) | #227 |
| W3 | dompurify, shell-quote, undici, vite, vue, vitest, source-map-js, postcss-selector-parser, brace-expansion | 30 (2 critical, 9 high) | #226 |
| W4 | Guard test for MCP bodies over 4 MiB; mcp held on 1.28.1 | 0 (4 explained) | #228 |
| W5 | setup-uv 10.0.1 → 10.3.3 | 0 | #208 |
| W6 | Grouped pip and npm security updates; `docs/deployment.md` §1.7.3; this entry | 0 | this PR |

minimatch (#180) was dismissed as tolerable risk, with the reason recorded on the alert. That
accounts for the last of the 114.

Each merge was verified live with `scripts/deploy.sh` (verify mode) after WUD deployed it:
`/healthz` reported the squash commit, with `ocr_models: ok`, at Alembic head `0042`. That was
`9f29345`, `ab53f50`, `d7ed81e`, `a84ae2f` and `eadfe5b`, between 11:20 and 12:20 CET
(confirmed). Two extra live checks: after W2, the OAuth authorization-server metadata answered
`200`; after W3, the SPA index and its main bundle loaded. Whether GitHub accepts the new
`dependabot.yml` can only be seen after this PR merges; it was checked against GitHub's
options reference, not run.

Superseded Dependabot PRs were closed with a pointer: #191 (by #225); #176, #177, #179, #180,
#190, #198 and #199 (by #227); #174, #182, #184 and #189 (by #226); and #183 (held, by #228).
Dependabot closed #171 itself after the rebase request ("node is up-to-date now").

## 2. Found on the way

- **mcp 1.30 backports the 4 MiB body cap (confirmed by a red test).** The cap that holds the
  fastmcp 4 / mcp 2 upgrade (`261107-library-followups-run-closed.md` §3) is also in mcp 1.30,
  as `RequestBodyLimitMiddleware` with `DEFAULT_MAX_REQUEST_BODY_SIZE = 4 MiB`. fastmcp 3.4.11
  builds its session manager without a size. Nothing tested the 100 MiB path end to end, so
  Dependabot's one-line bump (#183) looked harmless apart from an unexplained red `ci-gate`.
  The new guard sends 5 MiB through the real transport: `1 passed` on 1.28.1, and
  `413 Request Entity Too Large` on 1.30.0.
- **#183's red CI was our code, not a flake (confirmed).** mcp 1.30 wraps the SDK's `/authorize`
  endpoint in that middleware, turning it into an ASGI app. The binding-cookie wrapper in
  `auth/oauth_provider.py` calls it as `Request → Response`, which raised
  `RequestBodyLimitMiddleware.__call__() missing 2 required positional arguments`.
- **The OpenTelemetry Prometheus exporter changed `/metrics` (confirmed by a red test).** 0.66
  adds `otel_scope_name`, `otel_scope_version` and `otel_scope_schema_url` to every series by
  default. `test_mcp_call_metrics_reach_the_prometheus_scrape` failed on it. The reader is now
  built with `scope_info_enabled=False`, and the exporter floor is 0.66b2.
- **An unconstrained uv upgrade takes soupsieve across two majors (observed).** The dry run
  went 2.8.4 → 4.0.1. The alerts need 2.9, so the lock holds 2.9.2.
- **npm 10 cannot resolve vitest 4.1.12's peers (observed).** npm 10.9.4 crashed with
  `Cannot read properties of null (reading 'edgesOut')`, so the frontend lock was resolved
  with npm 11. A clean `npm ci` with npm 10 installs it unchanged, which is what CI runs.
- **`main` requires PRs to be up to date.** Each merge made the next PR `BEHIND`, so the five
  merges ran serially: update the branch, wait for CI, merge, wait for `promote`, verify.
- **pillow-heif 1.4.0 works with Pillow 13 (confirmed during planning).** The HEIC and
  thumbnail tests passed in an overlay environment before W1 was written.

## 3. What is deliberately not done

- **No new majors beyond Pillow.** vitest 5 and soupsieve 4 exist, but no alert needs them.
- **No npm `overrides`** to force minimatch 11 under eslint, which eslint hasn't been tested with.
- **No CI gate that fails on new alerts.** The grouping in §4 is the cheaper fix to try first.
- **mcp alerts #89, #174, #175, #176 (medium), left open.** Fixed only in mcp 1.30, which caps MCP uploads
  at 4 MiB under fastmcp 3.4.11. A spike raised the cap, but only by rebinding fastmcp's
  module-level session-manager class and overwriting mcp's private `asgi_app`. The advisories
  don't reach this server: it runs stateless (#89), its authorization server issues tokens only
  for itself (#176), and #174 and #175 are client-side. `docs/mcp.md` §1.2 records it. Revisit
  when fastmcp exposes the limit. The guard test fails on any SDK that brings the cap back.
- **glob 10.5.0 under `@vue/test-utils` (dev-only).** `npm audit` reports GHSA-3g3m-2fjr-6pw4,
  but no fix is in range and it isn't a Dependabot alert yet. If one appears, treat it like
  minimatch.

## 4. Why the alerts piled up, and the change

Security updates for pip and npm came from the repository setting, one PR per package, with no
`dependabot.yml` entry. Fifteen PRs sat open, the oldest from 2026-09-03, and most went stale
against `main`. `.github/dependabot.yml` now has `uv` and `npm` entries with
`open-pull-requests-limit: 0` (security updates only) and one `applies-to: security-updates`
group each. Each ecosystem now gets a single PR carrying every available fix.
`docs/deployment.md` §1.7.3 says how to handle them.

## 5. Later the same day: the 4 MiB cap matters less than it looked

Asked whether a 3 MiB limit on MCP uploads would hurt, a look at the live archive found:

- **No document has come in through MCP** (confirmed). Every PDF arrived by email, import,
  the consume folder or web upload.
- **A few percent of PDFs are over 3 MiB** (confirmed), as are some phone-photo JPEGs.

The bigger point is about how tool calls work (strongly supported, not measured). The model
writes a call's arguments itself, so every character of `content_base64` is model output. A
3 MiB file is about 4 million characters, far more than a model writes in one reply. The
practical limit on an MCP upload is therefore much lower than the 4 MiB transport cap, and the
cap is not what holds the SDK upgrade back. `docs/mcp.md` §1.2 now says the hold is about
value: small gains against about a session of OAuth-path fixes. It also lists when to upgrade.
