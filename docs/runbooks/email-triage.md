# Email triage: reading the decision trace

**Status:** active. **Last updated:** 2026-07-14.

"What happened to the email I forwarded?" — the answer is always in the
**decision trace**: one greppable log line per email recording what happened to
every item (`[body, att1 … attN]`), at which stage, and why. This runbook is the
reading guide: the line format, the queries that pull it, and the
action-per-reason table. The selection *rules* themselves (noise gate, substance
gate, LLM label pass, hold-for-review) live in
[../ingestion.md](../ingestion.md), "Email item selection" — this doc only
covers reading the outcome.

## 1. When to use this

Reach for the trace when:

- a forwarded email did not show up as a document (or showed up flagged);
- an email landed in the held queue and you want to know which gate held it;
- a real attachment seems to have been filtered as noise;
- you are tuning the noise/label/hold settings and want to see their effect.

## 2. The trace line

Emitted by `_log_selection_trace` (`src/library/email_ingest.py`), once per
**processed** message and once per **held** message (the hold path traces too,
before the message moves to the Held folder). Format:

```
email-selection msg='<subject>' from='<sender>' items=N ingested=a duplicate=b \
  dropped=c filtered=d flagged=e :: <item> | <item> | …
```

Each `<item>` renders as `name:stage:verdict(reason)`; an item without a
filename renders its kind, e.g. `<body>` or `<email>`. A processed invoice
forward looks like:

```
email-selection msg='Energy bill' from='biller@example.com' items=3 ingested=1 \
  duplicate=0 dropped=0 filtered=1 flagged=0 :: \
  bill.pdf:classify:ingested | logo.png:classify:filtered(signature_image) | \
  <body>:body_substance:filtered(not_needed)
```

A **held** email carries one extra item — the whole-email verdict, kind
`email`, stage `email_verdict`, verdict `held`, with the hold reason in
parentheses. A newsletter held by the LLM pass looks like:

```
email-selection msg='Your weekly digest' from='news@example.com' items=2 \
  ingested=0 duplicate=0 dropped=0 filtered=1 flagged=0 :: \
  banner.png:classify:filtered(signature_image) | \
  <email>:email_verdict:held(marketing newsletter, no document content)
```

Note the counters (`ingested=…flagged=`) count only the five per-item verdicts;
a `held` item shows up in `items=N` and in the item list, not in the counters.

## 3. When there is no trace line

Two kinds of message never reach the trace — grep for their own log lines
instead:

- **Allowlist-rejected mail while holds are off** (`LIBRARY_EMAIL_HOLD_ENABLED`
  or `LIBRARY_EMAIL_HOLD_UNKNOWN_SENDERS` false) — skipped before selection
  runs and left in place; each poll logs a per-message WARNING:
  `grep 'not in allowlist'`. (With both switches on — the default — an unknown
  sender is *held* instead, and the hold path does emit a trace.)
- **Messages whose processing raised** — the error is logged and the message is
  left in place for the next poll: `grep 'failed to process message'`.

## 4. Queries

Plain stdlib logging, so grep and Loki both work:

```
# grep the worker logs
grep 'email-selection' worker.log | grep 'Energy bill'

# Loki (worker job label)
{job="library-worker"} |= "email-selection"

# Was the LLM label pass skipped? (budget/error — fail-open, not a per-item verdict)
{job="library-worker"} |= "email-label" |= "skipped"
```

**What is currently held:** held emails are durable rows in the `held_emails`
table (`status='held'`), each carrying the full trace — queryable directly,
via `GET /api/held-emails` ([../api.md](../api.md) §1.21), and in the web
app's `/held-emails` view.

```sql
SELECT id, created_at, sender, subject, verdict, reason
FROM held_emails WHERE status = 'held' ORDER BY created_at DESC;
```

## 5. What each verdict/reason means, and what to do

| verdict(reason) | Meaning | Action |
| --- | --- | --- |
| `ingested` | Filed as a document | none |
| `duplicate` | Content already in the library | none |
| `flagged_ambiguous` | Ingested, LLM thought it might be noise | check the needs-review queue; verify the doc |
| `filtered(signature_image\|tiny_image\|non_document_type)` | Deterministic noise, quietly dropped | none if truly noise; if a real doc was filtered, lower the threshold or set `LIBRARY_EMAIL_FILTER_NOISE_ENABLED=false` and re-forward |
| `filtered(below_substance:<n>w\|blank\|not_needed\|oversize)` | Body not filed (thin — `<n>w` is the post-strip word count, e.g. `below_substance:8w` / empty / an attachment already won / too large). A body-only `below_substance` mail is *held* instead when `LIBRARY_EMAIL_HOLD_BELOW_SUBSTANCE` is on (the default) | none |
| `dropped(oversize\|unsupported_type\|error)` | Surfaced attachment drop — also on the sibling's `email_siblings_dropped` + a push. (A body whose ingest is rejected renders as `dropped(rejected)`.) | investigate; re-send in a supported form if it was real |
| `held(<LLM reason>)` — row verdict `llm_hold` | The label pass judged the *whole email* not library material (newsletter/marketing/notification); nothing ingested, message moved to the Held folder | open the held queue; *ingest anyway* or *dismiss* |
| `held(below_substance:<n>w)` — row verdict `below_substance` | Body-only mail under the substance gate; instead of quietly filing it away, it waits for review | open the held queue; *ingest anyway* or *dismiss* |
| `held(no attachment or body produced a document)` — row verdict `nothing_ingested` | Every user-facing drop failed and nothing was filed (all-duplicate emails still file to Processed) | open the held queue; fix the cause of the drops, then *ingest anyway* or *dismiss* |
| `held(sender <addr> not in allowlist)` — row verdict `sender_unknown` | Allowlist reject, held when `LIBRARY_EMAIL_HOLD_UNKNOWN_SENDERS` is on | open the held queue; add the sender to `LIBRARY_EMAIL_ALLOWED_SENDERS` and *ingest anyway*, or *dismiss* |
| a trace with `flagged=0` while the LLM pass is on | The label pass was skipped (not a per-item verdict) | look for a `email-label: pass skipped (budget\|error)` log line — raise the budget or accept degradation |

All hold triggers require the master switch `LIBRARY_EMAIL_HOLD_ENABLED` (on by
default); with it off, every trigger reverts to the pre-hold behaviour. Held
messages live in `LIBRARY_EMAIL_HELD_FOLDER` (default `Library/Held`), never in
Processed; a held email also does **not** fire the "attachments couldn't be
added" push (that fires only after a successful Processed move).

## 6. Where the persisted copies live

The log line is ephemeral; two durable copies exist:

- **Documents.** When an email produced a document, the same per-item trace is
  stored as an `email_selection` `IngestionEvent` on each *new* document, and
  the LLM billing as an `email_label_completed` event anchored on the **first
  produced document — new or duplicate** (so an all-duplicate re-send still
  records its spend). The per-item trace renders in the document history's
  **"Email triage"** breakdown (`DocumentHistoryTimeline.vue`); the billing
  event is telemetry — hidden from the milestone timeline, visible under
  **"Show all events"**.
- **Held emails.** A held email produced no document, so its `held_emails` row
  snapshots the full trace in the `trace` column (the `email_selection` event
  shape, plus `label_usage` when the LLM pass billed for the email). The
  `label_usage` cost is not audit-only: it counts toward the daily label
  budget alongside the `email_label_completed` event totals.

An email that was neither held nor produced any document lives only in the log
line — which is why the trace is the primary triage surface.

**Tuning knobs**: the noise-gate, label-pass, and hold settings
(`LIBRARY_EMAIL_FILTER_*`, `LIBRARY_EMAIL_LABEL_*`, `LIBRARY_EMAIL_HOLD_*`,
`LIBRARY_EMAIL_HELD_FOLDER`) are all listed with their defaults in
[../ingestion.md](../ingestion.md), "Configuration".

## 7. Enabling the LLM email verdict

The label pass — the per-item `keep`/`probably_noise` labels **and** the
whole-email `file`/`hold` verdict — is off by default because it spends money.
To enable it:

```sh
LIBRARY_EMAIL_LABEL_ENABLED=true
# LIBRARY_ANTHROPIC_API_KEY must be set — it is already, wherever extraction
# runs (the label pass reuses the same key). Optional knobs (defaults shown):
#LIBRARY_EMAIL_LABEL_MODEL=claude-haiku-4-5
#LIBRARY_EMAIL_LABEL_DAILY_BUDGET_USD=2.0
#LIBRARY_EMAIL_LABEL_BODY_SNIPPET_CHARS=1000
```

All email settings are **env-only** (no admin API), so the change takes effect
only after a **worker container restart**. Expect roughly **$0.002–0.007 per
email** at Haiku pricing (one small call per polled message), hard-capped by
the daily budget — over budget the pass skips and everything files as before
(fail-open). The cap counts **all** billed calls: spend from emails that filed
(`email_label_completed` events) *and* spend from emails that were held (the
`label_usage` in each held row's trace), so a day of nothing-but-newsletters
still stops at the budget. Known residual: a poll retry after a failed
Held-folder move re-bills the label call without recording the second call's
cost (the held row already exists) — a small under-count, itself bounded by
the budget gate.

**Verify** within one poll cycle (`LIBRARY_EMAIL_POLL_MINUTES`, default 10):

1. Worker logs show the pass at work: `grep 'email-selection'` for the traces,
   `grep 'email-label'` for skip lines (`pass skipped (budget|error)` means
   fail-open degradation, not a crash).
2. An email that produced a document records an `email_label_completed` event
   (nonzero `cost_usd`) — visible under the document history's **"Show all
   events"** list (billing telemetry, not a milestone).
3. Send a newsletter to the dropbox: it should appear in `/held-emails` with
   verdict `llm_hold` and the model's reason. A forwarded invoice should file
   normally.
