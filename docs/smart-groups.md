# Smart Groups

**Status:** shipped 2026-07-24. Design: [superpowers/specs/2026-07-24-smart-groups-semantic-series-design.md](superpowers/specs/2026-07-24-smart-groups-semantic-series-design.md).

## 1. What it is

A Smart Group is an [authored series](api.md) (`AuthoredSeries`) with
`mode = "semantic"`. Like a manual authored series it is a user-named group
that charts even without a natural `(sender, kind, currency)` seed, but its
membership is **learned from document meaning** rather than hand-picked:
create one named "EV charging fees" and it can pull in Fastned, Shell
Recharge, and Allego receipts — different senders, potentially different
currencies — without the user enumerating them.

This exists because emergent series are keyed on `(sender_id, kind_id,
currency)`, so a concept that spans senders (an accountant who sends both
bookkeeping and tax-filing invoices, a set of EV charging networks) can never
become one emergent chart. A `mode="manual"` authored series already solves
this by letting a user list documents by hand; a Smart Group adds the
learning on top — same table, same chart, same deep-linking, just an
`origin`-tracked membership set that grows (and prunes) itself.

## 2. Membership model

Two sets per group, both scoped `(authored_series_id, document_id)`:

- **Positives** — `AuthoredSeriesMember` rows, each carrying an `origin`:
  - `manual` — added by hand (default; also what plain `mode="manual"` series use).
  - `accepted_suggestion` — promoted from a staged backfill suggestion (§3.1).
  - `auto` — silently added by the forward auto-add job (§3.2).
- **Negatives** — `AuthoredSeriesExclusion` rows: documents the scorer must
  never re-add. Written whenever a member is removed or a suggestion is
  dismissed (§3.3), regardless of that member's `origin`.

Re-adding a document (manual add, or accepting a suggestion) clears any
matching exclusion, so "I changed my mind" is not permanent.

## 3. The scorer (`src/library/semantic_membership.py`)

**Document vector.** Documents don't carry a single embedding; `bge-m3`
chunk embeddings live per-chunk on `document_chunks.embedding`. The engine
builds a document-level vector by averaging a document's chunk embeddings and
L2-normalizing the mean (`document_vectors`). Documents with no chunks are
omitted — they can't be scored either as a candidate or as a member/exclusion
anchor.

**Scoring** (`score_vector`) a candidate against a group's positive set `P`
and negative set `N`:

- `sim_pos = max(cosine(candidate, p) for p in P)` — nearest-positive-neighbour, not a centroid.
- `sim_neg = max(cosine(candidate, n) for n in N)`, or `0.0` if `N` is empty.
- **belongs** iff `sim_pos >= τ` **and** `sim_pos > sim_neg + margin`.

Two `Settings` fields control the thresholds (`src/library/config.py`),
mirroring the existing `series_autocontinue_min_dominance` precedent:

| Setting | Default | Meaning |
|---|---|---|
| `semantic_group_enabled` | `True` | Feature flag; gates auto-add and the backfill sweep. |
| `semantic_group_min_similarity` (τ) | `0.55` | Minimum cosine similarity to the nearest positive. |
| `semantic_group_neg_margin` | `0.02` | How much `sim_pos` must beat `sim_neg` by. |

Both `0.55`/`0.02` are first-guess tunables, not calibrated against a labeled
set — see the journal entry for the reasoning and how they might get
revisited.

Using nearest-positive-neighbour (`max` over positives) rather than a single
centroid or majority-vote kNN keeps diverse groups working: Fastned and Shell
Recharge receipts don't need to resemble each other, only *each* needs a
close positive. It's also cold-start safe — with a few seeds and zero
negatives, a kNN vote would admit everything (every candidate's nearest
labeled neighbour is a positive when there are no negatives yet); the `τ`
gate alone decides admission until a negative exists to sharpen the
boundary.

## 4. The three flows

### 4.1 Staged backfill, on create

`POST /api/charts/authored` with `mode="semantic"`:

1. The group is created with any hand-picked `seed_document_ids` as
   `origin=manual` members. **The group's name is never used to widen the seed
   set.** An earlier version turned the name into a semantic search and injected
   the hits as positive examples; on a mixed archive that poisoned membership —
   unrelated documents became positives (an insurance policy scoring sim=1.000
   as an "anchor" for a group named "Anthropic"). Positives come only from the
   user's explicit seeds, and
   `test_create_authored_does_not_use_name_search` fails if the path is
   reintroduced.
2. `sweep_backfill` scores every eligible document in the library (non-deleted,
   amount-bearing, not already a member/exclusion) against the seeded
   positives and writes `pending` `AuthoredSeriesSuggestion` rows (with
   `score = sim_pos`) for every match, capped at
   `min(settings.series_suggestion_limit, 100)` (the API's `limit <= 100`
   cap). The write is an upsert that no-ops on conflict, so re-sweeping is
   idempotent.
3. The response carries the staged hits under `backfill`; the frontend opens
   a one-time review modal ("Review N documents that look like this group")
   before creation is really "done." Accept promotes a hit to a member with
   `origin=accepted_suggestion` (via the existing
   `POST …/suggestions/{doc}/accept`); dismiss/leave-unchecked writes an
   exclusion (`POST …/suggestions/{doc}/dismiss`).
4. After the review commits, `refresh_group_blurb` best-effort fills the
   group's description (§6).

Only this first sweep is staged for review — it is a one-time retroactive
sweep of the whole library, which is exactly the kind of bulk change a user
should eyeball once. Everything *after* creation auto-adds silently (§4.2).

### 4.2 Forward auto-add (silent)

`jobs.py`'s per-document `INDEXED` pipeline already queues
`generate_series_insight` and `evaluate_series_autocontinue` when a document
is filed with a resolved sender/kind. Alongside those, it unconditionally
queues `evaluate_semantic_groups(document_id)` — Smart Groups don't need a
sender or kind, only chunk embeddings, so this runs for every indexed
document. `auto_add_document` scores the document against every
`mode="semantic"` group (skipping if `semantic_group_enabled` is off, or if
the document is already a member/exclusion of a group) and, on a match,
inserts an `AuthoredSeriesMember` with `origin=auto`.

This is silent by design — no review step — but stays visible: the group
tile (`SeriesChartTile.vue`) shows a "N added automatically" badge whenever
`series.mode === 'semantic'` and `auto_added_count > 0`, and the members list
flags `origin=auto` rows so pruning a mistake is the same one-click remove
control as any other member.

### 4.3 Prune = negative example

Removing a member (`DELETE …/members/{document_id}`) or dismissing a staged
suggestion (`POST …/suggestions/{document_id}/dismiss`) writes an
`AuthoredSeriesExclusion` — applies to a member of any `origin`. This means
the document (a) won't be re-suggested by a later backfill sweep and (b)
won't be silently re-added by the auto-add job, and (c) actively pulls the
decision boundary away from documents that resemble it (`sim_neg`). Adding
the document back (manual add, or accepting a suggestion) clears the
exclusion, so a prune is reversible, not permanent.

## 5. Mixed currency

The scorer is currency-agnostic — embeddings carry meaning, not money — so a
Smart Group can freely mix currencies. The group's **display** currency is
resolved by `_resolve_display_currency` (`series.py`): an explicit
`AuthoredSeries.currency` wins, and when it is NULL the currency falls back to
the **dominant currency among the group's amount-bearing, non-deleted
members** (ties broken alphabetically). Without that fallback a group created
without a currency charted empty — every member was dropped converting into a
NULL target. It resolves to `None` only when no amount-bearing member carries a
currency at all. Once resolved, `_load_authored_members` (`series.py`)
FX-converts every member's amount into it via `convert_amount(session,
amount, currency, target_currency, ddate)`, the same path used for pinned
emergent members. A member whose FX rate can't be resolved for its date is
dropped from the chart's totals and logged (not silently zeroed) — it can't
contribute a comparable data point, but it's still a real member
(`_load_authored_origins` still counts it for `auto_added_count`, which is
queried independently of the amount-bearing `_Member` rows).

One consequence: the "odd-one-out" signature-break feature (`odd_ones_out`,
surfaced as `odd_one_out_count` on the `/charts` authored payload) is still
computed for Smart Groups off the FX-converted members, but since every
member's `currency` is normalized to the group's display currency before the
signature is derived, the currency axis of that comparison can never fire —
every member always "matches" on currency. Sender and kind can still differ
and be flagged; only the currency dimension is inert for authored series
(manual or semantic).

## 6. The LLM's role — and what it explicitly does not do

The LLM has exactly one job in Smart Groups, and it is narrow:

1. **Description blurb** (`refresh_group_blurb` in `series_insight.py`):
   reuses the existing `series_insight.py` machinery
   (`SERIES_SYSTEM_PROMPT`/`generate_description`, `settings.extraction_model`,
   200-token cap) to write `AuthoredSeries.description` — but **only** when
   `description` is currently `None`. A user-written description is never
   clobbered (same precedent as `SeriesMetaOverride`), and a failure here is
   caught and logged, never allowed to fail group creation.

**Membership is never an LLM decision.** Every add/keep/prune call in
Smart Groups runs through the deterministic scorer in §3. This mirrors an
existing lesson recorded in the codebase: `series_match.py`'s odd-one-out
rationale documents that an LLM once asked to phrase *why* a document breaks
a series' signature hallucinated a sender name that appeared in none of the
documents — so that reasoning was made purely mechanical (built only from
real database values). Smart Groups' membership engine follows the same
rule from day one: the LLM only writes prose after the fact — it never
decides who's in or out, and since the name→seed-query step was removed it no
longer influences the inputs to that decision either.

## 7. See also

- [superpowers/specs/2026-07-24-smart-groups-semantic-series-design.md](superpowers/specs/2026-07-24-smart-groups-semantic-series-design.md) — full design, including the duplicate-sender companion fix (§9, not yet shipped — tracked separately).
- [api.md §1.14](api.md) — the `/api/charts` and `/api/charts/authored/*` REST surface (semantic-mode fields are not yet reflected in that doc's authored-series sections; this doc is the source of truth for Smart Group behavior until it is).
- [frontend.md](frontend.md) — `/charts` view, `SeriesChartTile.vue`, `ChartsView.vue`.
