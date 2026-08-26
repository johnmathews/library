# Series Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `compare_to_series` the same coverage reporting `query_documents` already has, so no Ask answer states an aggregate without disclosing what it left out — completing the spec's Group A goal.

**Architecture:** `_load_members` and `summarize_series` currently discard three sets of documents silently. A `SeriesCoverage` object records those drops as they happen, rides on `SeriesSummary`, and is serialised into the Ask tool result. The system prompt's existing disclosure rule is widened from `query_documents` to any tool result carrying a `coverage` block.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async + PostgreSQL, pytest (`pytest.mark.integration`), Anthropic Python SDK, ruff, mypy, Vue 3 + TypeScript (one optional interface field).

**Spec:** `docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md` — §3 Group A ("No Ask answer states an aggregate without the model knowing, and being required to disclose, how much of the filtered set it actually covers and how much of it is flagged"). This plan closes the part of that goal the answer-trustworthiness branch left open.

## Global Constraints

- Python 3.13. Full type annotations on every new signature and module-level name.
- `uv` for everything: `uv run pytest`, `uv run ruff`, `uv run mypy`. Never bare `pytest`/`pip`.
- CI runs `ruff check` AND `ruff format --check` over the **whole repo**, migrations included. Run `uv run ruff format .` before every commit.
- `src/library/ask/engine.py` and `src/library/structured_query.py` are out of the mypy quarantine and must stay clean. `src/library/series.py` must not gain new errors.
- Backend DB tests are `pytest.mark.integration`; `api_database_url` is **session-scoped**, so scope every list assertion to a marker unique to the test.
- Implementers run ONLY their focused suite. The controller runs the full `uv run pytest`. Never background a test run.
- This repo is **PUBLIC**. No real sender name, amount, policy number, or address in any test, doc, comment, commit message, or PR body. Invented values only.
- Commit style: Conventional Commits, ending every body with:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018bMe5zwdaLxpwhc3Swyjsj
```

---

## Design Validation (done before this plan was written)

The counting logic below was **prototyped against the real test Postgres** before being written into this plan. Seeded: 3 documents in the dominant `(sender, kind)` group in EUR (one flagged `needs_review`), 1 same-group USD, 1 in a different `(sender, kind)` group, 1 with no amount. Result:

```
SeriesCoverage(matched=6, included=3,
               excluded={'no_amount': 1, 'other_series_group': 1, 'other_currency': 1},
               needs_review=1)
PARTITION HOLDS
```

`included + sum(excluded.values()) == matched` verified. Also verified structurally:

- `_load_members` has exactly **one** caller (`summarize_series`), so changing its return type has a one-function blast radius.
- `serialise_summary` has 11 call sites (Ask, `api/charts.py`, `api/documents.py`); adding a key is additive.
- No test asserts an exact `serialise_summary` key set (only `test_healthz.py` does exact-key assertions, unrelated).
- The frontend `DocumentSeries` interface already uses optional fields for later additions, so `coverage?:` follows the established pattern.

## Why this plan exists separately

The answer-trustworthiness branch (PR #96) delivered coverage for `query_documents` and explicitly did **not** deliver it for `compare_to_series`. It removed the `review_status` filter that tool could not report on — stopping the bleeding — but `summarize_series` still drops three sets of documents with no denominator. The spec's Group A goal is not met until this lands.

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/library/series.py` | Series detection, statistics, and now its own coverage | Modify |
| `src/library/ask/engine.py` | `compare_to_series` tool description; the prompt's disclosure rule | Modify |
| `tests/test_series_db.py` | DB-backed series behaviour (has `engine`/`session` fixtures, `_sender`, `seed`, `_settings` helpers) | Modify |
| `tests/test_api_ask.py` | Prompt/tool-description pins | Modify |
| `frontend/src/api/documents.ts` | `DocumentSeries` interface | Modify |
| `docs/ask.md`, `journal/` | Documentation | Modify/Create |

`SeriesCoverage` lives in `series.py` beside `SeriesSummary`: it is assembled from state only the series pipeline holds (which group was dominant, which currency bucket won), and no other module can compute it.

---

### Task 1: `SeriesCoverage`, and `_load_members` reports its drops

**Files:**
- Modify: `src/library/series.py` (new dataclass near `SeriesSummary` ~line 330; `_load_members` ~line 373)
- Test: `tests/test_series_db.py`

**Interfaces:**
- Consumes: `library.search.DocumentFilters`, `filter_conditions`, `library.models.ReviewStatus` (add to the existing models import if absent).
- Produces:
  - `SeriesCoverage` — frozen slotted dataclass: `matched: int`, `included: int`, `excluded: dict[str, int]`, `needs_review: int`.
  - `_load_members(session, filters) -> tuple[list[_Member], int, int]` returning `(members, no_amount_count, other_group_count)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_series_db.py`:

```python
async def test_load_members_reports_amountless_and_non_dominant_drops(
    session: AsyncSession,
) -> None:
    """_load_members silently discarded two sets of documents. It now counts them."""
    alpha = await _sender(session, "AlphaEnergy")
    beta = await _sender(session, "BetaEnergy")
    await _seed(session, "lm1", sender=alpha, kind_slug="utility-bill", amount="100.00")
    await _seed(session, "lm2", sender=alpha, kind_slug="utility-bill", amount="110.00")
    await _seed(session, "lm3", sender=beta, kind_slug="utility-bill", amount="200.00")
    await _seed(session, "lm4", sender=alpha, kind_slug="utility-bill")  # no amount

    members, no_amount, other_group = await _load_members(
        session, DocumentFilters(kind_slug="utility-bill")
    )

    assert [m.amount for m in members] == [Decimal("100.00"), Decimal("110.00")]
    assert no_amount == 1
    assert other_group == 1
```

`tests/test_series_db.py` already defines `_sender(session, name)` and `seed(...)`; read their exact signatures and use them rather than adding new helpers. The examples below write `_seed(...)` generically — substitute the real `seed(...)` signature.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_series_db.py -k load_members_reports -v
```

Expected: FAIL — `ValueError: too many values to unpack` or `ImportError`, because `_load_members` still returns a bare list.

- [ ] **Step 3: Write the implementation**

Add beside `SeriesSummary` in `src/library/series.py`:

```python
@dataclass(frozen=True, slots=True)
class SeriesCoverage:
    """How much of the filtered set a series summary's statistics account for.

    ``summarize_series`` narrows aggressively and used to do it silently: it
    drops documents with no amount, then every ``(sender_id, kind_id)`` group
    but the most populous, then every currency bucket but the dominant one.
    Each narrowing is deliberate — a series must be one provider's one kind of
    document in one currency — but the result was a "usual" band computed over
    an unknown fraction of what the caller asked about.

    ``matched`` is every non-deleted document meeting the caller's filters,
    ``included`` is what the statistics were computed from, and ``excluded``
    maps a reason to a count. ``included + sum(excluded.values()) == matched``
    is an invariant, pinned by a test. Reasons that dropped nothing are
    omitted, so an empty ``excluded`` reads as "the statistics cover
    everything that matched".

    ``needs_review`` counts documents *inside* ``included`` whose extracted
    metadata the validator flagged — most often an ``amount_grounding``
    finding, meaning an amount in this very distribution does not appear in
    its document's text.
    """

    matched: int
    included: int
    excluded: dict[str, int]
    needs_review: int
```

Then replace `_load_members`:

```python
async def _load_members(
    session: AsyncSession, filters: DocumentFilters
) -> tuple[list[_Member], int, int]:
    """Members of the dominant series matching ``filters``, plus what was dropped.

    Returns ``(members, no_amount_count, other_group_count)``. The two counts
    are the documents this function discards: those with no ``amount_total``
    (they cannot contribute a data point) and those in a non-dominant
    ``(sender_id, kind_id)`` group (a loosely-filtered query must not mix two
    providers into one series). The caller folds them into
    :class:`SeriesCoverage`; returning them rather than logging them is what
    lets an answer say how much of the archive its "usual" band covers.
    """
    statement = (
        select(
            Document.id,
            Sender.name,
            Kind.slug,
            Document.document_date,
            Document.amount_total,
            Document.currency,
            Document.sender_id,
            Document.kind_id,
            Document.title,
        )
        .outerjoin(Sender, Document.sender_id == Sender.id)
        .outerjoin(Kind, Document.kind_id == Kind.id)
        .where(*filter_conditions(filters), Document.amount_total.isnot(None))
    )
    rows = (await session.execute(statement)).all()

    # Counted with its own aggregate rather than inferred: the statement above
    # already excludes amountless documents, so they are not in `rows` to count.
    no_amount = int(
        (
            await session.execute(
                select(func.count(Document.id)).where(
                    *filter_conditions(filters), Document.amount_total.is_(None)
                )
            )
        ).scalar_one()
    )

    # Restrict to the single most-populous (sender_id, kind_id) group so a
    # loosely-filtered query (kind only) can't mix providers into one series.
    groups: dict[tuple[int | None, int | None], list[_Member]] = {}
    for did, sname, kslug, ddate, amount, currency, sid, kid, title in rows:
        groups.setdefault((sid, kid), []).append(
            _Member(did, sname, kslug, ddate, amount, currency, sid, kid, title)
        )
    if not groups:
        return [], no_amount, 0
    dominant = max(groups.values(), key=len)
    other_group = sum(len(group) for group in groups.values() if group is not dominant)
    return dominant, no_amount, other_group
```

`func` is already imported in `series.py`; confirm before adding an import.

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/test_series_db.py -k load_members_reports -v
```

Expected: PASS. Other tests in the file may now fail on the changed return type — that is Task 2's scope; note them, do not fix them here.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/series.py
git add src/library/series.py tests/test_series_db.py
git commit -m "feat(series): count the documents _load_members discards"
```

---

### Task 2: `summarize_series` assembles coverage onto `SeriesSummary`

**Files:**
- Modify: `src/library/series.py` (`SeriesSummary` ~line 330, `_insufficient` ~line 527, `summarize_series` ~line 688)
- Test: `tests/test_series_db.py`

**Interfaces:**
- Consumes: `SeriesCoverage`, the new `_load_members` tuple (Task 1).
- Produces:
  - `SeriesSummary.coverage: SeriesCoverage | None = None` — a new **optional, defaulted** field, so `summarize_authored_series` and every other constructor keep working untouched.
  - `_insufficient(members, coverage=None) -> SeriesSummary`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_series_db.py`:

```python
async def test_summarize_series_reports_all_three_drops(session: AsyncSession) -> None:
    """The partition invariant, across every way a series narrows."""
    alpha = await _sender(session, "AlphaEnergy")
    beta = await _sender(session, "BetaEnergy")
    for index, amount in enumerate(["100.00", "110.00", "120.00"]):
        await _seed(
            session, f"sc{index}", sender=alpha, kind_slug="utility-bill",
            amount=amount, currency="EUR",
        )
    await _seed(
        session, "sc-usd", sender=alpha, kind_slug="utility-bill",
        amount="90.00", currency="USD",
    )
    await _seed(
        session, "sc-beta", sender=beta, kind_slug="utility-bill",
        amount="200.00", currency="EUR",
    )
    await _seed(session, "sc-none", sender=alpha, kind_slug="utility-bill")

    summary = await summarize_series(
        session, filters=DocumentFilters(kind_slug="utility-bill"),
        settings=get_settings(),
    )

    assert summary.coverage is not None
    assert summary.coverage.matched == 6
    assert summary.coverage.included == 3
    assert summary.coverage.excluded == {
        "no_amount": 1,
        "other_series_group": 1,
        "other_currency": 1,
    }
    assert (
        summary.coverage.included + sum(summary.coverage.excluded.values())
        == summary.coverage.matched
    )


async def test_summarize_series_coverage_flags_untrusted_members(
    session: AsyncSession,
) -> None:
    """A distribution built partly on amounts the validator could not ground."""
    alpha = await _sender(session, "AlphaEnergy")
    ids = []
    for index, amount in enumerate(["100.00", "110.00", "120.00"]):
        ids.append(
            await _seed(
                session, f"cf{index}", sender=alpha, kind_slug="utility-bill",
                amount=amount, currency="EUR",
            )
        )
    document = await session.get(Document, ids[0])
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    summary = await summarize_series(
        session, filters=DocumentFilters(kind_slug="utility-bill"),
        settings=get_settings(),
    )

    assert summary.coverage is not None
    assert summary.coverage.needs_review == 1


async def test_insufficient_series_still_reports_coverage(session: AsyncSession) -> None:
    """The case where coverage matters MOST: too few documents to summarise, but
    the caller cannot tell whether that is because the archive is thin or
    because the series narrowed away most of what matched."""
    alpha = await _sender(session, "AlphaEnergy")
    beta = await _sender(session, "BetaEnergy")
    await _seed(
        session, "ins1", sender=alpha, kind_slug="utility-bill",
        amount="100.00", currency="EUR",
    )
    await _seed(
        session, "ins2", sender=beta, kind_slug="utility-bill",
        amount="200.00", currency="EUR",
    )

    summary = await summarize_series(
        session, filters=DocumentFilters(kind_slug="utility-bill"),
        settings=get_settings(),
    )

    assert summary.status == "insufficient"
    assert summary.coverage is not None
    assert summary.coverage.matched == 2
```

Note: `tests/test_series.py` is a PURE UNIT file (`_member(...)`/`_dist(...)` builders, no DB session) — do NOT put DB-backed tests there. `tests/test_series_db.py` is the DB file and already defines `engine`, `session`, `_sender(session, name)`, `seed(...)` and `_settings()`; use those exact helpers rather than adding new ones.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_series_db.py -v
```

Expected: the three new tests fail with `AttributeError: 'SeriesSummary' object has no attribute 'coverage'`.

- [ ] **Step 3: Write the implementation**

Add the field to `SeriesSummary`, after `description` and before the other defaulted fields:

```python
    # Present for emergent series (``summarize_series``); None for authored
    # series, whose narrowing rules differ and are not reported yet. Absent
    # therefore means "not reported", never "nothing was dropped" — an empty
    # `excluded` inside a present coverage is what means the latter.
    coverage: SeriesCoverage | None = None
```

Change `_insufficient` to carry coverage through:

```python
def _insufficient(
    members: list[_Member], coverage: SeriesCoverage | None = None
) -> SeriesSummary:
```

and pass `coverage=coverage` in its `SeriesSummary(...)` construction. An insufficient result is exactly where the caller most needs to know how much was narrowed away, so it must not drop the block.

In `summarize_series`, take the new tuple and build coverage. Replace the opening of the function:

```python
    members, no_amount, other_group = await _load_members(session, filters)
    matched = len(members) + no_amount + other_group

    def _coverage(bucket: list[_Member], other_currency: int) -> SeriesCoverage:
        """Assemble the block from this call's three narrowings.

        ``matched`` is closed over: it is the pre-narrowing total, so the
        partition holds regardless of which bucket won.
        """
        excluded = {
            reason: count
            for reason, count in (
                ("no_amount", no_amount),
                ("other_series_group", other_group),
                ("other_currency", other_currency),
            )
            if count > 0
        }
        return SeriesCoverage(
            matched=matched,
            included=len(bucket),
            excluded=excluded,
            needs_review=sum(
                1 for member in bucket if member.review_status is ReviewStatus.NEEDS_REVIEW
            ),
        )

    if len(members) < settings.series_min_documents:
        return _insufficient(members, _coverage(members, 0))
```

After the currency bucket is chosen and `other_currencies` computed, add:

```python
    other_currency = sum(
        len(group) for code, group in by_currency.items() if code != currency
    )
```

Then pass `coverage=_coverage(bucket, other_currency)` into both the second `_insufficient(...)` return and the final `_summarize_members(...)` call. `_summarize_members` needs a `coverage: SeriesCoverage | None = None` keyword forwarded onto the `SeriesSummary` it builds.

**`_Member` needs `review_status`.** It is a plain dataclass built from the `_load_members` query — add `review_status: ReviewStatus` to it, select `Document.review_status` in that statement, and thread it through the `_Member(...)` construction. Check `_load_authored_members` too: it builds `_Member` as well, so it must supply the new field or the constructor call breaks.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_series_db.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/series.py
git add src/library/series.py tests/test_series_db.py
git commit -m "feat(series): report coverage for the documents a series narrows away"
```

---

### Task 3: Coverage reaches the model, and the disclosure rule widens

**Files:**
- Modify: `src/library/series.py` (`serialise_summary` ~line 1107)
- Modify: `src/library/ask/engine.py` (the `compare_to_series` tool description; `ASK_SYSTEM_PROMPT_TEMPLATE`)
- Modify: `frontend/src/api/documents.ts` (`DocumentSeries` interface)
- Test: `tests/test_series_db.py`, `tests/test_api_ask.py`

**Interfaces:**
- Consumes: `SeriesSummary.coverage` (Task 2).
- Produces: `serialise_summary` emits a `coverage` key when `summary.coverage is not None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_series_db.py`:

```python
async def test_serialise_summary_emits_coverage(session: AsyncSession) -> None:
    """Coverage has to survive serialisation or the model never sees it."""
    alpha = await _sender(session, "AlphaEnergy")
    for index, amount in enumerate(["100.00", "110.00", "120.00"]):
        await _seed(
            session, f"ss{index}", sender=alpha, kind_slug="utility-bill",
            amount=amount, currency="EUR",
        )
    await _seed(session, "ss-none", sender=alpha, kind_slug="utility-bill")

    body = serialise_summary(
        await summarize_series(
            session, filters=DocumentFilters(kind_slug="utility-bill"),
            settings=get_settings(),
        )
    )

    assert body["coverage"] == {
        "matched": 4,
        "included": 3,
        "excluded": {"no_amount": 1},
        "needs_review": 0,
    }
```

Append to `tests/test_api_ask.py`:

```python
def test_compare_to_series_tool_description_explains_coverage() -> None:
    from library.ask.engine import TOOLS

    tool = next(tool for tool in TOOLS if tool["name"] == "compare_to_series")
    assert "coverage" in tool["description"]


def test_disclosure_rule_is_not_scoped_to_one_tool() -> None:
    """The rule used to name query_documents alone, which left compare_to_series
    uncovered even once it reported coverage."""
    from library.ask.engine import ASK_SYSTEM_PROMPT_TEMPLATE

    rules = ASK_SYSTEM_PROMPT_TEMPLATE.split("Rules:")[1]
    disclosure = next(
        line for line in rules.splitlines() if "coverage" in line
    )
    assert "query_documents results carry" not in disclosure
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_series_db.py -k serialise_summary_emits tests/test_api_ask.py -k "compare_to_series_tool_description or disclosure_rule" -v
```

Expected: `KeyError: 'coverage'` and two `AssertionError`s.

- [ ] **Step 3: Write the implementation**

In `serialise_summary`, after the `body` literal:

```python
    if summary.coverage is not None:
        body["coverage"] = asdict(summary.coverage)
```

`asdict` is NOT currently imported in `series.py` (line 17 imports only `dataclass, field`) — add it. `func` IS already imported (line 22), so Task 1 needs no import change there.

In `ask/engine.py`, append to the `compare_to_series` tool description:

```python
            "The result carries a `coverage` block on the same terms as "
            "query_documents: a series is deliberately narrowed to one sender, "
            "one kind and one currency, so `excluded` reports the documents "
            "that narrowing removed — `no_amount`, `other_series_group`, "
            "`other_currency`. A 'usual' band computed over 3 of 11 matching "
            "documents is not a fact about all 11. "
```

Then widen the prompt's disclosure bullet so it is not scoped to one tool. Replace its opening clause `- query_documents results carry a "coverage" block.` with:

```
- Some tool results carry a "coverage" block (query_documents and
  compare_to_series).
```

leaving the rest of that bullet — the `MUST` obligations for `excluded` and
`needs_review`, and the ban on dropping flagged documents — exactly as it is.

In `frontend/src/api/documents.ts`, add to the `DocumentSeries` interface:

```typescript
  /** How much of the filtered set the statistics cover; absent for authored
   *  series, which do not report it. `excluded` maps a reason to a count. */
  coverage?: {
    matched: number
    included: number
    excluded: Record<string, number>
    needs_review: number
  }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_series_db.py tests/test_api_ask.py tests/test_ask_tool_filters.py -q
cd frontend && npx vue-tsc --noEmit; cd ..
```

Expected: backend tests pass; the frontend type-checks (the new field is optional, so no call site needs to change).

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/series.py src/library/ask/engine.py
git add src/library/series.py src/library/ask/engine.py frontend/src/api/documents.ts tests/test_series_db.py tests/test_api_ask.py
git commit -m "feat(ask): compare_to_series reports what the series narrowed away"
```

---

### Task 4: Documentation

**Files:**
- Modify: `docs/ask.md` (stamp lines; §1.2 coverage subsection; §1.7 series section; §1.10)
- Create: `journal/<yymmdd>-series-coverage.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Verify against shipped code before writing**

Read `src/library/series.py` and `src/library/ask/engine.py` as they now stand and confirm the reason strings, field names, and prompt wording. **Do not transcribe this plan's prose** — the answer-trustworthiness plan was wrong seven times in exactly this way, and its docs task caught two of those by checking the code first. Correct anything that disagrees, and list every correction in your report.

- [ ] **Step 2: Extend `docs/ask.md` §1.2's coverage subsection**

The existing subsection documents `query_documents` coverage. Widen it to say `compare_to_series` also carries the block, and add its three reasons — `no_amount`, `other_series_group`, `other_currency` — to the per-aggregate reason list, with one clause each on what the narrowing is for.

- [ ] **Step 3: Update §1.7 (Document series) and §1.10**

In §1.7, add a short paragraph: the series is deliberately narrowed to one `(sender, kind, currency)` triple, and that narrowing is now reported rather than silent.

In §1.10 there is **no** item asserting that `compare_to_series` lacks coverage — verified: the list has 8 items and none makes that claim, so there is nothing to remove. Item 6 mentions `compare_to_series` only in the context of `semantic_search` filters, and item 7 says coverage is honest about documents not periods; both remain true. Leave §1.10 alone unless your code reading turns up a genuinely new limitation.

- [ ] **Step 4: Re-stamp the doc honestly**

Prepend today's entry to `**Last updated:**`, preserving every existing `Earlier (...)` clause verbatim. Rewrite `**Last verified:**` to state exactly what you checked and what you did not — in particular, whether the disclosure rule's effect on real answer wording was measured (it was not; there is still no answer-quality eval).

```bash
uv run python scripts/check_docs.py
```

Must exit 0. If `src/library/series.py` crosses the 400-line module-map floor and is not already in `docs/architecture.md`'s map, add the row — the answer-trustworthiness branch hit exactly this and it broke CI.

- [ ] **Step 5: Write the journal entry, rebuild its index, commit**

Create `journal/<yymmdd>-series-coverage.md` (`yymmdd` = today) with a clean H1 carrying no date or number. Record what changed, why, and the decisions — particularly that `coverage` is optional on `SeriesSummary` so authored series are honestly marked "not reported" rather than falsely marked "nothing dropped".

The journal index is generated and its staleness is a CI gate:

```bash
uv run python scripts/build_journal_index.py
uv run python scripts/build_journal_index.py --check   # must exit 0
uv run pytest tests/test_build_journal_index.py -q
```

```bash
uv run ruff format . && uv run ruff check .
git add docs/ask.md journal/ docs/architecture.md
git commit -m "docs(ask): series coverage"
```

---

## Verification Checklist

- [ ] `uv run pytest` — full backend suite green (controller runs this, not the implementer)
- [ ] `uv run ruff format --check . && uv run ruff check .` — whole repo
- [ ] `uv run mypy src/library` — no new errors
- [ ] `uv run python scripts/check_docs.py` — exit 0
- [ ] `uv run python scripts/build_journal_index.py --check` — exit 0
- [ ] `cd frontend && npx vue-tsc --noEmit` — the new optional field type-checks
- [ ] Manually: ask the deployed instance "is my latest electricity bill higher than usual?" for a provider with a mixed-currency or multi-provider history, and confirm the answer names what was narrowed away. This is the only end-to-end check of the disclosure rule — no automated test exercises real model wording.

## Self-Review Notes

- **Spec coverage:** spec §3 Group A's unmet half → Tasks 1-3. The `docs/ask.md` §1.10 item asserting the gap is removed in Task 4.
- **Type consistency:** `SeriesCoverage` is defined in Task 1 and used under that exact name in Tasks 2 and 3. `_load_members`' three-tuple is produced in Task 1 and consumed only in Task 2 (verified: one caller).
- **The riskiest change is `_Member` gaining `review_status`** — it is constructed in two places (`_load_members` and `_load_authored_members`), and missing the second breaks the authored/charts path. Task 2 Step 3 names both explicitly.
- **Deliberately NOT in scope:** coverage for authored series / Smart Groups. Their narrowing rules differ (membership is learned, not derived), `summarize_authored_series` is not reachable from `compare_to_series`, and `coverage=None` states honestly that it is unreported. Widening to them is separate work.
