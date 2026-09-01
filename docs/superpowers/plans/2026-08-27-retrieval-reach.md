# Retrieval Reach (Plan B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Ask the ability to scope, deepen and measure content retrieval — metadata filters and a tunable `top_k` on `semantic_search`, chunks that embed their document's identity, and a recall eval that makes all three measurable instead of asserted.

**Architecture:** Four findings, built in dependency order. The recall eval lands first so every later change has a baseline to move; the tool-schema changes (#5, #7) follow because they are cheap and touch no stored data; the Ask-loop eval layer follows them because it exists to observe them; and the contextual chunk header (#6) lands last because it is the only change that invalidates every stored vector. Retrieval-level scoring is deterministic and needs no Claude credentials; Ask-level scoring needs them and therefore stays a CLI command, exactly as `eval-disclosure` does.

**Tech Stack:** Python 3.13, `uv`, pytest, SQLAlchemy 2 async + asyncpg, PostgreSQL + pgvector (HNSW), Alembic, Typer, Procrastinate, bge-m3 via a local text-embeddings-inference sidecar.

**Spec:** `docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md` — §2.2 states findings #5/#6/#7/#15; **§8 is the design this plan implements and every task below argues from it.** Read §8 before starting any task.

## Global Constraints

- **This repository is PUBLIC.** No real archive content anywhere — not in code, fixtures, docs, commit messages or PR bodies. Every sender name, amount, date and document body in this plan is invented. Illustrate the *shape* of a real document; never reproduce one. GitGuardian does not catch this.
- **Every synthetic sender name carries a `(recall-eval fixture)` suffix**, matching `disclosure_scenarios.py`'s `(disclosure-eval fixture)` convention, so it cannot collide with a real archive's senders and a human skimming query logs can tell at a glance the rows are synthetic.
- **Python 3.13**, full type annotations on every function signature. `uv run` for everything; never bare `pytest`/`python`.
- **CI runs `ruff check` and `ruff format --check` over the WHOLE repo, `migrations/` included.** Run `uv run ruff format .` before every commit; a newly created migration file is the classic miss.
- **`uv run mypy` must pass.**
- **`uv run python scripts/check_docs.py` must pass** — it is part of `make lint`. A clean run is *not* evidence docs are current: a doc with no `**Covers:**` line is invisible to the stale-covered-code rule. `docs/ask.md` declares `**Covers:** src/library/ask/`, so it IS covered and will go stale if this plan's `src/library/ask/` changes land without editing it.
- **To test whether a gate failure predates your work, compare against the BASE BRANCH, never `git stash`** — stash removes only uncommitted edits and cannot reveal a violation introduced by an earlier commit on this branch. Use `git worktree add /tmp/base main && (cd /tmp/base && uv run python scripts/check_docs.py)`.
- **Run the FULL backend suite before merge**, not just the focused files: `uv run coverage run -m pytest && uv run coverage report`.
- **`GET /api/documents` 422s on `?limit>100`**; any new list-shaped code must keep `limit ≤ 100`.
- **This brief is fallible.** It was written from probes executed against the real test Postgres, but shipped code may have moved. Verify every signature and line reference against the code in front of you, and say so if this document is wrong rather than working around it.

## Probe results this plan depends on

All produced by executing throwaway tests against the real test database (spec §8.7). **Do not re-derive these by reading; if you doubt one, re-run it.**

| Fact | Value |
|---|---|
| `ranked[:top_k]` with negative `top_k` | `-3`→4 hits, `-1`→6 hits, `0`→0, `1000`→all (7). **Silent, no error.** |
| `chunks_per_doc` with `≤0` | degrades to 1 passage (the `if chunks_per_doc > 1` guard absorbs it) |
| `func.count().filter(~exists(...))` in one round trip | works; returned `matched=5 unembedded=2` as expected |
| `SemanticHit.chunk_text` / `chunk_texts` | return `DocumentChunk.text` **verbatim** — a separate header column needs no change to `search.py` |
| `apply_document_update` returns, per edit | `sender`→`['sender_id']`, `kind_slug`→`['kind_id']`, `title`→`['title']`, `document_date`→`['document_date']`, `summary`→`['summary']` |
| `get_sessionmaker()` | `expire_on_commit=False`, so selectin relationships survive a commit; `run_embed` may read `document.sender`/`.kind` anywhere in its body |
| A document matching filters but having no chunks | counted by `matched`, unreachable by vector search — `matched=2, hits=1` |

## File structure

**Created**
- `src/library/ask/recall_eval.py` — pure scoring for retrieval + Ask-loop recall. Stdlib only, no DB, no network, so CI unit-tests it. Mirrors `disclosure_eval.py`.
- `src/library/ask/recall_scenarios.py` — the synthetic corpus and the question→expected-document cases. Mirrors `disclosure_scenarios.py`.
- `migrations/versions/0031_chunk_context_header.py` — adds `document_chunks.context_header`.
- `tests/test_recall_eval.py` — unit tests for the scorer (no DB).
- `tests/test_recall_scenarios.py` — structural invariants of the corpus (no DB, no embedder).
- `tests/test_ask_search_filters.py` — integration tests for #5 and #7.
- `tests/test_chunk_context_header.py` — integration tests for #6.

**Modified**
- `src/library/ask/engine.py` — `semantic_search` tool schema (+filters, +`top_k`), `_run_semantic_search` (filters, clamp, coverage block).
- `src/library/search.py` — new `search_reach()` helper returning the matched/unembedded counts.
- `src/library/jobs.py` — `run_embed` composes and stores the context header.
- `src/library/documents_service.py` — `header_fields_changed()` predicate.
- `src/library/api/documents.py`, `src/library/ask/engine.py` — defer a re-embed when a header field changed.
- `src/library/models.py` — `DocumentChunk.context_header`.
- `src/library/config.py` — `ask_search_max_top_k`.
- `src/library/cli.py` — `eval-recall` command.
- `.github/workflows/e2e-nightly.yml` — run `eval-recall` after the embedder is warm.
- `docs/ask.md` — tool schema, config table, recall-eval section, limitations.

---

### Task 1: Recall scoring module

Pure scoring, no DB and no network, so CI runs it with neither embedder nor credentials — the same split that lets `disclosure_eval.py` be tested in CI while `eval-disclosure` cannot be.

**Files:**
- Create: `src/library/ask/recall_eval.py`
- Test: `tests/test_recall_eval.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `RecallVerdict` (frozen dataclass: `case: str`, `passed: bool`, `expected: tuple[int, ...]`, `retrieved: tuple[int, ...]`, `found: tuple[int, ...]`, `missed: tuple[int, ...]`, `recall: float`, `k: int`) and `score_recall(case: str, expected: Iterable[int], retrieved: Iterable[int], *, k: int) -> RecallVerdict`. Tasks 3 and 6 call `score_recall`; Task 3 also reads `.recall` to compute a mean.

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the recall scorer — no DB, no embedder, no credentials."""

import pytest

from library.ask.recall_eval import score_recall


def test_all_expected_retrieved_passes() -> None:
    verdict = score_recall("a", [1, 2], [1, 2, 3], k=10)
    assert verdict.passed is True
    assert verdict.recall == 1.0
    assert verdict.missed == ()


def test_partial_retrieval_reports_which_were_missed() -> None:
    verdict = score_recall("b", [1, 2], [1, 9], k=10)
    assert verdict.passed is False
    assert verdict.recall == 0.5
    assert verdict.found == (1,)
    assert verdict.missed == (2,)


def test_k_truncates_before_scoring() -> None:
    """Documents ranked below k do not count as retrieved, even though the
    caller handed them to us — k is the measurement, not a display limit."""
    verdict = score_recall("c", [1, 5], [9, 8, 7, 5, 1], k=3)
    assert verdict.retrieved == (9, 8, 7)
    assert verdict.recall == 0.0
    assert verdict.missed == (1, 5)


def test_empty_expected_set_fails_rather_than_vacuously_passing() -> None:
    """A case with nothing expected has nothing to measure. It must FAIL.

    This is the exact defect `disclosure_eval.score` had to grow a guard for:
    a scenario whose check loop has nothing to iterate reports success having
    exercised nothing, which is worse than no eval at all.
    """
    verdict = score_recall("d", [], [1, 2], k=10)
    assert verdict.passed is False
    assert verdict.recall == 0.0


def test_duplicate_expected_ids_are_counted_once() -> None:
    """Otherwise a case that lists a document twice can never reach recall 1.0."""
    verdict = score_recall("e", [1, 1, 2], [1, 2], k=10)
    assert verdict.expected == (1, 2)
    assert verdict.recall == 1.0
    assert verdict.passed is True


def test_nothing_retrieved_is_zero_not_a_crash() -> None:
    verdict = score_recall("f", [1], [], k=10)
    assert verdict.passed is False
    assert verdict.recall == 0.0
    assert verdict.missed == (1,)


@pytest.mark.parametrize("k", [0, -1])
def test_non_positive_k_is_rejected(k: int) -> None:
    """k <= 0 makes recall meaningless and would silently score every case 0.0.
    Fail loudly instead — a caller passing k=0 has a bug, not a measurement."""
    with pytest.raises(ValueError):
        score_recall("g", [1], [1], k=k)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_recall_eval.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.ask.recall_eval'`

- [ ] **Step 3: Write the implementation**

```python
"""Scoring for the recall eval: did retrieval actually reach the right documents?

The counterpart to ``disclosure_eval``. That module asks whether an answer owned
up to a gap it was shown; this one asks whether the documents that could answer
the question were retrieved at all.

Pure by design — stdlib only, no DB and no network — so CI runs it while the
live halves (``library eval-recall``, which needs the bge-m3 sidecar, and its
``--ask`` mode, which additionally needs Claude credentials) cannot run there.

**Recall, not precision, and deliberately so.** The question this eval exists to
answer is "can Ask reach the document at all", which is what findings #5, #6 and
#7 move. Precision matters too, but a retrieval change that adds a true positive
at rank 9 and a false positive at rank 10 is an improvement this eval should
report as one. Ranking quality is re-ranking's problem, deferred in
``docs/roadmap.md`` §1.2 — and this eval is the thing that would fire that
trigger.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecallVerdict:
    """One case's result, carrying both sides so a human can read the failure.

    ``retrieved`` is kept (truncated to ``k``) because a case that fails is
    almost always diagnosed by looking at what came back *instead* — the
    near-miss distractors the corpus seeds on purpose.
    """

    case: str
    passed: bool
    expected: tuple[int, ...]
    retrieved: tuple[int, ...]
    found: tuple[int, ...]
    missed: tuple[int, ...]
    recall: float
    k: int


def score_recall(
    case: str, expected: Iterable[int], retrieved: Iterable[int], *, k: int
) -> RecallVerdict:
    """Score one case's retrieval as recall@k.

    ``retrieved`` is truncated to the first ``k`` ids before anything is
    measured: k IS the measurement, so a caller who over-fetches (and callers
    do — ``semantic_search``'s own ``top_k`` may exceed the k being scored)
    must not accidentally be credited for documents below the cut.

    ``expected`` is de-duplicated while preserving order, because a case that
    names the same document twice could otherwise never reach recall 1.0.

    An **empty** ``expected`` fails. It is not a vacuous pass: a case with
    nothing to find measures nothing, and reporting success for it is the
    failure mode ``disclosure_eval.score`` had to grow its own guard against.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")

    expected_ids = tuple(dict.fromkeys(expected))
    retrieved_ids = tuple(retrieved)[:k]

    if not expected_ids:
        return RecallVerdict(
            case=case,
            passed=False,
            expected=(),
            retrieved=retrieved_ids,
            found=(),
            missed=(),
            recall=0.0,
            k=k,
        )

    top = set(retrieved_ids)
    found = tuple(document_id for document_id in expected_ids if document_id in top)
    missed = tuple(document_id for document_id in expected_ids if document_id not in top)
    return RecallVerdict(
        case=case,
        passed=not missed,
        expected=expected_ids,
        retrieved=retrieved_ids,
        found=found,
        missed=missed,
        recall=len(found) / len(expected_ids),
        k=k,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_recall_eval.py -v`
Expected: PASS (8 tests — the last is parametrized twice)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/library/ask/recall_eval.py tests/test_recall_eval.py
git commit -m "feat(ask): score retrieval as recall@k"
```

---

### Task 2: The synthetic recall corpus

The corpus and the cases scored against it. **Read spec §8.6 first** — the
difficulty of this corpus is the entire validity of the eval, and the acceptance
criterion in Step 6 can send this task back for re-authoring.

**Files:**
- Create: `src/library/ask/recall_scenarios.py`
- Test: `tests/test_recall_scenarios.py`

**Interfaces:**
- Consumes: nothing (pure data).
- Produces: `RecallDoc` (frozen: `marker`, `sender_name`, `kind_slug`,
  `document_date`, `title`, `body` — all `str` except `document_date: date`),
  `RecallCase` (frozen: `name`, `question`, `expected_markers: tuple[str, ...]`,
  `why`, `k: int = 10`), `CORPUS: tuple[RecallDoc, ...]`,
  `CASES: tuple[RecallCase, ...]`, `MAX_BODY_CHARS: int`, `FIXTURE_SUFFIX: str`.
  Task 3 seeds `CORPUS`, maps `marker` → database id, and iterates `CASES`.

**Verified by execution while this plan was written:** corpus size 53, 6 cases,
0 duplicate markers, longest body 455 chars (limit 1800), every expected marker
resolves, and every kind slug is one of the migration-seeded set. The seven
structural tests below all passed against this exact module.

- [ ] **Step 1: Write the failing structural tests**

Create `tests/test_recall_scenarios.py`:

```python
"""Structural invariants of the recall corpus.

These do not measure retrieval — they check that the corpus can still
measure it. Every one of them corresponds to a way the corpus could be
edited into uselessness without any test going red.
"""

import pytest
from sqlalchemy import select

from library.ask.recall_scenarios import CASES, CORPUS, FIXTURE_SUFFIX, MAX_BODY_CHARS
from library.config import get_settings
from library.models import Kind
from tests.conftest import fetch_all

pytestmark = pytest.mark.integration

#: Below this the haystack stops discriminating: with ten retrieval slots and a
#: corpus of thirty, "retrieved" and "exists" converge and recall@10 is near 1.0
#: for everything. Chosen as a floor, not a target — growing the corpus is fine.
MIN_CORPUS_SIZE = 45


def test_markers_are_unique() -> None:
    markers = [doc.marker for doc in CORPUS]
    assert len(markers) == len(set(markers))


def test_every_case_expects_documents_that_exist() -> None:
    markers = {doc.marker for doc in CORPUS}
    for case in CASES:
        assert case.expected_markers, f"{case.name} expects nothing — see score_recall"
        unknown = set(case.expected_markers) - markers
        assert not unknown, f"{case.name} expects unknown markers: {sorted(unknown)}"


def test_corpus_is_large_enough_to_discriminate() -> None:
    assert len(CORPUS) >= MIN_CORPUS_SIZE


def test_every_body_yields_exactly_one_chunk() -> None:
    """Document-level recall is only unambiguous if a document is one chunk."""
    limit = get_settings().embedding_chunk_chars
    assert MAX_BODY_CHARS <= limit
    for doc in CORPUS:
        assert len(doc.body) <= MAX_BODY_CHARS, doc.marker


def test_every_sender_is_marked_as_a_fixture() -> None:
    for doc in CORPUS:
        assert doc.sender_name.endswith(FIXTURE_SUFFIX), doc.marker


def test_every_kind_slug_is_seeded_in_the_database(api_database_url: str) -> None:
    """A typo'd slug makes `_seed_corpus`'s `scalar_one()` raise mid-run."""
    rows = fetch_all(api_database_url, "SELECT slug FROM kinds")
    seeded = {row[0] for row in rows}
    used = {doc.kind_slug for doc in CORPUS}
    assert used <= seeded, f"unseeded kind slugs: {sorted(used - seeded)}"


def test_breadth_case_is_unreachable_at_the_shipped_top_k() -> None:
    """The #7 case must be constructed so today's depth cannot satisfy it."""
    breadth = next(c for c in CASES if c.name == "breadth-many-mentions")
    assert len(breadth.expected_markers) > get_settings().retrieve_top_k
    assert breadth.k >= len(breadth.expected_markers)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_recall_scenarios.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'library.ask.recall_scenarios'`

- [ ] **Step 3: Create the corpus module**

Create `src/library/ask/recall_scenarios.py` with exactly this content. Every
sender, amount, date and sentence is invented; do not substitute anything real.

```python
"""A synthetic corpus and the retrieval cases scored against it.

The counterpart to ``disclosure_scenarios``. That module seeds metadata-only
documents to drive coverage arithmetic; this one seeds documents with **body
text**, because retrieval is what is being measured and a document with no text
has nothing to retrieve on.

**Everything here is invented.** This repository is public. No sender, amount,
date or sentence below resembles anything real, and every sender name carries a
``(recall-eval fixture)`` suffix so it cannot collide with a real archive's
senders and a human skimming query logs can tell at a glance the rows are
synthetic.

**One shared haystack, many cases.** Unlike the disclosure scenarios — which
seed and roll back per scenario, because each one's coverage arithmetic must not
see the others' rows — every case here is scored against the SAME corpus, seeded
once. That is deliberate: one case's near-miss distractors are another case's
noise, and a haystack that shrinks to six documents per question makes recall@10
meaningless (ten slots, six documents, everything is retrieved).

**Difficulty is the whole design.** A corpus of obviously-distinct documents
scores recall 1.0 at baseline and can therefore never show an improvement. Every
case ships with hand-authored near-miss distractors: same sender, same kind,
adjacent dates, overlapping vocabulary. ``docs/ask.md`` records the acceptance
criterion this corpus is held to — if baseline recall@10 comes out at or above
0.90, the corpus is too easy and gets harder before any retrieval change is
measured against it.

**One chunk per document, by construction.** Every ``body`` is shorter than
``embedding_chunk_chars`` (1800), so each document produces exactly one content
chunk. ``tests/test_recall_scenarios.py`` asserts this. It keeps document-level
recall unambiguous: a document is retrieved or it is not, with no question of
which of its chunks won.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Ceiling every ``RecallDoc.body`` must stay under so each document yields
#: exactly one chunk. Mirrors ``Settings.embedding_chunk_chars``; the structural
#: test imports the real setting and asserts this does not drift above it.
MAX_BODY_CHARS: int = 1800

#: Suffix on every synthetic sender. See the module docstring.
FIXTURE_SUFFIX: str = "(recall-eval fixture)"


@dataclass(frozen=True, slots=True)
class RecallDoc:
    """One synthetic document in the shared haystack.

    ``marker`` is the stable handle a case refers to; the CLI maps markers to
    the database ids it just inserted, so cases never hard-code ids.
    """

    marker: str
    sender_name: str
    kind_slug: str
    document_date: date
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class RecallCase:
    """One question, and the documents that must come back for it.

    ``k`` is the rank cut recall is measured at. It defaults to the shipped
    ``retrieve_top_k`` so most cases measure what Ask actually does today;
    ``breadth-many-mentions`` overrides it deliberately (see its comment).

    ``why`` records what the case exists to exercise, so a future reader can
    tell a case that regressed from a case that was never load-bearing.
    """

    name: str
    question: str
    expected_markers: tuple[str, ...]
    why: str
    k: int = 10


def _sender(name: str) -> str:
    return f"{name} {FIXTURE_SUFFIX}"


# --- Case 1: a clause inside a long contract -----------------------------------
#
# The spec's own motivating example for #5. The target is one 2019 mortgage
# contract; the distractors are the SAME sender's mortgage paperwork from
# adjacent years, all of which discuss repayment in similar language. Unscoped,
# the right year has to win on content alone.

_MORTGAGE: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="mortgage-2019-contract",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="contract",
        document_date=date(2019, 6, 11),
        title="Mortgage agreement — fixed ten-year term",
        body=(
            "Clause 7 — Early repayment. The borrower may repay up to fifteen "
            "per cent of the original principal in any calendar year without "
            "penalty. Repayments beyond that threshold attract a compensation "
            "charge calculated on the difference between the contract rate and "
            "the prevailing reinvestment rate for the remaining fixed period. "
            "No compensation is due where repayment follows the sale of the "
            "property, the death of a borrower, or the expiry of the fixed term."
        ),
    ),
    RecallDoc(
        marker="mortgage-2017-offer",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="contract",
        document_date=date(2017, 2, 3),
        title="Mortgage offer — provisional terms",
        body=(
            "This provisional offer sets out indicative terms only and does not "
            "constitute an agreement. Early repayment conditions will be stated "
            "in full in the final agreement. The indicative fixed period is ten "
            "years and the indicative rate is held for ninety days from the date "
            "of this letter."
        ),
    ),
    RecallDoc(
        marker="mortgage-2021-statement",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2021, 1, 9),
        title="Annual mortgage statement",
        body=(
            "Opening balance, scheduled repayments received, and interest "
            "charged for the year. One voluntary repayment was received in "
            "March and applied to the principal. No compensation charge was "
            "raised. The remaining fixed period is stated on page two."
        ),
    ),
    RecallDoc(
        marker="mortgage-2019-insurance",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2019, 6, 14),
        title="Buildings insurance requirement",
        body=(
            "As a condition of the agreement dated this month, the property "
            "must be insured for its full reinstatement value for the duration "
            "of the loan. Evidence of cover must be provided annually. This "
            "letter does not vary any repayment term of the agreement."
        ),
    ),
)


# --- Case 2: the chunk that names nothing --------------------------------------
#
# THE case finding #6 exists for. The target's body is a bare figures block that
# never names its sender, its date or what kind of document it is — all of that
# lives only in the metadata. Without a context header the chunk cannot match a
# question that names the sender. Expected to FAIL at baseline and to pass once
# #6 lands; that delta is the measurement.

_BARE_FIGURES: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="energy-2024-annual-bare",
        sender_name=_sender("Northwind Energy"),
        kind_slug="utility-bill",
        document_date=date(2024, 11, 4),
        title="Annual statement",
        body=(
            "Period total                 412,80\n"
            "Instalments received         360,00\n"
            "Balance due                   52,80\n"
            "Meter reading start          18422\n"
            "Meter reading end            21067\n"
            "Standing charge included in the period total."
        ),
    ),
    RecallDoc(
        marker="energy-2023-annual-bare",
        sender_name=_sender("Northwind Energy"),
        kind_slug="utility-bill",
        document_date=date(2023, 11, 6),
        title="Annual statement",
        body=(
            "Period total                 388,15\n"
            "Instalments received         372,00\n"
            "Balance due                   16,15\n"
            "Meter reading start          15980\n"
            "Meter reading end            18422\n"
            "Standing charge included in the period total."
        ),
    ),
    RecallDoc(
        marker="water-2024-annual-bare",
        sender_name=_sender("Clearbrook Water"),
        kind_slug="utility-bill",
        document_date=date(2024, 10, 22),
        title="Annual statement",
        body=(
            "Period total                 141,20\n"
            "Instalments received         132,00\n"
            "Balance due                    9,20\n"
            "Meter reading start           0641\n"
            "Meter reading end             0718"
        ),
    ),
)


# --- Case 3: same words, different kind ----------------------------------------
#
# Exercises #5's `kind` filter. All four documents talk about the same boiler in
# similar language; only the kind distinguishes what the user is asking for.

_BOILER: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="boiler-warranty",
        sender_name=_sender("Halden Heating"),
        kind_slug="warranty",
        document_date=date(2022, 4, 18),
        title="Boiler warranty certificate",
        body=(
            "The appliance identified below is covered against defects in "
            "materials and workmanship for seven years from the installation "
            "date. Cover is conditional on an annual service being carried out "
            "by an approved engineer. This warranty does not cover damage "
            "caused by limescale, incorrect pressure, or third-party parts."
        ),
    ),
    RecallDoc(
        marker="boiler-invoice",
        sender_name=_sender("Halden Heating"),
        kind_slug="invoice",
        document_date=date(2022, 4, 18),
        title="Boiler supply and installation",
        body=(
            "Supply and installation of one condensing boiler including flue "
            "kit, system flush and commissioning. Labour two days. The seven "
            "year warranty is registered with the manufacturer on your behalf "
            "and the certificate follows separately."
        ),
    ),
    RecallDoc(
        marker="boiler-manual",
        sender_name=_sender("Halden Heating"),
        kind_slug="manual",
        document_date=date(2022, 4, 18),
        title="Boiler user instructions",
        body=(
            "Setting the system pressure, resetting after a lockout, and the "
            "annual service schedule. Operating outside the stated pressure "
            "range may invalidate the warranty. Keep this booklet with the "
            "appliance for the life of the installation."
        ),
    ),
    RecallDoc(
        marker="boiler-service-letter",
        sender_name=_sender("Halden Heating"),
        kind_slug="letter",
        document_date=date(2023, 4, 2),
        title="Annual service due",
        body=(
            "Your appliance is approaching its annual service date. An annual "
            "service by an approved engineer is a condition of the seven year "
            "warranty. Please book within the next thirty days to keep cover "
            "in force."
        ),
    ),
)


# --- Case 4: breadth ------------------------------------------------------------
#
# Twelve documents mention the same term. At the shipped top_k of 10 recall is
# capped at 10/12 = 0.83 BY CONSTRUCTION — no retrieval improvement can make this
# case pass at k=10, which is the point: it is unanswerable until #7 lets the
# model ask for more. Scored at k=12 so the case CAN pass, and the eval reports
# the k it used so a reader sees why this one differs.

_SOLAR: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"solar-{index:02d}",
        sender_name=_sender(sender),
        kind_slug=kind,
        document_date=date(2023, month, day),
        title=title,
        body=body,
    )
    for index, (sender, kind, month, day, title, body) in enumerate(
        (
            (
                "Solaris Install",
                "quote",
                1,
                12,
                "Quotation for a rooftop array",
                "Indicative pricing for a fourteen panel rooftop array with a single "
                "inverter, scaffolding, and grid registration. Valid ninety days.",
            ),
            (
                "Solaris Install",
                "invoice",
                3,
                2,
                "Rooftop array — first stage",
                "First stage payment for the rooftop array covering panels, mounting "
                "rail and scaffolding hire. Balance due on commissioning.",
            ),
            (
                "Solaris Install",
                "invoice",
                4,
                19,
                "Rooftop array — final stage",
                "Final stage payment for the rooftop array following commissioning "
                "and grid registration. Includes the inverter and its isolator.",
            ),
            (
                "Solaris Install",
                "certificate",
                4,
                21,
                "Array commissioning certificate",
                "Certifies that the rooftop array was commissioned and tested, and "
                "that the installation complies with the applicable wiring rules.",
            ),
            (
                "Solaris Install",
                "warranty",
                4,
                21,
                "Panel performance warranty",
                "Panel output is warranted not to fall below eighty five per cent of "
                "nominal within twenty five years of the array's commissioning date.",
            ),
            (
                "Solaris Install",
                "manual",
                4,
                21,
                "Inverter operating notes",
                "Reading the inverter display, interpreting fault codes, and the "
                "shutdown sequence for the rooftop array before any roof work.",
            ),
            (
                "Gridline Networks",
                "letter",
                5,
                8,
                "Grid connection registered",
                "Your rooftop array has been registered for export. Metering will "
                "record import and export separately from the date below.",
            ),
            (
                "Gridline Networks",
                "invoice",
                7,
                1,
                "Network charges",
                "Quarterly network charges. Export from your rooftop array is "
                "credited separately and shown on the statement overleaf.",
            ),
            (
                "Harbour Insurance",
                "letter",
                5,
                30,
                "Policy amended",
                "Your buildings policy has been amended to note the rooftop array. "
                "No change to the premium arises from this amendment.",
            ),
            (
                "Meridian Mortgages",
                "letter",
                6,
                14,
                "Consent to alterations",
                "Consent is given for the rooftop array described in your request. "
                "The alteration does not affect the security or the fixed period.",
            ),
            (
                "Solaris Install",
                "receipt",
                8,
                3,
                "Bird protection mesh",
                "Supply and fitting of perimeter mesh to the rooftop array to "
                "prevent nesting beneath the panels.",
            ),
            (
                "Solaris Install",
                "letter",
                11,
                27,
                "First season output",
                "A summary of the rooftop array's output across its first season, "
                "with monthly generation and export figures.",
            ),
        ),
        start=1,
    )
)


# --- Case 5: the same term across years ----------------------------------------
#
# Exercises #5's date filters. Four near-identical parking notices; only the
# issue year separates them, and their bodies deliberately do not state it.

_PARKING: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"parking-{year}",
        sender_name=_sender("Civic Parking Office"),
        kind_slug="parking-ticket",
        document_date=date(year, 9, 17),
        title="Penalty charge notice",
        body=(
            "A penalty charge notice has been issued in respect of the vehicle "
            "described below, which was observed parked in a controlled zone "
            "without a valid permit displayed. The reduced amount applies if "
            "paid within fourteen days. Representations may be made in writing."
        ),
    )
    for year in (2021, 2022, 2023, 2024)
)


# --- Case 6: the control --------------------------------------------------------
#
# One distinctive term in exactly one document, with no near neighbour anywhere
# in the corpus. This case must pass at baseline. If it does not, the embedder,
# the seeding or the eval harness is broken — not the retrieval design — and no
# other case's result should be believed until it is fixed. Mirrors the role
# `complete-no-gaps` plays in the disclosure scenarios.

_CONTROL: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="control-kiln",
        sender_name=_sender("Ashgrove Pottery"),
        kind_slug="receipt",
        document_date=date(2024, 2, 29),
        title="Kiln element replacement",
        body=(
            "Replacement of three spiral kiln elements and one thermocouple, "
            "including recalibration of the controller against a reference "
            "probe. The kiln was fired to a test schedule before collection."
        ),
    ),
)


# --- Filler ---------------------------------------------------------------------
#
# Bulk noise so the haystack is large enough for recall@10 to discriminate. These
# are never any case's expected answer; they exist to occupy ranks. Generated
# rather than hand-written because their only requirements are "plausible
# archive prose" and "does not collide with a case's vocabulary" — the cases'
# own distractors above are where the difficulty is deliberately placed.

_FILLER_SUBJECTS: tuple[tuple[str, str, str], ...] = (
    ("Lakeside Dental", "invoice", "Routine examination and a small filling to one molar."),
    ("Lakeside Dental", "letter", "A reminder that a routine examination is now due."),
    ("Vellum Books", "receipt", "Three secondhand hardbacks and a reading light."),
    ("Copperfield Removals", "quote", "Estimate for a two-room move including packing materials."),
    ("Copperfield Removals", "invoice", "Two-room move completed, including packing materials."),
    ("Harbour Insurance", "contract", "Contents policy schedule for the coming year."),
    ("Harbour Insurance", "letter", "Confirmation that the contents policy renewed automatically."),
    ("Fenwick Council", "letter", "Notice of the residents parking scheme consultation."),
    ("Fenwick Council", "invoice", "Annual local charge, payable in ten monthly instalments."),
    ("Orchard Vets", "invoice", "Annual vaccination and a general health check."),
    ("Orchard Vets", "receipt", "Flea and worming treatment collected from reception."),
    (
        "Stonebridge Gym",
        "contract",
        "Membership terms, including the notice period for cancellation.",
    ),
    ("Stonebridge Gym", "receipt", "Monthly membership payment."),
    ("Larkspur Travel", "ticket", "Return rail tickets with seat reservations both ways."),
    ("Larkspur Travel", "receipt", "Booking fee and seat reservation charges."),
    ("Kestrel Broadband", "invoice", "Monthly broadband and line rental."),
    ("Kestrel Broadband", "letter", "Notice of a change to the fair usage policy."),
    ("Thornbury Garage", "invoice", "Annual service, oil change and two new tyres."),
    ("Thornbury Garage", "certificate", "Roadworthiness test passed with no advisories."),
    ("Millrace Storage", "contract", "Terms for a small self-storage unit let monthly."),
    ("Millrace Storage", "invoice", "Monthly storage unit charge."),
    ("Bramble Landscaping", "quote", "Estimate for replacing a fence and re-turfing a lawn."),
    ("Bramble Landscaping", "invoice", "Fence replacement and re-turfing completed."),
    ("Aldergate Opticians", "invoice", "Eye examination and one pair of single-vision lenses."),
    ("Aldergate Opticians", "certificate", "Prescription record following an eye examination."),
)

_FILLER: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"filler-{index:02d}",
        sender_name=_sender(sender),
        kind_slug=kind,
        # Spread across 2021–2024 so no case's date filter accidentally isolates
        # the whole filler set into or out of its range.
        document_date=date(2021 + (index % 4), 1 + (index % 12), 1 + (index % 27)),
        title=summary.rstrip(".").split(",")[0],
        body=(
            f"{summary} This document was issued in the ordinary course and "
            "requires no action. Payment terms, where they apply, are thirty "
            "days from the date shown. Retain for your records."
        ),
    )
    for index, (sender, kind, summary) in enumerate(_FILLER_SUBJECTS, start=1)
)


#: The whole haystack, seeded once and shared by every case.
CORPUS: tuple[RecallDoc, ...] = (
    _MORTGAGE + _BARE_FIGURES + _BOILER + _SOLAR + _PARKING + _CONTROL + _FILLER
)


CASES: tuple[RecallCase, ...] = (
    RecallCase(
        name="control-unique-term",
        question="What was done to the kiln?",
        expected_markers=("control-kiln",),
        why=(
            "Control. A distinctive term in exactly one document with no near "
            "neighbour. Must pass at baseline; a failure here means the "
            "embedder, the seeding or the harness is broken and no other "
            "result should be believed."
        ),
    ),
    RecallCase(
        name="contract-clause",
        question="What does my mortgage contract say about repaying early?",
        expected_markers=("mortgage-2019-contract",),
        why=(
            "The spec's motivating example for #5. Three same-sender "
            "distractors discuss repayment in similar language; only one "
            "states the actual terms."
        ),
    ),
    RecallCase(
        name="sender-named-bare-chunk",
        question="What did Northwind Energy bill me for in 2024?",
        expected_markers=("energy-2024-annual-bare",),
        why=(
            "THE case for #6. The target's body is a figures block naming "
            "neither its sender nor its year — both live only in metadata — so "
            "a question naming the sender cannot match it on content. Expected "
            "to fail at baseline and to pass once contextual headers land; that "
            "delta is the measurement #6 is justified by."
        ),
    ),
    RecallCase(
        name="kind-scoped",
        question="Show me the warranty for the boiler.",
        expected_markers=("boiler-warranty",),
        why=(
            "Exercises #5's kind filter. Four documents about the same boiler "
            "all mention the warranty; only one IS the warranty."
        ),
    ),
    RecallCase(
        name="date-scoped",
        question="What parking penalty did I get in 2022?",
        expected_markers=("parking-2022",),
        why=(
            "Exercises #5's date filters. Four near-identical notices whose "
            "bodies deliberately never state their year, so content alone "
            "cannot separate them — only the metadata filter can."
        ),
    ),
    RecallCase(
        name="breadth-many-mentions",
        question="Find every document about the solar panel installation.",
        expected_markers=tuple(f"solar-{index:02d}" for index in range(1, 13)),
        why=(
            "Exercises #7. Twelve documents mention the array, so at the "
            "shipped top_k of 10 recall is capped at 0.83 BY CONSTRUCTION and "
            "no retrieval improvement can make it pass. Scored at k=12 so the "
            "case can pass once the model is able to ask for that depth."
        ),
        k=12,
    ),
)
```

- [ ] **Step 4: Run the structural tests**

Run: `uv run pytest tests/test_recall_scenarios.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add src/library/ask/recall_scenarios.py tests/test_recall_scenarios.py
git commit -m "feat(ask): a synthetic corpus for measuring retrieval recall"
```

- [ ] **Step 6: Record that the difficulty check is still owed**

The corpus cannot be validated until Task 3 can run it against a real embedder.
Do **not** mark this task done as "the corpus is right" — mark it done as "the
corpus is well-formed". Task 3 Step 8 applies the acceptance criterion from spec
§8.6 (baseline recall@10 below 0.90) and may send this task back for harder
distractors.

---

### Task 3: `library eval-recall` — retrieval recall (layer 1)

The measuring instrument. Seeds `CORPUS` through the **real** `run_embed` path —
not a reimplementation — so that when Task 7's contextual headers land, this eval
picks them up automatically and the #6 delta is measured rather than argued.

> **Honesty note, read this.** Every other task in this plan had its prescribed
> code executed against the real test database before it was written down. This
> one could not be fully executed: TEI publishes no arm64 image, so the author's
> machine has no local embedder. **Verified by execution:** seeding `CORPUS`
> through `run_embed` with a monkeypatched embedder produces exactly one chunk
> per document (8 documents → 8 chunks), the marker→id mapping works, and
> `jobs.embed_texts` is the correct monkeypatch seam. **NOT verified:** anything
> requiring real bge-m3 vectors — above all the baseline recall numbers. Treat
> Step 8 as a genuine experiment whose result is unknown.

**Files:**
- Modify: `src/library/cli.py`
- Modify: `.github/workflows/e2e-nightly.yml`
- Create: `recall-baseline.json` (repo root, written by Step 8)
- Test: `tests/test_recall_seed.py`

**Interfaces:**
- Consumes: `library.ask.recall_scenarios.{CORPUS, CASES, RecallDoc, RecallCase}`
  (Task 2), `library.ask.recall_eval.{score_recall, RecallVerdict}` (Task 1),
  `library.jobs.run_embed`, `library.embedding.client.embed_query`,
  `library.search.semantic_search`.
- Produces: `_seed_corpus(session: AsyncSession) -> dict[str, int]` mapping each
  `RecallDoc.marker` to its inserted document id. Task 6 calls it too.

- [ ] **Step 1: Rename the rolled-back-transaction runner so both evals can share it**

`_run_eval_disclosure` in `src/library/cli.py` is the "external transaction"
helper whose docstring explains why nothing it runs can commit. Task 6 and this
task both need it, so its name must stop naming one command.

Find every caller first — **`src/` alone is not enough**:

```bash
grep -rn "_run_eval_disclosure" src/ tests/ docs/
```

Rename it to `_run_rolled_back` (definition and all callers). Change nothing
else about it: the docstring's reasoning about `join_transaction_mode=
"create_savepoint"` applies unchanged, but update its one reference to
`eval_disclosure` to say "the eval commands".

- [ ] **Step 2: Write the failing seeding test**

Create `tests/test_recall_seed.py`:

```python
"""The recall corpus seeds through the real embedding path.

Uses a fake embedder — this test is about the seeding mechanics (one chunk per
document, markers resolving to ids), not about vector quality, which only
`library eval-recall` against a real bge-m3 sidecar can measure.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library import jobs
from library.ask.recall_scenarios import CORPUS
from library.cli import _seed_corpus
from library.config import get_settings
from library.models import EMBEDDING_DIM, DocumentChunk

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distinct, deterministic unit vectors — enough to store, not to rank."""
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "true")
    get_settings.cache_clear()

    async def fake_embed_texts(
        texts: list[str], *, settings: object, client: object = None
    ) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * EMBEDDING_DIM
            vector[hash(text) % EMBEDDING_DIM] = 1.0
            vectors.append(vector)
        return vectors

    monkeypatch.setattr(jobs, "embed_texts", fake_embed_texts)


async def test_seed_corpus_maps_every_marker_to_an_id(
    session: AsyncSession, fake_embedder: None
) -> None:
    ids_by_marker = await _seed_corpus(session)
    assert set(ids_by_marker) == {doc.marker for doc in CORPUS}
    assert len(set(ids_by_marker.values())) == len(CORPUS), "ids must be distinct"


async def test_seed_corpus_produces_exactly_one_chunk_per_document(
    session: AsyncSession, fake_embedder: None
) -> None:
    """The corpus promises document-level recall is unambiguous. Hold it to that.

    If this fails, a body has grown past `embedding_chunk_chars` and a document
    now spans several chunks — recall is still measurable, but the corpus's
    stated invariant is broken and the scenarios module's docstring is lying.
    """
    await _seed_corpus(session)
    per_document = (
        await session.execute(
            select(DocumentChunk.document_id, func.count()).group_by(DocumentChunk.document_id)
        )
    ).all()
    assert len(per_document) == len(CORPUS)
    assert {count for _, count in per_document} == {1}
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_recall_seed.py -v`
Expected: FAIL — `ImportError: cannot import name '_seed_corpus' from 'library.cli'`

- [ ] **Step 4: Implement `_seed_corpus`**

Add to `src/library/cli.py`, beside `_seed_scenario`. Note it mirrors that
function's rules — look senders up before inserting so a re-run against the same
rolled-back database reuses rows; never create kinds, so a typo'd slug fails
loudly on `scalar_one()`.

```python
async def _seed_corpus(session: AsyncSession) -> dict[str, int]:
    """Seed the recall corpus and embed it; return marker -> document id.

    Embeds through ``jobs.run_embed`` rather than inserting chunks directly, so
    the eval measures the pipeline that actually runs in production — chunking
    rules, comment handling, and (once Plan B Task 7 lands) the contextual
    header. A hand-rolled insert here would quietly stop measuring #6 the moment
    it shipped, which is the one thing this eval exists to measure.

    Flushes but never commits: the caller runs inside ``_run_rolled_back``.
    ``run_embed`` calls ``session.commit()`` internally to record its ingestion
    event; under that binding a commit can only release a SAVEPOINT.
    """
    senders: dict[str, Sender] = {}
    kinds: dict[str, Kind] = {}
    ids_by_marker: dict[str, int] = {}

    for doc in CORPUS:
        sender = senders.get(doc.sender_name)
        if sender is None:
            sender = (
                await session.execute(select(Sender).where(Sender.name == doc.sender_name))
            ).scalar_one_or_none()
            if sender is None:
                sender = Sender(name=doc.sender_name)
                session.add(sender)
                await session.flush()
            senders[doc.sender_name] = sender

        kind = kinds.get(doc.kind_slug)
        if kind is None:
            kind = (
                await session.execute(select(Kind).where(Kind.slug == doc.kind_slug))
            ).scalar_one()
            kinds[doc.kind_slug] = kind

        marker = f"recall-eval:{doc.marker}"
        document = Document(
            sha256=hashlib.sha256(marker.encode()).hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            title=doc.title,
            sender=sender,
            kind=kind,
            document_date=doc.document_date,
            ocr_text=doc.body,
        )
        session.add(document)
        await session.flush()
        ids_by_marker[doc.marker] = document.id

    await session.flush()
    for doc in CORPUS:
        document = await session.get(Document, ids_by_marker[doc.marker])
        if document is None:  # pragma: no cover - just flushed it
            raise RuntimeError(f"seeded document for {doc.marker} vanished")
        await run_embed(session, document)

    return ids_by_marker
```

Add the imports this needs to `cli.py`'s existing import block:

```python
from library.ask.recall_eval import RecallVerdict, score_recall
from library.ask.recall_scenarios import CASES, CORPUS
from library.embedding.client import embed_query
from library.jobs import run_embed  # add to the existing `from library.jobs import (...)`
from library.search import DocumentFilters, semantic_search
```

- [ ] **Step 5: Run the seeding tests**

Run: `uv run pytest tests/test_recall_seed.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Add the `eval-recall` command**

```python
@app.command("eval-recall")
def eval_recall(
    only: str | None = typer.Option(None, "--only", help="Run just this case by name."),
    write_baseline: bool = typer.Option(
        False, "--write-baseline", help="Overwrite recall-baseline.json with this run."
    ),
) -> None:
    """Measure whether retrieval reaches the documents that answer a question.

    Seeds the synthetic corpus (``library.ask.recall_scenarios``), embeds it
    through the real pipeline, runs each case's question through
    ``semantic_search``, and scores recall@k.

    **Nothing is committed**, structurally — see ``_run_rolled_back``.

    Requires a reachable bge-m3 sidecar (``LIBRARY_EMBEDDING_SERVICE_URL``); it
    does NOT require Claude credentials, which is why it can run in CI while
    ``eval-disclosure`` cannot. Note TEI publishes no arm64 image, so this does
    not run on an Apple Silicon laptop — use the deployed host or the nightly
    workflow.

    Exits non-zero if any case fails, so it can gate a release by hand.
    """
    settings = get_settings()
    typer.echo(
        f"WARNING: eval-recall seeds {len(CORPUS)} synthetic documents into "
        f"{_redact_database_url(settings.database_url)} and embeds them. "
        "Every seed is flushed then rolled back — nothing is committed."
    )

    cases = [case for case in CASES if only is None or case.name == only]
    if not cases:
        typer.echo(f"error: no case named {only!r}")
        raise typer.Exit(code=1)

    async def operation(session: AsyncSession) -> list[RecallVerdict]:
        ids_by_marker = await _seed_corpus(session)
        verdicts: list[RecallVerdict] = []
        for case in cases:
            embedding = await embed_query(case.question, settings=settings)
            hits = await semantic_search(
                session,
                query=case.question,
                query_embedding=embedding,
                filters=DocumentFilters(),
                top_k=case.k,
            )
            verdicts.append(
                score_recall(
                    case.name,
                    [ids_by_marker[marker] for marker in case.expected_markers],
                    [hit.document.id for hit in hits],
                    k=case.k,
                )
            )
        return verdicts

    verdicts = _run_rolled_back(operation)
    _report_recall(verdicts, write_baseline=write_baseline)
```

And the reporter, which is where the baseline diff lives:

```python
#: Where the last recorded run is kept, so a retrieval change is a measured
#: delta rather than a recollection. Repo root, not `docs/`: it is machine-
#: written data, and `scripts/check_docs.py` scans `docs/` for prose.
RECALL_BASELINE_PATH: Path = Path(__file__).resolve().parents[2] / "recall-baseline.json"


def _report_recall(verdicts: list[RecallVerdict], *, write_baseline: bool) -> None:
    """Print each case, the mean, and the delta against the recorded baseline."""
    baseline: dict[str, float] = {}
    if RECALL_BASELINE_PATH.exists():
        baseline = json.loads(RECALL_BASELINE_PATH.read_text()).get("cases", {})

    failures = 0
    for verdict in verdicts:
        status = "PASS" if verdict.passed else "FAIL"
        previous = baseline.get(verdict.case)
        delta = "" if previous is None else f"  ({verdict.recall - previous:+.2f} vs baseline)"
        typer.echo(
            f"{status} {verdict.case}  recall@{verdict.k}={verdict.recall:.2f}{delta}"
        )
        if not verdict.passed:
            failures += 1
            typer.echo(f"  missed document ids: {list(verdict.missed)}")
            typer.echo(f"  retrieved instead:   {list(verdict.retrieved)}")

    mean = sum(v.recall for v in verdicts) / len(verdicts) if verdicts else 0.0
    typer.echo(f"{len(verdicts) - failures} passed, {failures} failed, mean recall {mean:.3f}")

    if write_baseline:
        RECALL_BASELINE_PATH.write_text(
            json.dumps(
                {"mean": mean, "cases": {v.case: v.recall for v in verdicts}},
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        typer.echo(f"wrote baseline to {RECALL_BASELINE_PATH}")

    if failures:
        raise typer.Exit(code=1)
```

`cli.py` needs `import json` and `from pathlib import Path` if not already
present — check before adding, both are common.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
uv run pytest tests/test_recall_seed.py tests/test_recall_eval.py tests/test_recall_scenarios.py -v
git add -A
git commit -m "feat(ask): measure retrieval recall against a synthetic corpus"
```

- [ ] **Step 8: Run it for real, and apply the corpus acceptance criterion**

This needs a reachable embedder — the deployed host, or push the branch and let
the nightly workflow run it. On the host:

```bash
library eval-recall
```

Then apply spec §8.6's criterion to the **mean recall@10 across the five k=10
cases** (exclude `breadth-many-mentions`, which is constructed to be unreachable
at k=10 and is scored at k=12):

- **mean ≥ 0.90** → the corpus is too easy. **Go back to Task 2** and harden the
  distractors: more same-sender near neighbours, closer vocabulary, tighter
  dates. An eval with no headroom to fall cannot show Task 7 helping. Do not
  proceed to Task 4 until this clears.
- **mean < 0.90** → record it: `library eval-recall --write-baseline`, then
  commit `recall-baseline.json`.

Expected shape of the result, stated so a surprise is visible as one:
`control-unique-term` should PASS (if it does not, the harness is broken, not
the retrieval); `sender-named-bare-chunk` should FAIL, because that is precisely
the gap #6 exists to close and Task 7 has not landed yet.

- [ ] **Step 9: Wire it into the nightly workflow**

In `.github/workflows/e2e-nightly.yml`, add a step **after** "Wait for the
embedder to load its model" and before the Playwright steps. It goes in this
workflow and not the PR gate for the reason that workflow's own header already
gives: it needs the embedder, it is not fully deterministic, and a nightly
failure is a signal to look rather than a reason to block a merge.

```yaml
      # Retrieval recall (Plan B #15). Here rather than in the PR gate for the
      # reason at the top of this file: it needs the embedder. `|| true` is
      # deliberately ABSENT — a recall regression should red the nightly.
      - name: Measure retrieval recall
        run: docker compose exec -T api library eval-recall
```

Commit:

```bash
git add .github/workflows/e2e-nightly.yml recall-baseline.json
git commit -m "ci(nightly): measure retrieval recall with a warm embedder"
```

---

### Task 4: Filters and reach on `semantic_search` (closes #5)

Wires the filters `library.search.semantic_search` has always accepted to the
tool schema, and gives the result a coverage block so the model can tell "your
filter excluded everything" from "the archive genuinely does not say this".

**Files:**
- Modify: `src/library/search.py` (add `SearchReach` + `search_reach`)
- Modify: `src/library/ask/engine.py` (schema, `_run_semantic_search`)
- Modify: `tests/test_api_ask.py` (four existing stubs — see Step 1)
- Test: `tests/test_ask_search_filters.py`

**Interfaces:**
- Consumes: `_filters_from_args(args) -> DocumentFilters` — **already exists**
  in `engine.py` and is already shared by `query_documents` and
  `compare_to_series`. Do NOT write a second mapping; it is what translates the
  schema's `kind`/`tags`/`projects` onto `DocumentFilters`' `kind_slug`/
  `tag_slugs`/`project_slugs`.
- Produces: `SearchReach(matched: int, unembedded: int)` and
  `async search_reach(session: AsyncSession, filters: DocumentFilters) -> SearchReach`.
  Task 5 extends the same `_run_semantic_search`.

- [ ] **Step 1: Fix the four existing `semantic_search` stubs FIRST**

This is not cleanup, it is the blocking prerequisite, and it was found by
executing the suite rather than reading it. `tests/test_api_ask.py` stubs
`semantic_search` in four places. Three declare an explicit signature that will
raise `TypeError: got an unexpected keyword argument 'filters'` the moment the
call site changes. All four then hit a second failure: those tests pass a fake
session, and `search_reach` issues a **real** query, so it must be stubbed at
the same level.

Confirm the sites yourself — **`src/` alone will not find them**:

```bash
grep -rn "semantic_search" src/ tests/
grep -rn "def fake_search" tests/
```

Add the import:

```python
from library.search import SearchReach
```

Widen each of the three explicit stubs to accept `filters`:

```python
    async def fake_search(
        session: Any,
        *,
        query: str,
        query_embedding: Any,
        top_k: int,
        chunks_per_doc: int = 1,
        filters: Any = None,
    ) -> list[Any]:
```

And after **each** of the four `monkeypatch.setattr(ask_engine, "semantic_search", fake_search)`
lines, stub the reach query too:

```python
    async def fake_reach(session: Any, filters: Any) -> Any:
        return SearchReach(matched=0, unembedded=0)

    monkeypatch.setattr(ask_engine, "search_reach", fake_reach)
```

Verified: with these edits `uv run pytest tests/test_api_ask.py -q` reports
**47 passed**.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_ask_search_filters.py` with the shared fixtures and the
Task 4 tests below. (Task 5 appends two more tests to this same file.)

```python
"""Filters, reach and depth on Ask's semantic_search tool (#5, #7)."""

import hashlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library.ask import engine as engine_mod
from library.ask.engine import TOOLS, _run_semantic_search
from library.config import get_settings
from library.models import EMBEDDING_DIM, Document, DocumentChunk, DocumentSource, Kind
from library.search import DocumentFilters, search_reach

pytestmark = pytest.mark.integration


def vec(index: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[index] = 1.0
    return vector


@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(api_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def stub_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every query embeds to the same vector the seeded chunks carry, so
    ranking is decided by the filters under test rather than by similarity."""

    async def fake_embed_query(text: str, *, settings: Any, client: Any = None) -> list[float]:
        return vec(0)

    monkeypatch.setattr(engine_mod, "embed_query", fake_embed_query)


async def seed(
    session: AsyncSession,
    marker: str,
    *,
    kind_slug: str | None = None,
    chunks: tuple[tuple[str, list[float]], ...] = (),
) -> int:
    kind = None
    if kind_slug is not None:
        kind = (await session.execute(select(Kind).where(Kind.slug == kind_slug))).scalar_one()
    document = Document(
        sha256=hashlib.sha256(marker.encode()).hexdigest(),
        mime_type="application/pdf",
        source=DocumentSource.UPLOAD,
        ocr_text=f"{marker} alpha",
        kind=kind,
        title=marker,
    )
    session.add(document)
    await session.commit()
    for index, (text, embedding) in enumerate(chunks, start=1):
        session.add(
            DocumentChunk(
                document_id=document.id, chunk_index=index, text=text, embedding=embedding
            )
        )
    await session.commit()
    return document.id


def test_schema_offers_the_shared_filters_but_not_review_status() -> None:
    """`review_status` is deliberately absent — see the comment beside
    `_REVIEW_STATUS_PROPERTY`: a filter is only offered to a tool that can
    report what the filter removed, and this tool's coverage block reports
    reach, not exclusion reasons."""
    schema = next(tool for tool in TOOLS if tool["name"] == "semantic_search")["input_schema"]
    properties = schema["properties"]
    for name in ("kind", "sender_contains", "date_from", "date_to", "projects", "matters", "tags"):
        assert name in properties, name
    assert "review_status" not in properties
    assert schema["required"] == ["query"]


async def test_search_reach_counts_matched_and_unembedded(session: AsyncSession) -> None:
    """One round trip, two counts. `unembedded` is what distinguishes
    'the archive is silent' from 'these documents were never indexed'."""
    for n in range(3):
        await seed(session, f"reach-with-{n}", kind_slug="invoice", chunks=(("t", vec(0)),))
    for n in range(2):
        await seed(session, f"reach-without-{n}", kind_slug="invoice")
    await seed(session, "reach-other", kind_slug="receipt", chunks=(("t", vec(0)),))

    reach = await search_reach(session, DocumentFilters(kind_slug="invoice"))
    assert (reach.matched, reach.unembedded) == (5, 2)


async def test_filters_narrow_the_search_and_are_reported(
    session: AsyncSession, stub_embedder: None
) -> None:
    for n in range(3):
        await seed(session, f"filter-invoice-{n}", kind_slug="invoice", chunks=(("alpha", vec(0)),))
    for n in range(2):
        await seed(session, f"filter-receipt-{n}", kind_slug="receipt", chunks=(("alpha", vec(0)),))

    filtered = await _run_semantic_search(
        session, get_settings(), {"query": "alpha", "kind": "invoice"}, set(), {}
    )
    assert len(filtered["results"]) == 3
    assert filtered["coverage"] == {"matched": 3, "returned": 3, "unembedded": 0}

    unfiltered = await _run_semantic_search(
        session, get_settings(), {"query": "alpha"}, set(), {}
    )
    assert unfiltered["coverage"]["matched"] == 5
```

- [ ] **Step 3: Run to verify they fail**

Run: `uv run pytest tests/test_ask_search_filters.py -v`
Expected: FAIL — `ImportError: cannot import name 'search_reach' from 'library.search'`

- [ ] **Step 4: Add `SearchReach` and `search_reach` to `src/library/search.py`**

Add `exists` to the existing sqlalchemy import line:

```python
from sqlalchemy import Select, case, cast, exists, func, or_, select
```

Then insert directly above `async def semantic_search(`:

```python
@dataclass(frozen=True, slots=True)
class SearchReach:
    """How much of the archive a filtered content search could even see.

    ``matched`` counts documents passing the caller's filters. ``unembedded``
    counts how many of those have no chunks at all — they are invisible to the
    vector retriever no matter what the query says, so a caller that reports
    only ``matched`` cannot distinguish "the archive does not say this" from
    "the documents exist but were never indexed" (finding #14).
    """

    matched: int
    unembedded: int


async def search_reach(session: AsyncSession, filters: DocumentFilters) -> SearchReach:
    """Both counts in one round trip, via a conditional aggregate.

    Same shape as ``structured_query.count_coverage``: ``count(*)`` for the
    denominator and a ``FILTER``ed ``count(*)`` for the subset, so the two can
    never disagree by being computed against different snapshots.
    """
    has_chunk = exists().where(DocumentChunk.document_id == Document.id)
    statement = (
        select(
            func.count().label("matched"),
            func.count().filter(~has_chunk).label("unembedded"),
        )
        .select_from(Document)
        .where(*filter_conditions(filters))
    )
    row = (await session.execute(statement)).one()
    return SearchReach(matched=int(row.matched), unembedded=int(row.unembedded))
```

- [ ] **Step 5: Wire filters into the tool**

In `src/library/ask/engine.py`, extend the import:

```python
from library.search import DocumentFilters, search_reach, semantic_search
```

Replace the `semantic_search` entry of `TOOLS` with (note: the `top_k` property
is added by Task 5 — if you are doing Task 4 alone, omit that one key):

```python
        "name": "semantic_search",
        "description": (
            "Hybrid full-text + semantic search over document contents. Returns "
            "the most relevant documents with a matching excerpt. Use for "
            "questions about what documents say. Accepts the same metadata "
            "filters as query_documents — scope the search whenever the question "
            "names a sender, a kind or a date range, rather than searching the "
            "whole archive and hoping. The result carries a `coverage` block: "
            "`matched` is how many documents passed your filters, `returned` how "
            "many came back, and `unembedded` how many matched documents have no "
            "search index at all. Read it before concluding anything is absent — "
            "`matched: 0` means your filters excluded everything (widen them and "
            "retry), whereas `matched: 40, returned: 0` means those 40 documents "
            "genuinely do not say this. A non-zero `unembedded` means the answer "
            "is incomplete for a technical reason: say so. " + _kind_hint()
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language description of what to find.",
                },
                **_FILTER_PROPERTIES,
                "top_k": {
                    "type": "integer",
                    "description": (
                        "How many documents to return. Defaults to 10; raise it "
                        "for 'find every document that mentions X' questions. "
                        "Values above the configured maximum are clamped, so "
                        "asking for more than the archive allows is safe."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
```

And replace `_run_semantic_search` with:

```python
async def _run_semantic_search(
    session: AsyncSession,
    settings: Settings,
    args: dict[str, Any],
    cited: set[int],
    pages: dict[int, int],
) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "query is required"}
    try:
        embedding = await embed_query(query, settings=settings)
    except EmbeddingError as exc:
        logger.warning("ask semantic_search embedding failed: %s", exc)
        return {"error": "semantic search is temporarily unavailable"}
    filters = _filters_from_args(args)
    top_k = _top_k_arg(args.get("top_k"), settings)
    hits = await semantic_search(
        session,
        query=query,
        query_embedding=embedding,
        filters=filters,
        top_k=top_k,
        chunks_per_doc=settings.retrieve_chunks_per_doc,
    )
    reach = await search_reach(session, filters)
    rows = []
    for hit in hits:
        cited.add(hit.document.id)
        if hit.page_number is not None and hit.document.id not in pages:
            pages[hit.document.id] = hit.page_number
        rows.append(
            {
                "document_id": hit.document.id,
                "title": hit.document.title,
                "sender": hit.document.sender.name if hit.document.sender else None,
                "recipient": hit.document.recipient.name if hit.document.recipient else None,
                "document_date": (
                    hit.document.document_date.isoformat() if hit.document.document_date else None
                ),
                "excerpt": (
                    "\n\n[…]\n\n".join(hit.chunk_texts) if hit.chunk_texts else hit.chunk_text
                ),
            }
        )
    return {
        "results": rows,
        "coverage": {
            "matched": reach.matched,
            "returned": len(rows),
            "unembedded": reach.unembedded,
        },
    }
```

- [ ] **Step 6: Run the tests**

```bash
uv run pytest tests/test_ask_search_filters.py tests/test_api_ask.py -v
```
Expected: PASS.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add -A
git commit -m "feat(ask): scope semantic_search by metadata and report its reach"
```

---

### Task 5: Tunable retrieval depth (closes #7)

Lets the model ask for more than ten documents, and stops a negative `top_k`
from silently returning a near-complete set.

> **The clamp is the point of this task, not a nicety.** Measured against seven
> matching documents on the real database: `top_k=-3` returns **4** hits,
> `top_k=-1` returns **6**, with no error raised anywhere. `semantic_search`
> ends in `ranked[:top_k]`, and a negative slice counts from the end. A model
> emitting a negative value today gets a quietly wrong answer.

**Files:**
- Modify: `src/library/config.py` (`ask_search_max_top_k`)
- Modify: `.env.example` (**required** — `test_config.py` gates it)
- Modify: `src/library/ask/engine.py` (`_top_k_arg`, schema property, call site)
- Modify: `tests/test_ask_search_filters.py` (append two tests)
- Modify: `docs/ask.md` (config table row — Task 8 covers the rest)

**Interfaces:**
- Consumes: `Settings.retrieve_top_k` (default), `Settings.ask_search_max_top_k` (cap).
- Produces: `_top_k_arg(value: object, settings: Settings) -> int`.

- [ ] **Step 1: Append the failing tests**

Add to `tests/test_ask_search_filters.py`:

```python
def test_top_k_is_clamped_into_range() -> None:
    """Every row here was measured against the real retriever before being
    written down. The negative rows are the ones that matter: without the
    floor, `semantic_search`'s `ranked[:top_k]` slices from the END."""
    settings = get_settings()
    assert _top_k_arg(None, settings) == settings.retrieve_top_k
    assert _top_k_arg(-3, settings) == 1
    assert _top_k_arg(-1, settings) == 1
    assert _top_k_arg(0, settings) == 1
    assert _top_k_arg(1, settings) == 1
    assert _top_k_arg(25, settings) == 25
    assert _top_k_arg(1000, settings) == settings.ask_search_max_top_k
    # The schema says integer, but a schema steers the model without binding it.
    assert _top_k_arg("ten", settings) == settings.retrieve_top_k
    assert _top_k_arg("7", settings) == 7


async def test_negative_top_k_does_not_leak_a_near_complete_set(
    session: AsyncSession, stub_embedder: None
) -> None:
    """Regression guard for the measured behaviour: before the clamp, this
    returned six of seven documents."""
    for n in range(7):
        await seed(session, f"depth-{n}", chunks=(("alpha", vec(0)),))

    result = await _run_semantic_search(
        session, get_settings(), {"query": "alpha", "top_k": -1}, set(), {}
    )
    assert len(result["results"]) == 1
    assert result["coverage"] == {"matched": 7, "returned": 1, "unembedded": 0}
```

Extend the import at the top of the file:

```python
from library.ask.engine import TOOLS, _run_semantic_search, _top_k_arg
```

And add this assertion to `test_schema_offers_the_shared_filters_but_not_review_status`:

```python
    assert "top_k" in properties
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ask_search_filters.py -v`
Expected: FAIL — `ImportError: cannot import name '_top_k_arg'`

- [ ] **Step 3: Add the setting**

In `src/library/config.py`, directly after `ask_history_turns`:

```python
    # Ceiling on the `top_k` Ask's semantic_search tool may request. The model
    # can raise depth for "find every document mentioning X", but not without
    # bound: top_k also drives semantic_search's candidate pool
    # (`max(top_k * 5, 50)`), and every returned document costs
    # retrieve_chunks_per_doc passages of context.
    ask_search_max_top_k: int = 50
```

- [ ] **Step 3b: Document the setting in `.env.example` — this is a GATE**

`tests/test_config.py::test_env_example_documents_every_setting` asserts a bare
subset relation between `Settings.model_fields` and `.env.example`, with **no
exemption list**. Adding a setting without a matching line fails the suite. Found
by running the full suite, not by reading — a focused run on the ask tests passes
happily.

After the `#LIBRARY_ASK_HISTORY_TURNS=3` line:

```
# Ceiling on the top_k Ask's semantic_search tool may request. Values above it
# are clamped rather than rejected; non-positive values clamp to 1.
#LIBRARY_ASK_SEARCH_MAX_TOP_K=50
```

Verify: `uv run pytest tests/test_config.py -q` → 16 passed.

- [ ] **Step 4: Add `_top_k_arg`**

In `src/library/ask/engine.py`, directly above `def _text_arg(`:

```python
def _top_k_arg(value: object, settings: Settings) -> int:
    """A usable ``top_k`` from a tool argument, clamped into range.

    The clamp is load-bearing, not defensive tidiness. ``semantic_search`` ends
    in ``ranked[:top_k]``, so a NEGATIVE top_k slices from the end and silently
    returns a near-arbitrary subset — measured against seven matching documents,
    ``top_k=-1`` returns six hits and ``top_k=-3`` returns four, with no error
    anywhere. A model that emits a negative value would get a quietly wrong
    answer, so the floor of 1 is what stops that.

    A non-integer degrades to the configured default rather than raising: the
    schema's ``"type": "integer"`` steers the model but does not bind it, and a
    hallucinated ``"ten"`` must not 500 inside the tool loop. This mirrors how
    ``_review_status_arg`` treats an unrecognised enum value.
    """
    if value is None:
        return settings.retrieve_top_k
    try:
        requested = int(str(value).strip())
    except (TypeError, ValueError):
        logger.info("ask: ignoring non-integer top_k %r", value)
        return settings.retrieve_top_k
    return max(1, min(requested, settings.ask_search_max_top_k))
```

> **`int(value)` on an `object` does not type-check.** The first draft of
> `_top_k_arg` used `int(value)  # type: ignore[arg-type]` and `mypy` rejected
> it twice over: `No overload variant of "int" matches argument type "object"`
> AND `Unused "type: ignore" comment` (wrong error code). `int(str(value).strip())`
> narrows cleanly and needs no ignore. Found by running `uv run mypy`, which is
> part of `make lint` and gates CI.

- [ ] **Step 5: Add the schema property and use it**

Add to the `semantic_search` tool's `properties` (after the spread of
`_FILTER_PROPERTIES`):

```python
                "top_k": {
                    "type": "integer",
                    "description": (
                        "How many documents to return. Defaults to 10; raise it "
                        "for 'find every document that mentions X' questions. "
                        "Values above the configured maximum are clamped, so "
                        "asking for more than the archive allows is safe."
                    ),
                },
```

In `_run_semantic_search`, replace `top_k=settings.retrieve_top_k` with:

```python
    top_k = _top_k_arg(args.get("top_k"), settings)
```

and pass `top_k=top_k` to `semantic_search`. (If Task 4's code was applied
verbatim this is already in place — verify rather than re-apply.)

- [ ] **Step 6: Add the config-table row**

In `docs/ask.md` §1.3, after the `LIBRARY_RETRIEVE_CHUNKS_PER_DOC` row:

```markdown
| `LIBRARY_ASK_SEARCH_MAX_TOP_K` | `50` | Ceiling on the `top_k` Ask's `semantic_search` tool may request. Values above it are clamped rather than rejected; non-positive values clamp to `1`. |
```

- [ ] **Step 7: Run and commit**

```bash
uv run pytest tests/test_ask_search_filters.py tests/test_api_ask.py -v
uv run ruff format . && uv run ruff check . && uv run mypy
uv run python scripts/check_docs.py
git add -A
git commit -m "feat(ask): let the model choose retrieval depth, within a cap"
```

- [ ] **Step 8: Re-measure**

With an embedder reachable, `library eval-recall`. `breadth-many-mentions` is
scored at k=12 and should now be reachable; the other five should be unchanged.
A change in a non-breadth case here means the filters or the clamp altered
something they should not have — investigate before continuing.

---

### Task 6: Ask-loop recall (layer 2)

Layer 1 asks whether the retriever *can* reach a document. This asks whether the
**model** reaches it — whether it actually uses the filters and depth Tasks 4
and 5 gave it. A schema the model ignores is indistinguishable from no schema at
layer 1, so without this the two preceding tasks are unvalidated.

Scores `AskResult.citations` rather than the answer prose. `AskCitation` already
carries `document_id`, so this needs none of the heuristic text-screening
`disclosure_eval.mentions_count` required — and it incidentally re-exercises
Plan A's #11 fix, since an answer that cites documents it did not use will show
up here as spurious recall.

**Files:**
- Modify: `src/library/cli.py`

**Interfaces:**
- Consumes: `_seed_corpus` (Task 3), `score_recall` (Task 1), `CASES` (Task 2),
  `library.ask.engine.run_ask`.
- Produces: an `--ask` flag on the existing `eval-recall` command.

- [ ] **Step 1: Add the flag and the Ask-driven branch**

Extend the `eval_recall` signature:

```python
    ask: bool = typer.Option(
        False,
        "--ask",
        help="Drive the full Ask loop and score its citations, not raw retrieval.",
    ),
```

Extend the docstring with a paragraph:

```
    ``--ask`` drives the real Ask loop instead of calling the retriever
    directly, and scores the document ids the answer CITED. This is the only
    layer that can show whether the model actually uses the filters (#5) and
    depth (#7) the tool schema offers it — layer 1 calls the retriever with
    fixed arguments and so cannot tell a schema the model exploits from one it
    ignores. It needs Claude credentials, which is why it is a flag rather than
    the default: without it this command runs anywhere an embedder is reachable,
    CI included.
```

And branch inside `operation`, replacing the single retrieval call:

```python
    async def operation(session: AsyncSession) -> list[RecallVerdict]:
        ids_by_marker = await _seed_corpus(session)
        client = AsyncAnthropic(api_key="unused")  # subscription backend never calls the API
        verdicts: list[RecallVerdict] = []
        for case in cases:
            expected = [ids_by_marker[marker] for marker in case.expected_markers]
            if ask:
                try:
                    result = await run_ask(
                        session,
                        question=case.question,
                        settings=settings,
                        client=client,
                        backend="subscription",
                    )
                    retrieved = [citation.document_id for citation in result.citations]
                finally:
                    # Each case's Ask turn must not leave rows or state visible
                    # to the next one; the outer transaction still guarantees
                    # nothing reaches the database either way.
                    pass
            else:
                embedding = await embed_query(case.question, settings=settings)
                hits = await semantic_search(
                    session,
                    query=case.question,
                    query_embedding=embedding,
                    filters=DocumentFilters(),
                    top_k=case.k,
                )
                retrieved = [hit.document.id for hit in hits]
            verdicts.append(score_recall(case.name, expected, retrieved, k=case.k))
        return verdicts
```

> **Note on `k` in `--ask` mode.** `score_recall` truncates `retrieved` to `k`.
> Citations are not a ranked list — the model cites what it relied on — so `k`
> here acts as a cap on how many citations count, not as a rank cut. For every
> case except `breadth-many-mentions` that cap is 10 and will not bind. Leave it
> as-is rather than special-casing: a case whose answer cites more than `k`
> documents is telling you something worth seeing.

- [ ] **Step 2: Distinguish the two modes in the report**

`_report_recall` writes `recall-baseline.json`. An `--ask` run must NOT overwrite
a retrieval baseline — the two measure different things and their numbers are
not comparable. Guard it:

```python
    if write_baseline and ask:
        typer.echo("error: --write-baseline records retrieval recall; drop --ask")
        raise typer.Exit(code=1)
```

Put this check at the **top of the command**, before seeding, so a mistaken
invocation costs nothing.

- [ ] **Step 3: Verify the command surface**

```bash
uv run library eval-recall --help
```
Expected: `--only`, `--ask` and `--write-baseline` all listed.

```bash
uv run library eval-recall --ask --write-baseline
```
Expected: exits 1 with the guard message, having seeded nothing.

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add -A
git commit -m "feat(ask): score the Ask loop's own retrieval, not just the retriever's"
```

- [ ] **Step 5: Run it against real credentials**

On a host with both an embedder and Claude credentials
(`LIBRARY_CLAUDE_CONFIG_DIR` pointing at a directory with valid credentials):

```bash
library eval-recall --ask
```

Record the result in the journal entry (Task 8). What to look for, stated in
advance so a surprise is legible: `date-scoped` and `kind-scoped` should do
**better** here than at layer 1, because the model can apply the filters Task 4
gave it and layer 1 cannot. If they do not, the schema descriptions are not
persuading the model to use the filters, and the fix is prompt wording — not
more retrieval work.

**Do NOT gate the branch on this number.** It is a live-model measurement with
real variance; treat a single run as a reading, not a verdict.

---

### Task 7: Contextual chunk headers (closes #6)

The only irreversible change in this plan: it alters what every chunk embeds, so
adopting it means re-embedding the corpus. Tasks 1–3 exist so this can be
measured rather than argued.

**Files:**
- Create: `migrations/versions/0031_chunk_context_header.py`
- Modify: `src/library/models.py` (`DocumentChunk.context_header`)
- Modify: `src/library/jobs.py` (`compose_context_header`, `run_embed`)
- Modify: `src/library/documents_service.py` (`HEADER_FIELDS`, `header_fields_changed`)
- Modify: `src/library/api/documents.py`, `src/library/ask/engine.py` (defer a re-embed)
- Modify: `tests/test_ask_document_write.py` (three signatures — see Step 7b)
- Test: `tests/test_chunk_context_header.py`

**Interfaces:**
- Consumes: `apply_document_update`'s `list[str]` return (Task 7 Step 3 explains
  its exact contents), `library.jobs.embed_document`.
- Produces: `compose_context_header(document: Document) -> str` and
  `header_fields_changed(edited: Sequence[str]) -> bool`.

> **The field-name set is measured, not inferred.** `apply_document_update`
> returns *storage* names for relationship edits but *body* names for scalars,
> because it routes the first through `_EDITED_FIELD_NAMES` and appends the
> second verbatim. Executed against the real database: `sender` →
> `['sender_id']`, `kind_slug` → `['kind_id']`, `title` → `['title']`,
> `document_date` → `['document_date']`, `summary` → `['summary']`. Using the
> body names for all four would silently disable the hook for sender and kind —
> no error, no failing test unless one asserts the behaviour, which Step 1 does.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chunk_context_header.py`:

```python
"""Contextual chunk headers: composition, storage, and the re-embed hook (#6)."""

import hashlib
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from library import jobs
from library.config import get_settings
from library.documents_service import HEADER_FIELDS, header_fields_changed
from library.jobs import compose_context_header
from library.models import EMBEDDING_DIM, Document, DocumentChunk, DocumentSource, Kind, Sender
from library.search import semantic_search
from tests.conftest import fetch_all
from tests.test_documents_api import seed_document

pytestmark = pytest.mark.integration


def _embed_jobs(database_url: str, document_id: int) -> list[tuple[Any, ...]]:
    return fetch_all(
        database_url,
        "SELECT task_name FROM procrastinate_jobs "
        "WHERE task_name = 'library.jobs.embed_document' "
        "AND (args ->> 'document_id')::bigint = :id",
        id=document_id,
    )


# ---- the predicate ----

def test_header_fields_are_the_names_apply_document_update_returns() -> None:
    assert HEADER_FIELDS == {"sender_id", "kind_id", "title", "document_date"}


def test_header_fields_changed() -> None:
    assert header_fields_changed(["sender_id"]) is True
    assert header_fields_changed(["title"]) is True
    assert header_fields_changed(["document_date"]) is True
    assert header_fields_changed(["kind_id"]) is True
    assert header_fields_changed(["summary"]) is False
    assert header_fields_changed(["tags", "projects"]) is False
    assert header_fields_changed([]) is False
    assert header_fields_changed(["summary", "title"]) is True


# ---- the defer hook ----

def test_editing_a_header_field_defers_a_reembed(
    api_client: TestClient, api_database_url: str
) -> None:
    doc_id = seed_document(api_database_url, "hdr-sender", sender_name="Old Name BV")
    assert _embed_jobs(api_database_url, doc_id) == []
    response = api_client.patch(f"/api/documents/{doc_id}", json={"sender": "New Name BV"})
    assert response.status_code == 200, response.text
    assert len(_embed_jobs(api_database_url, doc_id)) == 1


def test_editing_a_non_header_field_defers_nothing(
    api_client: TestClient, api_database_url: str
) -> None:
    doc_id = seed_document(api_database_url, "hdr-summary")
    response = api_client.patch(f"/api/documents/{doc_id}", json={"summary": "changed"})
    assert response.status_code == 200, response.text
    assert _embed_jobs(api_database_url, doc_id) == []


def test_a_patch_that_changes_nothing_defers_nothing(
    api_client: TestClient, api_database_url: str
) -> None:
    doc_id = seed_document(api_database_url, "hdr-noop")
    response = api_client.patch(f"/api/documents/{doc_id}", json={})
    assert response.status_code == 200, response.text
    assert _embed_jobs(api_database_url, doc_id) == []


# ---- composition + storage ----

@pytest.fixture
async def engine(api_database_url: str) -> AsyncIterator[AsyncEngine]:
    e = create_async_engine(api_database_url)
    yield e
    await e.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as s:
        yield s


async def test_compose_header_omits_missing_fields(session: AsyncSession) -> None:
    kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()
    sender = Sender(name="Northwind Energy (fixture)")
    session.add(sender)
    await session.flush()
    full = Document(
        sha256=hashlib.sha256(b"hdr-full").hexdigest(), mime_type="application/pdf",
        source=DocumentSource.UPLOAD, sender=sender, kind=kind,
        document_date=date(2019, 3, 14), title="Jaarafrekening", ocr_text="x")
    bare = Document(
        sha256=hashlib.sha256(b"hdr-bare").hexdigest(), mime_type="application/pdf",
        source=DocumentSource.UPLOAD, ocr_text="x")
    session.add_all([full, bare])
    await session.commit()
    assert compose_context_header(full) == (
        "Northwind Energy (fixture) · 2019-03-14 · utility-bill · Jaarafrekening")
    assert compose_context_header(bare) == ""


async def test_header_is_stored_but_never_shown_to_ask(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The header must reach the embedder and NOT the excerpt."""
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "true")
    get_settings.cache_clear()
    seen: list[str] = []

    async def fake_embed_texts(texts, *, settings, client=None):
        seen.extend(texts)
        return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    monkeypatch.setattr(jobs, "embed_texts", fake_embed_texts)

    kind = (await session.execute(select(Kind).where(Kind.slug == "utility-bill"))).scalar_one()
    sender = Sender(name="Northwind Energy (fixture)")
    session.add(sender)
    await session.flush()
    document = Document(
        sha256=hashlib.sha256(b"hdr-embed").hexdigest(), mime_type="application/pdf",
        source=DocumentSource.UPLOAD, sender=sender, kind=kind,
        document_date=date(2024, 11, 4), title="Annual statement",
        ocr_text="Bedrag 0,00")
    session.add(document)
    await session.commit()

    await jobs.run_embed(session, document)

    assert len(seen) == 1
    assert seen[0].startswith("Northwind Energy (fixture) · 2024-11-04 · utility-bill")
    assert seen[0].endswith("Bedrag 0,00")

    chunk = (await session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id))).scalar_one()
    assert chunk.text == "Bedrag 0,00", "the stored passage must stay raw"
    assert chunk.context_header is not None

    hits = await semantic_search(
        session, query="bedrag", query_embedding=[1.0] + [0.0] * (EMBEDDING_DIM - 1), top_k=5)
    assert hits[0].chunk_text == "Bedrag 0,00", "Ask must not see the header"


async def test_document_with_no_metadata_embeds_bare_text(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty header must not become a leading blank line in the vector."""
    monkeypatch.setenv("LIBRARY_EMBEDDING_ENABLED", "true")
    get_settings.cache_clear()
    seen: list[str] = []

    async def fake_embed_texts(texts, *, settings, client=None):
        seen.extend(texts)
        return [[1.0] + [0.0] * (EMBEDDING_DIM - 1) for _ in texts]

    monkeypatch.setattr(jobs, "embed_texts", fake_embed_texts)
    document = Document(
        sha256=hashlib.sha256(b"hdr-none").hexdigest(), mime_type="application/pdf",
        source=DocumentSource.UPLOAD, ocr_text="just body text")
    session.add(document)
    await session.commit()
    await jobs.run_embed(session, document)
    assert seen == ["just body text"]
    chunk = (await session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id))).scalar_one()
    assert chunk.context_header is None
```

**Measured pre-implementation state**, so you can tell a real failure from a
misconfigured one: all three PATCH tests currently observe `[]` embed jobs. After
this task, `test_editing_a_header_field_defers_a_reembed` must observe exactly
one, and the other two must still observe none.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_chunk_context_header.py -v`
Expected: FAIL — `ImportError: cannot import name 'HEADER_FIELDS'`

- [ ] **Step 3: Add the column to the model**

In `src/library/models.py`, inside `class DocumentChunk`, between `text` and
`embedding`:

```python
    #: The document-identity line prepended to ``text`` before embedding, so a
    #: chunk retrieves on its sender/date/kind/title as well as its content.
    #: Stored separately rather than baked into ``text`` because ``text`` is
    #: also what Ask reads back as an excerpt: with three passages per document
    #: and ten documents per search, a baked-in header would repeat the same
    #: metadata up to thirty times per tool result, duplicating fields the
    #: result rows already carry. NULL for chunks written before this column
    #: existed, and for documents with no metadata at all.
    context_header: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 4: Write the migration**

Create `migrations/versions/0031_chunk_context_header.py`:

```python
"""chunk context header

Adds ``document_chunks.context_header``: the ``sender · date · kind · title``
line prepended to a chunk's text before it is embedded, so a chunk retrieves on
its document's identity as well as its own words (Plan B, finding #6).

Stored in its own column rather than baked into ``text`` because ``text`` is
also what Ask reads back as an excerpt. With ``retrieve_chunks_per_doc = 3`` and
``retrieve_top_k = 10``, a baked-in header would repeat the same metadata up to
thirty times in a single tool result, duplicating fields the result rows already
carry as structured values.

Nullable, with no backfill: existing chunks keep the vectors they were embedded
with, which do NOT include a header. Re-embedding is an operator action
(``library backfill-embeddings --include-existing``), deliberately not a
migration — it calls a network sidecar once per document and would make this
migration unbounded in time and able to fail for reasons unrelated to schema.

Revision ID: 0031
Revises: 0030
Create Date: 2026-08-27 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("context_header", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "context_header")
```

Then **format it** — `ruff` runs over `migrations/` too and a new migration file
is the classic miss:

```bash
uv run ruff format migrations/versions/0031_chunk_context_header.py
uv run alembic upgrade head
```

- [ ] **Step 5: Compose and embed the header**

In `src/library/jobs.py`, add above `async def _record_embed_event(`:

```python
def compose_context_header(document: Document) -> str:
    """The document-identity line prepended to every chunk before embedding.

    ``sender · date · kind · title``, omitting whatever the document lacks. A
    chunk reading only ``Bedrag EUR 0,00`` otherwise carries no trace of who
    sent it, when, or what it is, so a question naming any of those cannot
    match it on meaning (finding #6).

    Returns ``""`` for a document with none of the four — the caller must then
    embed the bare text rather than a leading blank line.

    Safe to read these attributes here: ``sender`` and ``kind`` are
    ``lazy="selectin"`` and ``get_sessionmaker()`` sets ``expire_on_commit=
    False``, so they survive the commits ``_record_embed_event`` performs.
    Verified by execution, not assumed — do not "defensively" re-fetch.
    """
    parts = (
        document.sender.name if document.sender else None,
        document.document_date.isoformat() if document.document_date else None,
        document.kind.slug if document.kind else None,
        document.title,
    )
    return " \u00b7 ".join(part for part in parts if part)
```

Then in `run_embed`, replace the embedding call:

```python
    # The header is embedded WITH each chunk but stored beside it, so retrieval
    # matches on document identity while Ask's excerpt stays the raw passage.
    context_header = compose_context_header(document)
    texts = [text for text, _ in chunk_records] + [text for text, _ in comment_records]
    embed_inputs = [f"{context_header}\n\n{text}" if context_header else text for text in texts]
    try:
        vectors = await embed_texts(embed_inputs, settings=settings)
```

and add `context_header=context_header or None,` to **both** `DocumentChunk(...)`
constructions in that function — the content-chunk loop and the comment-chunk
loop. Comment chunks get the header too: a comment on an energy bill should
retrieve on the sender's name, and its own `User comment (date):` framing stays
in `text` where it already is.

- [ ] **Step 6: Add the predicate**

In `src/library/documents_service.py`, add `from collections.abc import Sequence`
to the imports, then insert above `async def revalidate_after_edit(`:

```python
#: Storage-level field names that appear in a chunk's ``context_header``
#: (see ``jobs.compose_context_header``). An edit touching any of them makes
#: every stored header for that document stale, so it must be re-embedded.
#:
#: The mixed naming is NOT a mistake and must not be "tidied": the list this is
#: compared against is ``apply_document_update``'s return value, which maps
#: ``sender``/``kind_slug`` through ``_EDITED_FIELD_NAMES`` to ``sender_id``/
#: ``kind_id`` but appends plain scalar fields under their own names. Verified
#: by execution: sender -> ['sender_id'], kind_slug -> ['kind_id'],
#: title -> ['title'], document_date -> ['document_date'].
HEADER_FIELDS: frozenset[str] = frozenset({"sender_id", "kind_id", "title", "document_date"})


def header_fields_changed(edited: Sequence[str]) -> bool:
    """Whether an ``apply_document_update`` result touched a header field."""
    return bool(HEADER_FIELDS.intersection(edited))
```

- [ ] **Step 7: Defer the re-embed at both write sites**

There are exactly two callers of `apply_document_update`. Confirm before editing:

```bash
grep -rn "apply_document_update" src/ tests/
```

In `src/library/api/documents.py`, extend the import and add the defer after the
existing `await session.commit()`:

```python
from library.auth.deps import current_user
from library.config import get_settings
from library.db import get_session
from library.documents_service import (
    apply_document_update,
    header_fields_changed,
    revalidate_after_edit,
)
from library.ingest import DeletedDuplicateError, UnsupportedMimeTypeError, ingest_file
from library.jobs import embed_document, extract_document
```

**Import order matters** — `ruff check` enforces isort (`I001`) and will reject
`library.jobs` placed before `library.documents_service`. Run
`uv run ruff check . --fix` and let it sort rather than hand-placing the line.

```python
    # A chunk's context_header embeds the sender/date/kind/title, so editing one
    # makes every stored header for this document stale. Deferred only when a
    # header field actually changed — a summary or tags edit must not wake the
    # embedder. Mirrors how api/comments.py re-embeds on a comment write.
    if header_fields_changed(edited):
        await embed_document.defer_async(document_id=document.id)
```

In `src/library/ask/engine.py`, the same import change, and after that route's
`await session.commit()` in `_run_update_document`:

```python
    # Same reasoning as the PATCH route (api/documents.py): a header-field edit
    # invalidates this document's stored chunk headers.
    if header_fields_changed(edited):
        await embed_document.defer_async(document_id=document_id)
```

Both go **after** the commit, matching `api/comments.py`: the worker must not
pick the job up before the edit is visible.

- [ ] **Step 7b: Fix the three write-tool tests the new defer breaks**

Found by running the FULL suite; the focused ask tests pass without it. Three
tests in `tests/test_ask_document_write.py` drive `_run_update_document`
directly, with no Procrastinate app open, so the new `defer_async` raises
`procrastinate.exceptions.AppNotOpen`. `conftest.py` already has a
`job_connector` fixture that opens an `InMemoryConnector` for exactly this.

Add the import:

```python
from procrastinate.testing import InMemoryConnector
```

and the fixture to these three signatures:

- `test_update_tool_commit_writes_with_ask_provenance`
- `test_update_tool_revalidates_and_clears_finding`
- `test_engine_confirm_after_prior_turn_preview_writes`

```python
async def test_update_tool_commit_writes_with_ask_provenance(
    api_database_url: str, job_connector: InMemoryConnector
) -> None:
```

Verified: `uv run pytest tests/test_ask_document_write.py -q` → **16 passed**.

> **A judgement call left to the implementer.** Taking the fixture keeps the
> Ask write tool consistent with `api/comments.py`, which also defers bare. The
> alternative is making the defer best-effort (`jobs._defer_best_effort` is the
> existing primitive), so a queue outage cannot turn an already-committed edit
> into a failed Ask turn. The fixture is prescribed because it matches shipped
> precedent; if you think the best-effort case is stronger, make it — but change
> `api/comments.py` too rather than leaving two conventions.

- [ ] **Step 8: Run the tests**

```bash
uv run pytest tests/test_chunk_context_header.py -v
```
Expected: PASS (8 tests).

- [ ] **Step 9: Run the FULL suite**

```bash
uv run coverage run -m pytest && uv run coverage report
```
The model gained a column and two routes gained a side effect; a focused run
will not catch what that disturbs.

- [ ] **Step 10: Lint and commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
uv run python scripts/check_docs.py
git add -A
git commit -m "feat(ask): embed each chunk with its document's identity"
```

- [ ] **Step 11: Measure it — this is what the eval was built for**

Re-embed the corpus and re-run layer 1 on a host with an embedder:

```bash
library eval-recall
```

`sender-named-bare-chunk` FAILED at the Task 3 baseline by construction — its
body names neither its sender nor its year. It should now PASS. Report the mean
against `recall-baseline.json`.

Then decide, and write the decision into the journal entry either way:

- **Recall improved** → adopt. Re-embed the deployed archive with
  `library backfill-embeddings --include-existing` (the worker must be running;
  this is one embedder call per document, so run it deliberately, not casually).
- **Recall did not improve** → do **not** re-embed the archive, and say so in
  `docs/ask.md` §1.10. The code can stay — new documents get headers at ingest
  and cost nothing — but a corpus-wide re-embed is not justified by a number
  that did not move. This is a legitimate outcome, not a failure of the task.

---

### Task 8: Documentation and the journal entry

`docs/ask.md` declares `**Covers:** src/library/ask/`, so it is inside
`check_docs`'s stale-covered-code rule and this branch has changed that
directory substantially. But note the rule's blind spot: it compares timestamps,
not content. **A clean `check_docs` run is not evidence a doc is current** — the
edits below are required on their merits, not to make a gate green.

**Files:**
- Modify: `docs/ask.md`
- Create: `journal/260827-retrieval-reach.md`

- [ ] **Step 1: Retire the limitation this branch closed**

`docs/ask.md` §1.10 item 6 currently reads:

> 6. `semantic_search` takes no metadata filters — only `query_documents` and
>    `compare_to_series` do. A content question scoped to a year or a sender must
>    search the whole archive and rely on ranking.

Delete it and renumber the items after it. **Check the renumbering against
inbound references** — §1.10 items are cited by number elsewhere:

```bash
grep -rn "§1.10" docs/ src/ frontend/src/
```

- [ ] **Step 2: Add the limitations this branch created**

Append to §1.10 (numbered after the existing items):

§1.10 currently has **10** items. Deleting item 6 (Step 1) leaves 9, so these
become items **10** and **11** — verify that count before writing the numbers,
since Plans C and D may have added their own by then.

```markdown
10. **Chunk context headers reflect metadata as of the last embed.** A chunk
   embeds a `sender · date · kind · title` line alongside its text. Editing one
   of those four fields defers a re-embed, so the header self-heals — but chunks
   written before migration `0031` carry no header at all until
   `library backfill-embeddings --include-existing` is run, and until then a
   question naming a sender cannot match those documents on metadata. Structured
   filters are unaffected: they read live metadata.
11. **`semantic_search`'s `matched` counts documents, not passages.** A
   document matching the filters but carrying no chunks is counted in `matched`
   and reported in `unembedded`, but is unreachable by vector search. That is
   the honest reading of finding #14, not a fix for it: there is still no UI
   listing documents missing from the index.
```

- [ ] **Step 3: Document the tool's new surface in §1.2**

Under the `semantic_search` description, record that it accepts the same
`_FILTER_PROPERTIES` as the structured tools, that it does **not** accept
`review_status` (with the reason — a filter is only offered to a tool that can
report what it removed), that `top_k` is clamped to
`[1, ask_search_max_top_k]`, and what the `coverage` block's three keys mean.

- [ ] **Step 4: Add a "Measuring recall" subsection**

Add beside the existing "Measuring disclosure: `library eval-disclosure`"
subsection (§1.2), covering: what `library eval-recall` does; that layer 1 needs
only the embedder and layer 2 (`--ask`) additionally needs Claude credentials;
that it runs nightly via `e2e-nightly.yml` and why it is not a merge gate; that
the corpus is synthetic and public-repo-safe; and — importantly — **the
acceptance criterion the corpus is held to** (baseline mean recall@10 below 0.90,
or the corpus is too easy to measure anything).

Record the actual baseline numbers from Task 3 Step 8 and Task 7 Step 11 here.

- [ ] **Step 5: Write the journal entry**

Create `journal/260827-retrieval-reach.md`. H1 is a clean title with no date or
number (repo convention). Cover:

- What shipped per finding (#5, #6, #7, #15-recall).
- **The measured numbers**: baseline mean recall, the per-case table, and the
  before/after for `sender-named-bare-chunk` — the case #6 was justified by.
- **The decision taken at Task 7 Step 11**, including if it was "do not
  re-embed the archive". A negative result recorded is worth more than a
  positive result assumed.
- The three defects this plan's probes caught before implementation: the
  negative-`top_k` slice, `matched` overcounting unembedded documents, and the
  mixed storage/body field naming in `apply_document_update`'s return.

- [ ] **Step 6: Final verification — run every gate**

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run python scripts/check_docs.py
uv run coverage run -m pytest && uv run coverage report
```

If `check_docs` fails, determine whether it predates this branch by comparing
against **the base branch, never `git stash`** (stash cannot reveal a violation
introduced by an earlier commit on this branch):

```bash
git worktree add /tmp/base main && (cd /tmp/base && uv run python scripts/check_docs.py)
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "docs(ask): record retrieval reach, its limits, and the recall eval"
```

- [ ] **Step 8: Open the PR — expect it to be blocked**

`main`'s ruleset has `require_extra_approval_for_unattributed_changes`, which
blocks every agent-authored PR, and the solo owner cannot self-approve. **Ask
the user explicitly** before using `--admin`; do not merge on your own judgement.
If a stale failing `ci-gate` check-run sits on the SHA, fix it with a **new
commit**, not a force-push.

Before merging, confirm CI's `promote` job actually succeeded — `gh run watch`
can exit 0 while the run is still in progress.

---

## Self-review

Checked after writing, against spec §8.

**Spec coverage.** §8.1 build order → task order 1–3, 4–5, 6, 7. §8.2 → Task 4
Steps 5. §8.3 → Task 4 Steps 4–5. §8.4 → Task 5. §8.5 → Task 7. §8.6 → Tasks 1,
2, 3, 6. §8.7 → the probe table in this plan's header, and the honesty note in
Task 3. No spec section is unimplemented.

**Verified by execution while writing this plan** (spec §8.7's rule applied to
the plan itself):

| Prescribed content | Result |
|---|---|
| Task 1 test + implementation, run verbatim | 8 passed |
| Task 2 corpus + 7 structural tests | 7 passed; 53 docs, 6 cases, longest body 455 chars |
| Task 3 `_seed_corpus` mechanics (fake embedder) | 8 docs → 8 chunks, one per document |
| Task 4/5 tests against real Postgres | 5 passed; `top_k=-1` returns 1, not 6 |
| Task 7 all 8 tests | 8 passed; defer fires on `sender`, not on `summary`/no-op |
| Full backend suite with Tasks 4+5 applied | 1799 passed, **1 failed** → `.env.example` gate, now Task 5 Step 3b |
| Full backend suite with ALL tasks applied | 1797 passed, **3 failed** → `AppNotOpen`, now Task 7 Step 7b |
| `ruff check` + `mypy` on the prescribed code | **2 defects**: isort `I001`, and `int(object)` rejected twice — both corrected in place |
| Full suite + all gates after those fixes | **1800 passed, 0 failed**; `ruff`, `mypy`, `check_docs` all clean |

**Not verified, and flagged in place:** anything needing real bge-m3 vectors —
the baseline recall numbers (Task 3 Step 8) and the #6 delta (Task 7 Step 11).
No arm64 TEI image exists, so these run on the host or in the nightly workflow.

**Type consistency.** `SearchReach(matched, unembedded)` is constructed in
`search_reach` (Task 4), stubbed in `tests/test_api_ask.py` (Task 4 Step 1) and
read in `_run_semantic_search` (Task 4). `RecallVerdict` fields are produced by
`score_recall` (Task 1) and read by `_report_recall` (Task 3) — `.case`,
`.passed`, `.recall`, `.k`, `.missed`, `.retrieved` all defined in Task 1.
`_seed_corpus` returns `dict[str, int]`, consumed by Tasks 3 and 6.
`header_fields_changed(Sequence[str]) -> bool` is defined in Task 7 Step 6 and
called in Step 7 at both sites.

**Known ordering hazard.** Task 4 and Task 5 both edit `_run_semantic_search`
and the same tool schema. Task 4's prescribed code block already contains
Task 5's `top_k` key and `_top_k_arg` call, because it was captured from a tree
with both applied. Task 4 Step 5 says so explicitly. If the tasks are done in
order this is harmless; if Task 4 is done alone, omit the `top_k` property and
leave `top_k=settings.retrieve_top_k`.
