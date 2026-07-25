# Smart Groups: semantic auto-populated series

**Status:** design, awaiting review (2026-07-24)

## 1. Problem

The `/charts` view only produces **emergent** series, auto-detected by grouping documents
on the triple `(sender_id, kind_id, currency)`. This has two consequences the user hit:

1. **Charts can only be per-sender.** There is no way to chart a concept that spans senders —
   "my EV charging fees" (Fastned, Shell Recharge, Allego, …) or "my accountant"
   (bookkeeper + tax filings + advisory) each live under several senders and can never
   become one chart.
2. **Duplicate tiles.** Two tiles named "De Hooge Waerder" / two named "Anthropic" appear
   because the group key is the integer `sender_id` (plus `kind_id`, `currency`), while the
   tile heading prints only the sender *name*. Same name, different tile means either two
   document *kinds*, two currencies, or a genuine **duplicate `Sender` row**. The duplicate-row
   case is a real bug — see §9.

**Authored series** already exist (`AuthoredSeries` + `AuthoredSeriesMember`): a user-named
group with a static, hand-curated membership list that *can* span senders. So the cross-sender
chart is already buildable by hand. What is missing is **learning**: the existing auto-continue
engine (`series_match.py`) only proposes new members matching a group's dominant *sender
signature*, so a genuinely cross-sender group — which has no dominant sender — never gets
suggestions. This design adds membership learned from document **meaning** (embeddings) instead.

## 2. Goal

Add a **semantic mode** to authored series ("Smart Groups"): a named, cross-sender,
optionally mixed-currency chart whose membership is learned from the embeddings of its
documents. The AI **auto-adds** documents it is confident belong; the user **prunes**
mistakes, and each prune is a negative example that sharpens the group.

Non-goals: replacing emergent series; per-group trained ML models; an LLM that *decides*
membership (§4 explains why membership stays mechanical).

## 3. Concept & data model

Extend the existing authored-series tables rather than build a parallel system. A Smart Group
inherits naming, the chart, deep-linking, and the description blurb for free.

Migration (`00NN_smart_groups`):

- `AuthoredSeries`
  - `mode` enum `manual` | `semantic`, default `manual`. Existing rows are `manual`.
  - `currency` becomes the **display** currency; members may be any currency (see §6).
- `AuthoredSeriesMember`
  - `origin` enum `manual` | `auto` | `accepted_suggestion`, default `manual`.
    Drives the "added automatically" affordance (§7) and the prune-as-negative rule (§5).
- **New** `AuthoredSeriesExclusion`
  - `id`, `authored_series_id` (FK, cascade), `document_id` (FK), `created_at`.
  - Unique `(authored_series_id, document_id)`. These are the **negative examples**.
- `AuthoredSeriesSuggestion`
  - Add `score` (float, nullable). Make the sender-signature snapshot columns
    (`signature_sender_id/kind_id/currency`) **nullable** — semantic suggestions are not
    sender-based. Existing `state` (`pending`/`dismissed`) is reused for the staged backfill.

Ruff-format the new migration before pushing (CI runs ruff over the whole repo, migrations
included).

## 4. Membership engine (`src/library/semantic_membership.py`, new)

Embeddings are stored **per chunk** in `document_chunks.embedding` (bge-m3, 1024-dim, pgvector
cosine index at `models.py:551`). The engine needs a document-level vector:

- **Document vector** = L2-normalized mean of that document's chunk embeddings, computed in SQL
  (`AVG` over `document_chunks` for the doc) and cached per call. Mean-pooling is the standard,
  cheapest document representation and needs no schema change.

Scoring a candidate document against a group with positive set `P` (members) and negative set
`N` (exclusions):

- `sim_pos = max(cosine(cand, p) for p in P)`   — nearest positive neighbour (k=1 over positives)
- `sim_neg = max(cosine(cand, n) for n in N)`   — nearest negative, `0.0` if `N` empty
- **belongs** iff `sim_pos >= τ` **and** `sim_pos > sim_neg + margin`

Rationale for this formulation (over a centroid or a majority-vote kNN):

- **Handles diverse groups** — `max` over positives means any sub-cluster counts, so Fastned and
  Shell chargers need not resemble each other.
- **Cold-start safe** — with only a few seeds and no negatives, the `τ` threshold alone gates
  admission; a plain kNN vote would admit everything (every doc's nearest labeled neighbour is a
  positive when there are no negatives).
- **Learns from pruning** — a negative vetoes anything closer to it than to any positive.
- **Cheap & explainable** — one pgvector query per candidate; "closest to <member doc>" is a
  human-readable reason.

`τ` and `margin` are global settings (`semantic_group_min_similarity`,
`semantic_group_neg_margin`), mirroring the existing `series_autocontinue_min_dominance`
precedent. Per-group overrides are a later iteration, not v1.

**Why membership stays mechanical:** `series.py:882` and `series_match.py:12` record that an
LLM was tried for membership-style reasoning and hallucinated a sender absent from the
documents, so those paths were made deterministic. This design honours that: the LLM only
seeds and writes prose (§5, §8); the add/keep/prune decision is always the scorer above.

## 5. Flows

### 5.1 Create + staged backfill

1. User picks "Smart Group" in the create flow, names it ("EV charging fees"), sets a display
   currency, and optionally hand-picks a few seed documents.
2. Backend widens the seed set: the LLM turns the **name** into a semantic query
   (`search.py`), and top hits join the seeds as initial positives.
3. Backend sweeps the whole library, scoring every eligible document (§4). Documents that
   `belong` become `pending` `AuthoredSeriesSuggestion` rows with their `score`.
4. Frontend shows a one-time bulk review: "Found 18 documents that look like EV charging —
   review & add." Accept → `AuthoredSeriesMember` (`origin=accepted_suggestion`); leave
   unchecked / dismiss → `AuthoredSeriesExclusion` (negative).
5. After the review commits, the LLM writes the group description blurb (§8).

The staged backfill is the "sweep, but stage it" decision — the user reviews the retroactive
matches once before they land; only *future* documents auto-add silently (§5.2).

### 5.2 Forward auto-add (silent)

`jobs.py:411-431` already queues `generate_series_insight` + `evaluate_series_autocontinue`
when a document reaches `INDEXED`. Add `evaluate_semantic_groups(document_id)` alongside them:
score the new document against every `mode=semantic` group; if it `belongs`, insert an
`AuthoredSeriesMember` with `origin=auto` and refresh the group insight. Silent by design;
the affordance in §7 keeps it visible.

### 5.3 Prune = learning

Removing a member writes an `AuthoredSeriesExclusion` (negative) so the document (a) won't be
re-added by a later sweep or auto-add and (b) pushes the boundary away from similar documents.
Applies to any origin. A user who later wants it back deletes the exclusion (member-add from
the UI clears any matching exclusion). This is the concrete meaning of "I prune."

## 6. Mixed currency

The scorer is currency-agnostic (embeddings ignore money). The **chart** aggregates members of
any currency by FX-converting each into the group's display currency via the existing
`convert_amount(session, amount, currency, target_currency, ddate)` (`series.py:437`), the same
path already used for pinned emergent members (`_load_pinned_members`, `series.py:402`). The
authored-member loader (`_load_authored_members`, `series.py:688`) must be updated to convert
non-matching currencies instead of assuming a single currency. A document whose FX rate is
unavailable is charted at its native amount only if it already matches the display currency,
otherwise skipped from the totals with a visible "N not converted" note (do not silently drop).

## 7. Auto-add guardrail

Silent auto-add moves real spending totals, so it must stay visible:

- The group tile (`SeriesChartTile.vue`) shows a badge — "3 added automatically" — counting
  `origin=auto` members added since last viewed.
- The "Documents in this series" list flags `origin=auto` rows so pruning a mistake is one
  click (the existing remove control, now writing an exclusion per §5.3).

A global "review auto-added" inbox is explicitly deferred; per-tile visibility is v1.

## 8. LLM's narrow role

- **Name → seed query** (§5.1): one embedding/search call to widen the initial positive set.
- **Description blurb**: reuse the `series_insight.py` machinery (`SERIES_SYSTEM_PROMPT`,
  `generate_description`, `settings.extraction_model`, 200-token cap). No new model setting →
  no new `MODEL_PRICING_USD_PER_MTOK` row required.

Nothing else. Membership is never an LLM decision.

## 9. Companion fix: duplicate senders (separate change)

Root cause of the duplicate tiles: `upsert_sender` (`extraction/apply.py:74`) only
`name.strip()`s, while `create_sender` (`taxonomy.py:428`) does `" ".join(name.split())`
(collapses internal whitespace too). OCR yielding `"De  Hooge  Waerder"` (double space) makes a
second `Sender` row that displays identically.

- **Fix:** normalize `upsert_sender` to `" ".join(name.split())` to match `create_sender`.
  Prevents *future* duplicates. One-line change + a regression test.
- **Existing dupes:** merge via `rename_sender(merge=True)` / `reassign_and_delete_sender`
  (`taxonomy.py:454`, `:496`). Verify against the live DB first whether the current
  De Hooge Waerder / Anthropic pairs are this bug vs. two document kinds vs. two currencies,
  then merge only the true duplicates.

Ship this as its own small PR, not bundled into the Smart Groups migration.

## 10. API surface

- `POST /charts/authored` — accept `mode` (`manual`|`semantic`) and optional
  `seed_document_ids`; for semantic, kick off the backfill sweep and return the staged
  suggestion set (respecting the `limit <= 100` cap on any list payload).
- Staged review reuses the existing suggestion accept/dismiss endpoints
  (`POST …/suggestions/{doc}/accept`, `…/dismiss`); dismiss now also writes an exclusion.
- Member remove (`DELETE …/members/{document_id}`) writes an exclusion.
- Everything else (`GET /charts`, `GET /charts/{id}`, meta PUT) works unchanged; semantic
  groups serialize as authored series with `mode=semantic` and the auto-added count.

## 11. Testing

- **Unit — scorer:** graded embedding vectors (not orthogonal one-hot — the equidistant-vectors
  gotcha gives arbitrary DB order); cover cold-start (positives only, `τ` gate), negative veto
  (`sim_neg + margin`), and threshold boundaries.
- **Unit — flows:** backfill sweep produces the right `pending` set; forward auto-add adds/skips
  correctly and tags `origin=auto`; prune writes an exclusion and a re-sweep does not re-add.
- **Unit — FX:** mixed-currency members convert; unavailable-rate documents are noted, not
  silently dropped.
- **API:** create semantic group, staged review round-trip, member add clears exclusion; honour
  the `limit <= 100` cap; scope list assertions by a unique tag (test DB isolation is
  session-scoped, default list limit 25).
- **Frontend/e2e:** create Smart Group, staged review, auto-added badge, prune. Mind the
  shared-backend serial sort (a dated fixture pollutes dashboard `.first()` specs) and the
  mobile/tablet-webkit visibility gotchas.
- Run the **full** backend suite + `ruff format --check` before merge; confirm CI's `promote`
  job is green before `make deploy`.

## 12. Build order

1. Migration + models (§3).
2. `semantic_membership.py` scorer + unit tests (§4).
3. FX-aware authored-member loading (§6).
4. Backfill sweep + create API (§5.1, §10).
5. Forward auto-add job hook (§5.2).
6. Prune-as-negative wiring (§5.3).
7. Frontend: create flow, staged review modal, auto-added affordance (§5, §7).
8. LLM seed-query + blurb (§8).
9. Companion sender fix as a separate PR (§9).
10. Docs (`docs/`), journal entry, roadmap update.
