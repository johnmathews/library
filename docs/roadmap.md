# Roadmap & deferred work

**Status:** active. **Last updated:** 2026-09-02 (§1.1's single item is **half done**. `structured_query.sum_amount` was moved onto `spend_facts` and Ask's tool schemas gained a `facets` property (#136), so the item is rewritten around what is left — rebuilding `compare_to_series` — rather than deleted, and it now says plainly which half shipped and why the comparison was the half deferred: it needs a distribution, a basis for "usual" and a coverage block spanning both sides, none of which `sum_amount` has a shape for. Ask has had no comparative *tool* since 2026-08-31; such questions still get an answer the model assembles from rows itself.) Earlier: 2026-09-01 (§1.4 gains the chart rule editor, issue #135 — deliberately as *shipped* rather than added to §1.1 and deleted in the same change. Its entry closes with what is now editable and what stays fixed at creation, which is the sentence whose absence let #135 go untracked, and names `charts.md` §13 as the canonical limits register while saying plainly that no gate enforces it.) Earlier: 2026-08-31 (the legacy series stack was deleted. §1.1 was "nothing is currently queued" and now carries one item: point Ask's money aggregate at `spend_facts` and give it facet filters, which is also the natural home for rebuilding `compare_to_series`. In §1.4, the `/charts` view and Smart Groups entries are marked shipped-then-removed with the reason, the `smart-groups.md` link repointed to `archive/`, and the dead `api.md §1.14` / `ask.md §1.7` citations dropped; the admin-CRUD entry loses "series-aware". **Fix round 1:** §1.1's payment-identity clause said "an unmerged invoice/receipt pair"; merging does not help either, so the qualifier is gone. Earlier: 2026-08-12 (documentation verification sweep: recorded Recently Deleted, Saved Views and document comments as shipped; narrowed the `ON DELETE SET NULL` claim to `documents`' own FKs). Earlier: 2026-07-25. **Supersedes:** none.
**Last verified:** 2026-09-02 — method: partial, scoped to §1.1. The shipped half was verified by execution rather than by reading: the ten new cases in `tests/test_structured_query.py` were run against the pre-#136 aggregate and eight failed, then run again after the change and all passed, with the full backend suite green (2117 passed, 7 skipped). The **deferred** half was checked as an absence, which is the claim that could rot silently: `grep -rn 'compare_to_series' src/` returns nothing, so "Ask has no comparative TOOL" is a fact about the tree, not a memory of the deletion. The grep supports only that narrower claim, which is why the prose now makes it: a first draft of this entry said Ask "has answered no comparative question", which the grep does not support and `ask.md` §1.1 contradicts — the model still assembles one from rows. Every other item on this page was NOT re-checked and carries forward the verification below. Earlier: 2026-09-01 — method: partial, scoped to the new §1.4 entry only. Its editable/fixed sentence rests on two separate observations, because one cannot carry both halves. **Backend:** `update_chart` in `src/library/api/spending.py` was read — it persists `default_grain` and `display_currency` as well as `rule`, `default_split`, `name` and `ordinal`, and `test_editing_and_deleting_a_chart` round-trips a patched grain. **Frontend:** every `updateChart` call site in `frontend/src` was listed — three, sending `{rule, default_split}`, `{name}` and `{ordinal}` — so no UI sends grain or currency, and the toolbar's controls for them were read and are session view state. An earlier draft of this stamp cited only the call sites and drew the backend conclusion from them, which they cannot support. The `question_text` claim is pinned by `test_patching_a_rule_leaves_question_text_untouched`. §1.1 and the older §1.4 entries were not re-checked and carry forward the verification below. Earlier: 2026-08-31 — method: partial, scoped to §1.1 and the three §1.4 entries touched. The new §1.1 item's three claims were checked in source, not asserted: `grep -n 'amount_kind\|spend_facts' src/library/structured_query.py` returns nothing, and `_FILTER_PROPERTIES` in `src/library/ask/engine.py` exposes eight properties, none of them a facet. Confirmed `docs/archive/smart-groups.md` resolves on disk after the `git mv` and that `docs/smart-groups.md` no longer exists, and re-read `src/library/currencies.py` for the "single `UPDATE documents`, no conflict case" wording. **Fix round 1:** confirmed `is_canonical` appears nowhere in `src/library/structured_query.py` or `src/library/search.py`, which is what makes the unqualified "counted twice whether or not it has been merged" true rather than merely likely. The deferred items in §1.2 and the decision in §1.3 were not re-checked and carry forward the 2026-08-12 verification, whose method was: checked every deferred item against the code that would implement it (greps for `rerank`, `ALLOWED_MIME_TYPES`, matter aggregation, the classifier), every shipped item against its module, migration or e2e spec, and the one cited commit against `git log`; then swept `git log --since=2026-07-20` and the recent migrations for shipped work the document did not record.

Living list of agreed-but-not-yet-built work and explicitly-deferred ideas, so
they don't get lost between sessions. Most recent context lives in
`journal/260628-general-document-store.md`.

## 1.1 Planned next

1. **Rebuild `compare_to_series` over `spend_facts`.**
   The deleted tool answered "is this bill higher than usual?" over a
   `(sender, kind, currency)` group of raw documents, which double-counted an
   invoice/receipt pair and could not be scoped to a curated label — the two
   things that made its answer wrong. A distribution computed over `spend_facts`
   is payment-deduplicated and label-scoped by construction, so a comparative
   tool built there starts correct rather than needing the same caveats bolted
   on. Ask has had no comparative *tool* since 2026-08-31 — such questions still
   reach `query_documents`/`semantic_search` and the model assembles a comparison
   from rows itself, with no shared basis and no coverage spanning the pair. See
   [ask.md](ask.md) §1.1 and §1.10 item 11.

   **The aggregate half of this is done.** `structured_query.sum_amount` was
   moved onto `spend_facts` and the tool schemas gained a `facets` property in
   #136 (2026-09-02), which was deliberately the harder half: the comparison is
   a query over the aggregate, and it now has a correct one to sit on. What
   remains is the comparative tool itself — a distribution, a basis for
   "usual", and a coverage block spanning both sides of the comparison, which
   `sum_amount` has no shape for.


The **admin role + admin views** shipped in the 2026-06-28 cycle: a boolean
`users.is_admin` role with a `require_admin` guard, global project mutations
gated to admins, the `/api/admin/*` API, the `/admin` views page, and a CI
coverage-summary pipeline baked into the image. The admin API started as
system/architecture/coverage/users and has since grown **reference-entity CRUD**
(see §1.4 and [admin.md](admin.md), [api.md §1.18](api.md)).

The previously-planned items all shipped in the 2026-06-28 notes + topics
refinement cycle:

- **Notes (in-app authoring)** — `DocumentSource.NOTE`, the `/api/notes` router
  (create / edit-in-place / version history / restore) and the New-note + detail
  editing UI. See [api.md §1.17](api.md), [ingestion.md](ingestion.md) "Notes",
  and [frontend.md](frontend.md).
- **Topics ↔ tags refinement** — decided (see §1.3): `topics` is now read-only
  and folded into full-text search; `tags` stays the editable filter facet.
- **Corpus backfill** — the `library backfill` command (re-enqueues
  extract→markdown→embed for documents on a stale `PROMPT_VERSION`, general-only
  by default, `--kinds a,b,c` to scope, budget-cap respected worker-side). Bumping
  `PROMPT_VERSION` (e.g. the 2026-07 recipient/date extraction upgrade) makes the
  older corpus stale; run `library backfill --kinds letter,invoice,receipt` to
  re-derive recipients from the document itself first. See
  [ingestion.md](ingestion.md) "Backfill (stale prompt version)".
- **E2E coverage** — Playwright specs `markdown-reader`, `projects`, `notes`,
  and `topics-readonly` (run in CI's e2e job). See [frontend.md §1.7](frontend.md).

## 1.2 Deferred — implement only if a trigger fires

1. **Ask re-ranker.** A second relevance-scoring pass (cross-encoder or
   LLM-judge) over retrieved candidates before they reach Claude.
   **Not needed today** — hybrid retrieval (vector + FTS, RRF-fused) plus
   multi-passage-per-doc (`LIBRARY_RETRIEVE_CHUNKS_PER_DOC`) is sufficient at
   personal scale (`top_k ≤ 10`, single user). **Trigger to revisit:** if Ask
   answers are visibly missing or misranking relevant passages that *are* in
   the corpus — i.e. retrieval quality, not generation, is the bottleneck.
   Cheapest first step then is an LLM-judge rerank reusing the existing
   Anthropic client; only stand up a dedicated reranker model if that's
   inadequate.
2. **`.epub` ingestion.** Not wanted — the corpus is PDF / images / `.md` /
   `.txt` / `.docx`. (`.docx` shipped 2026-07-07, converted to Markdown on
   ingest; the consume folder accepts it too as of the `EXTENSION_TO_MIME` map.)
3. **Document versioning / supersession (non-note files).** Edited-and-re-uploaded
   files create duplicates (SHA-256 content dedup). Now **mooted for in-app notes**
   — they are edited in place with their own `note_versions` history and bypass
   the content dedup (salted sha) — but still relevant for externally-edited
   files re-synced via the consume folder.
4. **Analytics / charts over business matters.** The matters dimension
   (§1.4) ships as a filter/grouping facet only; aggregate views (spend or
   document-count breakdowns *by matter*, over time) were explicitly out of
   scope for the initial cut. **Trigger to revisit:** a concrete need to see
   totals rolled up per matter rather than just filtering the document list.
5. **Embedding-based (non-LLM) matter classifier.** The classifier is one
   Anthropic call per document today. A cheaper future optimisation is to file
   documents by nearest-neighbour over the existing bge-m3 embeddings against
   per-matter centroids (built from each matter's `hint` and its member docs),
   reserving the LLM for ambiguous cases. **Not needed today** — the
   per-document classifier cost is negligible at personal scale and the
   vocabulary is small. (The classifier runs on `matter_classifier_model` =
   `claude-sonnet-4-6`, deliberately **not** Haiku: the judgement — "car-related
   but not car *insurance*" — rewards nuance, and the call is infrequent.)

## 1.3 Decided — `topics` vs `tags`

**Resolved (2026-06-28).** The two no longer overlap:

- `tags` = curated, low-cardinality, **cross-document** labels for
  finding/grouping — the editable **filter facet** (`PATCH /api/documents/{id}`,
  the `?tag=` filter).
- `topics` = a single document's own auto-extracted **subject list** — now
  **read-only** (removed from `DocumentUpdate` and the detail editor) and folded
  into the full-text search vectors (`search_vector_nl`/`search_vector_en` via
  `coalesce(topics::text,'')`, migration `0012_topics_fts`). It renders as
  read-only badges in the UI and still appears in list/detail REST responses and
  the MCP document summary.

`topics` earned its place by becoming search content rather than a parallel
editable taxonomy.

## 1.4 Shipped since 2026-06-28

Recorded here so they read as **done**, not queued:

- **The chart rule editor** (2026-09-01, issue #135): a saved chart's rule is
  editable from the workspace as clause rows over the facet vocabulary, with
  `POST /api/spending/preview` answering the edited rule before it is applied
  and `PATCH` preserving the chart's id, name and board position. The split axis
  is editable in the same panel — a chart created without one previously could
  never gain one, which was the same defect in a stronger form. See
  [spending-view.md](spending-view.md) §4.1 and [charts.md](charts.md) §9, §11.

  After this change a chart's **name, rule and split axis** are all editable;
  **`display_currency` and `default_grain`** are editable by `PATCH` and the
  backend persists both, but **no UI sends them** — the workspace toolbar's
  grain and currency controls are session view state, not the chart's saved
  defaults — and **`question_text`** is deliberately never rewritten by a rule
  edit
  ([charts.md](charts.md) §13). That sentence is the thing whose absence
  produced #135: the limit was stated only in the user guide, tracked in no
  plan and absent from `charts.md`'s own limits list, so nothing an engineer
  reads recorded it. `charts.md` §13 is now the canonical register — a limit
  told to users in `spending-view.md` §8 belongs there too. **That is a review
  convention, not a gate**; nothing checks it automatically.
- **Recently Deleted, Saved Views and document comments** (all 2026-07-06):
  a soft-delete holding area at `/deleted` with restore and purge; per-user
  saved dashboards (`saved_views`, migration 0024, scoped to their owner); and
  threaded per-document comments (`document_comments`, migration 0022) that also
  become Ask-searchable chunks.
- **Business matters (auto-filed subject categories).** An evergreen
  many-to-many dimension (`matters` + `document_matters`, migration 0028): a
  document belongs to any number of admin-curated matters (car insurance, health
  insurance, subscriptions). Filled automatically by a **separate LLM classifier
  pass** (its own Anthropic call on Sonnet, deferred best-effort after extraction,
  merge-only, budget-gated, user-edit-respecting) so the vocabulary can change
  and the corpus be re-filed cheaply — `library sweep-matters` backfills after a
  vocabulary edit. Full REST surface (`/api/matters` CRUD + counts, repeatable
  OR-composing `?matter=` filter, `matters` on document responses + PATCH body), a
  `/matters` admin page, and homepage/editor matter controls. See
  [api.md §1.22](api.md), [admin.md §1.2.7](admin.md), and
  [ingestion.md](ingestion.md) "Matter classification".
- **`/charts` view (series/charts) — shipped, then removed.** An aggregate
  charts dashboard of per-`(sender, kind)` series bar-chart tiles with cached LLM
  descriptions, a shared control bar, authored/manual series creation, editable
  "documents in series" lists, single-chart pages and PDF/JPEG/PNG export.
  **Deleted on 2026-08-31** along with its backend, its Ask tool and its
  `/api/charts` surface: the redesign replaced the `(sender, kind, currency)`
  series with a rule over the facet vocabulary, which spans senders
  deterministically. `/charts` now serves the spending board. See
  [charts.md](charts.md) for what replaced it and
  [journal/260831-delete-series-stack.md](../journal/260831-delete-series-stack.md)
  for why each piece went.
- **FX-rate seeding + admin reference-entity CRUD.** The admin API now covers
  reference entities: senders, kinds, recipients (create / rename-or-merge /
  reassign-then-delete), currency normalization, and **FX-rate seeding**
  (`/api/admin/fx-rates`, base = USD, date-aware) so cross-currency amounts can
  convert. `documents`' reference FKs (sender, recipient, kind) are
  `ON DELETE SET NULL`. Currency normalization was "series-aware" — rewriting
  five tables and refusing with a `409` on a user-authored override collision —
  until the series stack was deleted; it is now a single `UPDATE documents` with
  no conflict case. Every mutation is guarded by a shared advisory lock. See [admin.md](admin.md) and
  [api.md §1.18](api.md).
- **Per-user per-kind tile border colours.** Each user can colour dashboard tiles
  by document kind (a per-user preference); the border is owned by the tile's
  component-layer rule so it paints reliably under Tailwind v4 cascade layers.
  See [frontend.md](frontend.md).
- **Document verification flow.** `PATCH /api/documents/{id}` (and the Ask write
  tool) now recompute validation on edit, so correcting a flagged field clears
  its finding while genuine warnings persist; a "Why this needs review" panel
  lists every finding in plain language, dashboard rows show a short reason, and a
  step-through review queue (`?queue=1`) walks the `needs_review` set. See
  [frontend.md](frontend.md).
- **Smart Groups (semantic authored series).** A `mode="semantic"` authored
  series whose membership is *learned* from bge-m3 document embeddings
  instead of hand-picked: nearest-positive-neighbour scoring
  (`sim_pos ≥ τ` and `sim_pos > sim_neg + margin`) over members (positives)
  and pruned documents (`AuthoredSeriesExclusion`, negatives) lets a group
  span senders and currencies (e.g. "EV charging fees" across several
  networks). Creating one stages a one-time backfill sweep for review; future
  matching documents auto-add silently (`origin=auto`, surfaced by a
  "N added automatically" badge); removing/dismissing a document writes a
  negative example so it isn't re-added. The LLM never decides membership — its
  only job was a best-effort description blurb. **Removed on 2026-08-31**: a
  chart rule over the facet vocabulary spans senders deterministically, which is
  the thing a Smart Group existed to do, and it does so without a similarity
  threshold to tune or a negative-example store to keep. See
  [archive/smart-groups.md](archive/smart-groups.md) (superseded, kept for the
  name-to-seed-query poisoning incident and the nearest-positive-neighbour
  decision) and
  [journal/260725-smart-groups.md](../journal/260725-smart-groups.md). The
  companion duplicate-sender fix identified alongside this (design §9) **shipped
  in `a6c0457` (#40)** and survives — `upsert_sender` collapses internal
  whitespace, so senders differing only by spacing no longer split.
