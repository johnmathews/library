# The scenario that passed either way

**Date:** 2026-09-02
**Branch:** `et/disclosure-scorer-caveat`
**Follows:** [#156](https://github.com/johnmathews/library/pull/156), [#159](https://github.com/johnmathews/library/pull/159)

## 1. What was being settled

#156 shipped a cross-call disclosure rule in Ask's system prompt and a scenario,
`comparative-uneven-coverage`, to measure it — but shipped them **unmeasured**,
because the eval needs Claude subscription credentials and the machine had none
wired up. The finding behind the rule was graded [SUSPECTED] and said so
everywhere.

This entry is that measurement.

## 2. Getting it to run

`docker compose run api library eval-disclosure` fails on macOS:

```
SubscriptionBackendError: no credentials at /app/.claude/.credentials.json
```

Not a misconfiguration. **Claude Code stores its credentials in the macOS
Keychain**, not as a file, so the compose mount of `~/.claude` finds nothing to
mount. The deployed host is Linux, where the file exists — which is why
`/healthz` there reports `claude_credentials: healthy`.

The documented invocation already runs the CLI on the *host*, where the SDK can
reach the Keychain. All that was missing was a database with a published port: a
throwaway `pgvector/pgvector:pg17` container migrated to `0039`. Never the
archive — and the command is doubly safe there anyway, but a scratch database
means the fixtures never compete with real documents for retrieval.

## 3. Three runs

| run | verdict |
| --- | --- |
| the scenario, **with** the cross-call rule | PASS |
| the scenario, **with the rule reverted** | **PASS** |
| `complete-no-gaps` control, with the rule | PASS |

The second row is the whole result. **The scenario passes either way**, so a
green run is not evidence that the rule does anything.

That was already written into the scenario's own comment as a limitation: *"a
model that mentions the three bills but still calls the fall a trend would
pass."* What the run added is that the limitation is not partial. The scorer
requires the excluded count to appear; the per-result rule already secures that;
there is nothing left for the cross-call rule to be measured by.

Both verdicts read `PASS`, so none of this was visible from the output. It took
adding `--show-answer` and reading the two answers side by side — which is why
that flag is the one thing from this investigation that ships.

## 4. The finding is not the one that was expected

With the rule **reverted**, the model still answered:

> though the 2025 figure is incomplete … that EUR 960 almost certainly
> **understates** your actual 2025 spending — the two years may well be closer,
> or 2025 could even be higher. I'd recommend checking those 3 unpriced 2025
> documents before drawing a firm conclusion.

So the behaviour the cross-call rule asks for is behaviour the model already
produced without it.

The two halves of the original finding now separate cleanly:

- **The prompt gap is real** and stays [VERIFIED] — read the template end to
  end; there is no obligation scoped to more than one result.
- **The predicted consequence was not observed.** Three live answers, none of
  which presented the artefact as a real trend.

The rule is **kept**, and its justification is downgraded in place rather than
quietly retained: it makes an implicit behaviour explicit and cheap to re-check,
and the control confirms it causes no spurious hedging. It is not a fix for an
observed defect, and `docs/ask.md` now says so.

## 5. A check built, and abandoned on its own evidence

The obvious repair is a scorer that requires the *comparison* to be qualified
rather than merely the count to be mentioned. That was built — an
`expect_comparison_caveat` flag, a pattern list drawn from the two real answers
above, tests, a mutation confirming it reds on an unqualified answer.

Its first live run **failed a correct answer**:

> So although 960 looks lower than 1,200, **the drop is likely not real**: 2025
> is missing the amounts on 3 of its 6 bills, while 2024 excluded none. The true
> 2025 total is almost certainly higher than 960 …

Three answers, three vocabularies — *"isn't reliable"*, *"understates"*, *"the
drop is likely not real"* — and a list built from two of them false-failed the
third immediately.

The distinction that matters: **a count is a literal token and screens well; "did
it qualify the comparison" is a semantic property and does not.** `mentions_count`
works because it looks for `3`. There is no token for hedging. This module's own
docstring already says it is *"a screen, not a judge"*, and this is where that
boundary actually falls.

So it was reverted rather than widened. A gate that reds on correct behaviour is
worse than no gate: it trains its readers to ignore it, and the next widening
raises the false-*pass* risk on the unqualified answer it exists to catch.

## 6. What ships

Only `--show-answer`, plus the documentation of everything above. That is a
small diff for the work, and it is the honest size: the investigation's product
is a corrected belief, not a feature.

`docs/ask.md`'s stamp previously read *"the new scenario has never been run, so
nothing claims the rule works"*. It has now been run, and the sharper statement
replaces it — including that a green run there never could have supported the
rule.
