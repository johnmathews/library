# Charts candidate series — surfacing near-threshold groups

Added visibility for emergent series that are one document short of charting,
plus a one-click way to start tracking them immediately.

## 1. Motivation

Emergent charts appear only once a `(sender, kind, currency)` bucket reaches
`LIBRARY_SERIES_MIN_DOCUMENTS` (default 3) amount-bearing documents. Below that
the bucket is invisible: `GET /api/charts` dropped it silently. So a user
building up, say, Anthropic invoices had no signal that a chart was *almost*
there — nothing to confirm the sender/kind were resolving consistently, and no
way to start a chart before the third document landed.

The trigger was a concrete user goal: "track Anthropic (AI service) invoices and
have new ones auto-added." The right primitive already existed — an emergent
series is a live query on `(sender, kind, currency)`, so a new matching document
auto-appears with zero manual work. The only gap was the pre-threshold blind
spot. This change fills it.

## 2. What changed

### 2.1 Backend — `candidates` on `GET /api/charts`

`src/library/api/charts.py` gains `_candidate_buckets()`: a single `GROUP BY
(sender, kind, currency)` with `HAVING 2 <= count < min`, returning
`{sender_id, sender, kind_id, kind, currency, count, needed, document_ids}` per
bucket, busiest first. `list_charts` now returns `{"series": [...],
"candidates": [...]}`.

Key correctness decision: candidates are grouped **per currency**, not per
`(sender, kind)` pair. That matches the granularity at which an emergent series
actually charts — `summarize_series` gates a second time on the dominant
currency bucket (`series.py:661`), so a pair with 3 docs split 2 USD / 1 EUR is
*not* chartable. Counting per currency makes "N of `needed`" an honest promise:
"one more USD invoice and it charts." Pin/exclude overrides are not applied
(they only matter once a series is charted). Empty when `min <= 2`.

The candidate query carries **no points**, so it's cheap enough to always
compute alongside the charts rather than behind a separate lazy endpoint.

### 2.2 Frontend — opt-in toggle + promote (`ChartsView.vue`)

- Candidates are hidden by default behind a header toggle
  (`charts-candidates-toggle`, shows the count). Revealed, they render as a
  panel (`charts-candidates`) of rows: "`sender · kind` — N of M documents".
- Each row's **Create chart** button (`charts-candidate-promote`) *promotes* the
  bucket: it calls the existing `POST /api/charts/authored` (`createAuthoredSeries`)
  seeded with the bucket's `document_ids` and a derived `sender · kind` name.
  Authored series have no minimum-document gate, so it charts immediately and
  then auto-suggests future matches. **No new backend write path was needed.**

### 2.3 Dedup — don't re-offer a promoted bucket (from code review)

The first cut suppressed a promoted bucket only in session memory
(`promotedKeys`). Code review caught the hole: `create_authored_series` has no
dedup, and the candidate query still reports the raw bucket, so a **page reload**
re-listed it — a second click silently created a *duplicate* authored series over
the same documents.

Fixed server-side instead: `list_charts` now collects the dominant
`(sender, kind, currency)` signature of every authored series and excludes any
candidate bucket matching one. A promoted bucket therefore stops being offered
immediately, and the guard survives reloads. This let the frontend drop the
`promotedKeys` / `visibleCandidates` bookkeeping entirely — the post-promote
reload simply returns without the row.

## 3. Design choices (from the user)

- **Threshold** = "two or more, below min" (`2 ≤ count < min`), not just "exactly
  one short". Identical at the default `min=3`, but generalises if `min` rises.
- **Placement** = a toggle on the Charts page, hidden by default — not a separate
  route or always-on section.
- **Action** = promote to an authored series (charts now), rather than a purely
  informational row that waits for the third document.

Note the two paths this leaves the user: an emergent series is *truly* automatic
(new matching docs appear with no action) but depends on clean, consistent
sender/kind extraction; a promoted authored series is immune to metadata drift
but auto-*suggests* rather than auto-*adds* (one accept-click per new document).

## 4. Verification

- Backend: two new `test_charts_api.py` specs —
  `test_charts_surfaces_near_threshold_candidates` (a 2-doc bucket is a
  candidate with `count 2` / `needed 3` / 2 `document_ids`; a 3-doc series is
  not; neither leaks into the other list) and
  `test_promoting_a_candidate_removes_it_from_the_list` (promote → the bucket
  drops out of `candidates` and appears as an authored series). `test_charts_api.py`
  green (10 passed); full backend suite green (1046). `ruff check` + `ruff format
  --check` clean repo-wide.
- Frontend: three new `ChartsView.spec.ts` specs (toggle reveals candidates;
  no toggle when none; promote calls `createAuthoredSeries` with the derived
  name/currency/docs, reloads, and drops the promoted row). Full unit suite
  green (900). `vue-tsc` + `eslint` clean.
- Code review (subagent, adversarial): flagged the session-only dedup hole;
  fixed as §2.3 above.
- Docs: `docs/api.md §1.14` (candidates response, promote, signature-based
  exclusion) and `docs/frontend.md` `ChartsView` row updated.

## 5. Follow-ups

- Not yet exercised end-to-end against a live stack or in Playwright e2e
  (`e2e/charts.spec.ts`); run the full backend suite + e2e before merge per the
  shared-backend isolation notes.
- The emergent-series auto-tracking hinges on the extraction pipeline resolving
  one `Sender` per real-world sender. If Anthropic invoices fracture across
  sender variants, the candidate/chart won't form — a sender-merge/alias tool
  would be the natural next lever, but it's out of scope here.
