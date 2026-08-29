# Chart Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer "how much am I spending on X per period" over the faceted archive, as an API — a saved question resolves to a signed total per time bucket per split value, with everything it touched but did not count reported alongside.

**Architecture:** One relation, `spend_facts`, unions unsplit documents with split lines and applies label inheritance, so no query reimplements either rule. A chart is a stored rule over that relation plus two independent axes (time, split). Sign lives in `amount_kind`, never in `amount_total`. Nothing is excluded silently: every chart's response carries the money its rule touched but its total did not.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 17, Anthropic SDK, pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-charts-redesign-design.md` — this plan implements **layer C** (§9), plus §8.1.1, §8.4 and the §8.3 sign guard. The `/charts` view (layer D, §10) is **plan 4**; deleting the old series stack (layer E) is **plan 5**.

**Plans 1 and 2 have shipped.** Facets, `document_labels` (migration `0032`), `amount_kind`, `reference`, `payment_overrides` and the `payment_edges`/`payments` views (`0033`) all exist and are deployed. Do not rebuild them.

## Two things settled before this plan was written

**The `spend_facts` SQL in Task 3 and the sign guard in Task 1 were executed against PostgreSQL 17, not reasoned about.** Thirteen cases pass and each was mutation-checked — the view was deliberately broken and the tests confirmed red. Spec §5.2 records the four findings. **Do not "simplify" the SQL below.** Three of its clauses look redundant and are not:

- `COALESCE(e.amount_kind = 'payment_made', false)` — Postgres sorts NULLs FIRST under `DESC`, so the obvious spelling makes an *undecided* document canonical.
- The sign guard sits in the `pairs` CTE, above the rules, not beside them.
- `spend_facts` depends on `payments`; a migration rewriting `payment_edges` must drop and recreate it.

**`/api/spending`, not `/api/charts`.** The old series stack still owns `/api/charts` (13 routes in `src/library/api/charts.py`). Spec §9.6 names `/api/charts` as the end state; plan 5 renames this router when it deletes the old one. Shipping at the final path now would mean editing 13 string literals in a live feature that is about to be deleted.

## Global Constraints

- Python target is 3.13. Type annotations on every function signature.
- `uv` for all dependency management; `pytest` for tests.
- **Do not add a new `*_model` setting.** Every `*_model` setting requires a matching row in `MODEL_PRICING_USD_PER_MTOK` (`src/library/extraction/pricing.py`) or the app refuses to boot. This plan reuses `settings.extraction_model`.
- **Never call a model with `messages.create()` + `json.loads`.** Use `client.messages.parse()` with a Pydantic `output_format`. This shipped twice (#108, #116) and was reverted both times.
- CI runs `ruff check` **and** `ruff format --check` over the **whole repository including `migrations/`**.
- **CI runs `uv run mypy` as a required gate.** Never add a `# type: ignore` or a mypy quarantine entry. For `.rowcount` on a `session.execute()` result, follow the `cast(CursorResult[Any], ...)` pattern in `src/library/currencies.py`.
- **Adding a new module under `src/library/` requires a module-map entry in `docs/architecture.md`**, enforced by `tests/test_check_docs.py::TestModuleMap`.
- **Adding a `Settings` field requires a matching entry in `.env.example`**, enforced by `tests/test_config.py::test_env_example_documents_every_setting`.
- List endpoints reject `limit > 100` with a 422.
- Integration tests share one session-scoped Postgres, truncated between tests, and list endpoints default to 25 rows. Scope every list assertion by a unique marker, never by absolute counts.
- No `except Exception -> pytest.skip` guards; they read as green while hiding breakage.
- **In `text()` SQL, write `CAST(x AS type)`, never `::type`, wherever a bind parameter is nearby.** `text()` parses `:name` itself and `:since::date` leaves the parameter unsubstituted — verified, it raises `ProgrammingError`. Raw DDL with no bind parameters (the views) may use `::` freely.
- **The repository is public.** No real sender names, personal names, addresses, registrations or real amounts in code, fixtures, comments, docs, or commit messages. Counts and proportions are fine.
- Both docs gates must pass: `uv run pytest tests/test_check_docs.py -q` and `uv run python scripts/check_docs.py --max-violations 0`.
- Migration numbering continues from `0033`. This plan adds `0034`, `0035` and `0036`.

## File Structure

**Create:**
- `migrations/versions/0034_refund_amount_kind.py` — the `refund` value, the `amount_kind` CHECK, and the sign guard in `payment_edges`.
- `migrations/versions/0035_spend_facts.py` — `spend_lines`, `line_labels`, the sum trigger, and the `spend_facts` view.
- `migrations/versions/0036_charts.py` — the `charts` table.
- `src/library/charts/__init__.py` — package marker.
- `src/library/charts/rule.py` — the rule model and its translation to a SQL predicate. Pure; no session.
- `src/library/charts/query.py` — the aggregate and drill-through queries over `spend_facts`.
- `src/library/charts/footer.py` — the §9.4 accounting: what the rule touched but the total did not.
- `src/library/charts/draft.py` — question text -> proposed rule, via `messages.parse`.
- `src/library/spend_lines.py` — the write path for manual allocation.
- `src/library/api/spending.py` — the seven routes.
- `tests/test_spend_facts.py`, `tests/test_chart_rule.py`, `tests/test_chart_query.py`, `tests/test_chart_footer.py`, `tests/test_chart_draft.py`, `tests/test_spend_lines.py`, `tests/test_api_spending.py`.
- `docs/charts.md`, `journal/260829-chart-engine.md`.

**Modify:**
- `src/library/models.py` — `AmountKind.REFUND`, `AMOUNT_SIGN`, `SpendLine`, `LineLabel`, `Chart`.
- `src/library/extraction/schema.py` — `AMOUNT_KINDS` and the `amount_kind` field description.
- `src/library/money/backfill.py` — `AMOUNT_SYSTEM_PROMPT`.
- `src/library/money/payments.py` — the module docstring's rule list.
- `src/library/api/payments.py` — refuse a mixed-sign `MERGE` with a 400.
- `src/library/app.py` — register the spending router.
- `docs/architecture.md` — module-map entries for `library.charts` and `library.spend_lines`.

**Boundaries:** `rule.py` is pure and takes no session, so rule translation is testable without a database. `query.py` owns every `SELECT` against `spend_facts`; no router builds SQL. `footer.py` is separate from `query.py` because the footer answers the opposite question — what the total *missed* — and mixing the two is how "nothing is excluded silently" quietly stops being true.

---

### Task 1: The `refund` amount kind and the payment sign guard

Spec §8.1.1 and §8.3. Closes issue #117.

**Files:**
- Create: `migrations/versions/0034_refund_amount_kind.py`
- Modify: `src/library/models.py`, `src/library/extraction/schema.py`, `src/library/money/backfill.py`, `src/library/money/payments.py`, `src/library/api/payments.py`
- Test: `tests/test_money_schema.py`, `tests/test_payment_identity.py`, `tests/test_api_payments.py`

**Interfaces:**
- Produces: `AmountKind.REFUND`; `AMOUNT_SIGN: Mapping[AmountKind, int]`; `SUMMABLE_AMOUNT_KINDS: frozenset[AmountKind]` (now derived from `AMOUNT_SIGN`, same name and type as before).

**Background the implementer needs.** `documents.amount_kind` has **no database constraint today**. `sa.Enum(..., native_enum=False)` under SQLAlchemy 2.0 defaults `create_constraint=False`, so `0033` produced a bare `varchar(16)` and `INSERT ... amount_kind = 'not_a_real_kind'` currently succeeds. This migration adds the CHECK; it is not a swap.

- [ ] **Step 1: Write the failing vocabulary and sign tests**

Add to `tests/test_money_schema.py`:

```python
def test_refund_is_a_known_kind_and_contributes_negatively() -> None:
    from library.models import AMOUNT_SIGN, SUMMABLE_AMOUNT_KINDS, AmountKind

    assert AmountKind.REFUND in SUMMABLE_AMOUNT_KINDS
    assert AMOUNT_SIGN[AmountKind.REFUND] == -1
    assert AMOUNT_SIGN[AmountKind.PAYMENT_DUE] == 1
    assert AMOUNT_SIGN[AmountKind.PAYMENT_MADE] == 1
    assert AMOUNT_SIGN[AmountKind.ASSESSMENT] == 1


def test_summable_is_derived_from_the_sign_map_not_declared_twice() -> None:
    """Two hand-maintained lists are two lists that can disagree."""
    from library.models import AMOUNT_SIGN, SUMMABLE_AMOUNT_KINDS

    assert SUMMABLE_AMOUNT_KINDS == frozenset(AMOUNT_SIGN)


def test_a_non_contributing_kind_has_no_sign() -> None:
    from library.models import AMOUNT_SIGN, AmountKind

    for kind in (AmountKind.COVERAGE_LIMIT, AmountKind.BALANCE,
                 AmountKind.ESTIMATE, AmountKind.NONE):
        assert kind not in AMOUNT_SIGN


def test_the_database_now_rejects_a_kind_outside_the_vocabulary(
    api_database_url: str,
) -> None:
    """`0033` shipped `amount_kind` as an unconstrained varchar(16).

    Verified against Postgres: `'not_a_real_kind'` inserted successfully
    before `0034`. The vocabulary is load-bearing twice over — it decides
    what enters a total and what may merge — so it belongs in the database.
    """
    import sqlalchemy
    from sqlalchemy import create_engine, text

    engine = create_engine(
        api_database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    )
    with engine.begin() as connection:
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO documents (sha256, mime_type, status, source, "
                    "language, amount_kind, created_at, updated_at) VALUES "
                    "(:s, 'application/pdf', 'ready', 'upload', 'en', "
                    "'not_a_real_kind', now(), now())"
                ),
                {"s": "c" * 64},
            )
```

The existing `test_the_three_copies_of_the_amount_kinds_agree` already fails once `AmountKind` gains a value and the other two lists do not — leave it as it is; it is the guard that makes this task's edits complete.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_money_schema.py -q`
Expected: FAIL — `AttributeError: REFUND`, `ImportError: cannot import name 'AMOUNT_SIGN'`, and `test_the_database_now_rejects_a_kind_outside_the_vocabulary` fails because the INSERT succeeds.

- [ ] **Step 3: Add the value and the sign map**

In `src/library/models.py`, add `REFUND = "refund"` to `AmountKind` immediately after `ASSESSMENT` (contributing kinds first, then non-contributing — the enum's declaration order is its documentation), update the class docstring, and replace the frozenset:

```python
class AmountKind(enum.StrEnum):
    """What a document's ``amount_total`` actually is.

    ``amount_total`` is always a magnitude. The sign of a document's
    contribution to a spending total is a property of what the number
    *means*, so it is carried here and nowhere else — see ``AMOUNT_SIGN``.
    The non-contributing values exist so that a coverage ceiling, an opening
    balance, a quote or a nil-return confirmation can be recorded faithfully
    without contaminating a total.
    """

    PAYMENT_DUE = "payment_due"
    PAYMENT_MADE = "payment_made"
    ASSESSMENT = "assessment"
    REFUND = "refund"
    COVERAGE_LIMIT = "coverage_limit"
    BALANCE = "balance"
    ESTIMATE = "estimate"
    NONE = "none"


#: How each contributing kind enters a spending total. A kind absent from this
#: map never enters one, so "summable" and "signed" are the same predicate and
#: cannot drift apart. A refund is the only negative: money returned, or an
#: amount owed cancelled.
AMOUNT_SIGN: Mapping[AmountKind, int] = MappingProxyType(
    {
        AmountKind.PAYMENT_DUE: 1,
        AmountKind.PAYMENT_MADE: 1,
        AmountKind.ASSESSMENT: 1,
        AmountKind.REFUND: -1,
    }
)

SUMMABLE_AMOUNT_KINDS: frozenset[AmountKind] = frozenset(AMOUNT_SIGN)
```

Add `from collections.abc import Mapping` and `from types import MappingProxyType` to the imports if they are not already there.

- [ ] **Step 4: Update the other four vocabulary surfaces**

`src/library/extraction/schema.py` — add `"refund"` to `AMOUNT_KINDS` in the same position, and add this clause to the `amount_kind` field description, immediately after the `assessment` clause:

```
"refund (money returned to the reader, or an amount owed cancelled — a "
"credit note, refund receipt or reversal); "
```

`src/library/money/backfill.py` — add the matching line to `AMOUNT_SYSTEM_PROMPT`'s value list, aligned with the others:

```
  refund          money returned, or an amount owed cancelled
```

`src/library/money/payments.py` — the module docstring lists the rules in the order the view applies them. Add the precondition above them:

```
  SIGN  a refund never pairs with a non-refund      -> above every rule
```

- [ ] **Step 5: Write the migration**

`migrations/versions/0034_refund_amount_kind.py`:

```python
"""refund amount kind, the amount_kind check, and the payment sign guard

Revision ID: 0034
Revises: 0033

`amount_kind` arrived in 0033 as a bare varchar(16): SQLAlchemy 2.0 defaults
`Enum.create_constraint` to False, so `native_enum=False` produced no CHECK at
all and any string was accepted. This adds the constraint as well as the value.

The sign guard lives in the `pairs` CTE, above every rule rather than beside
them, because R2 (same sender, same reference, any date gap) is the strongest
rule and a credit note quotes the reference of the invoice it reverses.
Merging +X with -X erases both from a total; keeping them apart nets them to
zero, which is the right answer. Verified against Postgres: without the guard
a credit note and its invoice 90 days apart DO merge.
"""

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_AMOUNT_KINDS = (
    "payment_due",
    "payment_made",
    "assessment",
    "refund",
    "coverage_limit",
    "balance",
    "estimate",
    "none",
)

_SIGN_GUARD = (
    "    AND (a.amount_kind IS DISTINCT FROM 'refund')"
    " = (b.amount_kind IS DISTINCT FROM 'refund')\n"
)


def upgrade() -> None:
    values = ", ".join(f"'{kind}'" for kind in _AMOUNT_KINDS)
    op.execute(
        "ALTER TABLE documents ADD CONSTRAINT ck_documents_amount_kind "
        f"CHECK (amount_kind IS NULL OR amount_kind IN ({values}))"
    )
    # `payments` depends on `payment_edges`, so both come down and go back up.
    # Nothing depends on `payments` yet — 0035's `spend_facts` does, and any
    # later migration touching these views must drop and recreate it too or it
    # fails with DependentObjectsStillExist.
    op.execute("DROP VIEW payments")
    op.execute("DROP VIEW payment_edges")
    op.execute(_payment_edges_sql())
    op.execute(_payments_sql())


def downgrade() -> None:
    op.execute("DROP VIEW payments")
    op.execute("DROP VIEW payment_edges")
    op.execute(_payment_edges_sql(guard=""))
    op.execute(_payments_sql())
    op.execute("ALTER TABLE documents DROP CONSTRAINT ck_documents_amount_kind")
```

`_payment_edges_sql(guard=_SIGN_GUARD)` must return **the whole view from `0033` verbatim**, with `guard` inserted immediately before `  WHERE a.deleted_at IS NULL AND b.deleted_at IS NULL` in the `pairs` CTE. Copy the SQL from `migrations/versions/0033_money_facts.py` rather than retyping it — every comment in it records a bug that took three attempts to fix. `_payments_sql()` likewise returns `0033`'s `payments` view unchanged.

- [ ] **Step 6: Run the schema tests**

Run: `uv run pytest tests/test_money_schema.py -q`
Expected: PASS, including `test_the_three_copies_of_the_amount_kinds_agree`.

- [ ] **Step 7: Write the failing sign-guard tests**

Add to `tests/test_payment_identity.py`, following that file's existing `rows` fixture helper:

```python
@pytest.mark.asyncio
async def test_a_refund_does_not_merge_with_the_same_day_payment_it_reverses(
    api_database_url: str,
) -> None:
    groups = await _payment_groups(
        api_database_url,
        rows=[
            ("2026-04-01", "49.00", AmountKind.PAYMENT_MADE, None),
            ("2026-04-01", "49.00", AmountKind.REFUND, None),
        ],
    )
    assert len(groups) == 2, "R1 must not merge across opposite signs"


@pytest.mark.asyncio
async def test_a_credit_note_quoting_its_invoice_reference_does_not_merge(
    api_database_url: str,
) -> None:
    """The case the precondition exists for.

    R2 is the strongest rule and matches at any date gap. Without the guard
    these two merge, which was confirmed by executing the pre-0034 view.
    """
    groups = await _payment_groups(
        api_database_url,
        rows=[
            ("2026-04-01", "120.00", AmountKind.PAYMENT_DUE, "X-1"),
            ("2026-06-30", "120.00", AmountKind.REFUND, "X-1"),
        ],
    )
    assert len(groups) == 2


@pytest.mark.asyncio
async def test_an_undecided_kind_never_merges_with_a_refund(
    api_database_url: str,
) -> None:
    """NULL counts as not-a-refund: the cautious direction, and a NULL
    contributes to no total anyway."""
    groups = await _payment_groups(
        api_database_url,
        rows=[
            ("2026-04-01", "30.00", None, None),
            ("2026-04-01", "30.00", AmountKind.REFUND, None),
        ],
    )
    assert len(groups) == 2


@pytest.mark.asyncio
async def test_the_guard_does_not_break_the_rules_it_sits_above(
    api_database_url: str,
) -> None:
    positive = await _payment_groups(
        api_database_url,
        rows=[
            ("2026-04-01", "77.00", AmountKind.PAYMENT_DUE, None),
            ("2026-04-01", "77.00", AmountKind.PAYMENT_MADE, None),
        ],
    )
    assert len(positive) == 1, "R1 must still fire between two positive kinds"

    both_refunds = await _payment_groups(
        api_database_url,
        rows=[
            ("2026-04-01", "15.00", AmountKind.REFUND, None),
            ("2026-04-01", "15.00", AmountKind.REFUND, None),
        ],
    )
    assert len(both_refunds) == 1, "two refunds on one day are still one payment"
```

`_payment_groups` is a thin wrapper over the file's existing document-inserting helper that returns `set` of `payment_id`; if that file already exposes an equivalent, use it rather than adding a second.

- [ ] **Step 8: Run them against the pre-migration view to watch three fail**

Run: `uv run pytest tests/test_payment_identity.py -q -k "refund or credit_note or undecided or sits_above"`
Expected: the first three FAIL (each returns 1 group, not 2) and `test_the_guard_does_not_break_the_rules_it_sits_above` PASSES. **If all four pass before the migration, the migration is not being applied — stop and find out why.** This is the check that distinguishes a working guard from a test that cannot fail.

- [ ] **Step 9: Apply the migration and re-run**

Run: `uv run pytest tests/test_payment_identity.py -q`
Expected: PASS, all cases, including every pre-existing one.

- [ ] **Step 10: Refuse a mixed-sign MERGE override**

Spec §8.3: the rules cannot merge across signs, so a human override must not either, or the "one payment group, one sign" invariant every chart total depends on holds only by luck.

Add to `tests/test_api_payments.py`:

```python
def test_merging_a_refund_with_a_payment_is_refused(api_client: TestClient) -> None:
    paid = _make_document(api_client, amount_kind="payment_made", amount="49.00")
    back = _make_document(api_client, amount_kind="refund", amount="49.00")
    response = api_client.post(
        "/api/payments/merge", json={"doc_a": paid, "doc_b": back}
    )
    assert response.status_code == 400
    assert "sign" in response.json()["detail"].lower()
```

Then in `src/library/api/payments.py`, inside the merge route after `_require_both_exist`:

```python
async def _refuse_mixed_sign(session: AsyncSession, doc_a: int, doc_b: int) -> None:
    """A payment group must have one sign (spec §8.3).

    The rules cannot merge across signs; without this an override could, and
    a group holding both +X and -X has no defined contribution to a total.
    """
    rows = (
        await session.execute(
            select(Document.amount_kind).where(Document.id.in_((doc_a, doc_b)))
        )
    ).scalars().all()
    # A set of two booleans has two members only when the signs differ.
    # `_require_both_exist` has already guaranteed there are exactly two rows.
    signs = {kind == AmountKind.REFUND for kind in rows}
    if len(signs) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot merge a refund with a non-refund: a payment group must have one sign.",
        )
```

Call it from the merge route only. The split route is unaffected — splitting is always safe.

- [ ] **Step 11: Run the full money suite and the gates**

```bash
uv run pytest tests/test_money_schema.py tests/test_payment_identity.py \
  tests/test_api_payments.py tests/test_money_backfill.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
```
Expected: all PASS.

- [ ] **Step 12: Commit**

```bash
git add migrations/versions/0034_refund_amount_kind.py src/library tests
git commit -m "feat(money): a refund kind, a signed amount map, and a payment sign guard

Closes #117."
```

---

### Task 2: Spend lines

Spec §8.4 and §5.1. A document either has no lines (the common case) or a complete set that sums to its total.

**Scope:** schema plus the manual write path. Extraction proposing lines is **deferred** — spec §14's open question 3 sets its own test ("widen only if the narrow rule is observed to miss real splits") and the archive has not yet been shown to contain one.

**Files:**
- Create: `migrations/versions/0035_spend_facts.py` (tables and trigger; the view is Task 3), `src/library/spend_lines.py`
- Modify: `src/library/models.py`, `docs/architecture.md`
- Test: `tests/test_spend_lines.py`

**Interfaces:**
- Consumes: `AmountKind` (Task 1).
- Produces:
  - `SpendLine` / `LineLabel` ORM models.
  - `async def replace_lines(session: AsyncSession, document_id: int, lines: Sequence[LineInput]) -> list[SpendLine]` — replaces a document's whole allocation in one transaction. Raises `AllocationError` when the lines do not sum to `amount_total`.
  - `async def clear_lines(session: AsyncSession, document_id: int) -> None`.
  - `class LineInput(BaseModel): amount: Decimal; note: str | None = None; labels: dict[str, str] = {}` — `labels` maps facet key to facet value key.
  - `class AllocationError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_spend_lines.py`:

```python
"""The allocation write path.

Every case here is shaped so that a plausible-but-wrong implementation goes
red: the sum check is exercised from both directions, the replace is run
A -> B -> A because a one-way run proves nothing, and the deferred trigger is
proved deferred by inserting a set whose first row alone does not balance.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from library.spend_lines import AllocationError, LineInput, clear_lines, replace_lines


@pytest.mark.asyncio
async def test_lines_that_sum_to_the_total_are_accepted(session, document) -> None:
    doc = await document(amount_total=Decimal("100.00"))
    lines = await replace_lines(
        session,
        doc.id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    assert [line.amount for line in lines] == [Decimal("60.00"), Decimal("40.00")]


@pytest.mark.asyncio
async def test_lines_that_undershoot_are_rejected(session, document) -> None:
    doc = await document(amount_total=Decimal("100.00"))
    with pytest.raises(AllocationError):
        await replace_lines(session, doc.id, [LineInput(amount=Decimal("60.00"))])


@pytest.mark.asyncio
async def test_lines_that_overshoot_are_rejected(session, document) -> None:
    """The other direction. A check written as `sum <= total` passes the
    undershoot test and fails only here."""
    doc = await document(amount_total=Decimal("100.00"))
    with pytest.raises(AllocationError):
        await replace_lines(
            session,
            doc.id,
            [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("60.00"))],
        )


@pytest.mark.asyncio
async def test_the_sum_constraint_is_deferred_within_the_transaction(
    session, document
) -> None:
    """A two-line split must insert as one transaction.

    An IMMEDIATE constraint fails on the first row, because 60 != 100 at that
    instant. This is the case the DEFERRABLE INITIALLY DEFERRED trigger
    exists for, and it is invisible in any single-line test.
    """
    doc = await document(amount_total=Decimal("100.00"))
    lines = await replace_lines(
        session,
        doc.id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_replacing_an_allocation_and_restoring_it_returns_the_original(
    session, document
) -> None:
    """A -> B -> A. Running a reversible operation one way proves nothing."""
    doc = await document(amount_total=Decimal("100.00"))
    original = [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))]
    await replace_lines(session, doc.id, original)
    await replace_lines(
        session,
        doc.id,
        [
            LineInput(amount=Decimal("25.00")),
            LineInput(amount=Decimal("25.00")),
            LineInput(amount=Decimal("50.00")),
        ],
    )
    restored = await replace_lines(session, doc.id, original)
    assert [line.amount for line in restored] == [Decimal("60.00"), Decimal("40.00")]
    assert len(restored) == 2, "the three-line allocation must be gone, not appended to"


@pytest.mark.asyncio
async def test_clearing_lines_leaves_the_document_unsplit(session, document) -> None:
    doc = await document(amount_total=Decimal("100.00"))
    await replace_lines(
        session,
        doc.id,
        [LineInput(amount=Decimal("60.00")), LineInput(amount=Decimal("40.00"))],
    )
    await clear_lines(session, doc.id)
    assert await _line_count(session, doc.id) == 0


@pytest.mark.asyncio
async def test_a_line_label_must_belong_to_the_facet_it_claims(
    session, document, facets
) -> None:
    """The composite foreign key, not a convention.

    Without it a line can claim facet `scope` while pointing at a `category`
    value, and the GROUP BY invariant silently breaks.
    """
    doc = await document(amount_total=Decimal("100.00"))
    with pytest.raises(Exception):
        await replace_lines(
            session,
            doc.id,
            [LineInput(amount=Decimal("100.00"), labels={"scope": "services"})],
        )
```

`_line_count` is a two-line helper over `select(func.count()).select_from(SpendLine)`. The `session`, `document` and `facets` fixtures follow the patterns already in `tests/test_money_schema.py` and `tests/test_facets.py`; reuse them rather than writing new ones.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_spend_lines.py -q`
Expected: FAIL — `ModuleNotFoundError: library.spend_lines`.

- [ ] **Step 3: Add the models**

In `src/library/models.py`, after `DocumentLabel`:

```python
class SpendLine(Base):
    """One part of a document's amount, when the money divides.

    A document has either no lines at all — the common case, and the one
    ``spend_facts`` synthesises a row for — or a complete set summing to
    ``amount_total``. There is no partial state; the sum is enforced by a
    deferred constraint trigger rather than by application code.
    """

    __tablename__ = "spend_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[SpendLineOrigin] = mapped_column(
        Enum(SpendLineOrigin, name="spend_line_origin", native_enum=False, length=16),
        default=SpendLineOrigin.MANUAL,
    )


class LineLabel(Base):
    """One line's value for one facet. Overrides the document's, if any."""

    __tablename__ = "line_labels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["facet_value_id", "facet_id"],
            ["facet_values.id", "facet_values.facet_id"],
            name="line_labels_value_facet",
        ),
    )

    line_id: Mapped[int] = mapped_column(
        ForeignKey("spend_lines.id", ondelete="CASCADE"), primary_key=True
    )
    facet_id: Mapped[int] = mapped_column(
        ForeignKey("facets.id", ondelete="RESTRICT"), primary_key=True
    )
    facet_value_id: Mapped[int] = mapped_column(Integer)
```

And the origin enum beside `AmountKind`:

```python
class SpendLineOrigin(enum.StrEnum):
    """Where a line came from. Only ``MANUAL`` is produced today; extraction
    proposing lines is deferred (spec §14, open question 3)."""

    EXTRACTED = "extracted"
    MANUAL = "manual"
```

- [ ] **Step 4: Write the migration**

`migrations/versions/0035_spend_facts.py` — tables and trigger only; the view is added by Task 3 in the same file.

```python
"""spend lines, line labels, and the spend_facts relation

Revision ID: 0035
Revises: 0034
"""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "spend_lines",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "origin", sa.String(16), nullable=False, server_default="manual"
        ),
        sa.CheckConstraint(
            "origin IN ('extracted','manual')", name="ck_spend_lines_origin"
        ),
    )
    op.create_index("ix_spend_lines_document", "spend_lines", ["document_id"])
    op.create_table(
        "line_labels",
        sa.Column(
            "line_id",
            sa.BigInteger(),
            sa.ForeignKey("spend_lines.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "facet_id",
            sa.Integer(),
            sa.ForeignKey("facets.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("facet_value_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["facet_value_id", "facet_id"],
            ["facet_values.id", "facet_values.facet_id"],
            name="line_labels_value_facet",
        ),
    )
    # DEFERRABLE INITIALLY DEFERRED: a two-line split inserts as one
    # transaction. An immediate check fails on the first row, because that
    # row alone never equals the document total.
    op.execute("""
CREATE FUNCTION spend_lines_sum_matches() RETURNS trigger AS $$
DECLARE
  doc_total numeric(14,2);
  line_total numeric(14,2);
  doc_id bigint := COALESCE(NEW.document_id, OLD.document_id);
BEGIN
  SELECT amount_total INTO doc_total FROM documents WHERE id = doc_id;
  SELECT COALESCE(sum(amount), 0) INTO line_total
    FROM spend_lines WHERE document_id = doc_id;
  IF EXISTS (SELECT 1 FROM spend_lines WHERE document_id = doc_id)
     AND line_total IS DISTINCT FROM doc_total THEN
    RAISE EXCEPTION
      'spend lines for document % sum to % but the document total is %',
      doc_id, line_total, doc_total;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER spend_lines_sum_matches_trigger
AFTER INSERT OR UPDATE OR DELETE ON spend_lines
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION spend_lines_sum_matches();
""")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS spend_lines_sum_matches_trigger ON spend_lines")
    op.execute("DROP FUNCTION IF EXISTS spend_lines_sum_matches()")
    op.drop_table("line_labels")
    op.drop_index("ix_spend_lines_document", table_name="spend_lines")
    op.drop_table("spend_lines")
```

Note the `EXISTS` — a fully cleared allocation is legal and must not fire the check. Test `test_clearing_lines_leaves_the_document_unsplit` is what proves this clause; without it, `clear_lines` raises. **This is a correction to an earlier draft of this plan, which tested `line_total <> 0`.** That clause tested the *sum* when it meant *"there are no lines"*: a document legitimately allocated across lines summing to zero (`0.00` split `[0.00, 0.00]`, or `[50.00, -50.00]`) would then let its `amount_total` be corrected to anything at all, and the allocation would be silently orphaned. `EXISTS` says what is meant. Migration 0035 as shipped also binds this same function to a second constraint trigger on `documents`, so the invariant is kept from both sides.

**Executed against Postgres 17 before this plan was written, and corrected by Task 2's own execution.** Three things settled: (1) the block does **not** go through one `op.execute` — Alembic runs over **asyncpg** here (`migrations/env.py`), which prepares every statement, so a multi-statement string fails with `cannot insert multiple commands into a prepared statement`. Task 2 hit exactly that and split it into one `op.execute` per statement; the `$$`-quoted body is itself a single statement and survives the split intact. (2) `text()` does **not** mis-parse the `:=` in `doc_id bigint := COALESCE(...)` as a bind parameter, so no escaping is needed. (3) Under asyncpg a plpgsql `RAISE EXCEPTION` surfaces as a bare `sqlalchemy.exc.DBAPIError`, **not** the `ProgrammingError` psycopg produces for the same raise — an earlier draft of this paragraph asserted the psycopg behaviour, which this repo never sees. Prefer asserting on `AllocationError` from the Python pre-check and let the trigger be the backstop it is; a composite-FK violation, by contrast, does map, and arrives as `IntegrityError`.

- [ ] **Step 5: Write the write path**

`src/library/spend_lines.py`:

```python
"""Manual allocation of a document's amount across spend lines.

Replace-whole rather than patch-one: a document's allocation is only ever
valid as a complete set, so a partial write has no meaning. Every mutation
goes through one transaction and the deferred constraint trigger checks the
sum once, at commit, rather than row by row.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Facet, FacetValue, LineLabel, SpendLine


class AllocationError(ValueError):
    """The proposed lines do not form a valid allocation."""


class LineInput(BaseModel):
    amount: Decimal
    note: str | None = None
    #: facet key -> facet value key. Only facets that DIFFER from the
    #: document's own labels need to appear; the rest are inherited by
    #: `spend_facts`, not copied here.
    labels: dict[str, str] = {}


async def replace_lines(
    session: AsyncSession, document_id: int, lines: Sequence[LineInput]
) -> list[SpendLine]:
    total = await session.scalar(
        select(Document.amount_total).where(Document.id == document_id)
    )
    if total is None:
        raise AllocationError("a document with no amount cannot be allocated")
    proposed = sum((line.amount for line in lines), Decimal("0"))
    if proposed != total:
        raise AllocationError(
            f"lines sum to {proposed} but the document total is {total}"
        )
    await clear_lines(session, document_id)
    created: list[SpendLine] = []
    for line in lines:
        row = SpendLine(document_id=document_id, amount=line.amount, note=line.note)
        session.add(row)
        await session.flush()
        for facet_key, value_key in line.labels.items():
            resolved = await _resolve(session, facet_key, value_key)
            session.add(
                LineLabel(
                    line_id=row.id,
                    facet_id=resolved[0],
                    facet_value_id=resolved[1],
                )
            )
        created.append(row)
    await session.flush()
    return created


async def clear_lines(session: AsyncSession, document_id: int) -> None:
    """Remove a document's whole allocation. `line_labels` cascades."""
    await session.execute(delete(SpendLine).where(SpendLine.document_id == document_id))
    await session.flush()


async def _resolve(session: AsyncSession, facet_key: str, value_key: str) -> tuple[int, int]:
    """Facet key + value key -> (facet_id, facet_value_id).

    Resolved here rather than accepting raw ids so a caller cannot pair a
    facet with another facet's value; the composite foreign key would catch
    it, but a 500 is a worse answer than a named error.
    """
    row = (
        await session.execute(
            select(FacetValue.facet_id, FacetValue.id)
            .join(Facet, Facet.id == FacetValue.facet_id)
            .where(Facet.key == facet_key, FacetValue.key == value_key)
        )
    ).one_or_none()
    if row is None:
        raise AllocationError(f"no value '{value_key}' in facet '{facet_key}'")
    return int(row[0]), int(row[1])
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_spend_lines.py -q`
Expected: PASS.

- [ ] **Step 7: Add the module-map entry**

`docs/architecture.md` gains a row for `library.spend_lines` — "manual allocation of a document's amount across spend lines". `tests/test_check_docs.py::TestModuleMap` fails without it; plan 1 went red on this for six tasks.

- [ ] **Step 8: Run the gates and commit**

```bash
uv run pytest tests/test_spend_lines.py tests/test_check_docs.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add migrations src/library tests docs/architecture.md
git commit -m "feat(charts): spend lines and the manual allocation write path"
```

---

### Task 3: The `spend_facts` relation

Spec §5.1. One relation feeds every chart, so label inheritance and the payment collapse each have exactly one place to be tested.

**This SQL was executed against PostgreSQL 17 and mutation-checked. Transcribe it; do not redesign it.** Spec §5.2 records what breaking each clause did.

**Files:**
- Modify: `migrations/versions/0035_spend_facts.py` (adds the view to Task 2's migration)
- Test: `tests/test_spend_facts.py`

**Interfaces:**
- Consumes: `spend_lines`, `line_labels` (Task 2); `payments` (0033, as amended by Task 1).
- Produces: the `spend_facts` view, with columns `document_id, line_id, payment_id, is_canonical, sender_id, date, amount, currency, amount_kind, reference, labels`. `labels` is `jsonb` mapping facet key to facet value key. Every chart query in Tasks 5–8 reads this and nothing else.

- [ ] **Step 1: Write the failing tests**

`tests/test_spend_facts.py`. These are the prototype's cases, each adversarial by construction:

```python
"""The one relation every chart reads.

Each case is built so a plausible-but-wrong view goes red. Three in
particular exist because the obvious SQL is wrong, not because the correct
SQL is subtle: NULLs sort first under DESC, a deleted twin can hold the
canonical slot, and a merged pair's two documents can carry different
labels.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_an_unsplit_document_becomes_one_row_carrying_its_labels(
    session, document, facets
) -> None:
    doc = await document(amount_total=Decimal("100.00"), labels={"category": "services"})
    rows = await _facts(session)
    assert len(rows) == 1
    assert rows[0]["line_id"] is None
    assert rows[0]["amount"] == Decimal("100.00")
    assert rows[0]["labels"] == {"category": "services"}
    assert rows[0]["is_canonical"] is True


@pytest.mark.asyncio
async def test_a_deleted_twin_neither_appears_nor_holds_the_canonical_slot(
    session, document
) -> None:
    """Adversarial: the deleted document has the LOWER id and is
    `payment_made`, so it wins every tie-break. A view that filters deletes
    only in its outer SELECT still ranks it, and the live document comes
    back is_canonical=false — contributing to no total, silently.

    The guarantee actually comes from the join to `payments`, which excludes
    deleted documents. Do not remove that join.
    """
    dead = await document(amount_total=Decimal("50.00"), amount_kind="payment_made",
                          deleted=True)
    live = await document(amount_total=Decimal("50.00"), amount_kind="payment_due")
    rows = await _facts(session)
    assert dead.id < live.id
    assert [row["document_id"] for row in rows] == [live.id]
    assert rows[0]["is_canonical"] is True


@pytest.mark.asyncio
async def test_an_undecided_kind_does_not_outrank_payment_made(
    session, document
) -> None:
    """`(amount_kind = 'payment_made') DESC` is NULL for an undecided
    document, and Postgres sorts NULLs FIRST under DESC. Without the
    COALESCE the undecided document becomes canonical and the payment is
    represented by a kind that is never summed. Confirmed red by mutation.
    """
    undecided = await document(amount_total=Decimal("40.00"), amount_kind=None)
    made = await document(amount_total=Decimal("40.00"), amount_kind="payment_made")
    rows = await _facts(session)
    assert undecided.id < made.id
    assert {row["document_id"] for row in rows if row["is_canonical"]} == {made.id}


@pytest.mark.asyncio
async def test_a_line_bearing_document_wins_canonical_despite_a_higher_id(
    session, document
) -> None:
    """Otherwise merging an itemised invoice with its receipt discards the
    split, and the allocation the owner made by hand disappears."""
    receipt = await document(amount_total=Decimal("100.00"), amount_kind="payment_made")
    invoice = await document(amount_total=Decimal("100.00"), amount_kind="payment_due")
    await _allocate(session, invoice.id, [Decimal("60.00"), Decimal("40.00")])
    rows = await _facts(session)
    assert receipt.id < invoice.id
    assert {row["document_id"] for row in rows if row["is_canonical"]} == {invoice.id}
    assert sum(r["amount"] for r in rows if r["is_canonical"]) == Decimal("100.00")


@pytest.mark.asyncio
async def test_a_line_overrides_one_facet_and_inherits_the_rest(
    session, document, facets
) -> None:
    doc = await document(
        amount_total=Decimal("100.00"),
        labels={"category": "services", "scope": "business"},
    )
    await _allocate(
        session,
        doc.id,
        [(Decimal("60.00"), {}), (Decimal("40.00"), {"scope": "personal"})],
    )
    rows = await _facts(session)
    assert [row["labels"] for row in rows] == [
        {"category": "services", "scope": "business"},
        {"category": "services", "scope": "personal"},
    ]


@pytest.mark.asyncio
async def test_a_merged_pair_contributes_once_under_the_canonical_labels(
    session, document, facets
) -> None:
    """Two properties on one fixture: no double count, and the money lands
    under the CANONICAL document's labels.

    Adversarial: the pair's two documents carry DIFFERENT categories, so a
    view that lets the non-canonical row through does not merely double the
    total — it moves money into a category the owner never chose.
    """
    invoice = await document(
        amount_total=Decimal("250.00"), amount_kind="payment_due",
        labels={"category": "supplies"},
    )
    await document(
        amount_total=Decimal("250.00"), amount_kind="payment_made",
        labels={"category": "services"},
    )
    totals = await _totals_by(session, "category")
    assert totals == {"services": Decimal("250.00")}
    assert "supplies" not in totals
    assert invoice is not None


@pytest.mark.asyncio
async def test_the_total_is_invariant_across_split_axes(
    session, document, facets
) -> None:
    await document(amount_total=Decimal("250.00"), amount_kind="payment_made",
                   labels={"category": "services", "scope": "business"})
    await document(amount_total=Decimal("80.00"), amount_kind="payment_made",
                   labels={"category": "services", "scope": "personal"})
    flat = await _totals_by(session, None)
    assert sum(flat.values()) == Decimal("330.00")
    for axis in ("category", "scope", "sender"):
        assert sum((await _totals_by(session, axis)).values()) == Decimal("330.00")


@pytest.mark.asyncio
async def test_merge_then_split_then_merge_returns_to_the_merged_total(
    session, document
) -> None:
    """A -> B -> A. Running a reversible operation one way proves nothing."""
    a = await document(amount_total=Decimal("90.00"), amount_kind="payment_due")
    b = await document(amount_total=Decimal("90.00"), amount_kind="payment_made")
    merged = await _totals_by(session, None)
    await _override(session, "SPLIT", a.id, b.id)
    after_split = await _totals_by(session, None)
    await _override(session, "MERGE", a.id, b.id)
    after_remerge = await _totals_by(session, None)
    assert sum(merged.values()) == Decimal("90.00")
    assert sum(after_split.values()) == Decimal("180.00")
    assert after_remerge == merged


@pytest.mark.asyncio
async def test_a_refund_lowers_the_total_of_the_category_it_belongs_to(
    session, document, facets
) -> None:
    await document(amount_total=Decimal("200.00"), amount_kind="payment_made",
                   document_date="2026-04-01", labels={"category": "services"})
    await document(amount_total=Decimal("49.00"), amount_kind="refund",
                   document_date="2026-05-01", labels={"category": "services"})
    assert await _signed_totals_by(session, "category") == {
        "services": Decimal("151.00")
    }
```

Helpers: `_facts` selects `* FROM spend_facts ORDER BY document_id, line_id NULLS FIRST` as dicts; `_totals_by(axis)` sums `amount` over `is_canonical` rows whose `amount_kind` is in `SUMMABLE_AMOUNT_KINDS`, grouped by `labels->>axis` (or `CAST(sender_id AS text)` for `"sender"`, or one bucket for `None`); `_signed_totals_by` is the same with `AMOUNT_SIGN` applied; `_allocate` wraps `replace_lines` from Task 2 and accepts either a bare `Decimal` or a `(Decimal, labels)` pair; `_override` inserts a `payment_overrides` row with `created_at = now()` for `SPLIT` and `now() + interval '1 second'` for the re-`MERGE`, because the later correction wins and identical timestamps fall to the SPLIT.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_spend_facts.py -q`
Expected: FAIL — `relation "spend_facts" does not exist`.

- [ ] **Step 3: Add the view to `0035`'s `upgrade()`**

Append to `upgrade()`, after the trigger. This is the executed SQL:

```python
    op.execute("""
CREATE VIEW spend_facts AS
WITH doc_labels AS (
  SELECT dl.document_id, jsonb_object_agg(f.key, fv.key) AS labels
  FROM document_labels dl
  JOIN facets f ON f.id = dl.facet_id
  JOIN facet_values fv ON fv.id = dl.facet_value_id
  GROUP BY dl.document_id
),
line_lbls AS (
  SELECT ll.line_id, jsonb_object_agg(f.key, fv.key) AS labels
  FROM line_labels ll
  JOIN facets f ON f.id = ll.facet_id
  JOIN facet_values fv ON fv.id = ll.facet_value_id
  GROUP BY ll.line_id
),
-- The join to `payments` is what excludes deleted documents: `payments`
-- builds its reachability from `documents WHERE deleted_at IS NULL`. The
-- filter below is defence, not the guarantee — removing it changes no
-- result, which was proved by mutation. Removing the JOIN would.
eligible AS (
  SELECT d.id, d.sender_id, d.document_date, d.amount_total, d.currency,
         d.amount_kind, d.reference, p.payment_id,
         EXISTS (SELECT 1 FROM spend_lines sl WHERE sl.document_id = d.id) AS has_lines
  FROM documents d
  JOIN payments p ON p.document_id = d.id
  WHERE d.deleted_at IS NULL AND d.amount_total IS NOT NULL
),
-- Exactly one document per payment contributes its money, or the merge would
-- not have removed the double count. A line-bearing document wins first, or
-- merging an itemised invoice with its receipt would discard the split.
--
-- COALESCE(..., false) is load-bearing: `amount_kind = 'payment_made'` is
-- NULL for an undecided document and Postgres sorts NULLs FIRST under DESC,
-- so without it an undecided document becomes canonical and the payment is
-- represented by a kind that is never summed. Confirmed by mutation.
ranked AS (
  SELECT e.*, row_number() OVER (
           PARTITION BY e.payment_id
           ORDER BY e.has_lines DESC,
                    COALESCE(e.amount_kind = 'payment_made', false) DESC,
                    e.id ASC
         ) = 1 AS is_canonical
  FROM eligible e
)
SELECT r.id AS document_id, NULL::bigint AS line_id, r.payment_id, r.is_canonical,
       r.sender_id, r.document_date AS date, r.amount_total AS amount, r.currency,
       r.amount_kind, r.reference,
       COALESCE(dl.labels, '{}'::jsonb) AS labels
FROM ranked r
LEFT JOIN doc_labels dl ON dl.document_id = r.id
WHERE NOT r.has_lines
UNION ALL
-- `||` on jsonb takes the RIGHT operand on a key collision, which is exactly
-- the inheritance rule: a line overrides the facets it names and inherits
-- the rest from its document.
SELECT r.id, sl.id, r.payment_id, r.is_canonical,
       r.sender_id, r.document_date, sl.amount, r.currency,
       r.amount_kind, r.reference,
       COALESCE(dl.labels, '{}'::jsonb) || COALESCE(ll.labels, '{}'::jsonb)
FROM ranked r
JOIN spend_lines sl ON sl.document_id = r.id
LEFT JOIN doc_labels dl ON dl.document_id = r.id
LEFT JOIN line_lbls ll ON ll.line_id = sl.id
WHERE r.has_lines
""")
```

And at the top of `downgrade()`, before the trigger drop: `op.execute("DROP VIEW IF EXISTS spend_facts")`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_spend_facts.py -q`
Expected: PASS, all nine.

- [ ] **Step 5: Prove the tests can fail**

This step is not optional. Temporarily edit the view in the migration to `(e.amount_kind = 'payment_made') DESC` — dropping the COALESCE — re-run the migration and the suite.

Run: `uv run pytest tests/test_spend_facts.py -q`
Expected: `test_an_undecided_kind_does_not_outrank_payment_made` FAILS. Restore the COALESCE and confirm green again. A suite that cannot go red is a suite that proves nothing; this repository has shipped a green branch that did nothing at all.

- [ ] **Step 6: Index the label lookups (a correction to spec §5.1)**

Spec §5.1 says "a GIN index on `labels` serves both" the rule filter and the split. **That is not buildable.** `labels` is computed by `jsonb_object_agg` inside the view, so there is no stored column to index — Postgres rejects `CREATE INDEX` on a view outright. The spec sentence was written against an earlier draft in which `labels` was a materialised column.

The lookups that actually cost anything are the joins that build `labels`, so index those. Add to `upgrade()`:

```python
    op.create_index("ix_line_labels_line", "line_labels", ["line_id"])
```

`document_labels` already has `(document_id, facet_id)` as its primary key, so its document lookup is covered; `line_labels` has `(line_id, facet_id)` for the same reason, making this index redundant **today**. Add it anyway only if `EXPLAIN` on `_totals_by` shows a sequential scan on `line_labels`; otherwise skip this step and record why.

Run `EXPLAIN (ANALYZE, BUFFERS)` on the aggregate query from Task 6 against a seeded database before deciding. **Do not add an index no plan uses** — measure, then decide, and write the measurement into `docs/charts.md`.

If §5.1's sentence needs correcting in the spec, correct it there in Task 11 rather than leaving the plan and the spec disagreeing.

- [ ] **Step 7: Run the gates and commit**

```bash
uv run pytest tests/test_spend_facts.py tests/test_spend_lines.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add migrations tests
git commit -m "feat(charts): the spend_facts relation"
```

---

### Task 4: The chart rule

Spec §9.1. A chart is a saved question; its rule is what turns that question into a set of `spend_facts` rows.

**Files:**
- Create: `src/library/charts/__init__.py`, `src/library/charts/rule.py`
- Modify: `docs/architecture.md`
- Test: `tests/test_chart_rule.py`

**Interfaces:**
- Produces:
  - `class Clause(BaseModel): facet: str; op: Literal["in", "not_in"]; values: list[str]`
  - `class Rule(BaseModel): all: list[Clause] = []` — clauses are ANDed. An empty rule matches everything, which is what the seeded "All spending" chart is (§10.1).
  - `def rule_predicate(rule: Rule) -> tuple[str, dict[str, object]]` — a SQL fragment over a `spend_facts` alias `sf`, plus its bind parameters. Returns `("TRUE", {})` for an empty rule.
  - `class RuleError(ValueError)`.

**Why this module is pure.** It takes no session and touches no database, so rule translation is testable exhaustively in milliseconds. Every `SELECT` lives in Task 5's `query.py`.

- [ ] **Step 1: Write the failing tests**

`tests/test_chart_rule.py`:

```python
"""Rule -> SQL predicate. Pure; no database.

The injection cases are not paranoia: facet and value keys reach this module
from an LLM draft (Task 8) as well as from the owner, so they are untrusted
input by construction.
"""

from __future__ import annotations

import pytest

from library.charts.rule import Clause, Rule, RuleError, rule_predicate


def test_an_empty_rule_matches_everything() -> None:
    """The seeded "All spending" chart is an empty rule (spec §10.1)."""
    sql, params = rule_predicate(Rule())
    assert sql == "TRUE"
    assert params == {}


def test_a_single_in_clause_reads_the_labels_column() -> None:
    sql, params = rule_predicate(
        Rule(all=[Clause(facet="category", op="in", values=["software"])])
    )
    assert sql == "(sf.labels->>:f0 = ANY(:v0))"
    assert params == {"f0": "category", "v0": ["software"]}


def test_clauses_are_anded() -> None:
    sql, params = rule_predicate(
        Rule(
            all=[
                Clause(facet="category", op="in", values=["software"]),
                Clause(facet="cost_type", op="in", values=["subscription", "usage"]),
            ]
        )
    )
    assert sql == "(sf.labels->>:f0 = ANY(:v0)) AND (sf.labels->>:f1 = ANY(:v1))"
    assert params["v1"] == ["subscription", "usage"]


def test_not_in_excludes_unlabelled_rows_too() -> None:
    """A row with no value for the facet has `labels->>facet IS NULL`, and
    `NULL <> ANY(...)` is NULL, not TRUE — so a naive negation drops every
    unlabelled row from a `not_in` result AND from its complement. Both
    would then under-report, and §9.4's footer would be the only place the
    money appeared.
    """
    sql, _ = rule_predicate(
        Rule(all=[Clause(facet="scope", op="not_in", values=["business"])])
    )
    assert sql == "(sf.labels->>:f0 IS NULL OR NOT (sf.labels->>:f0 = ANY(:v0)))"


def test_a_clause_with_no_values_is_rejected() -> None:
    with pytest.raises(RuleError):
        rule_predicate(Rule(all=[Clause(facet="category", op="in", values=[])]))


def test_facet_and_value_keys_are_bound_never_interpolated() -> None:
    """Keys arrive from an LLM draft as well as from the owner."""
    sql, params = rule_predicate(
        Rule(all=[Clause(facet="'; DROP TABLE documents; --", op="in", values=["x"])])
    )
    assert "DROP TABLE" not in sql
    assert params["f0"] == "'; DROP TABLE documents; --"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_chart_rule.py -q`
Expected: FAIL — `ModuleNotFoundError: library.charts.rule`.

**The `not_in` form was executed against Postgres, not reasoned about.** Over three rows — `category=services`, `category=supplies`, and one with no label — the naive `NOT (labels->>:f = ANY(:v))` returns **only `supplies`**: the unlabelled row satisfies neither the rule nor its complement and vanishes from both. The specified form returns `{supplies, NULL}`, and the two forms partition all three rows with no overlap. That partition is what makes the total split-invariant (§9.2), so this is not a cosmetic detail.

- [ ] **Step 3: Implement**

`src/library/charts/rule.py`:

```python
"""A chart's rule: which `spend_facts` rows the question is asking about.

Pure — no session, no I/O — so the translation is exhaustively testable
without a database. Facet and value keys are always bound, never
interpolated: they reach here from an LLM draft as well as from the owner.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RuleError(ValueError):
    """The rule cannot be translated into a predicate."""


class Clause(BaseModel):
    facet: str
    op: Literal["in", "not_in"] = "in"
    values: list[str]


class Rule(BaseModel):
    #: ANDed. Empty matches every row — that is the "All spending" chart.
    all: list[Clause] = []


def rule_predicate(rule: Rule) -> tuple[str, dict[str, object]]:
    """Translate a rule into a SQL fragment over the alias ``sf``."""
    if not rule.all:
        return "TRUE", {}
    fragments: list[str] = []
    params: dict[str, object] = {}
    for index, clause in enumerate(rule.all):
        if not clause.values:
            raise RuleError(f"clause {index} on facet '{clause.facet}' has no values")
        facet_key, values_key = f"f{index}", f"v{index}"
        params[facet_key] = clause.facet
        params[values_key] = list(clause.values)
        member = f"sf.labels->>:{facet_key} = ANY(:{values_key})"
        if clause.op == "in":
            fragments.append(f"({member})")
        else:
            # A row with no value for this facet has labels->>facet IS NULL,
            # and NULL <> ANY(...) is NULL rather than TRUE. Without the
            # explicit IS NULL arm an unlabelled row satisfies neither a
            # `not_in` rule nor its complement, and disappears from both.
            fragments.append(f"(sf.labels->>:{facet_key} IS NULL OR NOT ({member}))")
    return " AND ".join(fragments), params
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_chart_rule.py -q`
Expected: PASS.

- [ ] **Step 5: Add the module-map entry and commit**

`docs/architecture.md` gains `library.charts` — "the chart engine: rules, aggregate queries, footer accounting and question drafting".

```bash
uv run pytest tests/test_chart_rule.py tests/test_check_docs.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add src/library/charts tests/test_chart_rule.py docs/architecture.md
git commit -m "feat(charts): the chart rule and its SQL translation"
```

---

### Task 5: The `charts` table

Spec §9.1 and §6. A saved question, its rule, and the axes it defaults to.

**Files:**
- Create: `migrations/versions/0036_charts.py`
- Modify: `src/library/models.py`
- Test: `tests/test_chart_model.py`

**Interfaces:**
- Consumes: `Rule` (Task 4).
- Produces: `Chart` ORM model with `id, name, question_text, rule (JSONB), default_grain, default_split, display_currency, ordinal, created_at, updated_at`.

- [ ] **Step 1: Write the failing tests**

`tests/test_chart_model.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy import select

from library.charts.rule import Clause, Rule
from library.models import Chart, Grain


@pytest.mark.asyncio
async def test_a_chart_round_trips_its_rule(session) -> None:
    rule = Rule(all=[Clause(facet="category", op="in", values=["software"])])
    session.add(
        Chart(
            name="chart-a",
            question_text="money I spend on software",
            rule=rule.model_dump(),
            default_grain=Grain.MONTH,
            default_split="cost_type",
            display_currency="EUR",
        )
    )
    await session.flush()
    stored = (await session.execute(select(Chart))).scalar_one()
    assert Rule.model_validate(stored.rule) == rule


@pytest.mark.asyncio
async def test_a_chart_needs_no_split_axis(session) -> None:
    """`default_split` is nullable: a chart with no split is one series, and
    that is the shape of the seeded "All spending" card before the owner
    picks an axis."""
    session.add(
        Chart(name="chart-b", question_text="everything", rule={},
              default_grain=Grain.MONTH, display_currency="EUR")
    )
    await session.flush()
    stored = (await session.execute(select(Chart))).scalar_one()
    assert stored.default_split is None


@pytest.mark.asyncio
async def test_two_charts_may_not_share_a_name(session) -> None:
    session.add(Chart(name="dup", question_text="a", rule={},
                      default_grain=Grain.MONTH, display_currency="EUR"))
    await session.flush()
    session.add(Chart(name="dup", question_text="b", rule={},
                      default_grain=Grain.MONTH, display_currency="EUR"))
    with pytest.raises(Exception):
        await session.flush()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_chart_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'Chart'`.

- [ ] **Step 3: Add the model**

In `src/library/models.py`:

```python
class Grain(enum.StrEnum):
    """The time bucket a chart's x-axis uses (spec §9.2)."""

    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class Chart(Base):
    """A saved question over ``spend_facts``.

    ``rule`` is a serialised :class:`library.charts.rule.Rule`. The two axes
    are independent by design: ``default_grain`` and ``default_split`` are
    only starting positions, and changing either at request time never alters
    the total (spec §9.2).
    """

    __tablename__ = "charts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    question_text: Mapped[str] = mapped_column(Text)
    rule: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    default_grain: Mapped[Grain] = mapped_column(
        Enum(Grain, name="chart_grain", native_enum=False, length=16),
        default=Grain.MONTH,
    )
    default_split: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_currency: Mapped[str] = mapped_column(String(3))
    ordinal: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Write the migration**

`migrations/versions/0036_charts.py` creates `charts` with the columns above, a `UNIQUE` on `name`, and a `CHECK` restricting `default_grain` to the four values — the same lesson as Task 1: `native_enum=False` does **not** create one for you.

```python
        sa.CheckConstraint(
            "default_grain IN ('week','quarter','month','year')",
            name="ck_charts_default_grain",
        ),
```

- [ ] **Step 5: Run the tests, the gates, and commit**

```bash
uv run pytest tests/test_chart_model.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add migrations src/library/models.py tests/test_chart_model.py
git commit -m "feat(charts): the charts table"
```

---

### Task 6: The aggregate query

Spec §9.2 and §9.3. Two orthogonal axes over `spend_facts`, with date-aware currency conversion.

**Files:**
- Create: `src/library/charts/query.py`
- Test: `tests/test_chart_query.py`

**Interfaces:**
- Consumes: `rule_predicate` (Task 4); `Grain`, `Chart` (Task 5); `library.fx.convert_amount`.
- Produces:
  - `class Cell(BaseModel): period: date; split_value: str | None; total: Decimal; payments: int`
  - `class Series(BaseModel): cells: list[Cell]; total: Decimal; payments: int; documents: int; unconvertible: list[Unconvertible]`
  - `class Unconvertible(BaseModel): currency: str; amount: Decimal; documents: int`
  - `async def chart_series(session, rule: Rule, *, grain: Grain, split: str | None, currency: str, since: date | None, until: date | None) -> Series`

**Two things that are easy to get wrong.**

The **total must be invariant across split changes** (§9.2). That holds only if the split axis is applied as a `GROUP BY` over the *same* row set the flat total sums — never as an extra filter. An unlabelled row must land in a `NULL` split bucket, not be dropped.

**Conversion is per document date, never per period.** §9.3: each row converts at the rate on its own date. Converting a period's sum at the period's rate is a different number, and a wrong one whenever a rate moves inside the bucket.

- [ ] **Step 1: Write the failing tests**

`tests/test_chart_query.py`:

```python
"""The aggregate. Fixtures are shaped to make wrong answers visible.

February appears deliberately: a month bucket computed by adding 30 days
lands inside the next month, and every other month hides it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from library.charts.query import chart_series
from library.charts.rule import Clause, Rule
from library.models import Grain


@pytest.mark.asyncio
async def test_the_total_is_identical_under_every_split(session, seeded) -> None:
    """The property §9.2 exists to guarantee. Asserted by comparison, not by
    a literal, so it cannot pass by a fixture coincidence."""
    flat = await chart_series(session, Rule(), grain=Grain.MONTH, split=None,
                              currency="EUR", since=None, until=None)
    for axis in ("category", "scope", "sender"):
        split = await chart_series(session, Rule(), grain=Grain.MONTH, split=axis,
                                   currency="EUR", since=None, until=None)
        assert split.total == flat.total, f"split by {axis} changed the total"
        assert sum(cell.total for cell in split.cells) == flat.total


@pytest.mark.asyncio
async def test_an_unlabelled_row_lands_in_a_null_bucket_not_the_bin(
    session, document, facets
) -> None:
    """Dropping it would make the total depend on the split axis, which is
    exactly what §9.2 forbids."""
    await document(amount_total=Decimal("10.00"), amount_kind="payment_made",
                   labels={"category": "services"})
    await document(amount_total=Decimal("5.00"), amount_kind="payment_made", labels={})
    series = await chart_series(session, Rule(), grain=Grain.MONTH, split="category",
                                currency="EUR", since=None, until=None)
    assert series.total == Decimal("15.00")
    assert None in {cell.split_value for cell in series.cells}


@pytest.mark.asyncio
async def test_a_refund_lowers_its_period_rather_than_being_dropped(
    session, document
) -> None:
    await document(amount_total=Decimal("200.00"), amount_kind="payment_made",
                   document_date=date(2026, 4, 10))
    await document(amount_total=Decimal("49.00"), amount_kind="refund",
                   document_date=date(2026, 4, 20))
    series = await chart_series(session, Rule(), grain=Grain.MONTH, split=None,
                                currency="EUR", since=None, until=None)
    assert series.total == Decimal("151.00")
    assert [cell.total for cell in series.cells] == [Decimal("151.00")]


@pytest.mark.asyncio
async def test_a_non_contributing_kind_never_enters_the_total(
    session, document
) -> None:
    """The case that motivated the whole redesign: an insurance ceiling is
    large enough to wreck any total it enters (spec §2.2)."""
    await document(amount_total=Decimal("100.00"), amount_kind="payment_made")
    await document(amount_total=Decimal("500000.00"), amount_kind="coverage_limit")
    await document(amount_total=Decimal("450.00"), amount_kind="estimate")
    await document(amount_total=Decimal("77.00"), amount_kind=None)
    series = await chart_series(session, Rule(), grain=Grain.MONTH, split=None,
                                currency="EUR", since=None, until=None)
    assert series.total == Decimal("100.00")


@pytest.mark.asyncio
async def test_february_buckets_by_calendar_month_not_by_thirty_days(
    session, document
) -> None:
    """A bucket computed as a 30-day offset puts 2026-02-28 in March. Every
    month except February hides that."""
    await document(amount_total=Decimal("10.00"), amount_kind="payment_made",
                   document_date=date(2026, 2, 1))
    await document(amount_total=Decimal("20.00"), amount_kind="payment_made",
                   document_date=date(2026, 2, 28))
    series = await chart_series(session, Rule(), grain=Grain.MONTH, split=None,
                               currency="EUR", since=None, until=None)
    assert [cell.period for cell in series.cells] == [date(2026, 2, 1)]
    assert series.cells[0].total == Decimal("30.00")


@pytest.mark.asyncio
async def test_a_merged_pair_counts_as_one_payment(session, document) -> None:
    await document(amount_total=Decimal("60.00"), amount_kind="payment_due",
                   document_date=date(2026, 4, 1))
    await document(amount_total=Decimal("60.00"), amount_kind="payment_made",
                   document_date=date(2026, 4, 1))
    series = await chart_series(session, Rule(), grain=Grain.MONTH, split=None,
                                currency="EUR", since=None, until=None)
    assert series.total == Decimal("60.00")
    assert series.payments == 1
    assert series.documents == 2


@pytest.mark.asyncio
async def test_each_amount_converts_at_its_own_date_not_the_periods(
    session, document, fx_rates
) -> None:
    """Two documents in one month at different rates. Converting the
    period's sum at one rate gives a different, wrong number."""
    await fx_rates([("2026-04-01", "USD", "1.00"), ("2026-04-01", "GBP", "1.20"),
                    ("2026-04-20", "GBP", "1.50")])
    await document(amount_total=Decimal("100.00"), currency="GBP",
                   amount_kind="payment_made", document_date=date(2026, 4, 2))
    await document(amount_total=Decimal("100.00"), currency="GBP",
                   amount_kind="payment_made", document_date=date(2026, 4, 25))
    series = await chart_series(session, Rule(), grain=Grain.MONTH, split=None,
                                currency="USD", since=None, until=None)
    assert series.total == Decimal("270.00")


@pytest.mark.asyncio
async def test_an_unconvertible_amount_is_reported_never_counted_one_to_one(
    session, document, fx_rates
) -> None:
    """§9.3. Counting it 1:1 is the silent failure this replaces."""
    await fx_rates([("2026-04-01", "USD", "1.00")])
    await document(amount_total=Decimal("100.00"), currency="USD",
                   amount_kind="payment_made", document_date=date(2026, 4, 2))
    await document(amount_total=Decimal("40.00"), currency="ZZZ",
                   amount_kind="payment_made", document_date=date(2026, 4, 3))
    series = await chart_series(session, Rule(), grain=Grain.MONTH, split=None,
                                currency="USD", since=None, until=None)
    assert series.total == Decimal("100.00")
    assert [(u.currency, u.amount) for u in series.unconvertible] == [
        ("ZZZ", Decimal("40.00"))
    ]


@pytest.mark.asyncio
async def test_the_range_filters_the_data_rather_than_clamping_the_axis(
    session, document
) -> None:
    """Spec §2.5 and §10.3.2: the old page clamped the axis and left the
    statistics computed over six years, so the headline and the chart
    disagreed. The total must move when the range does."""
    await document(amount_total=Decimal("10.00"), amount_kind="payment_made",
                   document_date=date(2025, 1, 1))
    await document(amount_total=Decimal("20.00"), amount_kind="payment_made",
                   document_date=date(2026, 4, 1))
    series = await chart_series(session, Rule(), grain=Grain.MONTH, split=None,
                                currency="EUR", since=date(2026, 1, 1), until=None)
    assert series.total == Decimal("20.00")
    assert len(series.cells) == 1


@pytest.mark.asyncio
async def test_a_rule_restricts_the_rows_it_names(session, document, facets) -> None:
    await document(amount_total=Decimal("10.00"), amount_kind="payment_made",
                   labels={"category": "services"})
    await document(amount_total=Decimal("90.00"), amount_kind="payment_made",
                   labels={"category": "supplies"})
    series = await chart_series(
        session,
        Rule(all=[Clause(facet="category", op="in", values=["services"])]),
        grain=Grain.MONTH, split=None, currency="EUR", since=None, until=None,
    )
    assert series.total == Decimal("10.00")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_chart_query.py -q`
Expected: FAIL — `ModuleNotFoundError: library.charts.query`.

- [ ] **Step 3: Implement**

`src/library/charts/query.py`. The shape, with the parts that matter spelled out:

```python
"""Aggregate queries over `spend_facts`.

Every SELECT against `spend_facts` lives here; no router builds SQL.

Two invariants this module exists to hold:

* **The total is invariant across split changes** (§9.2). The split is a
  GROUP BY over the same rows the flat total sums, never an extra filter, and
  an unlabelled row lands in a NULL bucket rather than being dropped.
* **Each amount converts at its own document's date** (§9.3), not at the
  period's. Converting a period's sum at one rate is a different number
  whenever a rate moves inside the bucket.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from library.charts.rule import Rule, rule_predicate
from library.fx import convert_amount
from library.models import AMOUNT_SIGN, AmountKind, Grain

#: `date_trunc` takes the grain name directly, so the enum's values are the
#: SQL argument. Bound as a parameter, never interpolated.
_GRAIN_SQL: dict[Grain, str] = {
    Grain.WEEK: "week",
    Grain.MONTH: "month",
    Grain.QUARTER: "quarter",
    Grain.YEAR: "year",
}
```

The query selects **rows, not sums**, because conversion is per row and per date:

```sql
SELECT CAST(date_trunc(:grain, CAST(sf.date AS timestamp)) AS date) AS period,
       sf.labels->>:split                                          AS split_value,
       sf.amount, sf.currency, sf.date, sf.amount_kind, sf.payment_id,
       sf.document_id
FROM spend_facts sf
WHERE sf.is_canonical
  AND sf.amount_kind = ANY(:summable)
  AND sf.date IS NOT NULL
  AND (CAST(:since AS date) IS NULL OR sf.date >= CAST(:since AS date))
  AND (CAST(:until AS date) IS NULL OR sf.date <= CAST(:until AS date))
  AND <rule predicate>
```

**Write `CAST(x AS type)`, never the `::type` shorthand, anywhere a bind parameter is nearby.** `text()` parses `:name` itself, and `:since::date` is ambiguous to it: the parameter is left unsubstituted and Postgres receives a literal colon. Verified — `SELECT :since::date IS NULL` raises `ProgrammingError`, and `SELECT CAST(:since AS date) IS NULL` returns cleanly. The `::` casts inside the `spend_facts` view (Task 3) are fine because that SQL carries no bind parameters at all.

`date_trunc` takes the grain as a bound parameter; no interpolation is needed. Verified: `date_trunc(:grain, CAST('2026-02-28' AS timestamp))` with `grain='month'` returns `2026-02-01`.

`sender` as a split axis is the one exception — it is a real column, so substitute `CAST(sf.sender_id AS text) AS split_value` when `split == "sender"` (§9.2). Do this by choosing between two literal SQL fragments, never by interpolating the caller's string into the column list.

Then in Python: for each row, `converted = await convert_amount(session, row.amount, row.currency, currency, row.date)`. A `None` accumulates into `unconvertible` keyed by currency and is **never** added to a total. Otherwise accumulate `AMOUNT_SIGN[AmountKind(row.amount_kind)] * converted` into `(period, split_value)`. Count `payments` as `len({row.payment_id})` and `documents` as `len({row.document_id})` — both over the rows that reached a total.

Rows with `sf.date IS NULL` cannot be bucketed. They are **not** dropped silently: Task 7's footer reports them, and this task's query excludes them only because a chart with a time axis has nowhere to put them.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_chart_query.py -q`
Expected: PASS.

- [ ] **Step 5: Prove the split-invariance test can fail**

Temporarily change the split to a filter — append `AND sf.labels ? :split` to the WHERE clause — and re-run.

Run: `uv run pytest tests/test_chart_query.py -q`
Expected: `test_the_total_is_identical_under_every_split` and `test_an_unlabelled_row_lands_in_a_null_bucket_not_the_bin` both FAIL. Revert and confirm green.

- [ ] **Step 6: Run the gates and commit**

```bash
uv run pytest tests/test_chart_query.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add src/library/charts/query.py tests/test_chart_query.py
git commit -m "feat(charts): the aggregate query over spend_facts"
```

---

### Task 7: The footer — nothing is excluded silently

Spec §9.4. The most important part of the feature: a document the model failed to label matches no rule, so without this it disappears from every chart with no way to notice.

**Files:**
- Create: `src/library/charts/footer.py`
- Test: `tests/test_chart_footer.py`

**Interfaces:**
- Consumes: `Rule` (Task 4); `Unconvertible` from `library.charts.query` (Task 6) — imported, **not redefined**, so the two modules cannot drift into two shapes with one name.
- Produces:
  - `class ExcludedGroup(BaseModel): amount_kind: str; amount: Decimal; documents: int`
  - `class Footer(BaseModel): netted_refunds: Decimal; refund_count: int; excluded: list[ExcludedGroup]; uncategorised: ExcludedGroup | None; undated: ExcludedGroup | None; unconvertible: list[Unconvertible]`

**Both `Series` and `Footer` can produce `unconvertible` entries** — `query.py` from the rows that would have entered the total, `footer.py` from the rows it accounts for — and they are different rows, so neither is redundant. Task 10 **merges the two lists by currency** into one footer block before serialising, and asserts the merge in `tests/test_api_spending.py`; reporting the same currency twice with two amounts would read as two separate problems.
  - `async def chart_footer(session, rule, *, currency, since, until, facets_in_rule: set[str]) -> Footer`

**Why it is a separate module.** It answers the opposite question from `query.py` — what the total *missed*. Mixing the two is how "nothing is excluded silently" quietly stops being true: a refactor of the sum has no reason to keep the accounting correct, and no test notices.

- [ ] **Step 1: Write the failing tests**

`tests/test_chart_footer.py`:

```python
"""What the total did not count. Every branch is money that would otherwise
vanish from the archive with no way to notice."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from library.charts.footer import chart_footer
from library.charts.rule import Clause, Rule


@pytest.mark.asyncio
async def test_a_coverage_limit_is_reported_as_excluded(session, document) -> None:
    await document(amount_total=Decimal("500000.00"), amount_kind="coverage_limit",
                   document_date=date(2026, 4, 1))
    footer = await chart_footer(session, Rule(), currency="EUR", since=None,
                                until=None, facets_in_rule=set())
    assert [(g.amount_kind, g.amount) for g in footer.excluded] == [
        ("coverage_limit", Decimal("500000.00"))
    ]


@pytest.mark.asyncio
async def test_a_refund_is_reported_as_netted_not_as_excluded(
    session, document
) -> None:
    """§9.4: a refund IS in the total, and lowering it is the point.
    Reporting it as excluded would say the opposite of what is true."""
    await document(amount_total=Decimal("49.00"), amount_kind="refund",
                   document_date=date(2026, 4, 1))
    footer = await chart_footer(session, Rule(), currency="EUR", since=None,
                                until=None, facets_in_rule=set())
    assert footer.netted_refunds == Decimal("49.00")
    assert footer.refund_count == 1
    assert all(g.amount_kind != "refund" for g in footer.excluded)


@pytest.mark.asyncio
async def test_money_with_no_label_for_a_rules_facet_is_reported(
    session, document, facets
) -> None:
    """The line §9.4 calls the most important one.

    An unlabelled document matches no rule, so it is invisible in every
    chart. Reporting it inside the chart whose window contains it turns the
    archive's worst failure mode into a visible task.
    """
    await document(amount_total=Decimal("89.20"), amount_kind="payment_made",
                   document_date=date(2026, 4, 1), labels={})
    footer = await chart_footer(
        session,
        Rule(all=[Clause(facet="category", op="in", values=["services"])]),
        currency="EUR", since=None, until=None, facets_in_rule={"category"},
    )
    assert footer.uncategorised is not None
    assert footer.uncategorised.amount == Decimal("89.20")
    assert footer.uncategorised.documents == 1


@pytest.mark.asyncio
async def test_a_labelled_document_outside_the_rule_is_not_uncategorised(
    session, document, facets
) -> None:
    """It was categorised; the owner simply asked a different question.
    Reporting it would make every chart accuse the archive of a gap it does
    not have, and the real gaps would be lost in the noise."""
    await document(amount_total=Decimal("30.00"), amount_kind="payment_made",
                   document_date=date(2026, 4, 1), labels={"category": "supplies"})
    footer = await chart_footer(
        session,
        Rule(all=[Clause(facet="category", op="in", values=["services"])]),
        currency="EUR", since=None, until=None, facets_in_rule={"category"},
    )
    assert footer.uncategorised is None


@pytest.mark.asyncio
async def test_a_dated_none_document_is_undated_money_when_it_has_no_date(
    session, document
) -> None:
    """A summable amount with no document_date cannot be bucketed, so
    Task 6's query drops it. Here is where it surfaces."""
    await document(amount_total=Decimal("12.00"), amount_kind="payment_made",
                   document_date=None)
    footer = await chart_footer(session, Rule(), currency="EUR", since=None,
                                until=None, facets_in_rule=set())
    assert footer.undated is not None
    assert footer.undated.amount == Decimal("12.00")


@pytest.mark.asyncio
async def test_the_footer_respects_the_charts_date_window(
    session, document
) -> None:
    """Reporting money from outside the window would attach a gap to a chart
    that never claimed to cover it."""
    await document(amount_total=Decimal("500.00"), amount_kind="coverage_limit",
                   document_date=date(2024, 1, 1))
    footer = await chart_footer(session, Rule(), currency="EUR",
                                since=date(2026, 1, 1), until=None,
                                facets_in_rule=set())
    assert footer.excluded == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_chart_footer.py -q`
Expected: FAIL — `ModuleNotFoundError: library.charts.footer`.

- [ ] **Step 3: Implement**

`src/library/charts/footer.py`. Four queries over `spend_facts`, all sharing the chart's date and currency window:

1. **excluded** — `is_canonical`, `amount_kind` NOT in `AMOUNT_SIGN` and NOT NULL, grouped by `amount_kind`.
2. **netted refunds** — `is_canonical`, `amount_kind = 'refund'`, summed as a positive magnitude with its count. It is reported separately from `excluded` because it is *in* the total.
3. **uncategorised** — `is_canonical`, `amount_kind` in `AMOUNT_SIGN`, and `NOT (sf.labels ?& :facets)` for the facets the rule names. When the rule names no facet the group is `None`, because a rule that asks about everything cannot have a gap.
4. **undated** — `is_canonical`, summable, `sf.date IS NULL`. These are outside the window by definition, so this query alone ignores `since`/`until`.

Every amount converts through `convert_amount` at its own document's date, exactly as Task 6 does; a `None` joins `unconvertible` rather than being counted.

- [ ] **Step 4: Run the tests, the gates, and commit**

```bash
uv run pytest tests/test_chart_footer.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add src/library/charts/footer.py tests/test_chart_footer.py
git commit -m "feat(charts): footer accounting for money the total did not count"
```

---

### Task 8: Drill-through

Spec §9.5. Every number reaches its source documents in two clicks, so a correction is made where the problem was noticed.

**Files:**
- Modify: `src/library/charts/query.py`
- Test: `tests/test_chart_query.py`

**Interfaces:**
- Produces:
  - `class CellDocument(BaseModel): id: int; title: str | None; date: date | None; amount: Decimal; currency: str; amount_kind: str | None; reference: str | None; is_canonical: bool`
  - `class CellPayment(BaseModel): payment_id: int; total: Decimal; documents: list[CellDocument]`
  - `async def chart_cell(session, rule, *, grain, split, split_value, period, currency, since, until) -> list[CellPayment]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_chart_query.py`:

```python
@pytest.mark.asyncio
async def test_a_cell_lists_its_payments_with_every_document_in_the_group(
    session, document
) -> None:
    """Including the NON-canonical one. The drill-through is where a wrong
    merge is noticed and split, so hiding the other half of a merged pair
    hides the only evidence the merge was wrong (§9.5)."""
    await document(amount_total=Decimal("60.00"), amount_kind="payment_due",
                   document_date=date(2026, 4, 1), title="doc-due")
    await document(amount_total=Decimal("60.00"), amount_kind="payment_made",
                   document_date=date(2026, 4, 1), title="doc-made")
    payments = await chart_cell(
        session, Rule(), grain=Grain.MONTH, split=None, split_value=None,
        period=date(2026, 4, 1), currency="EUR", since=None, until=None,
    )
    assert len(payments) == 1
    assert payments[0].total == Decimal("60.00")
    assert len(payments[0].documents) == 2
    assert sum(d.is_canonical for d in payments[0].documents) == 1


@pytest.mark.asyncio
async def test_a_cells_payments_sum_to_the_cell_shown_in_the_chart(
    session, seeded
) -> None:
    """The property that makes drill-through trustworthy. Asserted by
    comparison against `chart_series`, so it cannot pass by coincidence."""
    series = await chart_series(session, Rule(), grain=Grain.MONTH,
                                split="category", currency="EUR",
                                since=None, until=None)
    for cell in series.cells:
        payments = await chart_cell(
            session, Rule(), grain=Grain.MONTH, split="category",
            split_value=cell.split_value, period=cell.period,
            currency="EUR", since=None, until=None,
        )
        assert sum(p.total for p in payments) == cell.total


@pytest.mark.asyncio
async def test_the_null_split_bucket_is_reachable(session, document, facets) -> None:
    """`split_value=None` must select the unlabelled rows, not every row.
    `= NULL` is never true in SQL — this needs `IS NOT DISTINCT FROM`."""
    await document(amount_total=Decimal("5.00"), amount_kind="payment_made",
                   document_date=date(2026, 4, 1), labels={})
    await document(amount_total=Decimal("7.00"), amount_kind="payment_made",
                   document_date=date(2026, 4, 1), labels={"category": "services"})
    payments = await chart_cell(
        session, Rule(), grain=Grain.MONTH, split="category", split_value=None,
        period=date(2026, 4, 1), currency="EUR", since=None, until=None,
    )
    assert sum(p.total for p in payments) == Decimal("5.00")
```

- [ ] **Step 2: Run to verify they fail, then implement**

Run: `uv run pytest tests/test_chart_query.py -q -k cell`
Expected: FAIL — `ImportError: cannot import name 'chart_cell'`.

`chart_cell` reuses the same predicate and window as `chart_series`, adds `CAST(date_trunc(:grain, CAST(sf.date AS timestamp)) AS date) = CAST(:period AS date)` and matches the split with **`IS NOT DISTINCT FROM`** so the `NULL` bucket is reachable, then joins `payments` to list every document in each group — canonical or not.

- [ ] **Step 3: Run, gate, commit**

```bash
uv run pytest tests/test_chart_query.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add src/library/charts/query.py tests/test_chart_query.py
git commit -m "feat(charts): drill-through from a cell to its payments and documents"
```

---

### Task 9: Drafting a rule from a question

Spec §9.1 and §7.5. The model drafts a rule against the **current** vocabulary and never invents a value.

**Files:**
- Create: `src/library/charts/draft.py`
- Test: `tests/test_chart_draft.py`

**Interfaces:**
- Consumes: `Rule`, `Clause` (Task 4); `settings.extraction_model`.
- Produces: `async def draft_rule(session, question: str) -> DraftResult`, where `DraftResult` carries `rule: Rule`, `proposed_split: str | None`, and `unknown_terms: list[str]`.

**Constraints that are not negotiable:**

- **`client.messages.parse()` with a Pydantic `output_format`.** Never `messages.create()` + `json.loads`. That shipped twice in this repository (#108, #116) and was reverted both times.
- **No new `*_model` setting.** Reuse `settings.extraction_model`, or the app refuses to boot for want of a `MODEL_PRICING_USD_PER_MTOK` row.
- **The vocabulary is closed.** A value the model returns that is not in `facet_values` is dropped from the rule and reported in `unknown_terms` — spec §7.5: when a question cannot be expressed in the current vocabulary the system says so and proposes the addition, rather than approximating.

- [ ] **Step 1: Write the failing tests**

`tests/test_chart_draft.py`. The model is stubbed; these test the boundary, not the model:

```python
@pytest.mark.asyncio
async def test_a_drafted_value_outside_the_vocabulary_is_dropped_and_reported(
    session, facets, stub_anthropic
) -> None:
    """§7.5: the vocabulary is never auto-extended. A rule that silently
    kept an invented value would resolve to zero rows and read as "you
    spent nothing on that", which is worse than an error."""
    stub_anthropic.returns(
        rule={"all": [{"facet": "category", "op": "in",
                       "values": ["services", "cryptocurrency"]}]},
        proposed_split="scope",
    )
    result = await draft_rule(session, "money I spend on services")
    assert result.rule.all[0].values == ["services"]
    assert result.unknown_terms == ["cryptocurrency"]


@pytest.mark.asyncio
async def test_a_drafted_facet_outside_the_vocabulary_drops_the_whole_clause(
    session, facets, stub_anthropic
) -> None:
    stub_anthropic.returns(
        rule={"all": [{"facet": "vibe", "op": "in", "values": ["good"]}]},
        proposed_split=None,
    )
    result = await draft_rule(session, "money I spend on good vibes")
    assert result.rule.all == []
    assert "vibe" in result.unknown_terms


@pytest.mark.asyncio
async def test_a_clause_left_with_no_values_is_dropped_not_left_empty(
    session, facets, stub_anthropic
) -> None:
    """An empty `values` list raises RuleError in Task 4, so leaving one
    behind turns a drafting miss into a 500 at query time."""
    stub_anthropic.returns(
        rule={"all": [{"facet": "category", "op": "in", "values": ["nonsense"]}]},
        proposed_split=None,
    )
    result = await draft_rule(session, "money I spend on nonsense")
    assert result.rule.all == []


@pytest.mark.asyncio
async def test_the_backend_uses_messages_parse_not_messages_create(
    session, facets, stub_anthropic
) -> None:
    """Asserted on the call shape, not on the output. #108 and #116 both
    passed every behavioural test while using the wrong call."""
    stub_anthropic.returns(rule={"all": []}, proposed_split=None)
    await draft_rule(session, "everything")
    assert stub_anthropic.used == "parse", (
        "the API backend must use messages.parse, not messages.create"
    )
```

- [ ] **Step 2: Run to verify they fail, then implement**

Run: `uv run pytest tests/test_chart_draft.py -q`
Expected: FAIL — `ModuleNotFoundError: library.charts.draft`.

`draft_rule` loads every `(facet.key, facet_value.key)` pair plus their aliases, puts them in the system prompt as the closed set, calls `messages.parse` with a `DraftedRule` Pydantic `output_format`, then filters the response against the same set it sent. Filtering after the call rather than trusting the prompt is the point: the prompt is a request, the filter is the guarantee.

- [ ] **Step 3: Run, gate, commit**

```bash
uv run pytest tests/test_chart_draft.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add src/library/charts/draft.py tests/test_chart_draft.py
git commit -m "feat(charts): draft a chart rule from a question against the closed vocabulary"
```

---

### Task 10: The API

Spec §9.6, at `/api/spending` for the reason given at the top of this plan.

**Files:**
- Create: `src/library/api/spending.py`
- Modify: `src/library/app.py`
- Test: `tests/test_api_spending.py`

**Routes:**

```
GET    /api/spending                 saved questions
POST   /api/spending/draft           question text -> proposed rule, axes, preview
POST   /api/spending                 save
GET    /api/spending/{id}/data       ?grain&split&from&to&currency
GET    /api/spending/{id}/cell       ?period&split_value -> payments -> documents
PATCH  /api/spending/{id}
DELETE /api/spending/{id}
GET    /api/documents/{id}/spend-lines    read an allocation (Task 2)
PUT    /api/documents/{id}/spend-lines    replace it
DELETE /api/documents/{id}/spend-lines    clear it
```

Nine routes, not seven: spec §9.6 lists the chart resource only, and the three spend-line routes are §8.4's write path, which has no other home.

- [ ] **Step 1: Write the failing tests**

`tests/test_api_spending.py`. Scope every list assertion by a unique name — the suite shares one database and list endpoints default to 25 rows:

```python
"""The routes. Thin by design — the behaviour is tested in the modules
underneath, so what is asserted here is status codes, shape, and the
mapping from a named error to a status a client can act on."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _save_chart(api_client: TestClient, name: str, rule: dict[str, object]) -> int:
    """Create a chart and return its id. Names are unique per test so list
    assertions can be scoped — the suite shares one database and list
    endpoints default to 25 rows."""
    response = api_client.post(
        "/api/spending",
        json={
            "name": name,
            "question_text": f"question for {name}",
            "rule": rule,
            "default_grain": "month",
            "default_split": None,
            "display_currency": "EUR",
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def test_listing_charts_rejects_a_limit_over_one_hundred(
    api_client: TestClient,
) -> None:
    assert api_client.get("/api/spending?limit=101").status_code == 422


def test_a_saved_chart_returns_data_with_its_footer(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-footer", {"all": []})
    response = api_client.get(f"/api/spending/{chart_id}/data?grain=month")
    assert response.status_code == 200
    body = response.json()
    assert "cells" in body and "total" in body
    # Present even when empty: an absent footer and an empty one are
    # different claims, and only one of them is "nothing was excluded".
    assert body["footer"]["excluded"] == []
    assert body["footer"]["netted_refunds"] == "0.00"


def test_the_data_endpoint_names_an_unknown_split_axis_rather_than_500ing(
    api_client: TestClient,
) -> None:
    """A facet deleted after the chart was saved. §12: the chart renders an
    error NAMING the value, never an empty chart — an empty chart is
    indistinguishable from "you spent nothing on that"."""
    chart_id = _save_chart(api_client, "api-split", {"all": []})
    response = api_client.get(
        f"/api/spending/{chart_id}/data?split=no_such_facet"
    )
    assert response.status_code == 422
    assert "no_such_facet" in response.text


def test_a_missing_chart_is_a_404(api_client: TestClient) -> None:
    assert api_client.get("/api/spending/999999/data").status_code == 404


def test_saving_a_chart_with_a_duplicate_name_is_a_409(
    api_client: TestClient,
) -> None:
    _save_chart(api_client, "api-dup", {"all": []})
    response = api_client.post(
        "/api/spending",
        json={
            "name": "api-dup",
            "question_text": "again",
            "rule": {"all": []},
            "default_grain": "month",
            "display_currency": "EUR",
        },
    )
    assert response.status_code == 409


def test_replacing_an_allocation_that_does_not_sum_is_a_400_not_a_500(
    api_client: TestClient, api_document_id: int
) -> None:
    """`api_document_id` is a document with amount_total = 100.00."""
    response = api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={"lines": [{"amount": "60.00"}]},
    )
    assert response.status_code == 400
    assert "sum" in response.text.lower()


def test_replacing_an_allocation_that_sums_is_accepted(
    api_client: TestClient, api_document_id: int
) -> None:
    response = api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={"lines": [{"amount": "60.00"}, {"amount": "40.00"}]},
    )
    assert response.status_code == 200
    assert len(response.json()["lines"]) == 2


def test_clearing_an_allocation_returns_the_document_to_unsplit(
    api_client: TestClient, api_document_id: int
) -> None:
    api_client.put(
        f"/api/documents/{api_document_id}/spend-lines",
        json={"lines": [{"amount": "60.00"}, {"amount": "40.00"}]},
    )
    assert (
        api_client.delete(
            f"/api/documents/{api_document_id}/spend-lines"
        ).status_code
        == 204
    )
    body = api_client.get(f"/api/documents/{api_document_id}/spend-lines").json()
    assert body["lines"] == []
```

`api_document_id` is a module fixture creating one ready document with `amount_total = Decimal("100.00")`, following the document-creating helpers already in `tests/test_api_payments.py`.

- [ ] **Step 2: Run to verify they fail, then implement**

Routes are thin: parse and validate, call `query.py` / `footer.py` / `draft.py` / `spend_lines.py`, serialise. **No router builds SQL.** `AllocationError` maps to 400, an unknown split axis or grain to 422, a missing chart to 404. Register in `app.py` beside `payments.router`; authentication is enforced at include level like every other router.

- [ ] **Step 3: Run, gate, commit**

```bash
uv run pytest tests/test_api_spending.py -q
uv run ruff format --check . && uv run ruff check . && uv run mypy
git add src/library/api/spending.py src/library/app.py tests/test_api_spending.py
git commit -m "feat(charts): the spending API"
```

---

### Task 11: Documentation, and the verification that actually counts

**Files:**
- Create: `docs/charts.md`, `journal/260829-chart-engine.md`
- Modify: `docs/superpowers/specs/2026-08-28-charts-redesign-design.md` (§5.1's GIN sentence, if Task 3 Step 6 found it wrong), `docs/money-facts.md` (the `refund` value and the sign guard), `docs/README.md`

- [ ] **Step 1: Write `docs/charts.md`**

Cover: the `spend_facts` relation and why one relation rather than scattered COALESCEs; the canonical-document rule and the three tie-breaks; rule translation; the two orthogonal axes and why the total is invariant; per-document-date conversion; the footer's four categories and why a refund is netted rather than excluded; the API. Include the `EXPLAIN` measurement from Task 3 Step 6 and the decision it led to.

- [ ] **Step 2: Update `docs/money-facts.md`**

§2's table gains `refund` and a sign column; §4 gains the sign precondition above the rules; §5's known limits gain the credit-note/refund-receipt double-net case. **`docs/money-facts.md` describes shipped behaviour**, so this edit belongs here, in the task that ships it, and not earlier.

- [ ] **Step 3: Run both docs gates**

```bash
uv run pytest tests/test_check_docs.py -q
uv run python scripts/check_docs.py --max-violations 0
```
Expected: PASS. Stamps must be current, and `--since` with a bare date means that date *at the current clock time*, so re-run rather than reasoning about it.

- [ ] **Step 4: Grep for anything that cannot be public**

```bash
git diff origin/main --stat
git diff origin/main | grep '^+' | grep -inE '[0-9]+[.,][0-9]{2}|€|£|\$'
```
Every amount, sender and reference in this branch must be invented. The repository is public and GitGuardian does not catch this class of leak.

- [ ] **Step 5: Verify against the live archive**

**This step is the one that matters.** A green branch is not a working feature: PR #115 passed every gate, deployed clean, and classified nothing.

After deploying, run in order and record the numbers in the journal entry:

1. `library backfill-amounts` — the archive's one credit note is currently NULL and already in the queue. **Expected: it resolves to `refund`.** If it comes back undecided again, the prompt change did not take.
2. Confirm the payment count is unchanged from the recorded 241 payments from 258 documents, except where a refund has been correctly separated. **A drop means the sign guard is merging things it should not.**
3. Create the three questions the feature exists for (§1) and check each total against the documents behind it by hand: AI subscriptions per month, accountancy fees per year, EV charging.
4. Confirm the footer reports the archive's real uncategorised money rather than zero. **A zero here almost certainly means the query is wrong**, not that the archive is perfectly labelled.

- [ ] **Step 6: Write the journal entry and commit**

`journal/260829-chart-engine.md` records the live numbers from Step 5, what the prototype found before implementation began, and anything that behaved differently against real data than against fixtures.

```bash
git add docs journal
git commit -m "docs(charts): the chart engine, and what the live archive said"
```

## Done when

- [ ] `refund` exists, contributes −1, and the archive's credit note classifies as one.
- [ ] `amount_kind` is constrained by the database, not only by Python.
- [ ] A refund never shares a payment group with a non-refund, by rule or by override.
- [ ] `spend_facts` returns one row per unsplit document and one per line, with labels inherited and overridden correctly, and exactly one canonical document per payment.
- [ ] A chart's total is identical under every split axis, and unlabelled money lands in a `NULL` bucket rather than vanishing.
- [ ] Each amount converts at its own document's date; an unconvertible amount is reported, never counted 1:1.
- [ ] Every chart reports the money its rule touched but its total did not — excluded kinds, netted refunds, uncategorised money and undated money.
- [ ] Every cell reaches its payments and their documents, including non-canonical ones.
- [ ] The three questions in §1 are answerable, and their totals were checked by hand against the live archive.
- [ ] `ruff format --check`, `ruff check`, `mypy`, both docs gates and the full backend suite pass.
- [ ] No real sender, amount or reference appears anywhere in the branch.
