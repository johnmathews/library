# Clearing the quarantine, and what it had been hiding

**Date:** 2026-08-13. Follow-up to
[260813-mypy-ratchet-two-directional.md](260813-mypy-ratchet-two-directional.md),
which closed `ask.engine` and made the ratchet fail in both directions. This
clears the remaining 29 errors. The quarantine is now empty; the check that
guards it stays.

## 1. The comments described a different problem than the errors did

Before writing any code I listed the 29 with the quarantine lifted. Almost none
matched what the `pyproject.toml` comments said they were.

| Module | Comment said | Errors actually were |
| --- | --- | --- |
| `series_insight` | `SeriesSummary` fields `\| None` at the dataclass; exit is a type split | **all 11 on one line** — a second instance of the `getattr(block, "type", None)` union-narrowing bug from `ask.engine` |
| `series` | "mostly `SeriesSummary.currency: str \| None` flowing into helpers typed `str`" | 6 from `date \| None` losing its narrowing, 2 from a currency bug, 1 assignment, 1 SQLAlchemy `Row` |
| `email_ingest` | "untyped `email.message.Message` payload indexing" | 5 from a `tuple \| None` re-indexed under a boolean, 1 `dict[str, object]` read, 2 uid |

The planned exit for the first two — "split the summary type so the chartable
case is its own non-optional shape" — was work that did not need doing. Nothing
in either module needed `SeriesSummary` split. **The comments were written from
the plan, not from the errors**, and no one had re-read the errors since.

## 2. The same one-line bug, twice

`series_insight.py:173`:

```python
block.text for block in response.content if getattr(block, "type", None) == "text"
```

Identical in shape and cause to the `ask.engine` loop: `getattr` defeats mypy's
narrowing of the SDK's content union, so `.text` is an access on all twelve
block types. `block.type` fixed all 11.

Across both PRs that is **51 of the original 98 errors from one idiom**, sitting
behind two overrides that each described it as a refactor. Worth naming as a
pattern: the cost of `getattr` on a union is not one error, it is one error per
member per access.

## 3. `series`: four unrelated causes, one of them a real bug

**Six errors: a filter that narrows at runtime and nowhere else.**
`_summarize_members` built `dated` by filtering `document_date is not None`
inside a generator, then used `.document_date` in five downstream expressions.
The narrowing does not survive the comprehension, so every use was trusted
rather than checked. Fixed by lifting the date into a `_Dated(member, date)`
record at the point the filter happens, which is the only place the fact is
actually known.

**Two errors: a latent bug, not a type gap.** `_load_pinned_members` and
`_load_authored_members` pass `target_currency: str | None` into
`convert_amount(to_currency: str)`. A series bucket *can* be keyed on `None` —
the documents carrying no currency at all — and that `None` went straight into
the FX lookup, which found no rate and dropped the member with a misleading
`no FX rate EUR->None` warning. The outcome was right by accident, reached
through a failed lookup rather than a rule. `_convert_into` states it: nothing
converts *into* the null bucket, so only a currency-less member belongs there.
Behaviour is unchanged in both directions; it is now unchanged *on purpose*.

**One assignment**, where `currency` inferred `str` from the
`reference_currency` branch and hid that the dominant bucket may be the NULL
one. **One `dict(rows)`**, where a SQLAlchemy `Row` is a sequence at runtime but
not a typed 2-tuple.

## 4. `email_ingest`: a boolean that carried a fact the types could not

Five errors were one shape, in two places:

```python
verdict = verdicts.get(index)                                   # tuple | None
flagged = verdict is not None and verdict[0] == "probably_noise"
...
if flagged:
    ... verdict[1] ...        # unchecked index, four lines or four hundred later
```

`flagged` carries "the verdict exists" at runtime and not in the types.
`_noise_verdict` returns `(flagged, reason)` so both facts travel together.
Applying it surfaced a fifth call site I had missed — mypy's `name-defined`
caught it immediately, which is the un-quarantined run doing exactly its job
mid-refactor.

## 5. The uid guard, and why it needed the test first

The last two errors were the ones the original comment deliberately deferred:

> the uid is non-None for anything the poller fetched, and adding a guard would
> change what the live email poller does on a path this unit has no test for.

That reasoning was right, and the exit it named — "one uid guard **with a
test**" — is what this does. The test came first, and it failed in the way that
matters:

```
assert mailbox.moved == []
E   AssertionError: assert [(None, 'Library/Held')] == []
```

That is `mailbox.move(None, "Library/Held")` reaching imap_tools. Not a
hypothetical: the type error was describing a real call that would build a
malformed IMAP command.

`_move_message` returns `False` rather than raising, and the reason is a
judgement worth recording. Every caller has already written the durable record
— the held row, the ingested documents — before it files anything. Leaving one
message unfiled in the inbox is recoverable; aborting a poll batch part-way
through is not. At the held-email site, where the surrounding code already
reports a failed move, a `False` return now records the same kind of error
instead of silently reporting success.

Three call sites, one rule. The third (`1509`) was not erroring — it had no
guard either, it just happened not to trip mypy — and now goes through the same
helper.

## 6. What is left

Nothing. 0 errors with the quarantine lifted, and no `disable_error_code`
overrides remain.

`mypy-baseline.json` is `{}` rather than deleted, and `scripts/check_mypy.py`
still runs in CI. That is deliberate: re-quarantining a module is a legitimate
thing to do, and the check makes it impossible to do it *unmeasured* — a new
override without a count fails, and once there is a count it can neither rise
nor quietly fall. The mechanism costs one CI step and removes the failure mode
that produced this entry's opening table.

**The honest summary of the whole two-PR arc:** a 98-error quarantine, whose
per-module comments had drifted from reality in three different directions,
turned out to be roughly one afternoon of work — over half of it two instances
of a single idiom. It stayed for weeks because nothing measured it and the
comments described harder problems than the errors actually were. The lesson is
not "quarantines are bad"; it is that an unmeasured one stops being a record of
work owed and becomes a record of what someone once assumed.
