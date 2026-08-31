# Deleting the series stack

**Date:** 2026-08-31
**Branch:** `delete-series-stack`

## 1. What went

Plan 5 of the charts redesign, code half. Thirteen tasks removed the legacy
series feature end to end:

- **Backend** — six modules (`series.py`, `series_insight.py`, `series_match.py`,
  `semantic_membership.py`, `api/series.py`, `api/charts.py`), seven ORM classes,
  two enums, ten `Settings` fields, three background jobs, fifteen routes under
  `/api/charts` and `/api/series/*`, plus `GET /api/documents/{id}/series`.
- **Ask** — the `compare_to_series` tool, its coverage machinery and its
  disclosure-eval scenario.
- **Frontend** — `ChartsView.vue`, `SeriesChartView.vue`, `SeriesChartTile.vue`,
  `ChartControls.vue`, `DocumentSeriesTrend.vue`, `useChartsGrouping`,
  `useChartsTimeframe`, `chartExport.ts`, 17 API-client functions and the routes
  `/charts/legacy` and `/charts/:seriesId`.
- **Elsewhere** — the currency rename's series-override machinery including its
  `409`, the `series_insight` LLM-backend surface, and the nightly workflow's
  Smart Groups journey.

Two things were *added*, not removed: a guard on Ask's `amount_total` commit
(§5), and an honest limitation in `docs/ask.md` §1.10 saying what Ask's money
answers still get wrong (§3.2).

The seven tables are still in the database. That is deliberate — see §4.

## 2. Six live consumers the redesign spec never named

The redesign spec (2026-08-28) fixed the removal list at the module level. It
named the modules and none of the things importing them, which is the difference
between a list and a plan: deleting §3's modules in the spec's order is a broken
build six times over. The plan-5 design spec's §4 exists solely to name them, and
they are worth recording because "what imports this" is exactly the question a
removal list is structurally bad at answering:

1. **The Ask engine.** It imported `summarize_series`/`serialise_summary` and
   registered `compare_to_series` as a tool — a declaration, a dispatch arm, an
   implementation and four separate blocks of prompt prose, all of which had to
   go together or the tool loop would advertise a tool it could not run.
2. **The document detail page.** `GET /api/documents/{id}/series` fed
   `DocumentSeriesTrend.vue`, mounted inside `DocumentDetailView.vue`. A
   backend-first deletion would have left a card fetching a 404 on every
   document.
3. **Three background jobs.** Series autocontinue, the series-insight refresh
   and the semantic-group membership proposal — imported, defined and queued in
   `jobs.py`, three call sites each.
4. **The disclosure eval.** One of its six scenarios, `series-other-currency`,
   was built on a `SeriesCoverage` exclusion reason. Deleting the coverage type
   without the scenario breaks a command nothing in CI runs, so it would have
   failed silently the next time a human ran it.
5. **The admin currency-normalise operation.** It rewrote four of the seven
   tables in raw SQL and refused with a `409` on an override collision — a
   *behavioural* dependency, not an import, and therefore invisible to a grep for
   module names.
6. **The nightly e2e workflow.** Its only job was `smart-groups`. Deleting the
   workflow would have taken the retrieval-recall measurement with it.

The pattern across all six: two were import-level (findable mechanically), two
were route/mount-level (findable only by following the data), and two were
neither — a raw-SQL behaviour and a CI job whose *name* was the feature. The
grep that finds the first pair finds neither of the last.

## 3. The five decisions, and why

### 3.1 Smart Groups dies rather than being ported

A Smart Group existed to do one thing an emergent series could not: span
senders. It did it by learning membership from bge-m3 embeddings —
nearest-positive-neighbour scoring against members as positives and pruned
documents as negatives, with a similarity threshold and a margin to tune.

A chart rule over the facet vocabulary spans senders too, and does it
**deterministically**: the rule is a SQL predicate, the same documents are in it
today and tomorrow, and the reason a document is in or out is a label a person
chose. The learned version had none of those properties. It also had a failure
mode the deterministic one cannot have — the group's *name* poisoned the seed
query, so naming a group after one of its members skewed what it pulled in.

So the capability survives and the mechanism does not. `docs/smart-groups.md` is
archived rather than deleted, specifically for the two things worth keeping: the
name-to-seed-query poisoning incident, and why membership scoring used
nearest-positive-neighbour rather than a centroid.

### 3.2 `compare_to_series` and `DocumentSeriesTrend` die: the answer was known-wrong

This is the decision that took the most argument, because the tool worked. It
answered "is this bill higher than usual?" with a real distribution over a real
group of documents, and it disclosed its own exclusions honestly.

It was still wrong, in three ways it could not report:

- It grouped raw documents by `(sender, kind, currency)`. One payment documented
  as an invoice *and* a receipt is two documents, so it entered the distribution
  twice and moved the mean and the median.
- It read `amount_total` without `amount_kind`, so a refund raised a total
  instead of lowering it, and non-summable kinds — a policy's cover limit, a
  running balance — sat in the same distribution as money actually spent.
- It could not be scoped to a curated label, only to a sender and a kind.

Rebuilding it on the relation that fixes all three (`spend_facts`, which is
payment-deduplicated and label-scoped by construction) is the obvious move, and
it is **deferred, not abandoned** — it is now the second half of the roadmap's
one queued item. Shipping the rebuild inside a deletion PR would have made the
deletion unreviewable, and a comparative tool is a design problem, not a port.

The uncomfortable half of this decision is that Ask is *less capable* today than
it was yesterday for one class of question. That is recorded where a reader will
find it, in `docs/ask.md` §1.10 item 11, rather than left as a gap someone
discovers by asking.

### 3.3 The tables drop in a second PR

The seven tables — `series_insights`, `series_membership_overrides`,
`series_meta_overrides`, `authored_series`, `authored_series_members`,
`authored_series_suggestions`, `authored_series_exclusions` — are untouched by
this PR. Nothing in `src/` reads or writes them; they are orphaned data.

They stay because **a revert must have somewhere to land**. If the code and the
drop shipped together and the deploy went wrong, reverting the image would
restore code that queries tables that no longer exist — a worse outcome than the
bug being reverted. Splitting the two means the soak window between them is a
real option, not a formality: for as long as the tables are there, rolling back
is just redeploying the previous image.

The cost is that `main` carries dead tables for a few days, and that someone has
to remember to finish. `docs/architecture.md` §1.9 names all seven under an
explicit "orphaned, awaiting the drop migration" heading so the debt is visible
in the document a new reader is pointed at first, not only in a plan file.

### 3.4 `/api/spending` keeps its prefix

`src/library/api/spending.py` carried a promise: it sat at `/api/spending`
"rather than §9.6's `/api/charts`" only because the old stack still owned that
prefix, and it would take it "when that one is deleted". That one is now deleted,
and the promise is withdrawn.

`/charts` is the name the redesign *replaced*. The routes under it answered "what
did this one `(sender, kind, currency)` series do over time" — the question this
engine deliberately does not ask. Moving the new surface onto the old name would
import the discarded model's vocabulary into the thing that replaced it, and
would break every stored link and client for no gain.

The SPA route does stay `/charts`, because that is the URL in the user's
bookmarks and muscle memory. So the API says `spending` and the frontend says
`charts`, and that asymmetry is accepted knowingly rather than overlooked — it
is written down in the module docstring, in `docs/charts.md` §11, and here.

### 3.5 The LLM-surface layer stays, with one row

`series_insight` was one of exactly two switchable LLM surfaces. With it gone,
`BACKEND_SURFACES` has a single key, and a single-key mapping is the classic
candidate for being collapsed into a boolean.

It was not collapsed. The map, the settings view that iterates it and the
per-surface override rows all stay, because the layer's cost is one dict entry
and its value is that adding the *next* switchable surface is one dict entry
too. Collapsing it would trade a line of code today for a re-derivation later.

`docs/llm-backends.md` §2 now says the table is one row long "because only one
surface qualifies today, not because the layer collapsed", and §3.2 keeps the
*rule* the deleted surface taught even though the surface is gone: a
high-frequency, small-prompt call site should not be made switchable at all,
because routing it through the subscription spends quota shared with the
interactive surface to save a fraction of a cent.

## 4. The Ask commit guard

Not a deletion. `docs/charts.md` §10.1 enumerates five writers of
`amount_total`, and recorded that four of them translated migration 0035's
deferred mirror trigger into an answer while the fifth — Ask's document-edit
tool — did not: it called `session.commit()` bare, so on an allocated document
the refusal escaped the whole Ask turn as a 500 with a poisoned session.

It now commits through the same `spend_lines.commit_allocation` helper as the
others and returns the refusal as a tool-result error, so the model can relay it
or clear the lines and retry.

Two reasons this rode along with a deletion PR rather than waiting. First, the
tool was being edited anyway — the same function lost `compare_to_series`'s
sibling machinery — so the guard was a three-line change to code already open.
Second, the limitation was written down in `docs/charts.md` §13 as a known gap,
and this PR was rewriting that section regardless; fixing it was cheaper than
carrying the paragraph forward for another cycle.

It remains latent: it is unreachable until the archive holds its first allocated
document, and there are none. The test that covers it constructs one.

## 5. What the documentation pass had to decide

Two ruling-shaped questions came up while rewriting the docs, and both are worth
recording because the answer is counter-intuitive.

**Section numbers do not close up.** Deleting `api.md` §§1.13–1.15 leaves a gap
before §1.16, and the instinct is to renumber. Twenty-eight citations of §1.16
and later live across nine documents and one Vue component, and **nothing in the
toolchain checks a section-number citation** — `check_docs.py` verifies stamps
and index reachability, not anchors. So renumbering is 29 silent edits with
link-rot as the failure mode, against a gap that costs one paragraph. The gap
stays, with a note at the seam saying so. Same for `ask.md` §1.7.

The corollary is the opposite instruction, and it also had to be carried out:
exactly three citations pointed *at* deleted sections and had to be repointed
(`architecture.md`'s §1.15, `roadmap.md`'s §1.14, and the archived
`smart-groups.md`'s §1.14). Everything else was left alone. In
`jobs-and-notifications.md` the numbers *were* closed up, because a grep proved
nothing cites them — the rule is "don't break citations", not "never renumber".

**An archived doc's index row is deleted, not repointed.** `check_docs.py`
excludes `archive/` from the stamp gate but requires every gated doc to be
reachable from `docs/README.md`. A row pointing into `archive/` therefore adds an
index entry the gate does not cover, and the index stops corresponding to the
gated set in both directions. `archive/` is already reachable as a directory.

## 6. Public-repo note

This repository is public. Nothing in this entry, in the rewritten documents or
in the archived Smart Groups document names a real sender, amount, address,
policy or person; the illustrative examples are shapes, not records.
