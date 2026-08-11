# Docs corrections and the archive restore

**Date:** 2026-08-11. **Units:** W13, W14 (batch H of the library-defect-generators run).

Two units, one PR, because both extend `scripts/check_docs.py` with a rule and
touching that file twice in sequence would have meant two rounds of the same
review.

## 1. W13 — correcting claims that had drifted from the code

Six corrections, each verified against `src/` before editing rather than trusted
from the plan:

- `docs/api.md` said `ask_model` was `claude-sonnet-4-6`. `config.py:112` has had
  `claude-opus-4-8` for weeks — a **67% understatement** of the cost of the
  feature that page documents.
- `docs/roadmap.md` implied Haiku for matter classification. `config.py:218` is
  `claude-sonnet-4-6`, and the comment above it says why: the judgement
  ("car-related but not car *insurance*") rewards nuance and the call is
  infrequent. The doc now says that, in those terms.
- `docs/roadmap.md` called the duplicate-sender fix "not yet shipped". It shipped
  as `a6c0457` (#40).
- `docs/smart-groups.md` §4.1 still documented the `_name_anchor_ids`
  seed-widening step, gone from `src/` and *forbidden* by
  `test_create_authored_does_not_use_name_search`. Deleted and renumbered.
- §6 therefore drops from "exactly two jobs" to **one**, and `roadmap.md`'s
  matching "only name→seed-query and a best-effort description blurb" sentence
  had to change with it — a coupling the plan did not list.
- §5 was stale for the same commit and was **not in the evaluation**:
  `_resolve_display_currency` now falls back to the members' dominant currency
  when `AuthoredSeries.currency` is NULL.

Two targets in the plan were **not** changed, deliberately.
`docs/architecture.md`'s Haiku mentions (`:80` Extract, `:107` Markdown) are
*correct* — those stages really do run Haiku — and `roadmap.md`'s `.docx` line
was already fixed in W19. Both were verified before being left alone.

Where the doc now records a rationale, it is the code's own rationale, not a
plausible-sounding substitute. The first draft of the roadmap edit said the
classifier avoids Haiku "because misfiling is hard for a user to notice"; the
comment in `config.py` says something different, and the comment wins.

## 2. W14 — the archive restore

`.engineering-team/runs/manual-20260610-154616/` was tracked (added in `2359327`,
untracked in `9d48ddc`), so `architecture.md` had been pointing readers at a path
that no longer existed, and every `Wn` citation in `docs/` was unresolvable.

Both files are restored under `docs/archive/` with a `**Status:** historical`
header saying plainly that they describe the system as *planned*, not as built,
and that the current docs win on disagreement.

**The repo is public, so both were read end to end before committing** — the
owner approved publishing, but the read-first condition was kept. Findings: no
credentials, no document content, no corpus-derived data. The only personal
material is the owner's name, the already-public `ghcr.io/johnmathews/library`,
and passing references to their Proxmox host and their paperless-ngx instance.

## 3. Two new checker rules

`check_work_unit_citations` — every `W\d{1,2}` in a gated doc must appear in the
restored plan's `units:` frontmatter. `\b` on both sides, so `W3C` is not read as
work unit 3.

`check_model_identity` — a `claude-*` id quoted next to its settings field must
be that field's current default, parsed **textually** from `config.py` (this
script is pure stdlib and must not drag pydantic into the docs gate).

Both are pure functions taking their inputs as arguments, for the reason
`interpret_shallow` and `ratchet_verdict` already are: the backend CI job checks
out at `fetch-depth: 1`, so anything asserting on ambient git state passes
locally and fails in CI.

### 3.1 What the citation rule deliberately cannot do

Three citations were wrong in a way **membership cannot detect**: `(W11)` in
`api.md`, `(W11)` and `(W9)` in `ask.md` cited *another run's* numbering. All
three tokens exist in the restored plan, so a token-membership test passes them.
They were found by reading and deleted by hand. The rule's docstring says this
outright, so nobody later mistakes the gate for proof that every citation is
*right* rather than merely *resolvable*.

The model rule is narrow for a related reason: it fires only when field and model
are joined by `=`, `:`, `default`, or immediate parentheses. Prose like
"escalates from `extraction_model` to `claude-sonnet-4-6`" describes a
relationship and asserts no default. A rule that cried wolf on that phrasing
would be switched off, and then it would catch nothing at all. There is a test
pinning that it stays quiet.

## 4. Verification

Both guards were **mutation-tested**, per this run's convention:

- reinstating `claude-sonnet-4-6` in `api.md` reds
  `test_every_model_claim_in_the_repo_is_current`;
- seeding `(W99)` reds `test_every_citation_in_the_repo_resolves`.

Full backend suite: **1481 passed, 7 skipped** (all 7 the golden-corpus tier,
absent locally because it is fetched, not committed). `ruff check` and
`ruff format --check` clean over the whole repo; `actionlint` clean.

The docs gate still reports **exactly 15** violations, matching the CI ratchet
baseline. That is not luck: the docs edited here are all `missing-verified`, and
that rule returns before `stale-doc-edit` can fire, while `smart-groups.md` is
`shipped` rather than `active` and is skipped entirely. `docs/archive/` is
excluded from the gated set by path, so the two restored files add nothing.
W27 is the sweep that takes this to zero.
