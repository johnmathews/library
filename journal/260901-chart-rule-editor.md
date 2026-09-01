# Editing a saved chart's rule

**Date:** 2026-09-01
**Branch:** `worktree-eng-chart-rule-editor`

> **Note on examples.** This repository is public. Every facet, value, chart
> name and amount below is invented.

## 1. What shipped

Issue #135: once a chart was saved, there was no way to change *what it asks*.
The card menu offered rename and delete; the workspace offered nothing. The only
recovery from a wrong rule was to delete the chart and re-ask — which changes
its id, loses its board position, and collides with its own unique name on the
way through.

Twenty work units across three surfaces:

- **Backend** — `POST /api/spending/preview`, `extra="forbid"` on `Clause` and
  `Rule`, a refusal for two `in` clauses on one facet, and eleven tests
  characterising the `PATCH` rule branch that had none.
- **Frontend** — `ChartRuleEditor.vue`, a shared `spending/ruleText.ts`, a
  shared facet-vocabulary cache, `postPreview`, and the workspace wiring.
- **Docs** — `charts.md`, `spending-view.md`, `frontend.md`, the index, the
  roadmap, and the two `Covers:` lines that let the docs gate see `frontend/`
  at all.

Backend 2073 tests, frontend 1409, `check_docs` clean.

## 2. The preview route already existed, one layer down

The issue framed this as a UI gap, and the apply half genuinely was: `PATCH`
has accepted a `rule` since the route was written, `updateChart` has been typed
to send one, and nothing derived from a chart is cached — so there was no
invalidation to design.

The preview half was not. Nothing in the system answered an *unsaved* rule:
`/data` needs a chart row, and `/spending/draft` takes English text and spends a
model call. A frontend-only plan would have discovered that mid-implementation
and settled for apply-then-preview, which writes to the archive to answer a
question and writes again to undo it.

What made the fix small is that the capability was already factored out one
level below the only route that exposed it. `_answer(session, chart_id=None,
query)` takes a nullable chart id *precisely* because a preview has no chart
behind it, and `/spending/draft` already called it that way. `_ChartQuery` is a
seven-field frozen dataclass with no chart id in it. So the new route is a
schema and seven lines of call sequence.

**The measurement that settles it:** `/spending/draft` calls
`anthropic.messages.parse()` on every request, embedding the entire vocabulary
in the prompt. `/spending/preview` calls no model at all. Reusing draft to
preview a rule the owner had already typed would have paid for a model to
re-derive it.

## 3. Two producers of a rule, two enforcement mechanisms

After this change a `Rule` has two sources, and they refuse bad input
differently on purpose:

| producer | mechanism | on an unusable term |
| --- | --- | --- |
| the draft flow | `filter_drafted_rule` | drops it, reports `unknown_terms` |
| the rule editor | `_validate_rule` | 422 naming it; nothing saved |

The model is guessing, so its overreach is narrowed silently and reported. The
owner is asserting, so their rule is refused with the offending key named —
silently dropping a clause someone chose would edit their question rather than
report a problem.

`charts.md` §9 previously read as though LLM drafting were *the* path from words
to a rule. An implementer reading it and looking for `filter_drafted_rule` on
the editor's path would not find it, and might hand the owner a raw 422 having
concluded the drop-and-report behaviour applied. §9 is now "Where a rule comes
from" and opens with both.

**The line this cost.** `/spending/draft` legitimately skips `_validate_rule`,
because `filter_drafted_rule` has already guaranteed vocabulary membership by
construction. A preview handler copy-pasted from it inherits that omission — and
then answers a rule naming a facet that does not exist with **200 and an empty
chart** instead of a 422. That is §12's "indistinguishable from you spent
nothing", reached through the one route built to prevent it. Removing the line
was observed turning two tests from green to `assert 200 == 422`, and the test
docstring records the mutation.

## 4. `question_text` is not rewritten, and that is a decision

A chart's heading is the plain-language question its rule was drafted from.
Editing the clauses leaves it alone, so a chart can end up headed by a question
its rule no longer answers.

Three options were on the table: clear it, mark it "edited since drafted", or
leave it. What settled it is that the backend cannot detect the mismatch at all
— the `rule` and `question_text` branches of the `PATCH` handler are
independent, and nothing anywhere compares a rule to a sentence. Any automatic
behaviour would therefore be a guess, and a heading the system rewrote would be
asserting something it cannot check. Only the owner knows whether a reworded
rule still answers the same question, so the remedy is to rename the chart.

Pinned by `test_patching_a_rule_leaves_question_text_untouched`, so changing the
policy means deleting that test deliberately rather than drifting into it.

## 5. The empty rule stays legal

Removing the last clause row leaves `{"all": []}`, which matches every row in
the archive. The tempting fix is a 422.

It would be wrong, because `{"all": []}` is a **legitimate saved state** — it is
exactly what the seeded "All spending" card stores (`SpendingEmptyState.vue`
builds it). A backend that refused it would refuse the chart the empty state
creates, and a preview that refused it would be useless at the one moment it is
most needed: just after the owner removes their last filter and is about to
widen the chart to everything.

So the API accepts it on `POST`, `PATCH` and `/preview`, and the guard is a
confirmation in the editor. `test_patching_to_an_empty_rule_widens_the_chart_to_everything`
pins the acceptance so a later blanket refusal has to delete it on purpose.

## 6. The split axis had the same defect, worse

Found while checking whether the editor should carry `default_split` alongside
`rule`: the workspace's split control is `v-if="chart.default_split"` and offers
exactly two options, `''` and that one facet. So a chart created with
`default_split: null` could **never gain a split axis at all** — not by
toggling, not by delete-and-recreate unless you happened to know the axis is
fixed at creation.

The rule at least had a documented workaround. This had none, and it was
documented as a feature ("the split axis is a toggle, not a picker"). The API
had always supported the change; only the UI was missing, which
`test_patching_default_split_onto_a_chart_created_without_one` confirmed by
passing on arrival.

It is fixed for free by the wiring: the toolbar block reads `chart` reactively,
so replacing `chart.value` after a save makes the control *appear* with the
right facet in it, with no toolbar change at all.

## 7. The values picker, and the control that was rejected

The obvious component for "pick several values" is `AppMultiSelect` — a chip
input with typeahead. It is the wrong one. Its `createCandidate` is computed
from the prop list alone and rendered unconditionally, so it always offers
`Create "<typed>"`, and there is no prop that suppresses it. The facet
vocabulary is closed: the API rejects a value that does not exist rather than
creating it. That component would ship a UI whose most prominent affordance is
a guaranteed 422.

The right substrate was already in the app: `AppCheckboxes` (a `string[]` model)
inside `FilterPill`, which is how the document filter bar does tags, projects and
matters. It brings Escape-closes, focus-return and outside-click for free, and
its panel is `absolute` and `max-w-[calc(100vw-1rem)]` — which is what makes §8
tractable. Recorded as a rejected option in `frontend.md` so it is not
re-proposed.

## 8. 343px

The workspace's content column measures **343px** at a 375px viewport, 608px at
656, and 960/1136px at 1280 — numbers `e2e/spending-layout.spec.ts` records in
its own header because this app measures geometry rather than reading it off
class lists.

A clause row is a facet select, an is/is-not select, a values pill and a Remove
button. That does not fit in 343px, so the row stacks below `@lg/workspace`
(32rem) and becomes a row above it. Two details carry it: the values list lives
in the popover rather than in flow, so a forty-value vocabulary contributes
nothing to the row's width; and the flexible cells carry `min-w-0`, because a
flex child defaults to `min-width: auto`, which is the usual cause of the
horizontal overflow the layout spec asserts against.

These are **container** queries, not `lg:`. The column is viewport-minus-sidebar,
so a viewport query measures the wrong box — the rule the workspace's own
docblock states and `frontend-view-principles.md` §5.1 owns.

## 9. The docs gate could not see this change

The evaluation's most useful finding was not about the feature. `check_docs.py`
fires `stale-covered-code` when a document's `Covers:` pathspec has commits
newer than its `Last verified` date — and **every `Covers:` line in the
repository named `src/library/`, `migrations/` or `scripts/`.** Not one named
`frontend/`.

So the two documents this change falsifies — `spending-view.md`, whose §8 said
in plain words that a saved chart's rule cannot be edited, and `frontend.md`,
which enumerates the workspace toolbar — were exactly the two the gate could
never flag. A PR shipping the editor with those untouched would have gone green.

Planning falsified the obvious reading of that. The gate needed **no change**:
its `Covers:` parser is a path-agnostic comma-split and `git_last_commit_date`
is a plain `git log -1 -- <path>`. The capability was always there; nobody had
used it. The fix was two lines of markdown plus three tests — one for the rule,
one proving a nested `frontend/` pathspec resolves against a real repository
(the assertion that would actually have caught this), and one asserting some
gated doc still names a `frontend/` path, so deleting a line to quiet CI reds a
test instead of silently reopening the hole.

`frontend.md`'s pathspec is deliberately narrow — `views/`, `router/index.ts`,
`components/layout/` — not `frontend/src/**`. A pathspec that fires on every
frontend PR trains people to re-stamp mechanically, which is the failure the
gate's own source argues against. It buys the enumerations, not the whole
1,092-line document, and its stamp says so.

**Then the gate was watched failing**, because a gate only ever seen green has
not been seen:

```console
$ # spending-view.md's Last verified backdated by one day
$ uv run python scripts/check_docs.py docs/spending-view.md; echo "exit=$?"
docs/spending-view.md: [stale-covered-code] code it declares it covers changed since
  2026-08-31 (frontend/src/components/spending/,
  frontend/src/views/SpendingWorkspaceView.vue, src/library/api/spending.py)
  — re-check the prose against it
docs/spending-view.md: [stale-doc-edit] edited 2026-09-01 but last verified 2026-08-31
2 violation(s) across 1 document(s): stale-covered-code=1, stale-doc-edit=1
exit=1

$ git checkout docs/spending-view.md
$ uv run python scripts/check_docs.py docs/spending-view.md; echo "exit=$?"
ok: 1 document(s) carry a current, verified stamp
exit=0
```

Two of the three paths it names are `frontend/`. That is the half that had never
fired in this repository's history.

## 10. Tests that could not fail

Two of them, both found before they mattered.

**`updateChart` was missing from `SpendingWorkspaceView.spec.ts`'s mock
factory.** That factory spreads `importOriginal()`, so a write function omitted
from it is not `undefined` — it is the *real* one, issuing an `apiFetch` under
jsdom. A test asserting the editor had saved would have passed or failed for
reasons unrelated to the editor.

**The `rule` branch of `PATCH` had zero coverage.** Sixty-seven tests in the
spending API module and not one sent a rule in a patch body, on a branch that
was live, reachable and already typed on the client. Those eleven tests were
written first, against behaviour that already worked — so they passed on
arrival, which is exactly when a test proves nothing. Dropping
`chart.rule = body.rule.model_dump()` was observed reddening two of them
(`assert Decimal('10.00') > Decimal('10.00')`), which is what establishes they
were not vacuous.

The same discipline caught the three that mattered most in the wiring: adding
`initControlsFromChart` to the save handler reddens "keeps the range the owner
was looking at"; removing the explicit `loadData()` reddens both refetch tests
with `expected 1 to be 2`; and filtering the editor's value list against the
live vocabulary reddens the unresolvable-value test with the message *"the lost
value must still be offered, not filtered away"*.

## 11. What is not verified

Two e2e steps were added — the arm/add/cancel journey in `spending-board.spec.ts`
and an overflow assertion with the editor and its values popover open in
`spending-layout.spec.ts`. **Neither was executed locally**: Playwright needs the
compose stack, and `e2e` is gated on the `pull_request` event, so a branch push
runs it either way. They are unproven until CI.

The 512px breakpoint in §8 is the specific claim resting on that. Nothing in
jsdom can measure it; the layout spec is the only check, and it has not run yet.

Also worth recording: this machine spent an hour asleep or starved partway
through, and the frontend suite failed three times in a row in a way that looked
real — one failure each run, in a *different* spec the branch never touched
(`DocumentDetailView`, then `SavedViewsView`), with 908s and 149s timeouts and
repeated `Failed to start forks worker`. Running with `--maxWorkers=2` gives
1409/1409 in 169s. A phantom failure in a spec you did not touch is worth one
isolated re-run before believing it.
