# A type checker that was never actually pinned

**Date:** 2026-08-12. **Units:** W15 (batch K of the library-defect-generators run).

The plan called for introducing mypy as a ratchet and quoted a baseline of 84
errors, with a 10-fix / 74-quarantine split across named modules. Almost none of
that survived contact.

## 1. The baseline was a property of the machine

mypy appeared in neither `pyproject.toml` nor `uv.lock`. `uv run mypy` therefore
fell through to whatever ambient install the developer happened to have, so "84
errors" was not a fact about this codebase — it was a fact about one laptop on
one day, and no CI job could have reproduced it.

Pinned to `>=1.19,<1.20` and re-measured: **111 errors in 27 files**. Thirteen of
those are `import-untyped` — third-party packages that ship no type information
(`asyncpg`, `filetype`, `img2pdf`, `mammoth`, `pgvector`, `pillow_heif`,
`pypdfium2`) — which is noise about someone else's packaging, handled by an
`ignore_missing_imports` override rather than by suppressing anything of ours.
That leaves **98 real errors**, concentrated hard: 45 in `ask/engine.py`, 15 in
`series_insight.py`, 10 in `series.py`, 7 in `email_ingest.py`. Seventy-seven of
98 in four files.

The plan's per-module numbers for the two biggest were right (45 and 15). Its
total was not, and it could not have been.

## 2. None of the "latent crashes" were crashes

The more consequential correction. The plan named several inline fixes as
"latent `None` crashes worth fixing regardless", and specified test coverage for
two of them as behavioural changes. Every one of them already had its guard:

- `cli.py:1005` — `settings.anthropic_api_key.get_secret_value()`. The guard is
  eleven lines above at `:984`: `if settings.anthropic_api_key is None: echo;
  Exit(1)`. mypy flags it because the use is inside a nested `async def
  operation(...)`, and narrowing does not survive into a closure.
- `api/settings.py:255` — Pushover credentials passed as `str | None`. The
  `if payload.enabled and not (app_token and user_key): raise 422` guard is
  directly above. mypy cannot connect it to the *second*, separate
  `if payload.enabled:` block that follows.
- `jobs.py:605` — `sender_id`/`kind_id` as `int | None`. Guarded by
  `if document.sender_id is not None and ...`; the use is inside a `lambda`, and
  attribute narrowing does not cross a nested scope.
- `series_insight.py:285`/`:360` — `client` as `AsyncAnthropic | None`. Guarded,
  but through a bool alias: `owned_client = client is None; if owned_client:`.
  A bool derived from a check cannot narrow the variable the check was about.

So **there are no behavioural fixes in this unit** and the plan's test-impact
section is void. Every one of the 21 inline changes is a narrowing or annotation
change, which is why the suite is byte-for-byte the same before and after: 1519
passed, 7 skipped, both times.

The fixes are still worth having — each one makes the guard visible to the
reader as well as the checker, and two of them (binding `document.sender_id` to a
local before the `lambda`, hoisting the "nothing to verify" branch out of the
retry loop in `importer/client.py`) remove a real re-read-at-call-time hazard.
But they are not bug fixes, and calling them bug fixes in the commit would have
been the same category of false claim this run keeps finding in the docs.

## 3. The quarantine started too blunt, and a flag said so

First pass disabled, per module, every error code that module produced —
including `arg-type` across all four. `warn_unused_ignores` immediately reported
three `# type: ignore[arg-type]` comments as unused: two in `ask/engine.py`, one
in `email_ingest.py`.

That is the flag earning its place on day one, and it was the signal that the
quarantine was wrong. Those three ignores were deliberate, narrow, and
documented; a module-wide `arg-type` suppression had swallowed them. Only 4 of
`ask/engine.py`'s 45 errors were `arg-type`, and two of those were already
handled.

Narrowed, and the residue turned out to be fixable:

- `ask/engine.py` — the `reference` local was annotated `Decimal | str` while
  both branches assign either a `Decimal` or the literal `"latest"`. The
  annotation was looser than the code; tightening it to
  `Decimal | Literal["latest"]` fixed the call. The remaining three were the
  SDK-TypedDict boundary, cast once at the call.
- `series_insight.py` — the two `owned_client` narrowings above.

`arg-type` is now quarantined only in `series.py` (where it is
`SeriesSummary.currency: str | None` flowing into helpers typed `str`, and the
real exit is splitting the summary type) and `email_ingest.py`.

`email_ingest.py` is the one place I deliberately left something a checker
called an error. `imap_tools`' `message.uid` is `str | None` and flows into
`mailbox.move`. The uid is non-None for anything the poller actually fetched, so
"fixing" it means adding a guard that changes what the live email poller does on
a path with no test — in a unit whose entire premise is that it changes no
behaviour. The override comment says exactly that, and names the guard-plus-test
as the exit.

## 4. This ratchet only fails in one direction

Stated in the config rather than left for someone to discover. The docs ratchet
fails when the count comes in *below* the baseline, which is what forces a gain
to be locked in. mypy has no baseline mechanism, so a `disable_error_code`
override on a module that has since been cleaned up lingers silently.
`warn_unused_ignores` covers the inline `# type: ignore` case; there is no
equivalent for a module override. Making it two-directional needs a per-module
count check, which is its own unit and is named as such.

What the quarantine does *not* do is grant an exemption: suppression is by error
code, never by excluding a file. Demonstrated rather than asserted — adding
`x: int = "s"` to `ask/engine.py`, the most heavily quarantined module, still
reds, because `assignment` is not in its disable list.

## 5. Two bugs found by the tools, not by me

**The Makefile still held the old ratchet.** W27 lowered `--max-violations` from
15 to 0 in `ci.yml` and missed the copy in `make check-docs`, so that target had
been failing since the sweep merged — on a *clean* tree, with the message
"0 violations is BELOW the baseline of 15". The ratchet's below-baseline
direction is precisely what surfaced it. `check-docs` now runs at 0 and has
joined `make lint`, along with `mypy`: the reason it was held out ("it reds
today's tree by design") stopped being true when the sweep landed.

**My own rewrite introduced a shadowing bug.** Replacing the
`not (tag.slug in seen or seen.add(tag.slug))` dedupe idiom in
`importer/mapper.py` with an explicit loop reused the name `tag`, which is
already bound earlier in that function to a paperless tag `dict`. The
comprehension had only avoided the clash by having its own scope. mypy reported
it as six errors on the next run. A checker introduced in the same commit
catching a defect introduced in the same commit is about as direct a
demonstration of its value as this unit is going to produce.
