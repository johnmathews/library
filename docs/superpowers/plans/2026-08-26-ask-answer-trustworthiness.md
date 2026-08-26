# Ask Answer Trustworthiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every `/ask` answer disclose how much of the archive its numbers actually cover, how much of that the archive distrusts, and cite only the documents it truly used.

**Architecture:** Every `query_documents` aggregate gains a uniform `Coverage` object (`matched` / `included` / `excluded{reason: count}` / `needs_review`) computed in one extra SQL round-trip per call. The Ask tool result carries it verbatim, and the system prompt makes disclosure mandatory. Separately, `review_status` becomes a filter the model can set and a field it can see, and the citation fallback stops firing on the no-answer path.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async + PostgreSQL (`FILTER (WHERE ...)` conditional aggregates), Pydantic v2, pytest (`pytest.mark.integration`), Anthropic Python SDK, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md` (this plan implements §2.1 — findings #1, #2, #3, #11 — and §6)

## Global Constraints

- Python 3.13. Full type annotations on every new function signature and every new module-level name.
- `uv` for all dependency and script running: `uv run pytest ...`, `uv run ruff ...`, `uv run mypy ...`. Never bare `pytest`/`pip`.
- CI runs `ruff check` and `ruff format --check` over the **whole repo**, migrations included. Run `uv run ruff format .` before every commit.
- `mypy` is gated with a bounded quarantine list; `src/library/ask/engine.py` is **out** of quarantine (see commit `e0fa061`). Any change to it must stay mypy-clean.
- Backend tests that touch the database are `pytest.mark.integration` and need the test Postgres up. The `api_database_url` fixture is **session-scoped** — scope every list assertion to a marker unique to the test, never assert on a global count.
- Default list limits are 25 in the API and 50 in `structured_query`; `GET /api/documents` 422s above `limit=100`. None of that changes here.
- Run the **full** backend suite before the final commit, not just the touched files.
- Docs in `docs/` carry a stamp in the first lines (`**Status:** … **Last updated:** …` / `**Last verified:** … — method: …`) enforced by `scripts/check_docs.py`. A doc edited without re-stamping fails CI.
- This repo is **public**. Never put a real sender name, policy number, amount, or address into a test fixture, doc, commit message, or PR body. Invented names only (`Vattenfall`/`Eneco` already appear in the existing test corpus and are fine as generic utility names; never pair one with a real amount from the live archive).
- Commit style: Conventional Commits (`feat(ask): …`, `fix(ask): …`, `test(ask): …`, `docs: …`).

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/library/structured_query.py` | Structured aggregation over extracted metadata; owns `Coverage` and the counting queries | Modify |
| `src/library/ask/engine.py` | Ask tool schemas, dispatch, citation assembly | Modify |
| `tests/test_structured_query.py` | Aggregation + coverage behaviour | Modify |
| `tests/test_ask_tool_filters.py` | Ask tool schema/forwarding pins | Modify |
| `tests/test_api_ask.py` | End-to-end `/api/ask` behaviour incl. citations | Modify |
| `docs/ask.md` | User/operator documentation for Ask | Modify |

`Coverage` lives in `structured_query.py` rather than a new module: it is computed from the same `filter_conditions` the aggregates use, and every consumer already imports from there. Splitting it out would separate the counting logic from the filtering logic it must stay consistent with.

---

### Task 1: The `Coverage` object and its counting query

**Files:**
- Modify: `src/library/structured_query.py` (imports at 19-25; new code after `AmountGroup`, ~line 87)
- Test: `tests/test_structured_query.py`

**Interfaces:**
- Consumes: `library.search.DocumentFilters`, `library.search.filter_conditions` (already imported at `structured_query.py:25`).
- Produces:
  - `Coverage` — frozen slotted dataclass with `matched: int`, `included: int`, `excluded: dict[str, int]`, `needs_review: int`.
  - `Aggregated[T]` — frozen slotted generic dataclass with `rows: list[T]`, `coverage: Coverage`.
  - `async def count_coverage(session, *, filters, include_condition, exclusions) -> Coverage` where `exclusions: dict[str, Any]` maps a reason name to a SQL condition.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_structured_query.py`:

```python
async def test_count_coverage_partitions_matched_into_included_and_excluded(
    session: AsyncSession,
) -> None:
    """matched = included + sum(excluded.values()), always."""
    await seed(session, "cov1", kind_slug="utility-bill", amount="10.00", currency="EUR")
    await seed(session, "cov2", kind_slug="utility-bill", amount="20.00", currency="EUR")
    await seed(session, "cov3", kind_slug="utility-bill")  # no amount

    coverage = await count_coverage(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        include_condition=Document.amount_total.isnot(None),
        exclusions={"no_amount": Document.amount_total.is_(None)},
    )

    assert coverage.matched == 3
    assert coverage.included == 2
    assert coverage.excluded == {"no_amount": 1}
    assert coverage.needs_review == 0
    assert coverage.included + sum(coverage.excluded.values()) == coverage.matched


async def test_count_coverage_omits_zero_reasons(session: AsyncSession) -> None:
    """A reason that excluded nothing is not reported — an empty dict means
    'the rows account for everything that matched'."""
    await seed(session, "cov4", kind_slug="invoice", amount="5.00", currency="EUR")

    coverage = await count_coverage(
        session,
        filters=DocumentFilters(kind_slug="invoice"),
        include_condition=Document.amount_total.isnot(None),
        exclusions={"no_amount": Document.amount_total.is_(None)},
    )

    assert coverage.excluded == {}


async def test_count_coverage_counts_needs_review_among_included(
    session: AsyncSession,
) -> None:
    """needs_review counts flagged documents that ARE in the rows — a flagged
    document the aggregate already dropped must not be double-reported."""
    included_id = await seed(
        session, "cov5", kind_slug="utility-bill", amount="30.00", currency="EUR"
    )
    excluded_id = await seed(session, "cov6", kind_slug="utility-bill")  # no amount
    for document_id in (included_id, excluded_id):
        document = await session.get(Document, document_id)
        assert document is not None
        document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    coverage = await count_coverage(
        session,
        filters=DocumentFilters(kind_slug="utility-bill"),
        include_condition=Document.amount_total.isnot(None),
        exclusions={"no_amount": Document.amount_total.is_(None)},
    )

    assert coverage.included == 1
    assert coverage.needs_review == 1
```

Extend the test module's imports — replace the existing `from library.models import ...` and `from library.structured_query import ...` lines:

```python
from library.models import Document, DocumentSource, Kind, ReviewStatus, Sender
from library.structured_query import (
    Coverage,
    count_coverage,
    distinct_senders,
    list_documents,
    query_documents,
    sum_amount,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/john/projects/syncthing/agent-lxc/library
uv run pytest tests/test_structured_query.py -k count_coverage -v
```

Expected: collection error — `ImportError: cannot import name 'count_coverage' from 'library.structured_query'`.

- [ ] **Step 3: Write the implementation**

In `src/library/structured_query.py`, extend the imports:

```python
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Kind, ReviewStatus, Sender
from library.search import DocumentFilters, filter_conditions
```

Then insert after the `AmountGroup` dataclass (before `async def list_documents`):

```python
@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of the filtered set a result's rows actually account for.

    Every aggregate silently drops documents — a spend total cannot include a
    bill whose amount never extracted, a sender breakdown cannot include a
    document with no sender, a list cannot exceed its limit. Reporting the
    result without reporting the drop is how a partial number gets presented as
    a complete one, so every aggregate returns this alongside its rows.

    ``matched`` is what met the caller's filters, ``included`` is what the rows
    account for, and ``excluded`` says why the difference was dropped —
    ``included + sum(excluded.values()) == matched`` is an invariant, pinned by
    a test. Reasons that dropped nothing are omitted, so an empty ``excluded``
    reads as "the rows are the whole story".

    ``needs_review`` is a *trust* signal, not a coverage one: those documents are
    counted in ``included``. It is the number of them whose extracted metadata
    ``library.extraction.validation`` flagged as untrustworthy — most often an
    ``amount_grounding`` finding, meaning the amount being summed here does not
    appear anywhere in the document's text.
    """

    matched: int
    included: int
    excluded: dict[str, int]
    needs_review: int


@dataclass(frozen=True, slots=True)
class Aggregated[T]:
    """An aggregate's rows plus the coverage of the set they were drawn from."""

    rows: list[T]
    coverage: Coverage


async def count_coverage(
    session: AsyncSession,
    *,
    filters: DocumentFilters,
    include_condition: ColumnElement[bool],
    exclusions: dict[str, ColumnElement[bool]],
) -> Coverage:
    """Count a result's coverage in one round-trip, using conditional aggregates.

    ``include_condition`` selects the documents the caller's rows are built
    from; ``exclusions`` maps a reason name to the condition identifying the
    documents dropped for it. The conditions must partition the matched set —
    the caller owns that, and the invariant is asserted by the caller's tests
    rather than here, so a legitimate partial count (``list_documents``, whose
    over-limit drop is positional and has no SQL predicate) is still expressible.

    One ``SELECT`` with Postgres ``FILTER (WHERE ...)`` clauses rather than N+1
    counts: this runs on every structured tool call, so it must not multiply
    the query cost of asking a question.
    """
    conditions = filter_conditions(filters)
    columns = [
        func.count(Document.id),
        func.count(Document.id).filter(include_condition),
        func.count(Document.id).filter(
            include_condition, Document.review_status == ReviewStatus.NEEDS_REVIEW
        ),
        *(func.count(Document.id).filter(condition) for condition in exclusions.values()),
    ]
    row = (await session.execute(select(*columns).where(*conditions))).one()
    matched, included, needs_review = int(row[0]), int(row[1]), int(row[2])
    excluded = {
        reason: int(count)
        for reason, count in zip(exclusions, row[3:], strict=True)
        # A reason that dropped nothing is noise in the model's context and
        # would read as a caveat where there is none.
        if int(count) > 0
    }
    return Coverage(
        matched=matched, included=included, excluded=excluded, needs_review=needs_review
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_structured_query.py -k count_coverage -v
```

Expected: 3 passed.

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/structured_query.py
git add src/library/structured_query.py tests/test_structured_query.py
git commit -m "feat(ask): a Coverage object saying what an aggregate's rows leave out"
```

---

### Task 2: `sum_amount` reports its coverage

**Files:**
- Modify: `src/library/structured_query.py:139-192` (the `sum_amount` function)
- Test: `tests/test_structured_query.py`

**Interfaces:**
- Consumes: `Coverage`, `Aggregated`, `count_coverage` from Task 1.
- Produces: `async def sum_amount(session, *, filters, group_by=None) -> Aggregated[AmountGroup]` — **return type changed** from `list[AmountGroup]`. Task 5 and the existing tests consume `.rows` / `.coverage`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_structured_query.py`:

```python
async def test_sum_amount_reports_documents_with_no_amount(session: AsyncSession) -> None:
    """The headline bug: a spend total that silently omits bills whose amount
    never extracted. The number is still returned — but so is the omission."""
    await seed(session, "s1", kind_slug="utility-bill", amount="100.00", currency="EUR")
    await seed(session, "s2", kind_slug="utility-bill", amount="50.00", currency="EUR")
    await seed(session, "s3", kind_slug="utility-bill")  # amount extraction failed

    result = await sum_amount(session, filters=DocumentFilters(kind_slug="utility-bill"))

    assert result.rows[0].total == "150.00"
    assert result.coverage.matched == 3
    assert result.coverage.included == 2
    assert result.coverage.excluded == {"no_amount": 1}


async def test_sum_amount_reports_the_quote_exclusion(session: AsyncSession) -> None:
    """Excluding quotes from spend is correct AND surprising, so it is disclosed
    rather than merely documented."""
    await seed(session, "s4", kind_slug="invoice", amount="200.00", currency="EUR")
    await seed(session, "s5", kind_slug="quote", amount="9999.00", currency="EUR")

    result = await sum_amount(session, filters=DocumentFilters())

    assert result.rows[0].total == "200.00"
    assert result.coverage.excluded == {"quote_not_spend": 1}


async def test_sum_amount_grouped_by_sender_reports_senderless_documents(
    session: AsyncSession,
) -> None:
    """group_by='sender' INNER JOINs Sender, so a document with no extracted
    sender drops out of a grouped total as well as an ungrouped one."""
    await seed(session, "s6", sender_name="Vattenfall", amount="80.00", currency="EUR")
    await seed(session, "s7", amount="20.00", currency="EUR")  # no sender

    result = await sum_amount(session, filters=DocumentFilters(), group_by="sender")

    assert [(row.key, row.total) for row in result.rows] == [("Vattenfall", "80.00")]
    assert result.coverage.excluded == {"no_sender": 1}


async def test_sum_amount_flags_untrusted_amounts(session: AsyncSession) -> None:
    """A summed amount the validator could not ground in the document text is
    counted, and reported as needing review."""
    document_id = await seed(
        session, "s8", kind_slug="utility-bill", amount="70.00", currency="EUR"
    )
    document = await session.get(Document, document_id)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    result = await sum_amount(session, filters=DocumentFilters(kind_slug="utility-bill"))

    assert result.coverage.needs_review == 1
```

Then update the four **existing** `sum_amount` tests to read `.rows` — they currently bind the return value directly. In `tests/test_structured_query.py`, in `test_sum_amount_groups_by_currency`, `test_sum_amount_excludes_quotes_from_spend`, `test_sum_amount_can_total_quotes_when_requested` and `test_sum_amount_grouped_by_sender`, change each

```python
    groups = await sum_amount(session, filters=...)
```

to

```python
    groups = (await sum_amount(session, filters=...)).rows
```

leaving every assertion in those four tests untouched.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_structured_query.py -k sum_amount -v
```

Expected: the four new tests fail with `AttributeError: 'list' object has no attribute 'rows'`, and the four edited tests fail the same way.

- [ ] **Step 3: Write the implementation**

Replace `sum_amount` in `src/library/structured_query.py` with:

```python
async def sum_amount(
    session: AsyncSession, *, filters: DocumentFilters, group_by: GroupBy | None = None
) -> Aggregated[AmountGroup]:
    """Sum ``amount_total`` over matching documents, with coverage.

    Always grouped by currency (amounts in different currencies cannot be
    added); optionally also by sender or kind. Three things drop documents from
    the total, and all three are reported in ``coverage.excluded`` rather than
    happening silently:

    * ``no_amount`` — extraction found no total. The dominant case, and the one
      that used to make a partial sum indistinguishable from a complete one.
    * ``quote_not_spend`` — quotes/estimates are not actual expenditure, so kind
      ``quote`` is excluded unless the caller explicitly filters for it (e.g.
      "how much have my quotes come to?"). Correct, but surprising enough that
      the answer should be able to say it happened.
    * ``no_sender`` / ``no_kind`` — only when grouping by that column, whose
      INNER JOIN drops documents that lack it.
    """
    is_quote = select(1).where(Kind.id == Document.kind_id, Kind.slug == "quote").exists()
    has_amount = Document.amount_total.isnot(None)

    include = has_amount
    exclusions: dict[str, ColumnElement[bool]] = {"no_amount": Document.amount_total.is_(None)}
    if filters.kind_slug != "quote":
        include = include & ~is_quote
        # Conditioned on `has_amount` so an amountless quote is counted once,
        # under `no_amount`, and the partition invariant holds.
        exclusions["quote_not_spend"] = has_amount & is_quote
    if group_by == "sender":
        include = include & Document.sender_id.isnot(None)
        exclusions["no_sender"] = has_amount & Document.sender_id.is_(None)
    elif group_by == "kind":
        include = include & Document.kind_id.isnot(None)
        exclusions["no_kind"] = has_amount & Document.kind_id.is_(None)

    conditions = [*filter_conditions(filters), has_amount]
    if filters.kind_slug != "quote":
        conditions.append(~is_quote)
    key_column = None
    statement = select(
        func.sum(Document.amount_total),
        Document.currency,
        func.count(Document.id),
        func.array_agg(Document.id),
    ).where(*conditions)

    if group_by == "sender":
        key_column = Sender.name
        statement = statement.join(Sender, Document.sender_id == Sender.id)
    elif group_by == "kind":
        key_column = Kind.slug
        statement = statement.join(Kind, Document.kind_id == Kind.id)

    if key_column is not None:
        statement = statement.add_columns(key_column).group_by(key_column, Document.currency)
    else:
        statement = statement.group_by(Document.currency)
    statement = statement.order_by(func.sum(Document.amount_total).desc())

    groups: list[AmountGroup] = []
    for row in (await session.execute(statement)).all():
        total, currency, count, ids = row[0], row[1], row[2], row[3]
        key = row[4] if key_column is not None else None
        groups.append(
            AmountGroup(
                key=key,
                total=str(Decimal(total)),
                currency=currency,
                document_count=count,
                document_ids=sorted(ids)[:MAX_CITED_IDS],
            )
        )
    coverage = await count_coverage(
        session, filters=filters, include_condition=include, exclusions=exclusions
    )
    return Aggregated(rows=groups, coverage=coverage)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_structured_query.py -k sum_amount -v
```

Expected: 8 passed (4 edited + 4 new).

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/structured_query.py
git add src/library/structured_query.py tests/test_structured_query.py
git commit -m "feat(ask): sum_amount reports the documents it left out of a total"
```

---

### Task 3: `list_documents` and `distinct_senders` report their coverage

**Files:**
- Modify: `src/library/structured_query.py` (`DocumentRef` ~line 56, `list_documents` ~line 89, `distinct_senders` ~line 115)
- Test: `tests/test_structured_query.py`

**Interfaces:**
- Consumes: `Coverage`, `Aggregated`, `count_coverage` from Task 1; `library.models.ReviewStatus`.
- Produces:
  - `DocumentRef.review_status: str` — `"verified" | "needs_review" | "unreviewed"`. Declared here rather than in Task 4 because `list_documents` is its only producer; splitting the field from its producer would leave both tasks unable to run their own tests.
  - `async def list_documents(session, *, filters, limit=50) -> Aggregated[DocumentRef]`
  - `async def distinct_senders(session, *, filters) -> Aggregated[SenderGroup]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_structured_query.py`:

```python
async def test_list_documents_reports_truncation(session: AsyncSession) -> None:
    """'List every invoice from 2024' must not return the newest N as though
    that were all of them."""
    for index in range(5):
        await seed(session, f"trunc{index}", kind_slug="invoice", sender_name="Acme")

    result = await list_documents(
        session, filters=DocumentFilters(kind_slug="invoice"), limit=2
    )

    assert len(result.rows) == 2
    assert result.coverage.matched == 5
    assert result.coverage.included == 2
    assert result.coverage.excluded == {"over_limit": 3}


async def test_list_documents_within_limit_reports_nothing_excluded(
    session: AsyncSession,
) -> None:
    await seed(session, "whole1", kind_slug="ticket")

    result = await list_documents(session, filters=DocumentFilters(kind_slug="ticket"), limit=50)

    assert result.coverage.excluded == {}
    assert result.coverage.included == result.coverage.matched == 1


async def test_list_documents_counts_needs_review_in_the_returned_page(
    session: AsyncSession,
) -> None:
    """needs_review describes the rows the model can see, not the whole match."""
    flagged = await seed(session, "flag1", kind_slug="warranty")
    document = await session.get(Document, flagged)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    result = await list_documents(session, filters=DocumentFilters(kind_slug="warranty"))

    assert result.coverage.needs_review == 1


async def test_distinct_senders_reports_documents_with_no_sender(
    session: AsyncSession,
) -> None:
    """The sender join is an INNER JOIN, so a document whose sender never
    extracted is absent from 'who were my providers?' entirely."""
    await seed(session, "ds1", sender_name="Vattenfall", kind_slug="utility-bill")
    await seed(session, "ds2", kind_slug="utility-bill")  # sender extraction failed

    result = await distinct_senders(session, filters=DocumentFilters(kind_slug="utility-bill"))

    assert [group.sender for group in result.rows] == ["Vattenfall"]
    assert result.coverage.matched == 2
    assert result.coverage.included == 1
    assert result.coverage.excluded == {"no_sender": 1}


async def test_list_documents_rows_carry_review_status(session: AsyncSession) -> None:
    """A per-row trust flag, so the model can caveat one line of a list rather
    than the whole answer."""
    document_id = await seed(session, "rs1", kind_slug="receipt")
    document = await session.get(Document, document_id)
    assert document is not None
    document.review_status = ReviewStatus.NEEDS_REVIEW
    await session.commit()

    result = await list_documents(session, filters=DocumentFilters(kind_slug="receipt"))

    assert result.rows[0].review_status == "needs_review"
```

Update the four **existing** tests that bind these functions directly. In `test_distinct_senders_ranked_by_document_count` and `test_distinct_senders_honours_date_window`, change

```python
    groups = await distinct_senders(session, filters=...)
```

to

```python
    groups = (await distinct_senders(session, filters=...)).rows
```

and in `test_list_documents_newest_first` and `test_list_documents_narrows_by_project_slug`, change

```python
    documents = await list_documents(session, filters=...)
```

to

```python
    documents = (await list_documents(session, filters=...)).rows
```

leaving every assertion untouched.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_structured_query.py -k "list_documents or distinct_senders" -v
```

Expected: `AttributeError: 'list' object has no attribute 'rows'` on the new and edited tests.

- [ ] **Step 3: Write the implementation**

First add the trust field to `DocumentRef` in `src/library/structured_query.py`, after `currency`:

```python
    review_status: str  # "verified" | "needs_review" | "unreviewed"
```

`_serialise_ref` builds its dict with `asdict`, so it picks the new field up with no change.

Then replace `list_documents` with:

```python
async def list_documents(
    session: AsyncSession, *, filters: DocumentFilters, limit: int = 50
) -> Aggregated[DocumentRef]:
    """Matching documents, newest first (unknown dates last), with coverage.

    The over-limit drop is positional, not predicated: which documents fall off
    depends on the ORDER BY, so there is no SQL condition to hand
    ``count_coverage``. It is therefore computed here from ``matched`` and the
    page size, and ``needs_review`` is counted over the returned page — the rows
    the caller can actually see — rather than over the whole match.
    """
    statement = (
        select(Document)
        .where(*filter_conditions(filters))
        .order_by(
            Document.document_date.desc().nulls_last(),
            Document.created_at.desc(),
            Document.id.desc(),
        )
        .limit(limit)
    )
    documents = (await session.execute(statement)).scalars().all()
    refs = [
        DocumentRef(
            id=document.id,
            title=document.title,
            sender=document.sender.name if document.sender else None,
            recipient=document.recipient.name if document.recipient else None,
            kind=document.kind.slug if document.kind else None,
            document_date=document.document_date,
            amount_total=str(document.amount_total) if document.amount_total is not None else None,
            currency=document.currency,
            review_status=document.review_status.value,
        )
        for document in documents
    ]
    matched = (
        await session.execute(
            select(func.count(Document.id)).where(*filter_conditions(filters))
        )
    ).scalar_one()
    over_limit = max(0, int(matched) - len(refs))
    return Aggregated(
        rows=refs,
        coverage=Coverage(
            matched=int(matched),
            included=len(refs),
            excluded={"over_limit": over_limit} if over_limit else {},
            needs_review=sum(
                1 for document in documents if document.review_status is ReviewStatus.NEEDS_REVIEW
            ),
        ),
    )
```

Replace `distinct_senders` with:

```python
async def distinct_senders(
    session: AsyncSession, *, filters: DocumentFilters
) -> Aggregated[SenderGroup]:
    """Distinct senders among matching documents, most documents first.

    The join to ``Sender`` is inner, so a document whose sender never extracted
    is absent from the breakdown entirely — reported as ``no_sender`` rather
    than left for the reader to notice the counts do not add up.
    """
    statement = (
        select(
            Sender.name,
            func.count(Document.id),
            func.array_agg(Document.id),
        )
        .join(Sender, Document.sender_id == Sender.id)
        .where(*filter_conditions(filters))
        .group_by(Sender.name)
        .order_by(func.count(Document.id).desc(), Sender.name)
    )
    rows = (await session.execute(statement)).all()
    groups = [
        SenderGroup(sender=name, document_count=count, document_ids=sorted(ids)[:MAX_CITED_IDS])
        for name, count, ids in rows
    ]
    coverage = await count_coverage(
        session,
        filters=filters,
        include_condition=Document.sender_id.isnot(None),
        exclusions={"no_sender": Document.sender_id.is_(None)},
    )
    return Aggregated(rows=groups, coverage=coverage)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_structured_query.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/structured_query.py
git add src/library/structured_query.py tests/test_structured_query.py
git commit -m "feat(ask): list and sender aggregates report truncation and missing senders"
```

---

### Task 4: `review_status` becomes a filter the model can set

**Files:**
- Modify: `src/library/ask/engine.py:126-152` (`_FILTER_PROPERTIES`), `:490-501` (`_filters_from_args`)
- Test: `tests/test_ask_tool_filters.py`

**Interfaces:**
- Consumes: `library.models.ReviewStatus`; `library.search.DocumentFilters.review_status` (already exists, `search.py:110`); `DocumentRef.review_status` from Task 3.
- Produces: `_FILTER_PROPERTIES["review_status"]`; `_review_status_arg(value) -> ReviewStatus | None`; `_filters_from_args` returning `DocumentFilters(review_status=...)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ask_tool_filters.py`:

```python
def test_structured_tools_expose_review_status() -> None:
    """The archive already knows which extractions it distrusts; the model
    cannot act on that unless the tool schema offers it."""
    for tool_name in ("query_documents", "compare_to_series"):
        tool = next(tool for tool in TOOLS if tool["name"] == tool_name)
        prop = tool["input_schema"]["properties"]["review_status"]
        assert prop["enum"] == ["verified", "needs_review", "unreviewed"]


@pytest.mark.asyncio
async def test_query_documents_forwards_review_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_query_documents(
        session: Any, *, filters: DocumentFilters, aggregate: Any, group_by: Any
    ) -> dict[str, Any]:
        captured["filters"] = filters
        return {"result_type": "list", "rows": [], "coverage": {}}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)

    await _run_query_documents(
        cast("AsyncSession", None),
        {"aggregate": "list", "review_status": "needs_review"},
        set(),
    )

    assert captured["filters"].review_status is ReviewStatus.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_query_documents_ignores_an_unknown_review_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-invented value must degrade to 'no filter', never raise into the
    tool loop — the enum is a hint to the model, not a guarantee."""
    captured: dict[str, Any] = {}

    async def fake_query_documents(
        session: Any, *, filters: DocumentFilters, aggregate: Any, group_by: Any
    ) -> dict[str, Any]:
        captured["filters"] = filters
        return {"result_type": "list", "rows": [], "coverage": {}}

    monkeypatch.setattr(ask_engine, "query_documents", fake_query_documents)

    await _run_query_documents(
        cast("AsyncSession", None), {"aggregate": "list", "review_status": "dubious"}, set()
    )

    assert captured["filters"].review_status is None
```

Extend that module's imports:

```python
from library.models import ReviewStatus
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_ask_tool_filters.py -k review_status -v
```

Expected: `KeyError: 'review_status'` on the schema test, and `AssertionError`/`KeyError` on the forwarding tests.

- [ ] **Step 3: Write the implementation**

In `src/library/ask/engine.py`, add to `_FILTER_PROPERTIES` (after the `date_to` entry):

```python
    "review_status": {
        "type": "string",
        "enum": ["verified", "needs_review", "unreviewed"],
        "description": (
            "Trust state of a document's EXTRACTED metadata, not of the document "
            "itself. needs_review means the archive's validator flagged the "
            "extraction — most often because the amount does not appear anywhere "
            "in the document's text. Omit to include everything (the default, and "
            "usually right). Use needs_review to LIST what the user should check; "
            "do not silently filter it out of a total, because dropping it changes "
            "the number without saying so — report the count instead."
        ),
    },
```

Extend the imports in `ask/engine.py`:

```python
from library.models import Document, DocumentComment, DocumentPage, ReviewStatus
```

And in `_filters_from_args`, add the parsed value:

```python
def _review_status_arg(value: object) -> ReviewStatus | None:
    """A ``ReviewStatus`` from a tool argument, or None.

    An unrecognised value degrades to "no filter" rather than raising: the JSON
    schema's ``enum`` steers the model but does not bind it, and a hallucinated
    status must not turn into a 500 inside the tool loop.
    """
    text = _text_arg(value)
    if text is None:
        return None
    try:
        return ReviewStatus(text)
    except ValueError:
        logger.info("ask: ignoring unknown review_status %r", text)
        return None
```

then inside `_filters_from_args`, after the `date_to=` line:

```python
        review_status=_review_status_arg(args.get("review_status")),
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_ask_tool_filters.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/ask/engine.py
git add src/library/ask/engine.py tests/test_ask_tool_filters.py
git commit -m "feat(ask): let the model filter on review_status"
```

---

### Task 5: Coverage reaches the model, and disclosure becomes a rule

**Files:**
- Modify: `src/library/structured_query.py` (`QueryResult` ~line 191, `query_documents` ~line 209)
- Modify: `src/library/ask/engine.py:44-96` (`ASK_SYSTEM_PROMPT_TEMPLATE`), `:174-200` (the `query_documents` tool description)
- Test: `tests/test_structured_query.py`, `tests/test_api_ask.py`

**Interfaces:**
- Consumes: `Aggregated` results from Tasks 2 and 3.
- Produces: `QueryResult` TypedDict gains `coverage: dict[str, Any]`. `_run_query_documents` in `ask/engine.py` is unchanged — it already returns `dict(query_result)` and the new key travels with it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_structured_query.py`:

```python
async def test_query_documents_result_carries_coverage(session: AsyncSession) -> None:
    """Coverage has to survive the dispatch layer or the model never sees it."""
    await seed(session, "qc1", kind_slug="utility-bill", amount="12.00", currency="EUR")
    await seed(session, "qc2", kind_slug="utility-bill")

    result = await query_documents(
        session, filters=DocumentFilters(kind_slug="utility-bill"), aggregate="sum_amount"
    )

    assert result["coverage"] == {
        "matched": 2,
        "included": 1,
        "excluded": {"no_amount": 1},
        "needs_review": 0,
    }
```

Append to `tests/test_api_ask.py`:

```python
def test_ask_system_prompt_requires_disclosing_partial_coverage() -> None:
    """The coverage block is only worth computing if the model is obliged to
    act on it."""
    from library.ask.engine import ASK_SYSTEM_PROMPT_TEMPLATE

    assert "coverage" in ASK_SYSTEM_PROMPT_TEMPLATE
    assert "needs_review" in ASK_SYSTEM_PROMPT_TEMPLATE


def test_query_documents_tool_description_explains_coverage() -> None:
    from library.ask.engine import TOOLS

    tool = next(tool for tool in TOOLS if tool["name"] == "query_documents")
    assert "coverage" in tool["description"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_structured_query.py -k coverage tests/test_api_ask.py -k "coverage or tool_description" -v
```

Expected: `KeyError: 'coverage'` and two `AssertionError`s.

- [ ] **Step 3: Write the implementation**

In `src/library/structured_query.py`, replace `QueryResult` and `query_documents`:

```python
class QueryResult(TypedDict):
    """The shape every ``query_documents`` branch returns.

    A ``TypedDict`` rather than ``dict[str, object]`` so the caller can iterate
    ``result["rows"]`` without a cast: the row type is derived from this
    declaration instead of being re-asserted at the call site.

    ``result_type`` reuses ``Aggregate`` rather than widening to ``str``, so a
    new aggregate cannot be echoed back under a name the dispatcher does not
    know about.

    ``coverage`` is a serialised :class:`Coverage`. It is present on every
    branch — an aggregate that has nothing to disclose reports
    ``excluded == {}`` rather than omitting the key, so the model never has to
    distinguish "nothing was dropped" from "this tool does not say".
    """

    result_type: Aggregate
    rows: list[dict[str, Any]]
    coverage: dict[str, Any]


async def query_documents(
    session: AsyncSession,
    *,
    filters: DocumentFilters,
    aggregate: Aggregate = "list",
    group_by: GroupBy | None = None,
    limit: int = 50,
) -> QueryResult:
    """Dispatch a structured query and return a JSON-friendly result.

    The single entry point the ``/ask`` tool-use loop calls. ``result_type``
    echoes the aggregate so the caller can interpret ``rows``; ``coverage``
    says how much of the filtered set those rows account for.
    """
    if aggregate == "distinct_senders":
        senders = await distinct_senders(session, filters=filters)
        return {
            "result_type": "distinct_senders",
            "rows": [asdict(group) for group in senders.rows],
            "coverage": asdict(senders.coverage),
        }
    if aggregate == "sum_amount":
        amounts = await sum_amount(session, filters=filters, group_by=group_by)
        return {
            "result_type": "sum_amount",
            "rows": [asdict(group) for group in amounts.rows],
            "coverage": asdict(amounts.coverage),
        }
    documents = await list_documents(session, filters=filters, limit=limit)
    return {
        "result_type": "list",
        "rows": [_serialise_ref(ref) for ref in documents.rows],
        "coverage": asdict(documents.coverage),
    }
```

In `src/library/ask/engine.py`, replace the `query_documents` tool description string with:

```python
        "description": (
            "Aggregate over structured metadata (sender, kind, document_date, "
            "amount_total). Use for who/how-many/how-much/over-time questions. "
            "Every result carries a `coverage` block — `matched` documents met "
            "your filters, `included` are the ones the rows account for, "
            "`excluded` maps a reason to how many were dropped for it, and "
            "`needs_review` counts included documents whose extracted metadata "
            "the archive flagged as untrustworthy. Read it before you answer: a "
            "total over `included` documents is not a total over `matched` ones. "
            + _kind_hint()
        ),
```

And append these two bullets to `ASK_SYSTEM_PROMPT_TEMPLATE`, immediately after the `- Cite the document id(s) …` line in the `Rules:` block:

```
- query_documents results carry a "coverage" block. If `excluded` is non-empty,
  the rows do NOT account for every matching document, and you MUST say so in
  your answer with the reason and the count — e.g. "EUR 1,240 across 14 bills;
  3 more matched but no amount could be read from them". If `needs_review` is
  above zero, say that too: those documents are included in the number but the
  archive flagged their extracted metadata as unreliable. Never present a
  partial total as if it were complete, and never silently drop the flagged
  documents to make the caveat go away.
- Cite a document with [#id] whenever your answer relies on it. If you cannot
  answer from the tool results, say so plainly and cite nothing — do not list
  the documents you looked at and rejected.
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_structured_query.py tests/test_api_ask.py tests/test_ask_tool_filters.py -v
```

Expected: all pass. If a pre-existing `test_api_ask.py` test asserts on an exact `tool_result` JSON body, extend its expectation with the `coverage` key rather than removing the key.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/structured_query.py src/library/ask/engine.py
git add src/library/structured_query.py src/library/ask/engine.py tests/test_structured_query.py tests/test_api_ask.py
git commit -m "feat(ask): carry coverage into the tool result and require the model to disclose it"
```

---

### Task 6: Stop citing rejected candidates on the no-answer path

**Files:**
- Modify: `src/library/ask/engine.py:1194-1198` (the citation assembly at the tail of `run_ask`)
- Test: `tests/test_api_ask.py`

**Interfaces:**
- Consumes: `_NO_ANSWER` (already module-level, `ask/engine.py:355`).
- Produces: no new names. `AskResult.citations` is empty whenever `AskResult.answer is _NO_ANSWER`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_ask.py`:

```python
def test_ask_cites_nothing_when_the_loop_produces_no_answer(
    api_client: TestClient,
    api_database_url: str,
    with_api_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cited` holds every candidate a read tool surfaced, including ones the
    model looked at and rejected. Attaching them to "I couldn't find an answer"
    presents rejected candidates as sources for a non-answer."""

    async def fake_embed_query(
        text_value: str, *, settings: Any, client: Any = None
    ) -> list[float]:
        return _unit_vector(0)

    async def fake_search(session: Any, **kwargs: Any) -> list[Any]:
        from dataclasses import dataclass as _dataclass

        @_dataclass
        class _Doc:
            id: int
            title: str | None
            sender: Any = None
            recipient: Any = None
            document_date: Any = None

        @_dataclass
        class _Hit:
            document: Any
            score: float
            chunk_index: int | None
            chunk_text: str | None
            page_number: int | None
            chunk_texts: tuple[str, ...]

        return [
            _Hit(_Doc(id=1, title="Unrelated"), 0.1, 0, "noise", None, ("noise",)),
            _Hit(_Doc(id=2, title="Also unrelated"), 0.1, 0, "noise", None, ("noise",)),
        ]

    monkeypatch.setattr(ask_engine, "embed_query", fake_embed_query)
    monkeypatch.setattr(ask_engine, "semantic_search", fake_search)

    # Every response is a tool_use, so the loop exhausts ask_max_tool_turns
    # without ever producing text and falls back to the _NO_ANSWER sentinel.
    settings = get_settings()
    _install_anthropic(
        monkeypatch,
        [
            _Response(
                stop_reason="tool_use",
                content=[
                    _ToolUseBlock(
                        name="semantic_search", input={"query": "tax"}, id=f"t{index}"
                    )
                ],
                usage=_Usage(100, 10),
            )
            for index in range(settings.ask_max_tool_turns)
        ],
    )

    response = api_client.post("/api/ask", json={"question": "Where are my tax returns?"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == "I couldn't find an answer to that in the archive."
    assert body["citations"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_api_ask.py::test_ask_cites_nothing_when_the_loop_produces_no_answer -v
```

Expected: FAIL — `assert [{'document_id': 1, ...}, {'document_id': 2, ...}] == []`.

- [ ] **Step 3: Write the implementation**

In `src/library/ask/engine.py`, replace the two citation lines at the tail of `run_ask`:

```python
    result.answer = answer or _NO_ANSWER
    # Prefer the documents Claude actually cited inline (#id); fall back to the
    # full retrieved set when the answer cited none explicitly.
    #
    # The fallback exists for a real case: an answer that names its sources in
    # prose rather than with the [#id] syntax. It must NOT fire for the
    # no-answer sentinel, because `cited` holds every candidate a read tool
    # surfaced — including the ones the model read and rejected. Falling back
    # there attaches a full source list to "I couldn't find an answer", which
    # reads as evidence for a non-answer.
    mentioned = {int(match) for match in re.findall(r"#(\d+)", answer)} & cited
    fallback: set[int] = set() if result.answer == _NO_ANSWER else cited
    result.citations = await _citations_for(session, mentioned or fallback, pages)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_api_ask.py -v
```

Expected: all pass — in particular `test_ask_empty_corpus_is_honest` and the existing prose-citation fallback test around line 565 must stay green.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library/ask/engine.py
git add src/library/ask/engine.py tests/test_api_ask.py
git commit -m "fix(ask): don't cite rejected candidates when the loop finds no answer"
```

---

### Task 7: Documentation

**Files:**
- Modify: `docs/ask.md` (stamp lines 3-4; §1.2; §1.10)
- Create: `journal/260826-ask-answer-trustworthiness.md`

**Interfaces:**
- Consumes: everything built in Tasks 1-6.
- Produces: no code.

- [ ] **Step 1: Add the coverage subsection to `docs/ask.md` §1.2**

Insert a `### Coverage and trust on structured results` subsection at the end of §1.2 (immediately before `## 1.3 Configuration`):

```markdown
### Coverage and trust on structured results

Every `query_documents` result carries a `coverage` block beside its rows:

| Field | Meaning |
|-------|---------|
| `matched` | Documents that met the call's filters |
| `included` | Documents the rows actually account for |
| `excluded` | Reason → count for the difference; `{}` when the rows are the whole story |
| `needs_review` | Of `included`, how many carry a `needs_review` extraction flag |

`included + sum(excluded.values()) == matched` is an invariant, pinned by
`tests/test_structured_query.py`.

The reasons a document is dropped, by aggregate:

- `sum_amount` — `no_amount` (extraction found no total), `quote_not_spend`
  (quotes are not expenditure; see below), and `no_sender`/`no_kind` when
  grouping by a column the document lacks.
- `distinct_senders` — `no_sender`.
- `list` — `over_limit` (the result limit is 50 and the drop is positional).

The system prompt requires the model to disclose a non-empty `excluded` and a
non-zero `needs_review` in its answer, so a partial total reads as one. It is
also told **not** to filter flagged documents out of a total to avoid the
caveat — `review_status` is offered as a filter for *listing* what needs
checking, not for quietly improving a number.

`needs_review` is a trust signal about the *extraction*, not the document: it
usually means `library.extraction.validation`'s `amount_grounding` rule fired,
i.e. the amount being summed does not appear anywhere in the document's text.
```

- [ ] **Step 2: Rewrite `docs/ask.md` §1.10 item by item**

Replace the §1.10 list with:

```markdown
## 1.10 Limitations (this release)

1. **Page citations are conditional on the markdown layer.** Documents that
   have a `document_pages` row (generated by the `markdown` pipeline stage or
   `backfill-markdown`) carry a `page_number` on their citation. Documents
   ingested before the markdown layer existed, `text/plain` files, and any
   document where the markdown stage was skipped or failed will cite without a
   page number — only the document title is shown.
2. History bounding is a sliding turn window only — no rolling summarization of
   long threads.
3. RRF fusion only — no cross-encoder re-ranking.
4. Ask is in-app only; it is not exposed as an MCP tool yet.
5. CPU embedding: the one-time backfill of a large archive is slow.
6. `semantic_search` takes no metadata filters — only `query_documents` and
   `compare_to_series` do. A content question scoped to a year or a sender must
   search the whole archive and rely on ranking.
7. Coverage reporting is honest about *documents*, not about *periods*.
   `sum_amount` groups by `document_date`, which is the issue date; a bill
   issued in January for December lands in the wrong year, and an annual
   settlement double-counts against the instalments it settles.
8. The no-answer citation suppression is keyed on the exact `_NO_ANSWER`
   sentinel. When the model phrases its own "not found" answer after a fruitless
   search, the prose-citation fallback still attaches the retrieved candidates.
   The system prompt instructs against it; it is not enforced in code.
```

- [ ] **Step 3: Re-stamp `docs/ask.md`**

Prepend to the `**Last updated:**` line's parenthetical (keeping every existing `Earlier (…)` clause intact, and preserving today's date), and replace the `**Last verified:**` line with:

```markdown
**Last verified:** 2026-08-26 — method: read `structured_query.py` and `ask/engine.py` against §1.2 and §1.10; ran `test_structured_query.py`, `test_ask_tool_filters.py` and `test_api_ask.py`, then the full backend suite; ruff, mypy and `check_docs` clean. The coverage block's effect on answer wording is **unmeasured** — the disclosure rule is a prompt instruction, exercised by schema tests, not by an answer-quality eval (see the spec's #15).
```

- [ ] **Step 4: Write the journal entry**

Create `journal/260826-ask-answer-trustworthiness.md`:

```markdown
# Ask answer trustworthiness

**Date:** 2026-08-26

## What changed

Findings #1, #2, #3 and #11 of the semantic-surface review
(`docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md`).

Every `query_documents` aggregate now returns a `Coverage` object alongside its
rows, and the system prompt obliges the model to disclose a non-empty
`excluded` or a non-zero `needs_review`.

## Why

`sum_amount` filtered on `amount_total IS NOT NULL` and returned only rows. A
spend total over 14 of 22 bills was indistinguishable from a complete one — to
the model as well as to the user. The same shape appeared in three more places:
the quote exclusion, the sender inner join, and the hard-coded list limit of 50.

Separately, the archive already computed a trust signal it then discarded:
`extraction/validation.py` flags a document `needs_review` when the extracted
amount's digits are absent from the document text, and that document was summed
with exactly the weight of a verified one.

## Decisions

- **One uniform `Coverage`, not per-aggregate keys.** Three aggregates with
  three shapes means the model learns three shapes and the fourth aggregate
  invents a fifth. `excluded` is a reason→count map because two drops can apply
  at once (an amountless quote is excluded once, under `no_amount`).
- **`needs_review` sits beside the coverage fields, not inside `excluded`.**
  Those documents *are* counted. Trust and completeness are different questions
  and the prompt treats them differently.
- **The model is told not to filter flagged documents out of a total.** Offering
  `review_status` as a filter creates an obvious way to make the caveat
  disappear by changing the number instead of reporting it.
- **The citation fix is keyed on the `_NO_ANSWER` sentinel only.** The prose
  fallback is load-bearing — an existing test covers an answer that names its
  source without `[#id]`. Removing it wholesale would regress that. The residual
  case (the model phrasing its own not-found) is recorded in §1.10 item 8 rather
  than papered over.

## Not done

The period-attribution problem (#4) is untouched: `sum_amount` still groups on
issue date, so coverage is honest about documents and silent about periods.
That is Plan C.
```

- [ ] **Step 5: Run the full suite and the docs gate, then commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy src/library
uv run python scripts/check_docs.py
uv run pytest -v
```

Expected: full backend suite green, `check_docs` clean.

```bash
git add docs/ask.md docs/superpowers/specs/2026-08-26-ask-semantic-quality-design.md docs/superpowers/plans/2026-08-26-ask-answer-trustworthiness.md journal/260826-ask-answer-trustworthiness.md
git commit -m "docs(ask): coverage and trust reporting on structured results"
```

---

## Verification Checklist

Run before opening the PR:

- [ ] `uv run pytest` — full backend suite green (not just the touched files)
- [ ] `uv run ruff format --check . && uv run ruff check .` — whole repo, migrations included
- [ ] `uv run mypy src/library` — no new quarantine entries; `ask/engine.py` still clean
- [ ] `uv run python scripts/check_docs.py` — stamps valid
- [ ] Manually: ask the deployed instance "how much did I spend on utilities in 2025?" and confirm the answer names the excluded count when one exists. This is the only end-to-end check of the disclosure rule — no automated test exercises real model wording.
- [ ] Confirm CI's `promote` job succeeded before `make deploy` (`gh run watch` can exit 0 mid-run)

## Design Validation

The `Coverage` design in Task 1 was **prototyped against real Postgres** before this plan was written, not reasoned about on paper. Confirmed working:

- `func.count(...).filter(cond_a, cond_b)` — multi-criterion Postgres `FILTER (WHERE ...)`, ANDed.
- PEP 695 generic frozen slotted dataclass (`Aggregated[T]`) on Python 3.13.
- Slicing a SQLAlchemy `Row` (`row[3:]`) to zip against the exclusion reasons.
- `Document.amount_total.isnot(None) & ~exists(...)` composing to a `ColumnElement[bool]`.
- The partition invariant on the awkward case: 5 documents = 2 included + 2 `no_amount` + 1 `quote_not_spend`, with an **amountless quote counted once** (under `no_amount`, not both buckets) and `needs_review` counting only an included document.
- The empty-`exclusions` path (three columns, `excluded == {}`).

## Self-Review Notes

- **Spec coverage:** #1 → Tasks 2, 5. #2 → Tasks 3 (per-row field) and 4 (filter). #3 → Task 3. #11 → Task 6. Spec §6's `Coverage` design → Task 1. Spec §7's open question → recorded in `docs/ask.md` §1.10 item 8 (Task 7).
- **Type consistency:** `Coverage` / `Aggregated[T]` / `count_coverage` are defined in Task 1 and used under those exact names in Tasks 2, 3 and 5. `DocumentRef.review_status` is declared in Task 3 alongside `list_documents`, its only producer, and consumed by Task 5's serialisation — no forward references between tasks.
- **Return-type changes are breaking by design, with a verified blast radius of one file.** Tasks 2 and 3 change `sum_amount`, `distinct_senders` and `list_documents` from `list[X]` to `Aggregated[X]`. `grep -rn "structured_query" src/ scripts/ tests/` returns exactly two importers: `ask/engine.py:40`, which imports only `CONCEPT_TO_KIND`, `QueryResult` and `query_documents` — none of whose signatures change — and `tests/test_structured_query.py`. (The `list_documents` at `api/documents.py:170` is an unrelated route handler that does not import this module.) So the only file needing updates is the one test module, and Tasks 2 and 3 name the exact tests to edit rather than leaving it as suite-wide fallout.
