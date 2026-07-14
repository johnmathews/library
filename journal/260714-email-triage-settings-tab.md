# Email triage settings tab

**Date:** 2026-07-14
**Branch:** `feat/email-triage-settings-tab`

## What

A read-only **Email triage** tab in `/settings` that exposes how the app
decides whether an inbound email is filed or held. Per the requirement:
"it doesn't have to be configurable for now, but the logic shouldn't be
hidden." Nothing is editable — the tab shows the live effective configuration
and explains the decision flow.

- **Backend:** `GET /api/settings/email-triage` (same authenticated settings
  router), computed from `get_settings()` on every request. Read models in
  `schemas.py` (`EmailTriageOut` + five sub-models). Secret-free by
  construction: never the IMAP credentials/host (only an
  `email_in_configured` boolean), never the Anthropic key (only
  `label.active` = enabled AND key present), never the allowlisted addresses
  (only `allowlist.count` — any authenticated user can read the page).
- **Frontend:** a fourth tab in `SettingsView.vue` (the view already had a
  `role="tablist"` tab mechanism), loaded lazily on the tab's first show.
  Master "Hold pipeline ON/OFF" badge, link to `/held-emails`, the five-step
  decision flow (allowlist → noise gate → LLM verdict → body substance →
  nothing-ingested) with live values and `AppBadge` ON/OFF per switch, an
  environment-only footnote, and an unconfigured empty state.

## Decisions

- **Config import alias.** `library.config.get_settings` collides with the
  existing `GET /settings` route handler (also named `get_settings`) in
  `api/settings.py` — imported as `get_app_settings`. The first test run
  caught the shadowing (`TypeError: get_settings() missing 1 required
  positional argument: 'user'`).
- **Body-substance constants exported.** `_BODY_MIN_WORDS`/`_BODY_MIN_CHARS`
  in `email_ingest.py` renamed to public `BODY_MIN_WORDS`/`BODY_MIN_CHARS`
  and imported by the endpoint (and its tests) rather than duplicating the
  literals. They stay module constants — changing them is a code change, and
  the tab presents them as fixed thresholds, not configuration.
- **Lazy load on first tab show** (a `watch` on the tab ref) rather than
  `onMounted`: the data is only needed if the user looks, and it leaves the
  existing SettingsView specs' fetch expectations untouched.
- **`prompt_version` from `email_label.PROMPT_VERSION`** so the tab can never
  drift from the shipped prompt.
- Tests drive the endpoint through env overrides + `get_settings.cache_clear()`
  (the conftest pattern); the label-model override uses `claude-sonnet-4-6`
  because any `email_label_model` value must have a `MODEL_PRICING_USD_PER_MTOK`
  row (startup validator).

## Docs

`api.md` §1.1 summary + new §1.10.6; `frontend.md` SettingsView row;
`CHANGELOG.md` [Unreleased] Added. Semantics stay single-sourced in
`ingestion.md` ("Email item selection" / "Held for review") and
`runbooks/email-triage.md`.
