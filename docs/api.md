# REST API

**Status:** active. **Last updated:** 2026-08-31 (§1.18.6's closing sentence said the series override tables were "still there, orphaned until the drop migration". Migration 0038 has now dropped them, so it says so.) Earlier the same day (the legacy series stack was deleted. **§§1.13–1.15 are removed and the numbering deliberately does not close up** — a note at the seam says so, because 28 citations of §1.16 and later live across nine documents plus `AdminMetadataPanel.vue`, and nothing in the toolchain checks a section-number citation, so renumbering would silently invalidate all of them. The route table loses sixteen rows: the fifteen `/api/charts` and `/api/series/*` routes and `GET /api/documents/{id}/series`. §1.18.6 is rewritten — currency normalisation is now a single `UPDATE documents`, `counts` carries only `documents`, and **there is no `409`**; the conflict case existed only because currency was part of series identity. Smaller: `compare_to_series` out of §1.12's `used_tools` list, the `series_insight` object out of the LLM-surfaces example payload, and "series" out of two soft-delete cascade lists and the shared-corpus sentence. **Fix round 1:** §1.18.6 said the override tables "went with the legacy series stack" — the *code* that read them did; the tables are orphaned until the drop migration, and saying otherwise implied PR 2 had already shipped.) Earlier: 2026-08-30 (facet vocabulary panel, Task 11: new §1.23.6 documents `GET /api/facets/label-counts` — the number the vocabulary panel shows, and the one `DELETE .../values/{value_key}` enforces via `count_labels` rather than a second inlined copy — and why it is a separate route from §1.23.5's `/api/facets/counts` rather than a field on it: the two diverge on amountless documents, soft-deleted/non-canonical documents, and split-line labels, and widening the money route would have broken `test_a_value_with_no_money_behind_it_is_absent`; new endpoint-summary rows, §1.1/§1.23). Earlier the same day (spending-view backend, final fix wave: §1.25's footer-bucket row now documents the `422` when `bucket=excluded` and `amount_kind` is omitted. Earlier the same day, Task 8: new §1.25 documents `GET /api/spending/{id}` and `GET /api/spending/{id}/footer/{bucket}` — the whole ten-route pre-existing `/api/spending` surface stays documented only in [charts.md](charts.md) §11, referenced rather than duplicated; new §1.23.5 documents `GET /api/facets/counts`, including why `is_canonical` and `count(DISTINCT ...)` in its query are not redundant; §1.23.2's JSON example gained the `colour` field it was missing since the colour write-surfaces pass below; new endpoint-summary rows for all three, §1.1)). Earlier the same day — colour write surfaces: `PATCH /api/facets/{facet_key}/values/{value_key}` now also accepts an optional `colour` — a six-digit `#rrggbb` hex or `null` to clear it — alongside `label`, each independently optional and told apart by presence in the body, not by value; documented `colour` on `GET /api/facets`'s values and `GET /api/senders`'s rows, and the new colour-only `PATCH /api/senders/{sender_id}` (§1.8.4/§1.8.4.0, §1.23); new endpoint-summary rows, §1.1). Earlier (2026-08-29, money facts fix round 3: §1.24's closing note no longer says repeating an override "is a no-op, not a conflict" — `add_override` inserts `ON CONFLICT DO UPDATE SET created_at = now()`, so a repeat refreshes the row's timestamp, which is exactly what makes a third correction on a pair land; the note also now says the identical-timestamp tie-break is defensive, since no request sequence can reach it. Earlier the same day — money facts fix round 2: §1.24's `merge`/`split` rows now document the `404` on an unknown or soft-deleted id, which `_require_both_exist` has always returned and the table listed only the `422` for; the reversal note no longer cites the split-then-merge test as if it proved both directions — each direction now has its own test, and the note states the latest-correction-wins rule and its tie-break. Earlier the same day — money facts: new §1.24 — payment identity endpoints (`GET /api/documents/{id}/payment`, `POST /api/payments/merge`/`split`, `GET /api/payments/duplicates`); new endpoint-summary rows, §1.1). Earlier (2026-08-28, facet vocabulary fix wave: the merge row now promises `404` on either side **on a dry run too** and `409` when `into` names the value being merged (§1.23/§1.23.1); `PUT /api/documents/{id}/labels` answers `404` for an unknown or soft-deleted document instead of a `500`; `POST /api/facet-suggestions/{id}/accept` sanitises the key it derives to the documented `^[a-z0-9_-]+$`, 1–64 contract and answers `422` when nothing usable remains (§1.23.3)). Earlier the same day (facet vocabulary: new §1.23 — the controlled facet CRUD surface (`/api/facets`), document labels (`/api/documents/{id}/labels`), and the suggestion queue (`/api/facet-suggestions`), including the `merge` route's `dry_run` and the `422`-on-out-of-vocabulary-value on `PUT .../labels`; repeatable `?facet=key:value` document filter, AND-composing, §1.3.1/§1.23.4; new endpoint-summary rows, §1.1. See [facets.md](facets.md) for the vocabulary design). Earlier (2026-08-27, series coverage: `GET /api/documents/{id}/series` (§1.13), `GET /api/charts` and `GET /api/charts/{series_id}` (§1.14) now carry a top-level `coverage` block — `matched`/`included`/`excluded`/`needs_review` — on every emergent `status:"ok"` result and on §1.13's own `status:"insufficient"` result once `summarize_series` has actually run; it is **absent** (the key omitted, not `null`) for an authored (user-curated) series and for §1.13's bespoke no-sender/kind short circuit, and a present block with an empty `excluded` means nothing was dropped, which is not the same claim as absent. See [ask.md §1.2/§1.7](ask.md) for the full design). Earlier (2026-08-25, Ask profile: new `PUT /api/settings/ask-profile` and the `ask_profile` key on the resolved preference set, §1.10.11; the Ask tools' new recipient/project/matter/tag filters are prompt-side, documented in [ask.md §1.2](ask.md). Earlier (2026-08-20, LLM backend selection: new `GET /api/settings/llm-backends` and admin-only `PUT`/`DELETE /api/settings/llm-backends/{surface}`, §1.10.8–1.10.10; `POST /api/ask` now answers **503** with the fix when the subscription backend cannot authenticate, §1.11). Earlier: 2026-08-12 (documentation verification sweep: documented `DELETE /api/admin/users/{id}` and the Smart Groups create/exclusion contract; corrected the `status` enum, the login/`me` and preferences shapes, the coverage and note-edit claims, and the `ts_rank` normalisation). Earlier (2026-07-17, business matters: business matters: `/api/matters` CRUD + per-matter document counts, new §1.22; repeatable `?matter=` document filter with OR semantics, §1.3.1; `matters` on document list/detail responses (§1.3.2) and the `PATCH /api/documents/{id}` body (§1.5)). Earlier (2026-07-15, email-triage skip audit: new `GET /api/settings/email-triage/recent-skips` — the last 20 emails with a skipped item, §1.10.7; `noise_filter` gains `decoration_max_bytes`/`decoration_max_edge_px`, §1.10.6). Earlier (2026-07-08, Ask conversation titles: new threads are auto-named by a cheap title model instead of the truncated first question; `PATCH /api/ask/threads/{id}` renames a conversation, §1.11). Earlier (2026-07-06, document comments: `GET`/`POST /api/documents/{id}/comments`, `PATCH`/`DELETE /api/documents/{id}/comments/{cid}` — new §1.19; document detail's `comments` field, §1.4; Ask's `used_tools` gains `get_document`, §1.11). Earlier (2026-07-03, verification flow): `PATCH /api/documents/{id}` now revalidates on save so a corrected field clears its own warning and never un-verifies a human-verified doc, §1.5; list rows carry compact `review_findings` explaining why a document needs review, §1.3.2. Earlier (2026-07-01, authored-series smart features): `signature`, `suggestions` (propose-for-review auto-continue), `odd-ones-out` with a deterministic grounded reason (no LLM — an earlier LLM reason hallucinated a sender absent from every document); additive `signature`/`suggestion_count`/`odd_one_out_count` on `/charts` authored entries, §1.14.3. Earlier: authored series `POST`/`PATCH`/`DELETE /api/charts/authored` + members — user-curated manual series alongside emergent ones, stable `a-{id}` ids, §1.14.2; admin recipient management: `PATCH`/`DELETE /api/admin/recipients/{id}`; recipient field: `GET /api/recipients`, `recipient` in document responses + PATCH body, `recipient_id` list filter).
**Last verified:** 2026-08-31 — method: partial, scoped to §1.18.6's closing sentence. Read `migrations/versions/0038_drop_series_stack.py` and confirmed `series_membership_overrides` and `series_meta_overrides` — the two tables that sentence names — are both in its `_DROP_ORDER`; `tests/test_migrations.py::test_series_stack_tables_are_dropped` proves it against a real database rather than against the migration's own text. The rest of §1.18.6 (the single `UPDATE documents`, `counts` carrying only `documents`, no `409`) was not re-checked and carries forward the verification below unchanged, whose method was: partial, scoped to the series surface.

The REST API is a first-class product surface: everything the web app can
do is available to scripts, shortcuts, and other tools over plain HTTP.
Interactive OpenAPI documentation is served at `/docs` (schema at
`/openapi.json`).

All endpoints live under the `/api` prefix and exchange JSON unless noted.
Decimal money values (`amount_total`) are serialized as JSON **strings**
(e.g. `"123.45"`) to preserve precision.

**Every `/api` endpoint requires authentication** (session cookie or
bearer token — see 1.9) except `POST /api/auth/login`. `/healthz` is open
(container healthcheck, no database access). Unauthenticated requests get
`401` with the generic body `{"detail": "not authenticated"}`.

## 1.1 Endpoint summary

| Method | Path | Purpose |
|--------|------|---------|
| POST   | `/api/auth/login` | Log in; sets session + CSRF cookies (no auth required) |
| POST   | `/api/auth/logout` | Log out; revokes the session, clears cookies |
| GET    | `/api/auth/me` | The authenticated user |
| GET    | `/api/auth/tokens` | List your API tokens (never their secrets) |
| POST   | `/api/auth/tokens` | Create an API token; secret shown **once** |
| DELETE | `/api/auth/tokens/{id}` | Revoke one of your API tokens |
| POST   | `/api/ask` | Ask a natural-language question; cited answer |
| GET    | `/api/ask/threads` | List your Ask conversations |
| GET    | `/api/ask/threads/{id}` | Full thread detail (all turns) |
| PATCH  | `/api/ask/threads/{id}` | Rename a conversation (`{"title": "..."}`, 1–120 chars) |
| DELETE | `/api/ask/threads/{id}` | Delete a conversation and its turns |
| POST   | `/api/documents` | Upload a file for ingestion |
| GET    | `/api/documents` | List / search documents |
| GET    | `/api/documents/{id}` | Full document detail |
| GET    | `/api/documents/{id}/markdown` | Per-page markdown rendering of a document |
| PATCH  | `/api/documents/{id}` | Edit metadata |
| DELETE | `/api/documents/{id}` | Soft-delete |
| GET    | `/api/documents/deleted` | List soft-deleted documents (Recently Deleted) |
| POST   | `/api/documents/{id}/restore` | Restore a soft-deleted document |
| DELETE | `/api/documents/{id}/permanent` | Permanently (hard) delete a soft-deleted document |
| POST   | `/api/documents/{id}/extract` | Queue metadata re-extraction |
| POST   | `/api/documents/{id}/verify` | Mark document metadata as verified |
| GET    | `/api/documents/{id}/original` | Download the original file |
| GET    | `/api/documents/{id}/searchable.pdf` | Download the OCR searchable PDF |
| GET    | `/api/documents/{id}/thumbnail` | First-page WebP thumbnail |
| GET    | `/api/documents/{id}/comments` | List a document's comments, newest first |
| POST   | `/api/documents/{id}/comments` | Add a comment to a document |
| PATCH  | `/api/documents/{id}/comments/{cid}` | Edit a comment's body |
| DELETE | `/api/documents/{id}/comments/{cid}` | Delete a comment |
| POST   | `/api/notes` | Author a new in-app markdown note |
| PATCH  | `/api/notes/{id}` | Edit a note's title/body in place (snapshots a version) |
| GET    | `/api/notes/{id}/versions` | A note's version history (newest first) |
| POST   | `/api/notes/{id}/versions/{version_no}/restore` | Restore a note to a previous version |
| GET    | `/api/kinds` | Document kinds with counts |
| POST   | `/api/kinds` | Create a document kind (dedupes / rejects near-duplicates) |
| GET    | `/api/senders` | Senders with counts |
| PATCH  | `/api/senders/{sender_id}` | Set or clear a sender's stored chart colour |
| GET    | `/api/recipients` | Recipients with counts |
| GET    | `/api/tags` | Tags with counts |
| GET    | `/api/projects` | List projects/collections with counts |
| POST   | `/api/projects` | Create a project (admin only) |
| GET    | `/api/projects/{slug}` | Project detail |
| PATCH  | `/api/projects/{slug}` | Edit a project (name/description/archived) (admin only) |
| DELETE | `/api/projects/{slug}` | Delete a project (memberships cascade) (admin only) |
| GET    | `/api/matters` | List business matters with counts |
| POST   | `/api/matters` | Create a matter (admin only) |
| GET    | `/api/matters/{slug}` | Matter detail |
| PATCH  | `/api/matters/{slug}` | Edit a matter (name/hint/archived) (admin only) |
| DELETE | `/api/matters/{slug}` | Delete a matter (memberships cascade) (admin only) |
| GET    | `/api/facets` | The whole facet vocabulary: facets, values, aliases |
| GET    | `/api/facets/counts` | Document counts per facet value (what the empty state proposes charts from) |
| GET    | `/api/facets/label-counts` | Documents actually carrying each facet value — the number the vocabulary panel shows and `delete` enforces (§1.23.6) |
| POST   | `/api/facets` | Create a facet |
| POST   | `/api/facets/{facet_key}/values` | Add a value to a facet |
| PATCH  | `/api/facets/{facet_key}/values/{value_key}` | Edit a value's label and/or stored chart colour |
| POST   | `/api/facets/{facet_key}/values/{value_key}/aliases` | Add an alias to a value |
| POST   | `/api/facets/{facet_key}/values/{value_key}/merge` | Fold one value into another; `dry_run` previews without writing |
| DELETE | `/api/facets/{facet_key}/values/{value_key}` | Delete an unused value (`409` if in use) |
| GET    | `/api/documents/{id}/labels` | This document's facet labels |
| PUT    | `/api/documents/{id}/labels` | Set or clear facet labels (`422` on an out-of-vocabulary value) |
| GET    | `/api/facet-suggestions` | Pending values the labeller wanted but could not use |
| POST   | `/api/facet-suggestions/{id}/accept` | Create the suggested value and label the document with it |
| POST   | `/api/facet-suggestions/{id}/dismiss` | Reject a suggestion |
| GET    | `/api/documents/{id}/payment` | The payment this document belongs to, and its partner documents |
| POST   | `/api/payments/merge` | Record that two documents are one payment |
| POST   | `/api/payments/split` | Record that two documents are separate payments |
| GET    | `/api/payments/duplicates` | Groups of ≥2 documents describing one payment, largest first, capped at 100 |
| GET    | `/api/spending/{id}` | One saved spending question, by id |
| GET    | `/api/spending/{id}/footer/{bucket}` | The documents behind one footer count, paged, `limit` ≤ 100 |
| GET    | `/api/jobs` | Recent background jobs (enriched with document state); filter by `task_name`/`document_id` |
| GET    | `/api/jobs/task-names` | Distinct task names (for the task-type filter) |
| GET    | `/api/events` | Live document-pipeline events (Server-Sent Events) |
| GET    | `/api/held-emails` | The hold-for-review queue (emails the poller held instead of filing) |
| GET    | `/api/held-emails/{id}` | One held email with its full decision trace |
| POST   | `/api/held-emails/{id}/ingest` | Ingest a held email anyway (queues the override task; `202`) |
| POST   | `/api/held-emails/{id}/dismiss` | Dismiss a held email (DB-only; the message stays in the Held folder) |
| GET    | `/api/settings` | Your display preferences (dashboard fields + page-canvas tone + tile preview + action dock position) |
| PUT    | `/api/settings` | Update your dashboard fields |
| PUT    | `/api/settings/appearance` | Update your page-canvas tone, tile preview, action dock position, phone columns, and mobile hide-description flag |
| PUT    | `/api/settings/kind-colors` | Update your per-kind tile border colours |
| PUT    | `/api/settings/notifications` | Update your Pushover notifications + email forwarding addresses |
| GET    | `/api/settings/email-triage` | Effective email-in triage configuration (instance-wide, read-only, secret-free) |
| GET    | `/api/settings/email-triage/recent-skips` | Last 20 emails with a skipped item (read-only skip audit) |
| GET    | `/api/admin/system` | System & infra context: version, config, deployment, DB stats (admin only) |
| GET    | `/api/admin/architecture` | Architecture docs as markdown (admin only) |
| GET    | `/api/admin/coverage` | Latest CI-generated test coverage (admin only) |
| GET    | `/api/admin/users` | List all users (admin only) |
| POST   | `/api/admin/users` | Create a user (admin only) |
| PATCH  | `/api/admin/users/{id}` | Promote/demote, activate/deactivate a user (admin only) |
| DELETE | `/api/admin/users/{id}` | Delete a user (admin only) |
| POST   | `/api/admin/recipients` | Create a recipient (dedupes case-insensitively) (admin only) |
| PATCH  | `/api/admin/recipients/{id}` | Rename or merge a recipient (admin only) |
| DELETE | `/api/admin/recipients/{id}` | Delete a recipient, reassigning its documents (admin only) |
| POST   | `/api/admin/senders` | Create a sender (dedupes case-insensitively) (admin only) |
| PATCH  | `/api/admin/senders/{id}` | Rename or merge a sender (admin only) |
| DELETE | `/api/admin/senders/{id}` | Delete a sender, reassigning its documents (admin only) |
| PATCH  | `/api/admin/kinds/{slug}` | Rename a kind's display name; slug is immutable (admin only) |
| DELETE | `/api/admin/kinds/{slug}` | Delete a kind, reassigning its documents (admin only) |
| GET    | `/api/admin/currencies` | Distinct currency codes with counts (admin only) |
| POST   | `/api/admin/currencies/normalize` | Rename a currency code store-wide (admin only) |
| GET    | `/api/admin/fx-rates` | FX rates per in-use currency (base = USD) (admin only) |
| POST   | `/api/admin/fx-rates` | Seed an FX rate (live fetch or manual) (admin only) |
| GET    | `/api/saved-views` | List the caller's saved views |
| POST   | `/api/saved-views` | Create a saved view |
| PATCH  | `/api/saved-views/{id}` | Rename / re-target / pin a saved view |
| DELETE | `/api/saved-views/{id}` | Delete a saved view |
| POST   | `/api/saved-views/reorder` | Reorder the caller's saved views |

Soft-deleted documents return **404** from every per-document read endpoint and
never appear in lists or search. They surface only via
`GET /api/documents/deleted` until restored (`POST /api/documents/{id}/restore`)
or purged after the retention window (see §1.6). Other error shapes: `404`
unknown document, `422` validation problem (FastAPI detail body),
`409`/`413`/`415` on upload (see [ingestion.md](ingestion.md)).

## 1.2 Upload — `POST /api/documents`

Multipart upload (`file` field). `201` with `{id, sha256, status,
duplicate}` for a new document, `200` for duplicate content (pointing at
the existing document), `409` if the content matches a soft-deleted
document, `413` over the size limit, `415` unsupported type. The
authenticated user is recorded as the document's uploader. Full
ingestion semantics are documented in [ingestion.md](ingestion.md).

## 1.3 List and search — `GET /api/documents`

### 1.3.1 Query parameters

| Param | Type | Meaning |
|-------|------|---------|
| `q` | string | Full-text search, [websearch syntax](https://www.postgresql.org/docs/current/textsearch-controls.html) (quoted phrases, `OR`, `-exclusion`) |
| `kind` | string | Kind slug (e.g. `invoice`) |
| `sender_id` | int | Sender id |
| `recipient_id` | int | Recipient id |
| `tag` | string, repeatable | Tag slug; repeating the parameter ANDs them (`?tag=energie&tag=wonen` requires both) |
| `project` | string, repeatable | Project slug; repeating the parameter **ORs** them (`?project=a&project=b` returns documents in *either*) — unlike `tag`, which ANDs. A document rarely belongs to several projects, so intersection would usually return nothing |
| `matter` | string, repeatable | Business-matter slug; repeating the parameter **ORs** them (`?matter=a&matter=b` returns documents in *either*) — like `project`, and unlike `tag` which ANDs. A document belongs to any number of matters, so OR is the useful default |
| `facet` | string, repeatable | `key:value` facet-label filter (§1.23.4); repeating the parameter with different keys **ANDs** them (a document holds at most one value per facet). `422` if malformed, or if the same key is repeated with two different values |
| `language` | enum | `nld` / `eng` / `mixed` / `unknown` |
| `status` | enum | `received` / `ocr` / `extract` / `markdown` / `embed` / `indexed` / `failed` |
| `date_from`, `date_to` | date | Inclusive bounds on `document_date` |
| `review_status` | enum | `verified` / `needs_review` / `unreviewed` — filter by extraction-quality review state |
| `source` | enum | `upload` / `consume` / `email` / `api` / `mcp` / `import` / `note` |
| `sort` | enum | `document_date` (default) / `added_date`. Field the non-search list is ordered by. **Ignored when `q` is set** — search always orders by relevance rank |
| `direction` | enum | `desc` (default) / `asc`. Direction for `sort`. Ignored when `q` is set |
| `limit` | int | Page size, default 25, max 100 |
| `offset` | int | Rows to skip, default 0 |

All filters compose (AND), including with `q`. Ordering: without `q`, results
are sorted by `sort`/`direction` (default `document_date desc`, with unknown
dates always last, then `created_at`, then `id`); `added_date` sorts by the
row's `created_at`. With `q` present, relevance rank always wins and
`sort`/`direction` are ignored.

### 1.3.2 Response

```json
{
  "items": [
    {
      "id": 12, "title": "Energierekening mei 2026", "summary": "…",
      "kind": {"slug": "invoice", "name": "Invoice"},
      "sender": {"id": 3, "name": "Eneco"},
      "recipient": {"id": 1, "name": "John"},
      "tags": [{"slug": "energie", "name": "Energie"}],
      "projects": [{"slug": "house-purchase", "name": "House purchase"}],
      "matters": [{"slug": "car-insurance", "name": "Car insurance"}],
      "document_date": "2026-05-15", "due_date": null, "expiry_date": null,
      "language": "nld",
      "status": "indexed", "review_status": "unreviewed",
      "mime_type": "application/pdf",
      "page_count": 2,
      "created_at": "2026-06-10T12:00:00Z", "updated_at": "2026-06-11T09:30:00Z",
      "has_searchable_pdf": true, "has_thumbnail": true,
      "amount_total": "123.45", "currency": "EUR",
      "snippet": "uw <b>rekening</b> voor mei … totaal",
      "rank": 0.31
    }
  ],
  "total": 1, "limit": 25, "offset": 0
}
```

`total` is the filtered count before pagination. `snippet` and `rank` are
only present (non-null) when `q` is given. Tags, `projects`, and `matters` are
each sorted by slug; `projects`/`matters` are `[]` when the document is in no
project/matter.
`review_status` reflects extraction-quality validation: `unreviewed` (no
issues found), `needs_review` (one or more validation findings), or
`verified` (user confirmed the metadata is correct). `review_findings` is a
compact list of `{rule, field, message}` explaining *why* a row needs review —
populated only when `review_status` is `needs_review`, `[]` otherwise (the full
provenance blob stays on the detail endpoint's `validation`, §1.4). It lets the
dashboard and review queue show a short reason without a second request.
`amount_total` (JSON string, preserves decimal precision) and `currency`
(3-letter code) are `null` when not set on the document. The list item carries
all five document dates so the dashboard tiles can show any of them: the
nullable `document_date`, `due_date` and `expiry_date`, plus the always-present
`created_at` ("Date added to library") and `updated_at` ("Last edited") — see the
dashboard-field catalog in §1.10.2.

### 1.3.3 Search semantics

- `q` is parsed with `websearch_to_tsquery` and matched against **both**
  generated tsvector columns — `search_vector_nl` (`dutch` config) and
  `search_vector_en` (`english` config), OR-combined. Stemming therefore
  works in both languages: `q=rekening` finds "rekeningen", `q=policy`
  finds "policies".
- Each vector folds in `title`, `summary`, the document body, **and `topics`**
  (the auto-extracted subject phrases, cast with `coalesce(topics::text,'')`;
  migration `0012_topics_fts`), so a document is findable by its topics even
  when the term never appears in its body. `topics` is read-only (see §1.5).
- The **body term is `coalesce(pages_markdown, ocr_text)`** — it prefers the
  vision "understood layer" (the concatenated per-page markdown, denormalized
  onto `documents.pages_markdown`) and falls back to raw `ocr_text` when a
  document has no markdown pages (migration `0025_fts_page_markdown`). This
  mirrors how semantic search and Ask read the page markdown, so a thin-OCR
  image PDF is findable by body text (an invoice number, a line item) that OCR
  never captured but vision did. Born-digital docs and notes have
  `pages_markdown == ocr_text`, and the `coalesce` indexes the body once (no
  double-count).
- The rank is `greatest(ts_rank(nl), ts_rank(en))` — the best of the two
  language interpretations — and results are ordered by it, descending.
  `ts_rank` is called with normalization bitmask `1`, so the raw rank is divided
  by `1 + log(document length)`; long documents do not out-rank short ones
  merely for containing the term more often.
- `snippet` is `ts_headline` over the same `coalesce(pages_markdown, ocr_text)`
  source, generated with whichever config produced the higher rank, capped with
  `MaxFragments=2, MaxWords=12, MinWords=4, ShortWord=2,
  FragmentDelimiter=" … "` and the **default `<b>`/`</b>` markers**, so an
  image-PDF snippet shows real body text rather than the thin letterhead.

> **Rendering snippets safely.** The snippet source (page markdown or raw OCR)
> is NOT HTML-escaped by the server; a document could contain literal
> HTML. Clients must render the snippet as plain text and handle the
> `<b>`…`</b>` markers deliberately (e.g. escape everything, then convert
> the known markers back to highlighting). Never inject a snippet into the
> DOM as HTML.

Without `q`, ordering is `document_date DESC NULLS LAST, created_at DESC`.

## 1.4 Detail — `GET /api/documents/{id}`

Everything in the list item (which already carries `document_date`, `due_date`,
`expiry_date`, `created_at` and `updated_at` — see §1.3.2), plus:

- `ocr_text`, `ocr_confidence`
- `source`, `original_filename`, `sha256`
- `extraction` — the provenance block written by Claude extraction
  (`prompt_version`, `model`, `confidence`, token/cost accounting,
  `fields_set`, …), or `null` if extraction has not run. This is a
  deliberate subset: the raw `extra` JSONB column is not exposed
  wholesale.
- `validation` — the latest deterministic-validation run:
  `{prompt_version, findings: [{rule, field, severity, message}, …],
  validated_at}`. `findings` is an empty list when no rules fired. `null`
  if validation has not run yet. See [ingestion.md](ingestion.md)
  "Extraction quality" for the rule table.
- `user_edited_fields` — fields locked by user edits (see 1.5)
- `events` — the full ingestion audit trail, oldest first:
  `[{event, detail, created_at}, …]`
- `comments` — the document's comments, **newest first**:
  `[{id, document_id, author_id, body, created_at}, …]` (the same `CommentOut`
  shape as §1.19). A comment is a distinct concept from a note (§1.17): it is
  user-authored dated text attached to *this* document, not a document of its
  own, and it is separately indexed for `/ask` (§1.19).
- `deleted_at` — when the document was soft-deleted, or `null` if live.
  Non-`null` only when fetched with `?include_deleted=true` (below).

**`?include_deleted=true`.** By default this endpoint `404`s a soft-deleted
document, the invariant every list/search path relies on. Passing
`include_deleted=true` returns the document instead (with `deleted_at` set),
so the Recently-Deleted view can open a trashed document read-only. The default
is `false`; only the detail view opts in. The same flag is accepted by
`…/markdown`, `…/original`, `…/searchable.pdf`, and `…/thumbnail`, so the
read-only trash view can render a deleted document's text, preview, and
downloads instead of 404ing them.

### 1.4.1 Per-page markdown — `GET /api/documents/{id}/markdown`

The document's per-page markdown rendering, assembled from the stored
`document_pages` rows and ordered by page number:

```json
{"page_count": 2, "pages": [{"page_number": 1, "markdown": "# Invoice…"},
                            {"page_number": 2, "markdown": "…"}]}
```

`page_count` is the length of `pages`. A document with no stored pages
returns `{"page_count": 0, "pages": []}` (still `200`). Unknown or
soft-deleted documents return `404` — unless `?include_deleted=true` is passed
(see §1.4.1), which renders a soft-deleted document's text for the read-only
Recently-Deleted detail view.

## 1.5 Edit metadata — `PATCH /api/documents/{id}`

JSON body; only the fields present in the body change. Editable fields:

| Body field | Notes |
|------------|-------|
| `title`, `summary` | `null` clears |
| `document_date`, `due_date`, `expiry_date` | ISO dates, `null` clears |
| `kind_slug` | Must be an existing kind slug (`422` otherwise); `null` clears the kind |
| `sender` | Sender **name**; upserted case-insensitively (same rule extraction uses); `null` clears |
| `recipient` | Recipient **name**; upserted case-insensitively — a manual edit always creates the recipient if new (extraction only creates from a **high-confidence** document-stated name, see ingestion.md); `null` clears |
| `tags` | **Full replacement** list of slugs; unknown slugs are created; `[]` clears; `null` is rejected |
| `projects` | **Full replacement** list of project slugs *or names*; unknown identifiers are upserted (a name becomes a new project, slugified); `[]` clears membership; `null` is rejected. Also appends a `project_changed` ingestion event |
| `matters` | **Full replacement** list of matter slugs *or names*; unknown identifiers are upserted (a name becomes a new matter, slugified); `[]` clears membership; `null` is rejected. Also appends a `matter_changed` ingestion event; the edit locks `matters` in `extra["user_edited_fields"]`, so the auto-classifier ([ingestion.md](ingestion.md), "Matter classification") never overwrites a hand-curated matter set |
| `language` | `nld` / `eng` / `mixed` / `unknown`; `null` rejected |
| `amount_total` | Decimal as string or number; `null` clears |
| `currency` | 3-letter code, normalized to upper case; `null` clears |

Every edited field is appended to `extra["user_edited_fields"]` (mapped to
storage names: `kind_slug`→`kind_id`, `sender`→`sender_id`,
`recipient`→`recipient_id`). Re-extraction
(W6) honours this list and never overwrites user-edited fields. An
ingestion event `user_edited` is recorded with the changed field names.
Returns the updated document detail.

This route now delegates the mutation to the shared **`apply_document_update`**
service (`src/library/documents_service.py`) with `edited_by="user"`; the same
service backs the Ask agent's `update_document_metadata` write tool
(`edited_by="ask"` — see [ask.md §1.8](ask.md)), so both surfaces apply edits,
record provenance, and lock fields against re-extraction identically.

**Revalidation on save.** After applying an edit, both this route **and** the
Ask agent's write tool re-run the deterministic validation rules
(`documents_service.revalidate_after_edit`) and rewrite `extra["validation"]` +
`review_status` in the same transaction, so a correction *clears its own warning
immediately*: fix an implausible
`document_date` and save, and the `date_plausibility` finding is gone from the
returned detail and the document drops off `needs_review`. Findings you can't fix
by editing (e.g. `ocr_confidence_gate`) remain until the document is explicitly
verified (§1.8.3). Status policy: any remaining finding → `needs_review`; no
findings → the document stays `verified` if a human already verified it (an edit
never silently un-verifies), otherwise `unreviewed`.

> **`topics` is read-only.** The auto-extracted `topics` list is **not** in this
> body (it was removed from `DocumentUpdate` and the detail editor). It still
> appears on every list/detail response (and the MCP document summary) and is
> now indexed for full-text search (§1.3.3), but it is owned by extraction, not
> the user. `tags` remains the curated, editable cross-document filter facet.

## 1.6 Delete, restore, and purge — the soft-delete lifecycle

**Delete — `DELETE /api/documents/{id}`.** Soft delete: sets `deleted_at`,
records a `deleted` ingestion event, returns `204`. The document then 404s on
every read endpoint and disappears from every list and search; its file and row
are kept. Re-uploading identical content returns `409` (see ingestion.md) — a
soft-deleted document is restored, not re-uploaded. Notes are documents
(`source = note`), so they follow the same path.

**Recently Deleted — `GET /api/documents/deleted`.** Paginated
(`limit` ≤ 100, `offset`) list of soft-deleted documents, newest-deleted first.
Each item is a normal list item plus `deleted_at`, `purge_at`, and
`days_remaining` (whole days until purge, floored at 0). The response also
carries `retention_days`, the configured window. This is the one endpoint that
*inverts* the `deleted_at IS NULL` predicate.

**Restore — `POST /api/documents/{id}/restore`.** Clears `deleted_at`, records a
`restored` event, and returns the document detail. `404` unless the document
exists and is *currently* soft-deleted (restoring a live document is an error).
Restore never collides with a re-upload, because `sha256` is unique and uploads
of soft-deleted content are refused with `409`.

**Permanent delete — `DELETE /api/documents/{id}/permanent`.** Hard-deletes a
document that is already in the trash on demand — the manual equivalent of the
daily purge, for emptying Recently Deleted without waiting out the retention
window. Removes the row (chunks, comments, pages, events, note versions, and
tag/project links cascade), commits, then unlinks the on-disk original and
derived artifacts (row committed gone *before* files are unlinked, so a failed
unlink leaves at worst a reclaimable orphan file). Returns `204`. `404` unless the
document exists and is *currently* soft-deleted — you must soft-delete first, so
this can never one-step nuke a live document (the same guard as restore).

**Purge.** A daily worker task (`purge_deleted_documents`) hard-deletes documents
whose `deleted_at` is older than `LIBRARY_DELETED_RETENTION_DAYS` (default 30):
it removes the row (chunks, comments, pages, events, note versions, and
tag/project links cascade) and unlinks the on-disk original and derived
artifacts — the same row-then-files ordering as the on-demand endpoint above. It
is gated by `LIBRARY_DELETED_PURGE_ENABLED` (default on) — turn it off to keep the
Recently-Deleted area indefinitely restorable. See
[jobs-and-notifications.md](jobs-and-notifications.md).

## 1.7 File downloads

- `GET /api/documents/{id}/original` — streams the stored original with
  its real `Content-Type` and a `Content-Disposition: attachment` header
  carrying the original filename.
- `GET /api/documents/{id}/searchable.pdf` — the OCR-produced searchable
  PDF (`application/pdf`); `404` if the document has none (text-layer
  PDFs, photos, and plain text don't produce one).

Both endpoints take `?disposition=inline|attachment` (default
`attachment`; anything else is a `422`). `inline` keeps the filename in
the header but lets the browser render the file instead of downloading
it — the detail page's previews depend on this: the image preview
renders inline in an `<img>`, and the PDF preview (rendered to canvas by
pdf.js in `DocumentPdfPreview.vue`) plus its "Open in new tab" link fetch
the inline URL, because an attachment response shows nothing inline and
triggers a download instead.
- `GET /api/documents/{id}/thumbnail` — first-page thumbnail,
  `image/webp`, ~480 px wide. Generated by a background job after OCR;
  `404` until it exists (and always for plain-text documents).
  `has_thumbnail` in list/detail responses reflects file existence.

## 1.8 Jobs — `GET /api/jobs`

Most recent document-processing work (newest first) from the Procrastinate
queue, enriched with each document's pipeline state. `limit` 1–500, default 50.

**One row per document.** A document spawns several jobs (`process_document`,
`generate_thumbnail`, and the per-document backfill tasks); the endpoint
collapses them to a single row — the document's **most recent** job — so the
same document isn't repeated. `id` / `task_name` / `status` / `started_at` /
`finished_at` are that latest job's; `document_*` / `cost_usd` / `error` are
document-level. `started_at` / `finished_at` come from Procrastinate's
`procrastinate_events` table (the job's last `started` and last terminal event),
so the UI can show a timestamp and compute a run duration.

By default, document-less system/periodic jobs (the scheduled email poll) are
omitted when they succeeded — they fire constantly and would bury document work
— while any that **failed or are still running** are kept, so a broken poller
stays visible. Pass `include_system=true` to list them too (still one row per
document; system jobs are not deduplicated, having no document to group by).

**Filters.**

- `task_name=<fully-qualified name>` — restrict to a single task type (e.g.
  `library.jobs.poll_email_inbox`). A task filter implies system rows are shown,
  so it overrides the hide-succeeded-system-tasks default.
- `document_id=<id>` — **history mode**: returns *every* job for that one
  document (uncollapsed), newest first, so a document's full processing history
  can be traced. The per-document collapse and the hide-system default do not
  apply in this mode.

Each row:

```jsonc
{
  "id": 123,
  "status": "doing",            // Procrastinate status: todo|doing|succeeded|failed|…
  "task_name": "library.jobs.process_document",
  "attempts": 0,
  "scheduled_at": "2026-06-23T10:00:00Z",
  "started_at": "2026-06-23T10:00:01Z",  // last `started` event, else null
  "finished_at": "2026-06-23T10:00:04Z", // last succeeded/failed/aborted event, else null
  "document_id": 42,            // null for document-less jobs (e.g. email poll)
  "active": true,               // status is todo or doing
  "document_title": "Energierekening",  // null if no/deleted document
  "document_status": "ocr",     // current pipeline stage, or terminal indexed/failed
  "error": "ocr exploded",      // latest `failed` event detail, else null
  "cost_usd": 0.0123,           // extraction cost from document provenance, else null
  "tokens": 1500                // extraction input+output tokens, else null
}
```

The `document_*` / `error` / `cost_usd` / `tokens` fields are `null` for jobs
without a document or whose document has been deleted. For a live feed of state
changes, use `GET /api/events` (§1.8.5) rather than polling this endpoint.

## 1.8.1 Job task names — `GET /api/jobs/task-names`

A plain JSON array of the distinct `task_name` values present in the queue,
ordered alphabetically — e.g. `["library.jobs.poll_email_inbox",
"library.jobs.process_document", …]`. Used to populate the Jobs view's
task-type filter dropdown without the client inferring the set from a partial
result window.

## 1.8.2 Re-extraction — `POST /api/documents/{id}/extract`

Queues the W6 metadata-extraction task for one document and returns
**`202`** with `{"queued": true, "job_id": <procrastinate job id>}` —
the work happens in the background worker. Works on documents in any
state (including already `indexed`); the run honours
`extra["user_edited_fields"]` (user edits are never overwritten) and
never removes tags. `404` for unknown or deleted documents. Track the
outcome via the document's `extraction` provenance block and
`extraction_*` audit events (GET detail, 1.4) or `GET /api/jobs`.
Extraction can also be *skipped* (disabled, missing API key, daily
budget reached) — that is recorded as an `extraction_skipped` event,
not an error.

## 1.8.3 Mark verified — `POST /api/documents/{id}/verify`

Sets `review_status = verified` and records a `review_verified` ingestion
event. Returns the updated document detail (`200`). `404` for unknown or
deleted documents. Auth + CSRF apply.

Use this after reviewing a document's metadata in the detail view and
confirming it is correct. The `review_status` can return to `needs_review`
if extraction is re-run and new findings are produced, or if a later **edit**
introduces a finding (a save that produces no findings keeps `verified` — see
the revalidation-on-save note in §1.5).

## 1.8.4 Taxonomy — `GET /api/kinds`, `/api/senders`, `/api/recipients`, `/api/tags`

Plain JSON arrays for filter options and edit forms; the same data the
MCP `list_*` tools return (one shared service, `library.taxonomy`).
Counts exclude soft-deleted documents; zero-count entries are included.

- `GET /api/kinds` → `[{slug, name, document_count}, …]`, ordered by
  slug. The seeded set plus any kinds created via `POST /api/kinds`.
- `GET /api/senders` → `[{id, name, document_count, colour}, …]`, ordered by
  name. `colour` is a stored six-digit `#rrggbb` override for this sender as a
  chart split value, or `null` if the client should derive a palette slot from
  `id` (spec §2.5).
- `GET /api/recipients` → `[{id, name, document_count}, …]`, ordered by
  name.
- `GET /api/tags` → `[{slug, name, document_count}, …]`, ordered by
  name.

### 1.8.4.0 Edit a sender's colour — `PATCH /api/senders/{sender_id}`

The only sender edit exposed here; a sender's name is derived from ingested
documents and renaming one is a taxonomy operation with its own merge
semantics (`PATCH /api/admin/senders/{id}`, admin-only — §1.18.4). Body
`{"colour": "#rrggbb"}` or `{"colour": null}`; an absent `colour` leaves it
untouched. `404` unknown sender; `422` a `colour` that is not a six-digit hex.
Returns the sender as `GET /api/senders` would: `{id, name, document_count,
colour}`.

### 1.8.4.1 Create a kind — `POST /api/kinds`

Adds a new document kind so users aren't limited to the seeded set (e.g.
a `quote` kind alongside `invoice`/`receipt`). Available to any
authenticated user, mirroring how senders/recipients/tags are created
inline through a document edit.

- **Body.** `{"name": "Quote"}` — the human display name (1–255 chars).
- **Slug.** Derived from the name (`slugify`: lowercased, non-alphanumeric
  runs → hyphens), so `"Bank statement"` → slug `bank-statement`.
- **Casing.** The stored display name is standardised to sentence case
  (first letter upper, rest lower, internal whitespace collapsed) to match
  the seeded names — `"BANK STATEMENT"` → `"Bank statement"`.
- **Exact dedupe.** A name/slug that matches an existing kind
  case- and whitespace-insensitively returns that kind with **`200`**
  (no duplicate row created).
- **Near-duplicate guard.** A name within a small edit distance of an
  existing kind (e.g. `"Quotes"` vs `"Quote"`) is refused with **`409`**;
  the flat body carries `detail`, `existing_slug`, and `existing_name` so
  the client can point the user at the existing kind.
- **Success.** A genuinely new kind is created and returned as
  `{slug, name}` with **`201`**.

## 1.8.5 Live job events (SSE) — `GET /api/events`

A [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events)
stream of document-pipeline state changes, used by the web app to drive the
navbar running-jobs indicator, toasts, the live Jobs view, and the live status
badges on the document list and detail pages — all without polling. (Each event
also bumps the jobs Pinia store's `lastEvent`, which those views watch to
refetch or patch themselves; document-less system tasks emit no SSE event, so the
Jobs view polls for them while "Show system tasks" is on.)

- **Transport.** The worker emits a Postgres `NOTIFY` on the `library_doc_events`
  channel each time a document changes pipeline stage (`status_changed`) or fails
  (`failed`). The api process runs a single process-wide events broker
  (`library.events_broker`) that holds *one* `LISTEN` connection for its whole
  lifetime and fans each notification out in-process to every connected client;
  this endpoint just drains a per-client queue. SSE Postgres usage is therefore
  capped at one connection per process, not one per open tab. The worker→api hop
  crosses processes via Postgres itself, so both must point at the same database
  (they do in the standard compose deployment).
- **Auth.** Same session cookie as the rest of `/api` (§1.9). A GET is CSRF-safe,
  so a browser `EventSource` — which cannot send headers — authenticates with the
  cookie alone. Unauthenticated requests get `401` before the stream opens.
- **Wire format.** Named SSE events:
  - `event: document` — `data` is JSON `{document_id, event, status, title}`,
    where `status` is the stage the document just entered. A document enters at
    `received`, so the stages actually emitted are `ocr`→`extract`→`markdown`→
    `embed`→`indexed`, plus `failed` on a terminal error.
  - keep-alive comments every ~15 s so idle connections and proxies don't time
    out. The response sets `X-Accel-Buffering: no` to disable proxy buffering.
- **Lifecycle.** The browser's `EventSource` reconnects automatically; on
  disconnect the server drops only that client's in-process queue — the shared
  `LISTEN` connection lives on for the other clients. Events are not replayed on
  reconnect — fetch `GET /api/jobs` for the current snapshot.

## 1.9 Authentication

Two interchangeable credentials, checked by a single dependency on every
`/api` route:

1. **Browser session cookie** — set by `POST /api/auth/login`.
2. **Bearer API token** — `Authorization: Bearer library_…`, for scripts,
   shortcuts, and the MCP server.

When an `Authorization: Bearer` header is present it is authoritative: the
token is validated and cookies are ignored.

**Authorization is authentication-only, by design.** Beyond the admin/non-admin
split, there is no per-user resource ownership: any authenticated user can read
and mutate the whole library (documents, notes, comments, tags, projects).
Endpoints do not check the caller against a resource's creator, and
`uploader_id`/`author_id` are provenance, not access control — this is a
single-family shared library. See [architecture.md §1.5.1](architecture.md) for
the full rationale (and what would have to change to support multiple tenants).

Passwords are hashed with **Argon2id** (pwdlib). Accounts are managed from
the host with the bundled CLI — there is no signup endpoint:

```console
library user add anna --display-name "Anna"   # prompts for password
library user passwd anna
library user disable anna                      # also revokes all sessions/tokens
library user list
```

### 1.9.1 Sessions — `POST /api/auth/login`

JSON body `{"username": "...", "password": "..."}`. On success, `200` with
`{id, username, display_name, is_admin, preferences}` and two cookies:

| Cookie | Flags | Purpose |
|--------|-------|---------|
| `library_session` | `HttpOnly; Secure; SameSite=Lax; Path=/` | Opaque 256-bit session token; only its SHA-256 hash is stored server-side |
| `library_csrftoken` | `Secure; SameSite=Lax; Path=/` (readable by JS) | CSRF double-submit value |

Wrong username, wrong password, and disabled accounts all return the same
generic `401` (`{"detail": "invalid credentials"}`) — no account
enumeration. The `Secure` flag follows the `LIBRARY_COOKIE_SECURE` setting
(default `true`; set `false` only for plain-HTTP dev).

Sessions live in Postgres and expire after `LIBRARY_SESSION_TTL_DAYS`
(default 30) of inactivity, with **sliding expiry**: any authenticated use
pushes the expiry forward (the refresh is write-throttled to at most once
per ~5 minutes). `POST /api/auth/logout` deletes the session row — the
cookie is dead server-side immediately — and clears both cookies.

`GET /api/auth/me` returns `{id, username, display_name, is_admin, preferences}`
for the authenticated user (either credential). The login response (`POST
/api/auth/login`) returns the same shape. `preferences` is the resolved
preference set (defaults filled; see §1.10) — all nine keys:
`dashboard_fields`, `background_tone`, `tile_preview`, `dock_position`,
`phone_columns`, `hide_summary_mobile`, `kind_colors`, `notifications`,
`ask_profile`.

### 1.9.2 CSRF (cookie requests only)

State-changing requests (`POST`/`PATCH`/`PUT`/`DELETE`) authenticated by
the **session cookie** must echo the CSRF cookie in a header:

```
X-CSRF-Token: <value of the library_csrftoken cookie>
```

Missing or mismatched header → `403`. Exempt: `GET`/`HEAD`/`OPTIONS`,
requests carrying an `Authorization: Bearer` header, and
`POST /api/auth/login` itself.

### 1.9.3 API tokens

`POST /api/auth/tokens` with `{"name": "ios-shortcut"}` returns `201`:

```json
{"id": 4, "name": "ios-shortcut", "token": "library_3q2…", "created_at": "…"}
```

**The `token` secret is shown exactly once.** Only its SHA-256 hash is
stored; it cannot be retrieved again — lose it, revoke it, make a new one.
`GET /api/auth/tokens` lists your tokens as
`[{id, name, created_at, last_used_at, revoked_at}, …]` (never secrets;
`last_used_at` updates are throttled to ~5-minute granularity).
`DELETE /api/auth/tokens/{id}` revokes the token (sets `revoked_at`;
takes effect immediately) and returns `204`; tokens belonging to other
users `404`. Tokens do not expire — revocation is the lifecycle.

Usage:

```console
curl -H "Authorization: Bearer library_3q2…" \
  "https://library.example.org/api/documents?q=rekening"

curl -H "Authorization: Bearer library_3q2…" \
  -F "file=@scan.pdf" https://library.example.org/api/documents
```

Bearer requests are CSRF-exempt (the header cannot be set cross-site).
Revoked or unknown tokens, and tokens of disabled users, get `401`.

## 1.10 Settings — `GET /api/settings`, `PUT /api/settings`, `PUT /api/settings/appearance`, `PUT /api/settings/kind-colors`, `PUT /api/settings/notifications`, `GET /api/settings/email-triage`, `GET /api/settings/email-triage/recent-skips`, `GET /api/settings/llm-backends`, `PUT`/`DELETE /api/settings/llm-backends/{surface}`, `PUT /api/settings/ask-profile`

Per-user preferences: which metadata fields appear on the dashboard tiles, the
page-canvas tone behind them, how each tile previews the document's first page,
where the document-detail page's floating action dock sits on screen, the
per-kind tile border colours, and Pushover notification settings (incl.
email forwarding addresses). Auth and CSRF rules are identical to the rest of
`/api` (§1.9). All preferences live in one JSONB `preferences` blob on the user
row; writes are split per concern (fields vs appearance vs kind-colours vs
notifications) so each Settings tab saves independently, and every write
preserves the sibling keys.

### 1.10.1 `GET /api/settings`

Returns the resolved preference set for the authenticated user. If the
user has never saved preferences, the **default set** is returned (no
`404` or empty body).

```json
{"dashboard_fields": ["kind", "sender", "tags", "date", "language", "status"], "background_tone": "neutral", "tile_preview": "full_width", "dock_position": "top-right", "phone_columns": 2, "hide_summary_mobile": false, "kind_colors": {}, "notifications": {"enabled": false, "pushover_app_token_set": false, "pushover_user_key_set": false, "pushover_device": null, "events": [], "email_forward_addresses": []}, "ask_profile": ""}
```

### 1.10.2 `PUT /api/settings`

Body: `{"dashboard_fields": [...]}`. Persists the list and returns the
**full** resolved preference set (same shape as GET, incl. `background_tone`).
Auth + CSRF apply.

**Valid field keys** (the 12 selectable fields):

| Key | What it controls on the tile |
|-----|-------------------------------|
| `kind` | Document type tag (blue) |
| `sender` | Correspondent name |
| `tags` | Document tags row (capped at 4 + "+N" overflow) |
| `date` | Date on document (the document's own date; value kept as `date` for back-compat) |
| `due_date` | Due date, prefixed "Due" (invoice/payment due) |
| `expiry_date` | Expiry date, prefixed "Expires" (validity end) |
| `added_date` | Date added to library (`created_at`), prefixed "Added" |
| `last_edited` | Last edited (`updated_at`), prefixed "Edited" |
| `language` | Language tag (grey) |
| `status` | Status tag (red/yellow; only shown when non-indexed) |
| `amount` | Financial total (`amount_total` + `currency`, formatted) |
| `file_type` | Derived file type label (PDF / Image / Text / File) |

The five date keys mirror the detail-page hero's five document dates. Only `date`
is on by default; the others are opt-in. The list response (`GET /api/documents`)
returns `document_date`, `due_date`, `expiry_date`, `created_at` and `updated_at`
on every item so the tiles can render whichever the user enabled.

**Default set** (what new users see): `kind`, `sender`, `tags`, `date`,
`language`, `status`.

**Tolerant validation.** Unknown keys in `dashboard_fields` are silently
dropped — the server returns `200` with the cleaned set, never `422`.
Duplicates are also removed. This means a hand-edited database row or a
client sending a renamed field key can never break the dashboard.

**Explicit empty list** is honoured: `{"dashboard_fields": []}` is valid
and results in tiles showing only the title and thumbnail. The absent-key
rule applies only on read (`GET` / login / `GET /api/auth/me`): if the
`dashboard_fields` key is missing from the stored blob, defaults are
filled in; if the key is present but the list is empty, the empty list
is returned as-is.

### 1.10.3 `PUT /api/settings/appearance`

Body: `{"background_tone": "<tone>", "tile_preview": "<mode>", "dock_position":
"<position>", "phone_columns": <1|2|3>, "hide_summary_mobile": <bool>}`.
Persists the appearance settings and returns the full resolved preference set
(same shape as GET). Auth + CSRF apply. The tone applies to the light-mode page
background only — dark mode keeps its `gray-900` canvas.

**Valid tones:** `neutral` (default — `gray-200`), `light` (`gray-100`,
the original airier canvas), `soft`, `slate`, `sand`, `mist`. The token is
a name, not a colour: the frontend (`assets/main.css`) owns the actual hex
for each tone, so the palette can be retuned without a schema or data
migration.

**Valid tile previews:** `full_width` (default — the first-page thumbnail
fills the tile width, top-aligned, lower part of the page cropped) and
`whole_page` (the entire first page shown letterboxed inside the tile). The
token names a render mode; the frontend owns the CSS object-fit for each.

**Valid dock positions:** `top-left`, `top-middle`, `top-right` (default),
`bottom-left`, `bottom-right` — where the document-detail page's floating
**action dock** ([frontend.md §1.5, DocumentDetailView component
structure](frontend.md)) sits once it appears. Like `background_tone`, the
token is a name, not a coordinate: the frontend owns the actual CSS placement
(and the header-clearance offset for the `top-*` positions) for each, so the
layout can be retuned without a schema or data migration.

**Phone columns.** `phone_columns` (`1`, `2`, or `3`; default `2`) sets how
many dashboard tile columns render on phone-width screens (`< 641px`); the
frontend owns the CSS. **Hide description on mobile.** `hide_summary_mobile`
(bool; default `false`) hides each dashboard tile's description on phones when
`true`; larger screens always show it.

**Tolerant validation.** An unknown tone resolves to `neutral`, an unknown
tile preview to `full_width`, an unknown dock position to `top-right`, an
out-of-range `phone_columns` to `2`, and a non-bool `hide_summary_mobile` to
`false` — `200` with the default, never `422` — matching `dashboard_fields`.
`tile_preview`, `dock_position`, `phone_columns`, and `hide_summary_mobile` are
all optional in the body (defaulting to `full_width`, `top-right`, `2`, and
`false` respectively), so a client sending only `background_tone` still
succeeds. On read, absent keys resolve to their defaults.

### 1.10.4 `PUT /api/settings/kind-colors`

Body: `{"kind_colors": {"<slug>": "#rrggbb", ...}}`. Replaces the per-kind tile
**border colours** and returns the full resolved preference set (same shape as
GET). Auth + CSRF apply. The map is a **sparse set of overrides**: a kind absent
from the map falls back to the frontend's built-in default palette, and an empty
map (`{}`) resets every kind to its default.

**Hex, not a token.** Unlike `background_tone`, the value is a literal `#rrggbb`
hex, because the Settings colour picker offers an arbitrary colour rather than a
fixed palette. The *defaults* still live frontend-side (`api/settings.ts`
`DEFAULT_KIND_COLORS`), so the built-in palette can be retuned without a data
migration; only user overrides are persisted here.

**Tolerant validation.** Entries whose value isn't a 6-digit `#rrggbb` hex (or
whose key is empty) are silently dropped — `200` with the cleaned map, never
`422` — and the map is capped at 64 entries. Hex is normalised to lower-case.
On read, an absent `kind_colors` key resolves to `{}`.

### 1.10.5 `PUT /api/settings/notifications`

Per-user Pushover push notifications and the email addresses you forward
documents from. Auth + CSRF apply. Returns the full resolved preference set
(same shape as GET).

**Notification model.** Each user supplies their **own** Pushover application
token and user key (register a free app at pushover.net). Notifications target
the document **owner** (`uploader_id`): on completion the worker sends a single
push (the `needs_review` message when the document was flagged and the owner
subscribed, otherwise `document_success`); a failure sends `processing_error`;
and an ingest-time `duplicate` sends from the ingest path — each only if the
owner subscribed to that event. See
[jobs-and-notifications.md](jobs-and-notifications.md) §1.5 for the full rule.
Documents with no owner (consume-folder, paperless import) notify no one.

**Request body:**

```json
{
  "enabled": true,
  "pushover_app_token": "axxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "pushover_user_key": "uxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "pushover_device": "iphone",
  "events": ["document_success", "processing_error"],
  "email_forward_addresses": ["me@example.com"]
}
```

**Event keys:** `document_success`, `processing_error`, `needs_review`,
`duplicate`, `email_held` (an inbound email was held for review instead of
filed — see [ingestion.md](ingestion.md), "Held for review"). New users start
with **none** selected (opt-in). Unknown keys are dropped (`200`, never `422`).

**Secrets are write-only.** The read model returns booleans
`pushover_app_token_set` / `pushover_user_key_set` and the non-secret
`pushover_device` / `events` / `email_forward_addresses` — never the raw token
or key. On write, an **omitted or blank** `pushover_app_token` /
`pushover_user_key` keeps the stored value unchanged, so saving only `events`
never wipes credentials.

**Validation on save.** When `enabled` is `true`, both credentials must be
present (`422` otherwise) and are verified against Pushover's `users/validate`
endpoint; a typo returns `422` with the Pushover error in `detail` rather than
silently dropping every future push. When `enabled` is `false`, no Pushover call
is made.

**`email_forward_addresses`** are lowercased, de-duplicated, and trimmed.
Email-in attributes an incoming message to the user whose list contains the
sender's address (see [ingestion.md](ingestion.md), "Email-in").

The GET / login / `/api/auth/me` read model embeds a `notifications` object,
e.g. `{"enabled": false, "pushover_app_token_set": false,
"pushover_user_key_set": false, "pushover_device": null, "events": [],
"email_forward_addresses": []}`.

### 1.10.6 `GET /api/settings/email-triage`

The **effective email-in triage configuration** — instance-wide (not
per-user) and **read-only**; it backs the Settings → Email triage tab so the
hold/label pipeline's logic is visible rather than hidden. Computed from the
live environment on every request. The semantics of each gate live in
[ingestion.md](ingestion.md) ("Email item selection", "Held for review");
changing any value is environment-only (server `.env` + worker restart — see
[runbooks/email-triage.md](runbooks/email-triage.md)).

```json
{
  "email_in_configured": true,
  "poll_minutes": 10,
  "held_folder": "Library/Held",
  "processed_folder": "Library/Processed",
  "hold": {"enabled": true, "below_substance": true, "unknown_senders": true},
  "allowlist": {"configured": true, "count": 2},
  "noise_filter": {"enabled": true, "tiny_image_max_bytes": 4096, "tiny_image_max_edge_px": 64, "decoration_max_bytes": 65536, "decoration_max_edge_px": 384},
  "label": {"enabled": true, "active": true, "model": "claude-haiku-4-5", "daily_budget_usd": 2.0, "body_snippet_chars": 1000, "prompt_version": "email-label-v2"},
  "body_substance": {"min_words": 40, "min_chars": 240},
  "imap_timeout_seconds": 60.0
}
```

**Secret-free by construction.** Never the IMAP credentials or host (only the
`email_in_configured` boolean), never the Anthropic key (only `label.active` =
enabled **and** a key is present, so the UI can distinguish "disabled by flag"
from "no API key"), and never the allowlisted addresses (only
`allowlist.count` — any authenticated user can read this endpoint).
`body_substance` reports the module constants in `email_ingest`
(`BODY_MIN_WORDS` / `BODY_MIN_CHARS`) — fixed in code, not configuration.
`prompt_version` is `email_label.PROMPT_VERSION`. The `noise_filter` object
carries both threshold families: the tiny-image ones
(`LIBRARY_EMAIL_FILTER_TINY_IMAGE_*`) and the decoration-signal ceilings
(`decoration_max_bytes` / `decoration_max_edge_px` ←
`LIBRARY_EMAIL_FILTER_DECORATION_*`; a decoration skip needs ≥ 2 of the
filename/size/shape signals — see [ingestion.md](ingestion.md), "Email item
selection").

### 1.10.7 `GET /api/settings/email-triage/recent-skips`

The last **20** emails whose selection **skipped at least one item** (quiet
noise skips such as `decoration_image` included), newest first — the durable
answer to "did the pipeline just eat my attachment?" without grepping server
logs. Backed by the `email_selection_traces` table (one row per email with any
filtered/dropped item; see [runbooks/email-triage.md](runbooks/email-triage.md)
§6). A sibling of the config snapshot above — these are DB rows that change per
poll, not configuration. Backs the "Recently skipped items" card on the same
Settings tab. Read-only; any authenticated user.

```json
{
  "recent_skips": [
    {
      "id": 12,
      "message_id": "<abc@example.com>",
      "subject": "Fwd: invoice 42",
      "from_address": "alice@example.com",
      "created_at": "2026-07-14T10:00:00Z",
      "decisions": [
        {"kind": "attachment", "filename": "image001.png", "reason": "decoration_image", "detail": "decoration image (3/3 signals fired: filename, size, shape)"}
      ]
    }
  ]
}
```

`decisions` is filtered to the **actual skips** (the stored row keeps the full
per-item trace, ingested siblings included) and projected to a compact shape —
`kind`/`filename`/`reason`/`detail`, no mime/size/stage.

### 1.10.8 `GET /api/settings/llm-backends`

Which transport each LLM surface uses to reach Claude — **instance-wide, not
per-user**. Readable by any authenticated user (it explains why Ask behaves as
it does); only an admin may change it, which `editable` reports so the client
renders read-only controls rather than discovering a 403. The narrative — the
two backends, the per-call harness cost, and provisioning — is in
[llm-backends.md](llm-backends.md).

Secret-free by construction: it reports *whether* an API key is configured,
never the key, and the subscription credential status without the tokens
behind it.

```json
{
  "surfaces": [
    {
      "surface": "ask",
      "label": "Ask",
      "description": "The question-answering tool loop and the model that names new conversations. …",
      "backend": "subscription",
      "default": "api",
      "overridden": true
    },
  ],
  "credentials_status": "healthy",
  "credentials_detail": "access token valid (7.5h), refresh token present",
  "api_key_configured": true,
  "editable": true
}
```

`backend` is one of `api` (metered Anthropic Messages API) or `subscription`
(Claude Code CLI against a Claude subscription). `default` is what the
deployed environment supplies when no override is stored, and `overridden`
says whether an admin has changed it — so the UI can distinguish deployed
config from a person's decision.

### 1.10.9 `PUT /api/settings/llm-backends/{surface}`

Switch one surface's backend. **Admin only** (403 otherwise). Takes effect on
the next request — no restart, because the value is resolved per request.

**Request:** `{"backend": "api" | "subscription"}` — any other value is a 422.

**Responses:**

| Status | When |
| --- | --- |
| `200` | Saved. Body is the full `GET` payload above, re-resolved. |
| `403` | Not an admin. |
| `404` | Unknown `surface`. |
| `409` | The chosen backend cannot authenticate — e.g. `subscription` with no Claude credentials provisioned. The `detail` names the command to run on the host. |

The `409` is deliberate: the request is well-formed and the value is legal, the
server simply is not in a state to honour it. Validating on write means the
admin making the change hears about it, rather than the next person to ask a
question discovering it as a failed query.

### 1.10.10 `DELETE /api/settings/llm-backends/{surface}`

Drop the override so the surface follows the deployed default again. **Admin
only**; same `403`/`404` semantics. Returns the re-resolved `GET` payload.

### 1.10.11 `PUT /api/settings/ask-profile`

Body: `{"ask_profile": "<text>"}`. Persists the user's free-text **"About you"**
notes and returns the full resolved preference set (same shape as GET). Auth +
CSRF apply. The text is appended to the Ask system prompt on every turn as
authoritative personal context — who lives with the user, their current
address, which car is theirs — see [ask.md §1.2, *Archive context*](ask.md).
Leading/trailing whitespace is stripped; blank text clears the notes
(`ask_profile` reads back as `""`, the default).

**Not tolerant, on purpose.** Unlike the appearance flags, over-long text is a
`422` rather than a silent truncation: the notes are the user's own words, so a
cut would change what Ask is told without telling them. The cap is
`MAX_ASK_PROFILE_CHARS` (4000). On read, a stored value that is not a string
(a hand-edited blob) resolves to `""`, and a stored string longer than the cap
is clipped to it.

## 1.11 Ask — `POST /api/ask`

Answer a natural-language question about the archive, grounded in retrieved
documents. The narrative — architecture, the two question classes, config, cost,
and conversational threading — is in [ask.md](ask.md); this is the wire contract.

**Request:**

```json
{
  "question": "<1..1000 chars>",
  "thread_id": 42,
  "images": [{ "media_type": "image/png", "data": "<base64, no data: prefix>" }]
}
```

`thread_id` is optional. Omit it to start a new conversation; supply it to
continue an existing one. Auth + CSRF apply (it is a `POST`).

**503** — Ask cannot reach a model. Two causes, distinguished by the `detail`:
the `api` backend with no `LIBRARY_ANTHROPIC_API_KEY` configured, or the
`subscription` backend that cannot authenticate. The latter names the command
to run on the host, because the alternative is a bare `500` with the reason
buried in a container log (see [llm-backends.md](llm-backends.md) §5.2).

`images` is optional: up to **5** base64 attachments for the multimodal
model (`ask_model` = `claude-opus-4-8`). Each has a `media_type` of
`image/png`, `image/jpeg`, `image/gif`, or `image/webp` and base64 `data` with
no `data:` prefix. They become image content blocks on the question turn (and
persist in `ask_turns.messages` for replay). `422` if more than 5 images or an
unsupported `media_type`.

**Response `200`:**

```json
{
  "answer": "Yes — your contract grants a travel allowance of €0.21/km [#42].",
  "citations": [
    {"document_id": 42, "title": "Employment contract", "page_number": 3}
  ],
  "used_tools": ["semantic_search"],
  "cost_usd": 0.0031,
  "thread_id": 1
}
```

- `answer` — prose, grounded only in retrieved documents; it says plainly when
  the archive does not contain the answer (then `citations` is empty).
- `citations` — documents the answer relied on (`document_id`, `title`,
  `page_number`); link these to `GET /api/documents/{id}`.
- `used_tools` — which tools the engine invoked: the retrieval tools
  (`semantic_search`, `query_documents`, `get_document`)
  and the metadata write tool (`update_document_metadata`) when the turn
  previewed or saved a metadata edit (see [ask.md §1.8](ask.md)).
- `cost_usd` — estimated answer cost for this turn (recorded in `ask_turns`,
  not gated; thread total = sum of its turns).
- `thread_id` — the conversation thread this turn belongs to (new or existing).

**Errors:** `503` when no Anthropic API key is configured; `422` when the
question is empty or too long; `404` when `thread_id` does not exist or belongs
to another user.

## 1.12 Ask threads

Conversation threads persist server-side. All thread endpoints enforce
ownership: a thread belonging to another user returns `404` (not `403`) to
avoid disclosing thread existence.

### `GET /api/ask/threads`

List the authenticated user's conversations, newest-updated first.

```json
[
  {
    "id": 1,
    "title": "Do I have a travel allowance in my job contract?",
    "created_at": "2026-06-22T10:00:00Z",
    "updated_at": "2026-06-22T10:05:00Z",
    "turn_count": 3,
    "total_cost_usd": 0.012
  }
]
```

### `GET /api/ask/threads/{id}`

Full thread detail: metadata and every turn in chronological order.
The raw replay `messages` blob is not returned to the client.

```json
{
  "id": 1,
  "title": "Do I have a travel allowance in my job contract?",
  "turns": [
    {
      "id": 1,
      "query": "Do I have a travel allowance in my job contract?",
      "answer": "Yes — your contract grants …",
      "citations": [{"document_id": 42, "title": "Employment contract", "page_number": 3}],
      "used_tools": ["semantic_search"],
      "cost_usd": 0.0031,
      "created_at": "2026-06-22T10:00:00Z"
    }
  ]
}
```

`404` if the thread does not exist or belongs to another user.

### `DELETE /api/ask/threads/{id}`

Delete a conversation. Cascades to all its turns. Returns `204` on success;
`404` if the thread does not exist or belongs to another user.

> **Sections 1.13–1.15 are gone, and the numbering deliberately does not
> close up.** They documented `GET /api/documents/{id}/series`, the
> `/api/charts` series surface and the `/api/series/.../members` override
> routes — the legacy series stack, deleted on 2026-08-31 (see
> [charts.md](charts.md) for what replaced it). Renumbering §1.16 onwards would
> silently invalidate the 28 citations of those later sections spread across
> nine documents and `AdminMetadataPanel.vue`, and nothing in the toolchain
> checks a section-number citation — `check_docs.py` verifies stamps and index
> reachability, not anchors. A stable gap costs this paragraph; renumbering
> would cost 29 edits with link-rot as the failure mode.

## 1.16 Projects — `/api/projects`

First-class **collections**: a many-to-many grouping of documents
(`projects` + `document_projects` tables, migration 0011), mirroring the
tags pattern but with their own CRUD surface, descriptions, and a
soft-archive state. A document's project membership is edited through
`PATCH /api/documents/{id}` (the `projects` field, §1.5) and surfaced as the
`projects` array on every document list/detail item; documents are also
filterable by `?project=<slug>` (§1.3.1).

**Slugs are stable.** `POST` derives a slug from the name (or accepts an
explicit, normalised `slug` override); `PATCH` never changes it, so inbound
links and the `?project=` filter survive renames. Counts exclude soft-deleted
documents and include zero-count projects.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/projects` | All projects ordered by name. `?include_archived=true` to include archived ones (hidden by default). Open to all authenticated users. |
| POST | `/api/projects` | **Admin only** (`403` otherwise). Body `{name, slug?, description?}`. `201`; `409` if the slug already exists. |
| GET | `/api/projects/{slug}` | One project; `404` if unknown. Open to all authenticated users. |
| PATCH | `/api/projects/{slug}` | **Admin only** (`403` otherwise). Body `{name?, description?, archived?}`; only present fields change. The slug is **immutable**. `archived: true/false` toggles `archived_at`. `404` if unknown. |
| DELETE | `/api/projects/{slug}` | **Admin only** (`403` otherwise). Hard-delete; `204`. Memberships cascade away (`document_projects`), the **documents themselves are kept**. `404` if unknown. |

Projects are a global, shared taxonomy, so mutating them is restricted to
admins (reads stay open). See [admin.md](admin.md).

**Project object** (every endpoint returns this shape; `GET /api/projects`
returns an array of them):

```json
{
  "id": 3,
  "slug": "house-purchase",
  "name": "House purchase",
  "description": "Mortgage, survey, and notary paperwork",
  "archived": false,
  "document_count": 12
}
```

`document_count` is the number of non-deleted documents in the project.
Auth + CSRF apply exactly as elsewhere (§1.9).

## 1.17 Notes — `/api/notes`

In-app **note authoring**: compose a Markdown note directly in Library and it
becomes a first-class document (`source = "note"`, `mime_type =
"text/markdown"`) that flows through the normal pipeline — one
born-digital `DocumentPage`, no OCR/vision API call, with metadata still
auto-extracted from the body. Unlike an upload, a note is **edited in place**
(the same document row) with a version-history snapshot recorded on every edit,
and is **exempt from content dedup** (its `sha256` is a salted digest), so two
identical notes — or a note edited back to an earlier body — never collide. See
[ingestion.md](ingestion.md) "Notes" for the storage/dedup mechanics. Auth +
CSRF apply exactly as elsewhere (§1.9).

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/notes` | Body `{title, body_markdown}`. Creates the note and queues processing; `201` with the full document detail (same shape as `GET /api/documents/{id}`, §1.4). |
| PATCH | `/api/notes/{id}` | Body `{title?, body_markdown?}`; only present fields change. Snapshots the prior (title, body) into history, applies the edit, and (on a body change) re-runs extraction and embedding — no markdown pass. Returns the updated detail. `404` for unknown, deleted, or **non-note** documents. |
| GET | `/api/notes/{id}/versions` | The note's version history, **newest first**: `[{version_no, title, body, created_at}, …]`. `404` for non-note documents. |
| POST | `/api/notes/{id}/versions/{version_no}/restore` | Snapshots the current state, then re-applies the chosen version's title + body (a restore is itself an edit, so it can be undone). Returns the updated detail. `404` for an unknown note **or** unknown version number. |

**Title is locked.** A note's `title` is added to `extra["user_edited_fields"]`
on create, so re-extraction (and the re-extraction a body edit triggers)
never overwrites it; the body still drives the auto-extracted summary, topics,
tags, and kind. Each create/edit/restore also appends an ingestion event
(`received` / `note_edited` / `note_restored`) to the document's audit trail.

**Create body:**

```json
{"title": "Mortgage call notes", "body_markdown": "# Call with broker\n\n- rate 3.9% …"}
```

## 1.18 Admin — `/api/admin`

Admin-only context and user management. Every endpoint requires the **admin**
role (`require_admin`): anonymous → `401`, non-admin → `403`. Full design notes
in [admin.md](admin.md).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/admin/system` | App version + git sha, redacted operational config, deployment topology, and live DB stats (documents by status, users, job-queue depth, total extraction spend). |
| GET | `/api/admin/architecture` | `docs/architecture.md` + `docs/ingestion.md` as `{docs: [{name, title, markdown}]}` (rendered client-side). |
| GET | `/api/admin/coverage` | Backend/frontend coverage vs gate from the CI-baked summary: `{available, backend, frontend, test_types, generated_at, git_sha}`, where `test_types` lists the CI test types the summary recorded; `available: false` when no summary is baked in. |
| GET | `/api/admin/users` | Every user: `[{id, username, display_name, is_admin, is_active, created_at}]`. |
| POST | `/api/admin/users` | Body `{username, password, display_name?, is_admin?}`. `201`; `409` if the username exists. |
| PATCH | `/api/admin/users/{id}` | Body `{is_admin?, is_active?}`. Promote/demote, activate/deactivate. `404` unknown; `409` if it would remove the **last active admin**. Deactivating also revokes the user's sessions and tokens. |
| DELETE | `/api/admin/users/{id}` | Permanently delete a user. `204`; `404` unknown; `409` if it would remove the **last active admin**; `400` for your own account. The last-admin check runs first, so a sole admin deleting themselves gets the clearer `409`. Sessions and API tokens cascade away; the linked recipient is only **unlinked** (`recipients.user_id` is `ON DELETE SET NULL`), so documents addressed to that person stay addressed. |
| PATCH | `/api/admin/recipients/{id}` | Rename or merge a recipient. See §1.18.1. |
| DELETE | `/api/admin/recipients/{id}` | Delete a recipient, reassigning its documents. See §1.18.2. |

The system `config` view exposes only a curated, secret-free subset of settings
— never API keys, passwords, or internal URLs.

### 1.18.1 `PATCH /api/admin/recipients/{id}` — rename / merge

Body `{name, merge?}` (`name` trimmed, ≤255 chars; `merge` default `false`).
Recipients are a shared taxonomy, so this is admin-only.

- **`200`** → `{id, name}`. The name was updated in place. The collision check is
  **case-insensitive but excludes the recipient itself**, so a pure casing change
  (`john` → `John`) succeeds here.
- **`400`** → name was blank after trimming.
- **`404`** → no such recipient.
- **`409`** → the (case-insensitive) name matches **another** recipient and
  `merge` was not set. Body:

  ```json
  {"detail": "…", "target_id": 7, "target_name": "John", "target_document_count": 4}
  ```

  The conflict fields sit at the **top level** alongside the human-readable
  `detail` string (a flat body, returned via `JSONResponse` so FastAPI does not
  nest them). Re-send with `{name, "merge": true}` to reassign this recipient's
  documents onto `target_id`, delete this recipient, and return the surviving
  target `{id, name}` (`200`).

### 1.18.2 `DELETE /api/admin/recipients/{id}` — reassign-then-delete

Deletes a recipient, first moving its documents off it. The `reassign_to` query
param is **three-state**:

| `reassign_to` | Effect |
|---|---|
| *omitted* | Zero-document recipient → deleted (`204`). In-use recipient → `409` (see below). |
| `=<id>` | Move this recipient's documents to recipient `<id>`, then delete (`204`). |
| `=` (empty / `null`) | Clear the recipient on its documents (set NULL), then delete (`204`). |

All reassignments move **every** document, soft-deleted included. Responses:

- **`204`** → deleted (with documents reassigned/cleared as above).
- **`400`** → `reassign_to` equals the recipient being deleted (self-reassign).
- **`404`** → unknown recipient, or unknown `reassign_to` target.
- **`409`** → recipient still has documents and `reassign_to` was omitted. Body
  is flat (top-level fields, returned via `JSONResponse`):
  `{"detail": "…", "document_count": 4}`.
- **`422`** → `reassign_to` was neither an integer, empty, nor `null`.

### 1.18.3 `POST /api/admin/recipients` — create

Body `{name}` (trimmed, ≤255). Creates a recipient, deduping
case-insensitively. **`201`** → `{id, name}` for a new recipient; **`200`** →
the existing `{id, name}` when the name already exists (no duplicate is made);
**`422`** → blank name.

### 1.18.4 Senders — `POST` / `PATCH` / `DELETE /api/admin/senders`

Senders mirror recipients exactly (same id-keyed rename/merge and
reassign-then-delete contract; `Document.sender_id` is nullable `ON DELETE SET
NULL`, so documents are never deleted — only their pointer nulled):

- **`POST /api/admin/senders`** `{name}` → create/dedupe (`201` new, `200`
  existing, `422` blank), returns `{id, name}`.
- **`PATCH /api/admin/senders/{id}`** `{name, merge?}` → rename in place (`200`),
  or on a case-insensitive collision with another sender return `409`
  `{detail, target_id, target_name, target_document_count}`; re-send with
  `merge: true` to fold this sender into the target. `400` blank, `404` unknown.
- **`DELETE /api/admin/senders/{id}?reassign_to=…`** → three-state `reassign_to`
  exactly as recipients (omitted/`<id>`/empty). `204` deleted, `400`
  self-reassign, `404` unknown sender or target, `409` `{detail, document_count}`
  in-use without a target, `422` non-integer `reassign_to`.

### 1.18.5 Kinds — `PATCH` / `DELETE /api/admin/kinds/{slug}`

Kinds are keyed by their **stable, unique `slug`**. (Create already exists at the
public `POST /api/kinds`, §1.8.4.1.)

- **`PATCH /api/admin/kinds/{slug}`** `{name}` → rename the **display name only**;
  the slug never changes (anything keyed on it keeps working). The name is
  standardised to sentence case (matching `create_kind`). There is **no merge**:
  a case-insensitive name collision with another kind is refused with `409`
  `{detail, target_slug, target_name}`. `400` blank name, `404` unknown slug.
- **`DELETE /api/admin/kinds/{slug}?reassign_to=…`** → reassign-then-delete, but
  the target is a **kind slug** (not an id): omitted → `204` if unused / `409`
  `{detail, document_count}` if in use; `=<slug>` → move documents onto that kind,
  then delete; `=` (empty/`null`) → null the kind on its documents, then delete.
  `400` self-reassign, `404` unknown kind or target.

All reference mutations above (senders/kinds/recipients, create/rename/delete)
serialise on a shared transaction-scoped **advisory lock**, so concurrent admin
edits (e.g. two merges into the same target) can't interleave.

### 1.18.6 Currencies — `GET /api/admin/currencies`, `POST /api/admin/currencies/normalize`

Currency is free-text (no reference table) rather than a reference row, so
normalising a code is a whole-store rewrite, not a table CRUD.

- **`GET /api/admin/currencies`** → `[{code, document_count}, …]`: the distinct
  currency codes on non-deleted documents, with counts, ordered by code.
- **`POST /api/admin/currencies/normalize`** `{from_code, to_code}` → rename
  `from_code` to `to_code` everywhere. Both codes are trimmed and upper-cased
  before comparison and must match `^[A-Z]{3}$`; the response echoes the
  normalised (upper-case) codes. On success (**`200`**):

  ```json
  {"from_code": "USD", "to_code": "EUR", "counts": {"documents": 12}, "fx_rate_missing": true}
  ```

  What it touches (in one transaction, under a dedicated advisory lock): a single
  `UPDATE documents`. `fx_rates` is **never** mutated; `fx_rate_missing` is `true`
  when `to_code` has no rate row (FX conversion for it is unavailable until one is
  seeded).

  Errors: **`422`** a code is not `^[A-Z]{3}$`; **`400`** `from_code` and
  `to_code` are the same. **There is no `409`.** Until 2026-08-31 the rename also
  rewrote `authored_series`, `authored_series_suggestions` and the
  `series_insights` cache, and **refused** with a `409` plus a `conflicts` list
  when it would collide in `series_membership_overrides` or
  `series_meta_overrides` — those held user-authored pins and titles, and a
  currency was part of series identity. The **code** that read those tables went
  with the legacy series stack, so `counts` now carries only `documents` and the
  conflict case cannot arise. The tables themselves were dropped a little later,
  by migration 0038 — see [architecture.md](architecture.md) §1.9.

### 1.18.7 FX rates — `GET /api/admin/fx-rates`, `POST /api/admin/fx-rates`

Cross-currency conversion needs one `fx_rates` row per currency (base =
USD, so `rate_to_base` is the value of one unit in USD; a single row suffices —
`library.fx` falls back to the nearest-date rate). These endpoints report and
seed those rows; the normalise flow above only *flags* a missing rate.

- **`GET /api/admin/fx-rates`** → `[{code, document_count, is_base, has_rate, rate_to_base, as_of}, …]`:
  one entry per in-use currency. `is_base` is USD (always convertible at 1.0,
  never seeded); `has_rate` says whether a row exists, with the latest
  `rate_to_base`/`as_of` when it does (else both `null`).
- **`POST /api/admin/fx-rates`** `{currency, source, rate_to_base?, as_of?}` →
  seed (upsert on `(currency, as_of)`, `as_of` defaults to today). `source`:
  - `"live"` — fetch the current USD-per-unit rate from the provider
    (`open.er-api.com`, keyless; `rate_to_base(X) = 1 / (USD→X)`), then seed.
  - `"manual"` — seed the supplied `rate_to_base` (a positive decimal string,
    USD per one unit). Required for this source.

  Success (**`200`**): `{"currency": "EUR", "as_of": "2026-07-03", "rate_to_base": "1.09000000"}`.
  Errors: **`422`** the code is not `^[A-Z]{3}$`, is USD (the base), or a
  `"manual"` request omits `rate_to_base`; **`502`** the live provider failed or
  does not list the currency (the admin UI then offers manual entry).

## 1.19 Comments — `/api/documents/{id}/comments`

A **comment** is user-authored, dated free text attached to an *existing*
document (`library.models.DocumentComment`, table `document_comments`) — a
distinct concept from a **note** (§1.17), which is its own `source="note"`
Document. A comment cannot exist without a parent document, is never itself
searchable as a document, and is meant for annotating/correcting context
("this is my current house") rather than authoring new content. Auth + CSRF
apply exactly as elsewhere (§1.9).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/documents/{id}/comments` | The document's comments, **newest first**: `[{id, document_id, author_id, body, created_at}, …]`. `404` for unknown/deleted documents. |
| POST | `/api/documents/{id}/comments` | Body `{"body": "…"}`. `201` with the created comment. Blank/whitespace-only `body` is rejected `422`. Writes a `comment_added` ingestion event and defers a re-embed of the document. |
| PATCH | `/api/documents/{id}/comments/{cid}` | Body `{"body": "…"}`, full replacement (there is no partial-field edit — a comment has one field). `200` with the updated comment. Blank body `422`; unknown or foreign (belonging to a different document) comment `404`. Writes a `comment_edited` event and defers a re-embed. |
| DELETE | `/api/documents/{id}/comments/{cid}` | `204` on success; unknown or foreign comment `404`. Writes a `comment_deleted` event and defers a re-embed (the comment's chunk is dropped from search on the next embed run). |

**Response shape (`CommentOut`):**

```json
{"id": 7, "document_id": 42, "author_id": 3, "body": "This is our current house.", "created_at": "2026-07-06T09:15:00Z"}
```

The same shape is embedded in the document detail response as `comments`
(§1.4) — there is no separate "get one comment" endpoint; read a document's
comments via either `GET /api/documents/{id}` or this list route.

**Indexed for `/ask`.** Every create/edit/delete defers `embed_document`
(`library.jobs`), which re-embeds the document's page/OCR text **and** one
extra chunk per comment, framed `User comment (YYYY-MM-DD): <body>`. Each
comment chunk carries the nullable `document_chunks.comment_id`
back-reference (`NULL` for chunks derived from the document's own text,
migration `0022`), so a comment surfaces through `semantic_search` like any
other passage, and the Ask agent's `get_document` tool returns a document's
comments verbatim as authoritative personal context — see
[ask.md §1.9](ask.md).

## 1.20 Saved views — `/api/saved-views`

A **saved view** is a named, per-user snapshot of the document-list filter/search
state (`library.models.SavedView`, table `saved_views`). `filter_state` stores
the homepage URL query verbatim — the frontend's `buildDocumentQuery(applied)`
output — so applying a view is just pushing that query at the homepage. A
`pinned` view also appears in the sidebar as a **custom dashboard**. Views are
strictly per-user: every endpoint is scoped to the authenticated user, and a view
owned by another user is indistinguishable from a missing one (`404`, never
`403`). Auth + CSRF apply as elsewhere (§1.9).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/saved-views` | The caller's views, ordered by `sort_order` then `id`. |
| POST | `/api/saved-views` | Body `{"name", "filter_state"?, "pinned"?}`. `201` with the created view, appended after the caller's existing views. Blank `name` `422`. |
| PATCH | `/api/saved-views/{id}` | Body `{"name"?, "filter_state"?, "pinned"?}`; only fields present change. `200` with the updated view; `404` if not the caller's. |
| DELETE | `/api/saved-views/{id}` | `204` on success; `404` if not the caller's. |
| POST | `/api/saved-views/reorder` | Body `{"ids": [...]}` — **exactly** the caller's current view ids, in the desired order. Sets each view's `sort_order` to its position. `200` with the reordered list; `400` if `ids` is not exactly the caller's set (so a stale client can't silently drop a view). |

**Response shape (`SavedViewOut`):**

```json
{"id": 4, "name": "Unpaid invoices", "filter_state": {"kind": "invoice", "status": "needs_review"}, "pinned": true, "sort_order": 0, "created_at": "2026-07-06T09:15:00Z", "updated_at": "2026-07-06T09:15:00Z"}
```

`filter_state` values are strings or string arrays (repeated query params such
as `tag`), matching the homepage's URL contract (§1.3); an empty object is a
valid "no filters" view.

## 1.21 Held emails — `/api/held-emails`

The **hold-for-review queue**: emails the mailbox poller judged not
library-worthy sit in `held_emails` (their messages safe in the IMAP Held
folder) until a human resolves them. The hold triggers, the row-before-move
contract, and the override/dismiss semantics live in
[ingestion.md](ingestion.md), "Held for review" — this section only documents
the HTTP surface (`library.api.held_emails`). Auth + CSRF apply as elsewhere
(§1.9). Backs the web app's `/held-emails` view.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/held-emails` | Newest-held first. `?status=held` (default — the open queue) `\|ingested\|dismissed\|all`; `limit` 1–100 (default 25), `offset`. Returns `{items, total, limit, offset}`. |
| GET | `/api/held-emails/{id}` | The list item plus `trace` — the full per-item decision trace the poller snapshotted. `404` unknown. |
| POST | `/api/held-emails/{id}/ingest` | **Ingest anyway**: defers the `library.jobs.ingest_held_email` override task and returns `202` `{"queued": true, "job_id": …}`. Track the outcome via the row's `status`/`document_ids`/`last_error` (GET detail) or `GET /api/jobs`. `409` when the row is already resolved; the task itself also no-ops on a non-held row, so a race can never double-ingest. |
| POST | `/api/held-emails/{id}/dismiss` | **Dismiss**: DB-only status flip, returns the updated detail (`200`). The record is kept and the message stays in the Held folder. `409` when already resolved. |

**Row shape** (list item; detail adds `trace`): `id`, `message_id`, `sender`,
`subject`, `received_at`, `created_at`, `verdict` (`llm_hold` /
`below_substance` / `nothing_ingested` / `sender_unknown`), `reason`, `status`
(`held` / `ingested` / `dismissed`), `owner_id` + `owner` (display label,
resolved from the sender like a document's would be), `resolved_at`,
`document_ids` (populated by a successful ingest-anyway), `last_error` (the
most recent failed-resolution error, e.g. the message could not be re-fetched).

## 1.22 Matters — `/api/matters`

Evergreen **business matters**: subject categories (car insurance, health
insurance, subscriptions) a document may belong to any number of, a many-to-many
grouping (`matters` + `document_matters` tables, migration 0028) mirroring the
projects surface. Each matter carries a `hint` — free text that guides the LLM
matter classifier ([ingestion.md](ingestion.md), "Matter classification"),
which auto-files documents into existing matters. A document's matter membership
is edited through `PATCH /api/documents/{id}` (the `matters` field, §1.5) and
surfaced as the `matters` array on every document list/detail item; documents
are also filterable by `?matter=<slug>`, which **ORs** repeated values (§1.3.1).

**Slugs are stable.** `POST` derives a slug from the name (or accepts an
explicit, normalised `slug` override); `PATCH` never changes it, so inbound
links and the `?matter=` filter survive renames. Counts exclude soft-deleted
documents and include zero-count matters.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/matters` | All matters ordered by name, each with its document count. `?include_archived=true` to include archived ones (hidden by default). Open to all authenticated users. |
| POST | `/api/matters` | **Admin only** (`403` otherwise). Body `{name, slug?, hint?}`. `201`; `409` if the slug already exists. |
| GET | `/api/matters/{slug}` | One matter with its document count; `404` if unknown. Open to all authenticated users. |
| PATCH | `/api/matters/{slug}` | **Admin only** (`403` otherwise). Body `{name?, hint?, archived?}`; only present fields change. The slug is **immutable**. `archived: true/false` toggles `archived_at`. `404` if unknown. |
| DELETE | `/api/matters/{slug}` | **Admin only** (`403` otherwise). Hard-delete; `204`. Memberships cascade away (`document_matters`), the **documents themselves are kept**. `404` if unknown. |

Matters are a global, shared taxonomy, so mutating them is restricted to admins
(reads stay open). Editing a hint changes what the classifier sees, so re-file
existing documents afterwards with `library sweep-matters` (see
[admin.md](admin.md) and [ingestion.md](ingestion.md), "Matter classification").

**Matter object** (every endpoint returns this shape; `GET /api/matters`
returns an array of them):

```json
{
  "id": 4,
  "slug": "car-insurance",
  "name": "Car insurance",
  "hint": "Motor insurance policies, renewals, claims, and green cards",
  "archived": false,
  "document_count": 7
}
```

`document_count` is the number of non-deleted documents in the matter. Auth +
CSRF apply exactly as elsewhere (§1.9).

## 1.23 Facets — `/api/facets`, `/api/documents/{id}/labels`, `/api/facet-suggestions`

The controlled facet vocabulary (`library.api.facets`) and the labels
documents carry against it. See [facets.md](facets.md) for what a facet is,
why it replaced free-form tags, the shipped vocabulary, and the cost of each
edit; this section is the wire contract only.

A facet is one closed dimension (`category`, `scope`, `cost_type`, …); a
document holds **at most one value per facet**. The vocabulary is a
**closed set**: nothing below ever creates a value implicitly by naming
one — `POST /api/facet-suggestions/{id}/accept` (§1.23.3) is the only route
that widens it.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/facets` | The whole vocabulary: every facet, its values, each value's aliases, and each value's stored `colour` (`null` if none is set — see below). |
| GET | `/api/facets/counts` | Document counts per facet value, for proposing charts (§1.23.5). |
| GET | `/api/facets/label-counts` | Documents actually carrying each value — the number the vocabulary panel shows and `DELETE .../values/{value_key}` enforces (§1.23.6). |
| POST | `/api/facets` | Create a facet. Body `{key, label, ordinal?}`. `201`; `409` if the key already exists. |
| POST | `/api/facets/{facet_key}/values` | Add a value to a facet. Body `{key, label}`. `201`; `404` unknown facet; `409` if the value key already exists in that facet. |
| PATCH | `/api/facets/{facet_key}/values/{value_key}` | Edit a value. Body `{label?, colour?}`, both optional and independent. Renaming a label is free — labels reference the value's id, not its text. `colour` is a six-digit `#rrggbb` hex string or `null`; an **absent** `colour` leaves it untouched, an **explicit `null`** clears it back to the client's derived palette slot. `404` unknown facet/value; `422` a present `label` of `null` (a value must keep a display label) or a `colour` that is not a six-digit hex. Returns the resulting value: `{key, label, parent_id, aliases, colour}`. |
| POST | `/api/facets/{facet_key}/values/{value_key}/aliases` | Add an alias (a surface form the labeller and search also recognise). Body `{alias}`. `404` unknown facet/value. |
| POST | `/api/facets/{facet_key}/values/{value_key}/merge` | Fold this value into another. Body `{into, dry_run?}`. `404` unknown facet/value (either side, on a dry run too); `409` if `into` names the value being merged. |
| DELETE | `/api/facets/{facet_key}/values/{value_key}` | Delete an unused value. `204`; `404` unknown facet/value; `409` if any document still carries it. |
| GET | `/api/documents/{id}/labels` | This document's labels: `{"labels": {facet_key: value_key, ...}}`. Facets the document has no value for are simply absent from the map. |
| PUT | `/api/documents/{id}/labels` | Set or clear labels. Body `{"labels": {facet_key: value_key_or_null, ...}}`; `null` clears that facet. Returns the document's full label map (as GET). `404` unknown document (a soft-deleted one counts as unknown — a trashed document is not labelled) or unknown facet key; **`422`** if a value key is not in that facet's vocabulary — the closed set holds at the API boundary, not only in the labeller. |
| GET | `/api/facet-suggestions` | Up to 100 pending suggestions (oldest first): `{id, facet, suggested_label, reason, document_id}` — values the labeller wanted but the vocabulary did not contain (§1.23.3). |
| POST | `/api/facet-suggestions/{id}/accept` | Create the suggested value and apply it to the originating document in one call. Returns `{facet, value}`. `404` unknown suggestion; `409` if the derived key already exists in that facet; `422` if no key can be derived (§1.23.3). |
| POST | `/api/facet-suggestions/{id}/dismiss` | Reject a suggestion without creating anything. Returns `{"state": "dismissed"}`. `404` unknown suggestion. |

### 1.23.1 Merge, and its `dry_run`

`POST /api/facets/{facet_key}/values/{value_key}/merge` folds `value_key`
into `into`: every document label pointing at `value_key` is repointed at
`into`, and `value_key` survives only as an alias of `into` (so a document
still using the old wording is still recognised). With `dry_run: true`, the
route runs the same count the real merge would move and returns
`{"moved": N}` **without changing anything** — the source value, its labels
and its own aliases are all untouched — so a caller can preview the size of a
merge before committing to it. A dry run still resolves **both** sides, so an
unknown `into` is a `404` and `into` naming the value itself is a `409`
exactly as on the real merge — a preview must fail on everything the merge
would. Merging a value into itself is refused rather than treated as a no-op:
the fold is a copy-then-delete, so with both sides equal it would delete the
value and every alias it had.

### 1.23.2 Vocabulary object shapes

```json
{
  "facets": [
    {
      "key": "category", "label": "Category", "ordinal": 0,
      "values": [
        {
          "key": "vehicle-service", "label": "Vehicle service",
          "parent_id": null,
          "aliases": ["auto repair", "car service", "oil change"],
          "colour": null
        }
      ]
    }
  ]
}
```

`parent_id` is always `null` today — reserved for a facet to gain a second
level as a data change rather than a migration (see [facets.md](facets.md)
§7). Value and facet `key`s are restricted to `^[a-z0-9_-]+$`, 1–64
characters; labels are free text up to 255 characters. `colour` is a
six-digit `#rrggbb` hex string or `null`; `null` means the client derives a
stable palette slot from the value's `key` rather than reading a stored one
(see [facets.md](facets.md) §4.1).

### 1.23.3 Suggestions

The labeller never invents a vocabulary value. When it wants one that is not
in the vocabulary it returns `unknown` for that facet plus a suggested label,
which is queued as a pending row (`facet_value_suggestions`) rather than
entered directly. `GET /api/facet-suggestions` lists the pending queue;
`accept` creates the value (deriving its key from the suggested label) and
labels the document that prompted it in the same call; `dismiss` rejects the
suggestion, leaving the vocabulary and the document's labels unchanged.

`accept` is the only route that widens the vocabulary, so the key it derives
is held to the same contract `POST /api/facets/{key}/values` enforces: the
label is lower-cased, spaces become hyphens, anything outside `[a-z0-9_-]` is
dropped, runs of `-`/`_` collapse, and the result is trimmed to 64
characters — `"EV charging (home)!"` becomes `ev-charging-home`. The
suggested label itself is stored unchanged as the value's display label. If
nothing usable remains (a label of pure punctuation), the response is `422`
naming the label rather than a value with an unusable key. The labeller
clamps a suggested label to the column's 255 characters before it is queued.

### 1.23.4 Filtering documents by facet

`GET /api/documents` accepts a repeatable `?facet=key:value` parameter
(§1.3.1): `?facet=category:energy&facet=scope:business` requires **both**.
Two different facet keys AND-compose (a document must match every one given);
the same key repeated with the **same** value is accepted (a no-op
duplicate); the same key repeated with **two different values** is `422` —
a document can only ever hold one value per facet, so that combination could
never match anything. A malformed pair (missing the `:`, an empty key, or an
empty value) is also `422`. A facet or value that does not exist in the
vocabulary is not an error — the filter is a plain equality match, so it
narrows results to nothing rather than being rejected.

### 1.23.5 Document counts per facet value — `GET /api/facets/counts`

`{"counts": [{"facet_key": str, "value_key": str, "documents": int,
"first_date": date | None, "last_date": date | None}, ...]}`, ordered by
`documents` descending then `facet_key`/`value_key`. What the empty state
proposes charts from: a value nobody's ever put money against should not be
offered as a question worth asking. A separate route rather than counts added
to `GET /api/facets`, so `DocumentFilterBar` — which loads the vocabulary on
every document list render — does not start paying for an aggregate it never
reads.

Counted over `spend_facts` (see [charts.md](charts.md) §2), not
`document_labels`, and that choice does the filtering for free: the view
requires `amount_total IS NOT NULL` and its join to `payments` excludes
soft-deleted documents, so neither an amountless nor a deleted document's
label can put a moneyless proposal in front of the owner — a value with no
qualifying document is simply absent from the response, not present with
`documents: 0`. `is_canonical` is the one filter that is not free and so is
explicit in the query: a merged twin (two documents recognised as one real
payment) is a second row for money already counted once.

The query joins `spend_facts` to `jsonb_each_text(sf.labels)` and counts
`DISTINCT sf.document_id` per `(facet_key, value_key)` pair, which guards a
second, unrelated overcounting mechanism from `is_canonical`: one canonical
document split across spend lines emits one `spend_facts` row per line, and
two or more lines can carry the same label — each producing its own
`(facet_key, value_key)` pair from the same document. Without `DISTINCT`, a
three-line document all labelled `category=software` would count as three
documents rather than one. `is_canonical` and `count(DISTINCT ...)` are not
redundant with each other: the first deduplicates merged-twin *documents*,
the second deduplicates split *lines* of a single document — removing either
overcounts, on a different class of archive data than the other catches.

### 1.23.6 Documents actually carrying a value — `GET /api/facets/label-counts`

`{"counts": [{"facet_key": str, "value_key": str, "labelled": int}, ...]}`.
No date span, because nothing here reads `spend_facts` — this route counts
rows in `document_labels` directly, unfiltered, grouped by `(facet_key,
value_key)`. A value no document carries is absent from the response, which
is what makes it deletable.

**Why this is a separate route rather than a field on §1.23.5's
`/api/facets/counts`, and not just a smaller aggregate over the same
table.** The vocabulary panel's whole premise is that the number it shows an
owner is the number an operation will act on, and every write path it offers
— rename, alias, merge, delete — acts on `document_labels`, not
`spend_facts`. The two counts diverge in three directions, all of them in the
direction that makes a panel built on the money count lie:

1. `spend_facts`'s `eligible` CTE requires `amount_total IS NOT NULL`, so a
   value carried only by documents with no amount has **no row at all** in
   `/api/facets/counts` — it renders as unused, and deleting it then answers
   `409` naming a count the owner had no way to see.
2. `spend_facts` joins to `payments`, which excludes soft-deleted documents,
   and §1.23.5's route filters to `is_canonical`; a value carried by a
   soft-deleted or non-canonical document is excluded from the money count
   but still blocks a delete.
3. For a split document, `spend_facts` reads per-line `labels` (which
   coalesce the document's own labels with each line's), so a `(facet,
   value)` pair carried only by a split line can appear in the money count
   without the document carrying it in `document_labels` at all — the
   divergence runs in both directions, not only toward under-counting.

Widening `/api/facets/counts` to also emit money-less rows was the first
design, and it is rejected: that route's docstring names it as what the
spending-view empty state proposes charts from, and
`tests/test_api_spending.py::test_a_value_with_no_money_behind_it_is_absent`
asserts a money-less value's absence there **on purpose** — proposing a
chart of a value the archive has no amounts for is exactly the noise the
empty state exists to not show. Adding rows for those values would change
that route's contract underneath the spending-view plan that owns it, to
serve a panel that can simply ask its own question instead. Two questions,
two routes; `/api/facets/counts` is unchanged by this section.

`GET /api/facets/label-counts` is also the number
`DELETE /api/facets/{facet_key}/values/{value_key}` enforces: `delete_value`
calls the same `count_labels` helper this route's grouped form (`label_counts`)
is built from, rather than a second, separately-maintained count query — the
implementation previously inlined its own duplicate of that count, and the
duplicate was deleted rather than covered by a test asserting the two agree,
since such a test passes whenever neither exercises the branch where they
would have differed.

## 1.24 Payments — `/api/documents/{id}/payment`, `/api/payments/merge`, `/api/payments/split`, `/api/payments/duplicates`

Which documents describe one real-world payment (`library.api.payments`,
`library.money.payments`). An invoice and its receipt, or a statement and the
transaction it lists, often land as two separate documents with the same
`amount_total` — summed naively, that double-counts. A **payment** is the
connected component of documents the `payment_edges`/`payments` SQL views
(migration 0033) join by shared sender, date, amount, currency and reference,
plus any human `merge`/`split` override; `payment_id` is stable only for the
lifetime of the current row set (it is `min(document_id)` over the group, so
it can change as documents are added, deleted, merged or split — never persist
it). See `src/library/money/payments.py` for the rule set (R1–R3, VETO,
OVERRIDE).

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/documents/{id}/payment` | `{payment_id, documents: [{id, title, document_date, amount_kind, reference}, ...]}`, sorted by document id. `404` unknown or soft-deleted document. |
| POST | `/api/payments/merge` | Body `{doc_a, doc_b}`. Records a human override that these two documents are one payment, regardless of what the automatic rules would otherwise decide. Returns the resulting payment (as GET, anchored on `doc_a`). `422` if `doc_a == doc_b`; `404` if either id is unknown or soft-deleted (`_require_both_exist`, so a typo'd id is a 404 rather than a foreign-key 500). |
| POST | `/api/payments/split` | Body `{doc_a, doc_b}`. Records a human override that these two documents are **not** one payment, even if an automatic rule would otherwise merge them. Same response shape, and the same `422` and `404` as merge. |
| GET | `/api/payments/duplicates` | The review surface: every payment with more than one document, `{"groups": [{payment_id, document_ids, count}, ...]}`, largest group first, capped at 100 with no pagination. |

`merge` and `split` both write through `add_override`, the one place that
orders the pair (`doc_a < doc_b` is a database check constraint). Repeating an
override that is already recorded is never a conflict and never changes the
resulting group, but it is not a no-op at the row level: the insert is
`ON CONFLICT DO UPDATE SET created_at = now()`, so it **refreshes that row's
timestamp**. That is deliberate — see below. There is no `DELETE` for an
override: to reverse one, record the opposite kind. That works in **both**
directions, and `tests/test_api_payments.py` covers each separately — `split`
then `merge` back to the original group, and `merge` then `split` back apart.
The pair can carry a row of each kind at once (uniqueness is on the
`(kind, doc_a, doc_b)` triple); the more recently recorded of the two decides,
which is why the refresh matters: without it the *third* correction on a pair
(merge, split, merge) would carry the first merge's stale timestamp and lose.
A tie falls to `split`, but no request sequence can produce one — `created_at`
is the transaction timestamp and each route writes one row and commits — so
that rule is defensive only. See [money-facts.md](money-facts.md) §7.

## 1.25 Spending — `GET /api/spending/{id}`, `GET /api/spending/{id}/footer/{bucket}`

The chart engine's REST surface (`library.api.spending`, `library.charts.*`)
answers a saved question against `spend_facts` and reports what it did not
count. Unlike every other feature in this document, the **full** ten-route
surface — `GET`/`POST /api/spending`, `PATCH`/`DELETE /api/spending/{id}`,
`GET /api/spending/{id}/data`, `GET /api/spending/{id}/cell`,
`POST /api/spending/draft`, and the three
`/api/documents/{id}/spend-lines` routes — is documented in
[charts.md](charts.md) §11, which is this feature's canonical wire reference,
not only its design doc; nothing here duplicates it. This section covers only
the two routes added after that surface was first documented, at the same
level of detail this document gives every other endpoint.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/spending/{id}` | One saved question by id: `{id, name, question_text, rule, default_grain, default_split, display_currency, ordinal}` — the same shape as one element of `GET /api/spending`. `404` unknown id. Exists so the workspace can load a chart directly rather than paging the list, which stops finding a chart once there are more of them than `limit`. |
| GET | `/api/spending/{id}/footer/{bucket}` | The documents behind one footer count (charts.md §7.1). `bucket` is one of `excluded`, `unclassified`, `uncategorised`, `undated`, `unaccounted`; an unrecognised name is a `422` naming it. Query: `?from&to&currency` (resolved exactly as `/data`'s, against the chart's *default* split — `split` itself is not accepted here, `chart_footer_documents` takes no split axis), `?amount_kind` (selects one group out of `excluded`; ignored for every other bucket; `422` naming the requirement if `bucket=excluded` and `amount_kind` is omitted, since an unmatched `excluded` row would otherwise render as an indistinguishable empty page), `?limit` (≤ 100, default 100) and `?offset`. Returns `{bucket, total, documents: [{id, title, date, amount, currency, amount_kind}, ...]}`. `total` is the bucket's **full size before paging** — a bucket bigger than `limit` still returns one page, and without `total` a client cannot tell a complete list of 3 from the first 100 of 340 (`uncategorised` calls this "a visible task" in charts.md §7 precisely because it tends to be large). A document's `amount` is the **sum of its rows in this bucket**: a spend-line-split document can contribute more than one row to the same bucket (charts.md §7.1), and the list deduplicates by document id. `404` unknown chart id. |

`unconvertible` is deliberately not one of the bucket names above: it is not
one of `_CLASSIFY_SQL`'s categories but a merge of two separately-reported
lists, so it has no drill-through today (charts.md §13).
