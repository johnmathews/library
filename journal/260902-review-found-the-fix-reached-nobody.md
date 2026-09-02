# The review found the fix reached nobody

**Date:** 2026-09-02
**Branch:** `et/ux-followup-20260902`
**Follows:** [#156](https://github.com/johnmathews/library/pull/156)

## 1. What happened

#156 shipped, merged and deployed. A security review and a correctness review
were then run against the merged diff — **out of order**: they belong before the
merge, and running them after meant they could not gate it. That is the process
defect this entry exists to record, and it is why the finding below shipped.

The correctness review found that the user-facing half of one fix never reached
the user.

## 2. The defect

#156 split the draft path's drop reporting into two causes, because a clause
dropped for being uncombinable names **only vocabulary that exists** — reporting
it under "not in the vocabulary" would give a false reason for a real drop. The
route composes `message` from the two causes separately. There is an API test
asserting the wording.

`QuestionDraft.vue` renders `message` in its **collapsed** branch only.

And an unmatchable-only draft can never be collapsed. The proof is one line: for
`unmatchable_terms` to be non-empty, some facet must already be in
`claimed_by_in`; that set is written only immediately before a clause is
appended; so a non-empty `unmatchable_terms` implies at least one surviving
clause, hence a non-empty rule, hence not collapsed.

So the sentence was composed, tested, serialised — and rendered nowhere. What
the owner actually saw was:

> This is an approximation of your question.
> `category in [services]`

a raw rule fragment in an unlabelled grey list whose every other member has, for
the life of this feature, meant "not in your vocabulary". The fix replaced a
wrong explanation with **no** explanation.

## 3. This is the same mistake #156 criticised

#156's own commit message says of issue #124:

> The issue says the event detail "carries `fields_set`" and that one line in
> `apply.py` fixes it. In fact `fields_set` appears nowhere in the frontend.

That is exactly what happened here, one file over. Worse: the session *read*
`QuestionDraft.vue`'s template during the work and noted that the approximation
branch renders `question-draft-approximate` and the terms list but not
`message` — and then concluded "no frontend change was needed."

The fact was observed and the wrong inference drawn from it. Knowing the failure
mode by name, having just written it up, and having the evidence on screen were
all insufficient. What caught it was an adversarial read by something that had
not written the code.

The general lesson is not "check the frontend". It is that **a claim about
where a value ends up is a claim about a code path, and reading the producer
does not establish it.** The producer was read. The consumer was read. The
question "does this consumer run in this state?" was not asked.

## 4. Fixed here

- **`message` renders in the approximation branch too**, with a test confirmed
  to red against exactly the shipped state.
- **`unmatchable_terms` is de-duplicated**, like `unknown_terms` already was.
  Without it a repeated clause produced a repeated sentence and a duplicate
  Vue `:key` on a list keyed by the term.
- **The cap applies to the union.** `MAX_UNKNOWN_TERMS` bounds what the response
  carries; slicing each list separately let the field hold 40 where the constant
  says 20, and the constant is what the client's own docstring promises.
- **`?split_value=` (the empty string) is refused on every axis.** The sender
  branch caught it incidentally via `int("")`; the facet branch did not, so it
  reached the SQL and produced the `0.00` silence.
- **`FIELD_LABELS` is a `Map`.** As an object literal it inherited
  `Object.prototype`, so `FIELD_LABELS['constructor']` returned a function and
  `['__proto__']` the prototype — neither nullish, so `?? field` never fired and
  the timeline would have rendered `function Object() { [native code] }`. Not
  reachable from today's backend (a fixed 3-tuple), but the newly-exported
  `fieldLabel()` is what widened the input to arbitrary API JSON, and
  `Record<string, string>` hid it from `vue-tsc`.

  `Object.hasOwn` was the first fix and **`vue-tsc` rejected it**: under
  `noUncheckedIndexedAccess` the index access is still `string | undefined`
  after the guard, so it types no better than what it replaced. The `Map` fixes
  the hole and the type in one move, and closes the same hole at the other call
  site (`resolveReviewReason`) for free. Pinned by a test that reds when the
  literal is restored.

## 5. Recorded, not fixed (issue 158)

- **`charts.md` §11's `"007"` claim was corrected rather than the behaviour.**
  It said non-canonical sender ids "still resolve". They resolve the sender's
  *name* — `_resolve_splits` keys on the parsed id — while `chart_cell` binds
  the **raw** string, and `"7" != "007"`, so the panel is correctly labelled and
  **empty**. The guard admits a narrow class of the silence it exists to remove.
  The tell was in the pinning test all along: it asserts `label` and never
  `payments` or `total`. Tightening to canonical form means deliberately
  rewriting that test, which is a decision, not a tidy-up.
- **`/footer`'s `amount_kind` is unvalidated** against `AmountKind`'s closed set,
  so `?bucket=excluded&amount_kind=bogus` returns an empty bucket three lines
  below a check that 422s and names the problem. Same class, sibling route,
  pre-existing.

## 6. What the reviews cleared

The security review found no Critical or High. The one I most expected —
`int(split_value)` on a 100k-digit string — is safe: CPython's 4300-digit guard
raises `ValueError`, which the existing `except ValueError` catches, giving a
clean 422 in ~55µs, verified by execution rather than argument. The new 422s
reflect only the caller's own input, in a JSON context, behind auth. The widened
extraction lock is purely a write-suppressor.
