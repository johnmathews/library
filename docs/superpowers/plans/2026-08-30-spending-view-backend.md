# Spending View Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the five backend additions the `/charts` view needs, deployed and live, before any UI is written against them.

**Architecture:** Everything is additive to `src/library/api/spending.py`, `src/library/api/facets.py`, `src/library/api/taxonomy.py` and `src/library/charts/footer.py`. **No change to `charts/query.py`, `charts/rule.py` or `charts/draft.py`** — the engine's two invariants (the total is invariant across split changes; the drill-through sums to the bar) are untouched, and nothing here creates a second path to a number the engine already computes. The footer drill reuses `footer.py`'s existing `_CLASSIFY_SQL` rather than restating its `CASE`, so the count and the list cannot disagree.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, PostgreSQL 17, pytest, `uv`, ruff, mypy.

**Spec:** [docs/superpowers/specs/2026-08-30-charts-view-design.md](../specs/2026-08-30-charts-view-design.md) — plan 4a is its §3. Read §2.3, §2.5, §3.3 and §3.4 before starting; they carry the reasoning this plan does not repeat.

## Global Constraints

- **This repository is PUBLIC.** No real sender name, personal name, address, vehicle registration or real monetary amount reaches code, tests, fixtures, docs, journal entries, commit messages or PR bodies. The live facet vocabulary contains address-shaped and vehicle-shaped values; invent everything. GitGuardian does not catch this class — grep before committing.
- **`limit <= 100`** on every list route, matching `GET /api/spending`.
- **CI runs `ruff check` and `ruff format --check` over the WHOLE repository**, `migrations/` included. Run `uv run ruff format .` before every commit that adds a migration.
- **`mypy` must pass.** `make lint` runs `ruff check`, `ruff format --check`, `actionlint`, the journal index check, `mypy` and `check_docs`.
- **No `except Exception -> pytest.skip` guards.** They read as green while hiding breakage.
- **Every new test gets a mutation check**: break the implementation, confirm the test goes red, restore. Several suites in this repository have passed with the feature under test entirely disabled.
- **Test fixtures are scoped** so they do not pollute the shared serial backend's document ordering. `api_database_url` is session-scoped; list defaults cap at 25. Scope list assertions by something unique to the test.
- **The fixture vocabulary is `FIXTURE_VOCABULARY` in `tests/conftest.py`**: `category` → `software`, `services`, `supplies`, `accountancy`; `scope` → `business`, `personal`; `cost_type` → `subscription`, `usage`. Labelling a document with any other value raises `UnknownValueError`.
- **Direct pushes to `main` are rejected by a ruleset.** Everything goes through a PR. CI's `backend` job takes ~16–18 minutes and is not hanging.
- Run the full backend suite before opening the PR: `uv run pytest -q`.

---

## File Structure

| file | responsibility |
| --- | --- |
| `migrations/versions/0037_split_colour.py` | **create** — nullable `colour` on `facet_values` and `senders`, each with an explicit CHECK |
| `src/library/models.py` | modify — `FacetValue.colour`, `Sender.colour` |
| `src/library/facets/vocabulary.py` | modify — `VocabularyValue.colour`, read by `load_vocabulary` |
| `src/library/api/facets.py` | modify — `colour` on `ValueOut`; `ValueRename` gains optional `colour`; **new** `GET /api/facets/counts` |
| `src/library/api/taxonomy.py` | modify — `colour` on `SenderWithCount`; **new** `PATCH /api/senders/{id}` |
| `src/library/charts/footer.py` | modify — extract the row fetch; **new** `chart_footer_documents` |
| `src/library/api/spending.py` | modify — `GET /api/spending/{id}`; `SplitValueOut` + `DataOut.splits`; `CellOutBody.label`/`.colour`; **new** `GET /api/spending/{id}/footer/{bucket}` |
| `tests/test_split_colour.py` | **create** — the migration's constraint and the read/write surfaces |
| `tests/test_api_spending.py` | modify — chart-by-id, split resolution, the footer drill route |
| `tests/test_chart_footer.py` | modify — `chart_footer_documents` and its count/list invariant |
| `tests/test_api_facets.py` | modify — `GET /api/facets/counts` |

---

## Task 1: Migration 0037 — nullable `colour` with an explicit CHECK

**Files:**
- Create: `migrations/versions/0037_split_colour.py`
- Modify: `src/library/models.py` (`FacetValue` ~line 381, `Sender` ~line 319)
- Test: `tests/test_split_colour.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `FacetValue.colour: Mapped[str | None]`, `Sender.colour: Mapped[str | None]`; constraints named `ck_facet_values_colour_hex` and `ck_senders_colour_hex`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_split_colour.py`:

```python
"""`colour` is a nullable override over a derived palette slot (spec §2.5).

Nullable is the design, not a shortcut: when it is null the renderer derives a
stable palette slot from the value's key, so every legend is correctly coloured
from the first render and the migration invents no data.

The CHECK is explicit because nothing else would enforce it. A declarative type
alone does not: `sa.Enum(native_enum=False)` creates no constraint, and a plain
`String` column accepts any text at all — including a colour name, a 3-digit
hex, or a sentence.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "table",
    ["facet_values", "senders"],
)
async def test_colour_defaults_to_null(session: AsyncSession, facets, table: str) -> None:
    """Every existing row has no colour, and that is a valid state."""
    if table == "senders":
        await session.execute(text("INSERT INTO senders (name) VALUES ('Corvus Test Supply')"))
    await session.commit()
    nulls = await session.scalar(text(f"SELECT count(*) FROM {table} WHERE colour IS NULL"))
    total = await session.scalar(text(f"SELECT count(*) FROM {table}"))
    assert total > 0
    assert nulls == total


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["facet_values", "senders"])
@pytest.mark.parametrize("colour", ["#1f77b4", "#FFFFFF", "#000000", "#aAbBcC"])
async def test_a_six_digit_hex_is_accepted(
    session: AsyncSession, facets, table: str, colour: str
) -> None:
    if table == "senders":
        await session.execute(text("INSERT INTO senders (name) VALUES ('Corvus Test Supply')"))
        await session.commit()
    await session.execute(text(f"UPDATE {table} SET colour = :colour"), {"colour": colour})
    await session.commit()
    stored = await session.scalar(text(f"SELECT DISTINCT colour FROM {table}"))
    assert stored == colour


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["facet_values", "senders"])
@pytest.mark.parametrize(
    "colour",
    [
        "1f77b4",  # no leading hash
        "#1f7",  # three-digit shorthand
        "#1f77b44",  # seven digits
        "#gggggg",  # not hex
        "rebeccapurple",  # a CSS colour name
        "",  # empty string is not "no colour"; NULL is
    ],
)
async def test_anything_that_is_not_a_six_digit_hex_is_refused(
    session: AsyncSession, facets, table: str, colour: str
) -> None:
    """The reason the CHECK is written out explicitly: without it the column
    accepts every one of these, and the first anyone would know is a legend
    rendering nothing."""
    if table == "senders":
        await session.execute(text("INSERT INTO senders (name) VALUES ('Corvus Test Supply')"))
        await session.commit()
    with pytest.raises(IntegrityError):
        await session.execute(text(f"UPDATE {table} SET colour = :colour"), {"colour": colour})
        await session.commit()
    await session.rollback()
```

- [ ] **Step 2: Run the test and watch it fail**

```bash
uv run pytest tests/test_split_colour.py -q
```

Expected: every test errors with `psycopg.errors.UndefinedColumn: column "colour" does not exist`.

- [ ] **Step 3: Write the migration**

Create `migrations/versions/0037_split_colour.py`:

```python
"""a stored colour for the two split axes

Revision ID: 0037
Revises: 0036

A split value's colour, so a value is the same colour in every chart it appears
in (spec §10.3). Both split axes get one: ``facet_values`` for a facet split and
``senders`` for ``split=sender``, which is a real column and so a real axis.

**Nullable, and null is the normal state.** A null colour means "derive the
palette slot from the key", which is what makes a legend stably and accessibly
coloured before anyone has chosen anything — so this migration invents no data.
A NOT NULL column would have to, and would then own the palette: changing it
later would need a second data migration, and a value created afterwards would
need a colour picked at insert.

The CHECK is written out because **nothing else would enforce the format**. A
plain ``String`` accepts any text, and the lesson 0034 and 0036 already paid for
is that a declarative type does not create a constraint on its own
(``sa.Enum(native_enum=False)`` creates none at all). Without it the column
takes ``rebeccapurple``, ``#1f7`` or a sentence, and the first anyone knows is a
legend that renders nothing.

``name=`` carries the convention-relative suffix only. Alembic's ``"ck"``
template is ``"ck_%(table_name)s_%(constraint_name)s"`` and substitutes an
explicit name *into* the token, so a name already carrying the prefix is
prefixed twice in the live database (the note in 0036).

Create Date: 2026-08-30 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Six-digit hex with a leading hash, either case. Anchored at both ends, so a
#: valid colour with trailing text is refused rather than truncated.
_HEX = "colour ~ '^#[0-9a-fA-F]{6}$'"


def upgrade() -> None:
    for table in ("facet_values", "senders"):
        op.add_column(table, sa.Column("colour", sa.String(length=7), nullable=True))
        op.create_check_constraint("colour_hex", table, _HEX)


def downgrade() -> None:
    for table in ("facet_values", "senders"):
        op.drop_constraint(f"ck_{table}_colour_hex", table, type_="check")
        op.drop_column(table, "colour")
```

- [ ] **Step 4: Add the columns to the models**

In `src/library/models.py`, in `class Sender`, after `name`:

```python
    #: A stored colour for this sender as a chart split value (spec §10.3).
    #: NULL means "derive a palette slot from the id" — the normal state.
    colour: Mapped[str | None] = mapped_column(String(7), nullable=True)
```

In `class FacetValue`, after `ordinal`:

```python
    #: A stored colour for this value as a chart split value (spec §10.3).
    #: NULL means "derive a palette slot from the key" — the normal state.
    colour: Mapped[str | None] = mapped_column(String(7), nullable=True)
```

Add the matching `CheckConstraint`s to each model's `__table_args__` so the
ORM metadata and the migration agree (the models test compares them):

```python
        CheckConstraint("colour ~ '^#[0-9a-fA-F]{6}$'", name="colour_hex"),
```

`CheckConstraint` is imported from `sqlalchemy`; check whether `models.py`
already imports it and add it to the existing import if not.

- [ ] **Step 5: Run the test and watch it pass**

```bash
uv run pytest tests/test_split_colour.py -q
```

Expected: all parametrisations pass.

- [ ] **Step 6: Mutation-check the CHECK**

Temporarily delete the `op.create_check_constraint` loop body's second line from
the migration, re-run, and confirm
`test_anything_that_is_not_a_six_digit_hex_is_refused` goes **red** for every
parametrisation. Restore it. A constraint test that passes without the
constraint is testing nothing.

- [ ] **Step 7: Confirm the migration round-trips**

```bash
uv run pytest tests/test_migrations.py -q || uv run pytest -q -k migration
```

Expected: PASS. If no migration round-trip test exists, run
`uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
against a scratch database and confirm all three succeed.

- [ ] **Step 8: Format and commit**

```bash
uv run ruff format . && uv run ruff check .
git add migrations/versions/0037_split_colour.py src/library/models.py tests/test_split_colour.py
git commit -m "feat(charts): a nullable stored colour for both split axes"
```

---

## Task 2: `colour` on the read surfaces

**Files:**
- Modify: `src/library/facets/vocabulary.py` (`VocabularyValue` ~line 27, `load_vocabulary` ~line 48)
- Modify: `src/library/api/facets.py` (`ValueOut` ~line 59, `list_facets` ~line 108)
- Modify: `src/library/api/taxonomy.py` (`SenderWithCount` ~line 52)
- Test: `tests/test_split_colour.py`

**Interfaces:**
- Consumes: Task 1's `FacetValue.colour`, `Sender.colour`.
- Produces: `VocabularyValue.colour: str | None`; `ValueOut.colour: str | None` on `GET /api/facets`; `SenderWithCount.colour: str | None` on `GET /api/senders`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_split_colour.py`:

```python
@pytest.mark.asyncio
async def test_the_vocabulary_carries_each_value_s_colour(session: AsyncSession, facets) -> None:
    """`load_vocabulary` is what the spending router already loads to validate a
    rule, so reading colour from it is what makes split resolution free."""
    from library.facets.vocabulary import load_vocabulary

    await session.execute(
        text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'")
    )
    await session.commit()
    vocabulary = await load_vocabulary(session)
    category = next(f for f in vocabulary if f.key == "category")
    assert category.value("software").colour == "#1f77b4"
    assert category.value("services").colour is None


@pytest.mark.asyncio
async def test_get_facets_returns_colour(client, session: AsyncSession, facets) -> None:
    await session.execute(
        text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'")
    )
    await session.commit()
    body = (await client.get("/api/facets")).json()
    values = {
        v["key"]: v["colour"]
        for f in body["facets"]
        if f["key"] == "category"
        for v in f["values"]
    }
    assert values["software"] == "#1f77b4"
    assert values["services"] is None


@pytest.mark.asyncio
async def test_get_senders_returns_colour(client, session: AsyncSession) -> None:
    await session.execute(
        text("INSERT INTO senders (name, colour) VALUES ('Corvus Test Supply', '#d62728')")
    )
    await session.commit()
    rows = (await client.get("/api/senders")).json()
    assert [r["colour"] for r in rows if r["name"] == "Corvus Test Supply"] == ["#d62728"]
```

Check the existing `client` fixture's name in `tests/test_api_facets.py` and use
whatever that file uses; the fixtures in this repository are shared through
`tests/conftest.py`.

- [ ] **Step 2: Run the test and watch it fail**

```bash
uv run pytest tests/test_split_colour.py -q -k colour_
```

Expected: FAIL — `AttributeError: 'VocabularyValue' object has no attribute 'colour'` and `KeyError: 'colour'`.

- [ ] **Step 3: Add `colour` to `VocabularyValue`**

In `src/library/facets/vocabulary.py`:

```python
@dataclass(frozen=True, slots=True)
class VocabularyValue:
    id: int
    key: str
    label: str
    parent_id: int | None
    aliases: tuple[str, ...]
    #: A stored override; None means "derive a palette slot from `key`".
    colour: str | None = None
```

In `load_vocabulary`, pass it through where `VocabularyValue(...)` is
constructed: add `colour=value.colour,`.

- [ ] **Step 4: Add `colour` to the two response models**

In `src/library/api/facets.py`:

```python
class ValueOut(BaseModel):
    key: str
    label: str
    parent_id: int | None
    aliases: list[str]
    #: A stored colour for this value as a chart split value; null means the
    #: client derives a stable palette slot from `key` (spec §2.5).
    colour: str | None = None
```

and add `colour=value.colour` where `list_facets` builds each `ValueOut`.

In `src/library/api/taxonomy.py`:

```python
class SenderWithCount(BaseModel):
    """One row of GET /api/senders."""

    id: int
    name: str
    document_count: int = Field(description="Non-deleted documents from this sender.")
    #: A stored colour for this sender as a chart split value; null means the
    #: client derives a stable palette slot from `id` (spec §2.5).
    colour: str | None = None
```

`list_senders` uses `model_validate(..., from_attributes=True)`, so the new
field is picked up from the ORM object with no further change.

- [ ] **Step 5: Run the test and watch it pass**

```bash
uv run pytest tests/test_split_colour.py -q
```

Expected: PASS.

- [ ] **Step 6: Mutation-check**

Remove `colour=value.colour` from `load_vocabulary` and confirm
`test_the_vocabulary_carries_each_value_s_colour` goes **red** rather than
passing on the dataclass default. Restore it. This is the specific trap the
default `= None` creates.

- [ ] **Step 7: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add -A && git commit -m "feat(charts): expose split colours on the read surfaces"
```

---

## Task 3: `colour` on the write surfaces

**Files:**
- Modify: `src/library/api/facets.py` (`ValueRename` ~line 88, `rename_value` ~line 169)
- Modify: `src/library/api/taxonomy.py` (new route)
- Modify: `src/library/facets/vocabulary.py` (a `set_value_colour` helper)
- Test: `tests/test_split_colour.py`

**Interfaces:**
- Consumes: Task 2's models.
- Produces: `PATCH /api/facets/{facet_key}/values/{value_key}` accepting `{"label"?: str, "colour"?: str | null}`; `PATCH /api/senders/{sender_id}` accepting `{"colour"?: str | null}`. Both return the resulting row.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_split_colour.py`:

```python
COLOUR_PATTERN_REJECTS = ["1f77b4", "#1f7", "#gggggg", "rebeccapurple"]


@pytest.mark.asyncio
async def test_a_value_s_colour_can_be_set_without_renaming_it(client, facets) -> None:
    """Setting a colour must not force a rename. `label` is optional on the
    patch for the same reason `colour` is: an absent field is left alone."""
    before = (await client.get("/api/facets")).json()
    label_before = next(
        v["label"]
        for f in before["facets"]
        if f["key"] == "category"
        for v in f["values"]
        if v["key"] == "software"
    )
    response = await client.patch(
        "/api/facets/category/values/software", json={"colour": "#1f77b4"}
    )
    assert response.status_code == 200
    after = (await client.get("/api/facets")).json()
    value = next(
        v for f in after["facets"] if f["key"] == "category" for v in f["values"]
        if v["key"] == "software"
    )
    assert value["colour"] == "#1f77b4"
    assert value["label"] == label_before


@pytest.mark.asyncio
async def test_an_explicit_null_clears_a_colour_and_an_absent_field_does_not(
    client, facets
) -> None:
    """The `model_fields_set` distinction, which is the whole reason this is not
    one nullable field: "clear it" and "do not touch it" are different requests
    that both look like None."""
    await client.patch("/api/facets/category/values/software", json={"colour": "#1f77b4"})

    await client.patch("/api/facets/category/values/software", json={"label": "Software"})
    kept = (await client.get("/api/facets")).json()
    assert _colour(kept, "software") == "#1f77b4", "an absent colour must be left alone"

    await client.patch("/api/facets/category/values/software", json={"colour": None})
    cleared = (await client.get("/api/facets")).json()
    assert _colour(cleared, "software") is None, "an explicit null must clear it"


def _colour(body: dict, value_key: str) -> str | None:
    return next(
        v["colour"]
        for f in body["facets"]
        if f["key"] == "category"
        for v in f["values"]
        if v["key"] == value_key
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("colour", COLOUR_PATTERN_REJECTS)
async def test_a_malformed_colour_is_a_422_not_a_500(client, facets, colour: str) -> None:
    """Refused by the request model, so the database CHECK is defence in depth
    rather than the error path the owner sees."""
    response = await client.patch(
        "/api/facets/category/values/software", json={"colour": colour}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_sender_s_colour_can_be_set_and_cleared(client, session: AsyncSession) -> None:
    await session.execute(text("INSERT INTO senders (name) VALUES ('Corvus Test Supply')"))
    await session.commit()
    sender_id = await session.scalar(
        text("SELECT id FROM senders WHERE name = 'Corvus Test Supply'")
    )

    set_response = await client.patch(f"/api/senders/{sender_id}", json={"colour": "#d62728"})
    assert set_response.status_code == 200
    assert set_response.json()["colour"] == "#d62728"

    clear_response = await client.patch(f"/api/senders/{sender_id}", json={"colour": None})
    assert clear_response.json()["colour"] is None


@pytest.mark.asyncio
async def test_patching_an_unknown_sender_is_a_404(client) -> None:
    assert (await client.patch("/api/senders/999999", json={"colour": "#d62728"})).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("colour", COLOUR_PATTERN_REJECTS)
async def test_a_malformed_sender_colour_is_a_422(client, session, colour: str) -> None:
    await session.execute(text("INSERT INTO senders (name) VALUES ('Corvus Test Supply')"))
    await session.commit()
    sender_id = await session.scalar(
        text("SELECT id FROM senders WHERE name = 'Corvus Test Supply'")
    )
    response = await client.patch(f"/api/senders/{sender_id}", json={"colour": colour})
    assert response.status_code == 422
```

- [ ] **Step 2: Run the test and watch it fail**

```bash
uv run pytest tests/test_split_colour.py -q -k "colour_can_be_set or clears_a_colour or malformed or unknown_sender"
```

Expected: FAIL — `422` from `ValueRename` requiring `label`, and `405 Method Not Allowed` for `PATCH /api/senders/{id}`.

- [ ] **Step 3: Make `ValueRename` a patch**

In `src/library/api/facets.py`, replace `ValueRename` and add the shared
constraint:

```python
#: Six-digit hex with a leading hash. The same shape the database CHECK
#: enforces (migration 0037), stated here so a malformed colour is a 422 the
#: owner can read rather than an IntegrityError translated after the fact.
Colour = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class ValuePatch(BaseModel):
    """Edit a value's display attributes. Every field optional.

    `colour` is genuinely nullable, so "clear it" and "do not touch it" cannot
    both be `None` in one field — they are told apart by `model_fields_set`,
    exactly as `ChartPatch.default_split` already is.
    """

    label: Label | None = None
    colour: Colour | None = None
```

Keep the name `ValueRename` as an alias if anything else imports it; grep first
with `grep -rn ValueRename src/ tests/`.

- [ ] **Step 4: Rewrite the route**

Replace `rename_value` in `src/library/api/facets.py`:

```python
@router.patch("/facets/{facet_key}/values/{value_key}", summary="Edit a value")
async def patch_value(
    facet_key: str,
    value_key: str,
    body: ValuePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ValueOut:
    fields = body.model_fields_set
    try:
        if "label" in fields:
            if body.label is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="label cannot be cleared; a value must have a display label",
                )
            await vocabulary.rename_value(session, facet_key, value_key, body.label)
        if "colour" in fields:
            await vocabulary.set_value_colour(session, facet_key, value_key, body.colour)
        value = await vocabulary.get_value(session, facet_key, value_key)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown facet value"
        ) from exc
    await session.commit()
    return ValueOut(
        key=value.key,
        label=value.label,
        parent_id=value.parent_id,
        aliases=sorted(value.aliases),
        colour=value.colour,
    )
```

- [ ] **Step 5: Add the two vocabulary helpers**

In `src/library/facets/vocabulary.py`, beside `rename_value`:

```python
async def set_value_colour(
    session: AsyncSession, facet_key: str, value_key: str, colour: str | None
) -> None:
    """Set or clear a value's stored colour. `None` clears it, returning the
    value to the derived palette slot (spec §2.5)."""
    _facet_id, value_id = await _resolve(session, facet_key, value_key)
    await session.execute(
        update(FacetValue).where(FacetValue.id == value_id).values(colour=colour)
    )


async def get_value(session: AsyncSession, facet_key: str, value_key: str) -> VocabularyValue:
    """One value, with its aliases and colour. Raises like `rename_value` does."""
    _facet_id, value_id = await _resolve(session, facet_key, value_key)
    row = await session.get(FacetValue, value_id)
    assert row is not None  # _resolve just found it in this transaction
    aliases = (
        (
            await session.execute(
                select(FacetValueAlias.alias).where(FacetValueAlias.facet_value_id == value_id)
            )
        )
        .scalars()
        .all()
    )
    return VocabularyValue(
        id=row.id,
        key=row.key,
        label=row.label,
        parent_id=row.parent_id,
        aliases=tuple(aliases),
        colour=row.colour,
    )
```

`_resolve` is the existing private helper at ~line 90 that returns
`(facet_id, value_id)` and raises `UnknownFacetError` / `UnknownValueError`;
read it and call it by whatever name it actually has. Add `update` to the
`sqlalchemy` import if it is not already there.

- [ ] **Step 6: Add the sender route**

In `src/library/api/taxonomy.py`, after `list_senders`:

```python
class SenderPatch(BaseModel):
    """Edit a sender's display attributes. `colour` only, for now: a sender's
    name is derived from ingested documents and renaming one is a taxonomy
    operation with its own merge semantics (see the admin recipients route)."""

    colour: Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None


@router.patch("/senders/{sender_id}", response_model=SenderWithCount, summary="Edit a sender")
async def patch_sender(
    sender_id: int,
    body: SenderPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SenderWithCount:
    """Set or clear a sender's stored chart colour (spec §2.5).

    An absent `colour` leaves it alone; an explicit `null` clears it, returning
    the sender to a palette slot derived from its id.
    """
    sender = await session.get(Sender, sender_id)
    if sender is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no sender with id {sender_id}"
        )
    if "colour" in body.model_fields_set:
        sender.colour = body.colour
    await session.commit()
    return SenderWithCount(
        id=sender.id,
        name=sender.name,
        document_count=await taxonomy.sender_document_count(session, sender_id),
        colour=sender.colour,
    )
```

If `taxonomy.sender_document_count` does not exist, read `taxonomy.list_senders`
and reuse the count expression it already builds rather than writing a second
one — a second definition of "non-deleted documents from this sender" is the
kind of duplicate this codebase deletes rather than tests.

Add whatever imports the file is missing (`Sender`, `HTTPException`, `status`,
`BaseModel`, `StringConstraints`).

- [ ] **Step 7: Run the tests and watch them pass**

```bash
uv run pytest tests/test_split_colour.py tests/test_api_facets.py tests/test_facet_crud.py -q
```

Expected: PASS. `test_api_facets.py` and `test_facet_crud.py` are included
because Step 3 changed a request model they exercise; fix any caller that sent
`{"label": ...}` and relied on it being required.

- [ ] **Step 8: Mutation-check the sentinel**

Change `if "colour" in body.model_fields_set:` to `if body.colour is not None:`
and confirm `test_an_explicit_null_clears_a_colour_and_an_absent_field_does_not`
goes **red** on the clear. Restore it. That assertion is the only thing standing
between this and a colour nobody can remove.

- [ ] **Step 9: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add -A && git commit -m "feat(charts): set and clear a split value's colour"
```

---

## Task 4: `GET /api/spending/{id}`

**Files:**
- Modify: `src/library/api/spending.py` (beside `list_charts` ~line 645)
- Test: `tests/test_api_spending.py`

**Interfaces:**
- Consumes: the existing `_load_chart` and `_chart_out`.
- Produces: `GET /api/spending/{chart_id}` → `ChartOut`, 404 when unknown.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_spending.py`:

```python
@pytest.mark.asyncio
async def test_one_chart_can_be_read_by_id(client, facets) -> None:
    """The workspace loads one chart. Without this it has to page the list
    looking for a row, which breaks the moment there are more than `limit`."""
    created = (
        await client.post(
            "/api/spending",
            json={
                "name": "Software spending",
                "rule": {"all": [{"facet": "category", "values": ["software"]}]},
                "display_currency": "EUR",
                "default_split": "cost_type",
            },
        )
    ).json()

    response = await client.get(f"/api/spending/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.asyncio
async def test_reading_an_unknown_chart_is_a_404(client) -> None:
    response = await client.get("/api/spending/999999")
    assert response.status_code == 404
    assert "999999" in response.json()["detail"]
```

- [ ] **Step 2: Run and watch it fail**

```bash
uv run pytest tests/test_api_spending.py -q -k "read_by_id or unknown_chart"
```

Expected: FAIL — the path matches no route, or `422` because `{chart_id}` is
shadowed. If it 422s, the route ordering matters; see Step 3's note.

- [ ] **Step 3: Add the route**

In `src/library/api/spending.py`, immediately after `list_charts`:

```python
@router.get("/spending/{chart_id}", summary="One saved question")
async def get_chart(
    chart_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> ChartOut:
    """One chart by id. The workspace loads through this rather than paging the
    list, which would stop finding a chart as soon as there are more than
    `limit` of them."""
    return _chart_out(await _load_chart(session, chart_id))
```

`chart_id` is typed `int`, so `/spending/draft` — a `POST` — cannot be captured
by it, and the two `GET` sub-paths (`/data`, `/cell`) are longer and match
first. Declare this route **after** `list_charts` and **before** the `/data`
route only if a test shows an ordering problem; FastAPI matches on the full
path, so ordering is not expected to matter here. Verify with Step 4 rather than
assuming.

- [ ] **Step 4: Run the whole spending suite**

```bash
uv run pytest tests/test_api_spending.py -q
```

Expected: PASS, including every pre-existing test — this is the check that the
new path did not shadow `/data`, `/cell` or `/draft`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add -A && git commit -m "feat(charts): read one saved question by id"
```

---

## Task 5: split values resolve to a label and a colour

**Files:**
- Modify: `src/library/api/spending.py` (`_ChartQuery` ~line 355, `_resolve_query` ~line 471, `_data_out` ~line 561, `chart_cell_data` ~line 776)
- Test: `tests/test_api_spending.py`

**Interfaces:**
- Consumes: Task 2's `VocabularyValue.colour` and `Sender.colour`.
- Produces:
  - `class SplitValueOut(BaseModel): value: str | None; label: str; colour: str | None`
  - `DataOut.splits: list[SplitValueOut]`
  - `CellOutBody.label: str` and `CellOutBody.colour: str | None`
  - `async def _resolve_splits(session, query: _ChartQuery, values: list[str | None]) -> list[SplitValueOut]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_spending.py`:

```python
#: Invented. Nothing here corresponds to a real sender.
VENDOR_A = "Corvus Test Assurance"
VENDOR_B = "Kestrel Test Utilities"


@pytest.mark.asyncio
async def test_a_facet_split_resolves_value_keys_to_display_labels(
    client, session, document, facets
) -> None:
    """`spend_facts.labels` maps a facet key to a value *key*, so an unresolved
    legend reads `software`. §2.3: the legend carries names."""
    await document(
        amount_total=Decimal("10.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
        labels={"category": "software"},
    )
    await session.commit()
    chart = (
        await client.post(
            "/api/spending",
            json={"name": "All", "display_currency": "EUR", "default_split": "category"},
        )
    ).json()

    body = (await client.get(f"/api/spending/{chart['id']}/data")).json()

    assert {s["value"]: s["label"] for s in body["splits"]} == {"software": "Software"}


@pytest.mark.asyncio
async def test_a_sender_split_resolves_ids_to_names(client, session, document, facets) -> None:
    """The engine emits `CAST(sf.sender_id AS text)`, so without resolution the
    legend reads `41`. The id stays as `value` because `/cell` round-trips it."""
    await document(
        amount_total=Decimal("10.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
        sender=VENDOR_A,
    )
    await session.commit()
    chart = (
        await client.post(
            "/api/spending",
            json={"name": "All", "display_currency": "EUR", "default_split": "sender"},
        )
    ).json()

    body = (await client.get(f"/api/spending/{chart['id']}/data")).json()

    assert [s["label"] for s in body["splits"]] == [VENDOR_A]
    assert body["splits"][0]["value"].isdigit(), "value stays the id /cell must be sent back"


@pytest.mark.asyncio
async def test_the_unlabelled_bucket_is_named_by_the_axis(
    client, session, document, facets
) -> None:
    """`split_value` is null both for "no value for this facet" and for "no
    sender". A client cannot invent either name; the API supplies it."""
    await document(
        amount_total=Decimal("10.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
    )
    await session.commit()
    facet_chart = (
        await client.post(
            "/api/spending",
            json={"name": "By category", "display_currency": "EUR", "default_split": "category"},
        )
    ).json()
    sender_chart = (
        await client.post(
            "/api/spending",
            json={"name": "By sender", "display_currency": "EUR", "default_split": "sender"},
        )
    ).json()

    by_facet = (await client.get(f"/api/spending/{facet_chart['id']}/data")).json()
    by_sender = (await client.get(f"/api/spending/{sender_chart['id']}/data")).json()

    assert [(s["value"], s["label"]) for s in by_facet["splits"]] == [(None, "Uncategorised")]
    assert [(s["value"], s["label"]) for s in by_sender["splits"]] == [(None, "No sender")]


@pytest.mark.asyncio
async def test_a_split_value_carries_its_stored_colour_and_null_when_unset(
    client, session, document, facets
) -> None:
    await session.execute(
        text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'")
    )
    for value in ("software", "services"):
        await document(
            amount_total=Decimal("10.00"),
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2026, 4, 1),
            labels={"category": value},
        )
    await session.commit()
    chart = (
        await client.post(
            "/api/spending",
            json={"name": "All", "display_currency": "EUR", "default_split": "category"},
        )
    ).json()

    body = (await client.get(f"/api/spending/{chart['id']}/data")).json()

    colours = {s["value"]: s["colour"] for s in body["splits"]}
    assert colours == {"software": "#1f77b4", "services": None}


@pytest.mark.asyncio
async def test_a_split_value_whose_row_vanished_falls_back_to_the_raw_value(
    client, session, document, facets
) -> None:
    """Facet values are deletable at runtime and a saved chart can rot. A
    rotted legend entry is a legible defect; a 500 on every chart in range is
    not — the same failure the `sorted()` over a null currency caused."""
    await document(
        amount_total=Decimal("10.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
        sender=VENDOR_A,
    )
    await session.commit()
    chart = (
        await client.post(
            "/api/spending",
            json={"name": "All", "display_currency": "EUR", "default_split": "sender"},
        )
    ).json()
    # Break the reference the way runtime deletion would, without going through
    # a route that would refuse it.
    await session.execute(text("UPDATE documents SET sender_id = 999999"))
    await session.commit()

    response = await client.get(f"/api/spending/{chart['id']}/data")

    assert response.status_code == 200
    assert [s["label"] for s in response.json()["splits"]] == ["999999"]


@pytest.mark.asyncio
async def test_a_cell_carries_its_own_label_and_colour(
    client, session, document, facets
) -> None:
    """So a drilled panel can title itself without re-reading /data."""
    await session.execute(
        text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'")
    )
    await document(
        amount_total=Decimal("10.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
        labels={"category": "software"},
    )
    await session.commit()
    chart = (
        await client.post(
            "/api/spending",
            json={"name": "All", "display_currency": "EUR", "default_split": "category"},
        )
    ).json()
    data = (await client.get(f"/api/spending/{chart['id']}/data")).json()
    cell = data["cells"][0]

    body = (
        await client.get(
            f"/api/spending/{chart['id']}/cell",
            params={
                "period": cell["period"],
                "split_value": cell["split_value"],
                "grain": data["grain"],
                "split": data["split"],
                "currency": data["currency"],
            },
        )
    ).json()

    assert body["label"] == "Software"
    assert body["colour"] == "#1f77b4"


@pytest.mark.asyncio
async def test_an_unsplit_chart_has_no_split_values(client, session, document, facets) -> None:
    """`split_value` is null for an unsplit chart too, and that is not a bucket
    needing a name — it is the absence of an axis."""
    await document(
        amount_total=Decimal("10.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
    )
    await session.commit()
    chart = (
        await client.post(
            "/api/spending", json={"name": "All", "display_currency": "EUR"}
        )
    ).json()

    body = (await client.get(f"/api/spending/{chart['id']}/data")).json()

    assert body["split"] is None
    assert body["splits"] == []
```

- [ ] **Step 2: Run and watch every one fail**

```bash
uv run pytest tests/test_api_spending.py -q -k "split_resolves or unlabelled_bucket or stored_colour or raw_value or own_label or no_split_values"
```

Expected: FAIL with `KeyError: 'splits'` / `KeyError: 'label'`.

- [ ] **Step 3: Carry the vocabulary on `_ChartQuery`**

`_resolve_query` already calls `load_vocabulary` and throws the result away.
Keep it, so resolution costs nothing extra.

In `src/library/api/spending.py`, add a field to the frozen dataclass:

```python
@dataclass(frozen=True, slots=True)
class _ChartQuery:
    rule: Rule
    grain: Grain
    split: str | None
    currency: str
    since: date | None
    until: date | None
    #: The vocabulary this request was validated against. Kept so split
    #: resolution reads the labels already loaded rather than loading them
    #: again — and reads the *same* ones the rule was validated against, so a
    #: value deleted mid-request cannot be valid in one half and missing in the
    #: other.
    vocabulary: tuple[VocabularyFacet, ...]
```

and pass `vocabulary=vocabulary` in the `_ChartQuery(...)` construction at the
end of `_resolve_query`.

Note: `shared()` must **not** gain this field — it is the argument set the
engine is given, and `_SharedArgs` is a `TypedDict` deliberately typed so a
mismatch is a `mypy` error. Leave `shared()` alone.

- [ ] **Step 4: Add the model and the resolver**

Add beside `CellOut` in `src/library/api/spending.py`:

```python
class SplitValueOut(BaseModel):
    """One bucket of the split axis, resolved for display (§2.3).

    `value` is exactly what `/cell` must be sent back — the sender id as text,
    or the facet value key, never the label. Resolution is a display concern
    and lives here rather than in the engine, which keeps `split_value` stable
    across a rename (docs/charts.md §4).

    A **list** rather than a mapping: the unlabelled bucket's `value` is
    `null`, which cannot be a JSON object key, and it is the bucket whose name
    a client is least able to invent — it means "no value for this facet" under
    a facet split and "no sender" under `split=sender`.

    `colour` is a stored override; `null` means the client derives a stable
    palette slot from `value` (§2.5).
    """

    value: str | None
    label: str
    colour: str | None
```

and the resolver, beside `_merge_unconvertible`:

```python
#: What the `null` split bucket is called, per axis. It is a real bucket — an
#: unlabelled row lands in it rather than being dropped, which is the
#: mechanical basis of the total's invariance (docs/charts.md §4) — so it needs
#: a name, and only the API knows which one.
_NO_SENDER = "No sender"
_UNLABELLED = "Uncategorised"


async def _resolve_splits(
    session: AsyncSession, query: _ChartQuery, values: list[str | None]
) -> list[SplitValueOut]:
    """Label and colour each split bucket present in a result.

    Returns `[]` for an unsplit chart: there is no axis, so there are no
    buckets to name — distinct from a split axis whose only bucket is the
    unlabelled one.

    A `value` whose sender row or facet value has since been deleted resolves
    to itself. Facet values are deletable at runtime and a saved chart can rot;
    a rotted legend entry is a legible defect, while raising here would be a
    500 on every chart in range of one such row.
    """
    if query.split is None:
        return []

    if query.split == SENDER_SPLIT:
        ids = [int(value) for value in values if value is not None]
        rows = (
            await session.execute(
                select(Sender.id, Sender.name, Sender.colour).where(Sender.id.in_(ids))
            )
        ).all()
        by_id = {str(row.id): (row.name, row.colour) for row in rows}
        return [
            SplitValueOut(
                value=value,
                label=_NO_SENDER if value is None else by_id.get(value, (value, None))[0],
                colour=None if value is None else by_id.get(value, (value, None))[1],
            )
            for value in values
        ]

    facet = next((f for f in query.vocabulary if f.key == query.split), None)
    by_key = {v.key: v for v in facet.values} if facet is not None else {}
    return [
        SplitValueOut(
            value=value,
            label=_UNLABELLED if value is None else _label_of(by_key.get(value), value),
            colour=None if value is None else _colour_of(by_key.get(value)),
        )
        for value in values
    ]


def _label_of(value: VocabularyValue | None, fallback: str) -> str:
    return fallback if value is None else value.label


def _colour_of(value: VocabularyValue | None) -> str | None:
    return None if value is None else value.colour
```

Add `Sender` to the `library.models` import and `VocabularyValue` to the
`library.facets.vocabulary` import.

- [ ] **Step 5: Wire it into `/data`**

`_data_out` is synchronous and `_resolve_splits` is not, so resolve in
`_answer` (which already has the session) and pass the result down.

Change `_data_out`'s signature to take `splits: list[SplitValueOut]` and add
`splits=splits` to the `DataOut(...)` construction. Add the field to `DataOut`:

```python
    #: Every split bucket in `cells`, resolved for display (§2.3). Empty when
    #: the chart has no split axis.
    splits: list[SplitValueOut]
```

In `_answer`, after `chart_series` returns:

```python
    seen: list[str | None] = []
    for cell in series.cells:
        if cell.split_value not in seen:
            seen.append(cell.split_value)
    splits = await _resolve_splits(session, query, seen)
```

and pass `splits` to `_data_out`. The manual de-duplication preserves the
engine's ordering; `set()` would not, and the legend's order must match the
chart's.

- [ ] **Step 6: Wire it into `/cell`**

Add to `CellOutBody`:

```python
    #: This cell's split bucket, resolved for display — so a drilled panel can
    #: title itself without re-reading `/data`.
    label: str
    colour: str | None
```

In `chart_cell_data`, after the period check:

```python
    resolved = await _resolve_splits(session, query, [split_value])
    bucket = resolved[0] if resolved else None
```

and pass `label=bucket.label if bucket else "", colour=bucket.colour if bucket else None`
into `CellOutBody(...)`. An unsplit chart's cell has no bucket, so its label is
empty — the panel titles itself from the chart's name in that case.

- [ ] **Step 7: Run the tests and watch them pass**

```bash
uv run pytest tests/test_api_spending.py -q
```

Expected: PASS, including every pre-existing test.

- [ ] **Step 8: Mutation-check the fallback and the ordering**

Two mutations, each restored afterwards:

1. Change `by_id.get(value, (value, None))` to `by_id[value]` and confirm
   `test_a_split_value_whose_row_vanished_falls_back_to_the_raw_value` goes
   **red** with a `KeyError` rather than passing.
2. Replace the manual de-duplication with `seen = list(set(...))` and confirm a
   test asserting legend order goes red. If no such test exists, that is a gap:
   add one with two labelled values and assert `[s["value"] for s in splits]`
   matches the order the cells appear in.

- [ ] **Step 9: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add -A && git commit -m "feat(charts): resolve split values to labels and colours"
```

---

## Task 6: the footer drill route

**Files:**
- Modify: `src/library/charts/footer.py` (extract the row fetch from `chart_footer` ~line 203; add `chart_footer_documents`)
- Modify: `src/library/api/spending.py` (new route)
- Test: `tests/test_chart_footer.py`, `tests/test_api_spending.py`

**Interfaces:**
- Consumes: the existing `_CLASSIFY_SQL`, `_UNLABELLED`, `_NEVER_UNLABELLED`.
- Produces:
  - `async def _classified_rows(session, rule, *, currency, since, until, facets_in_rule) -> Sequence[Row]` — the shared fetch, called by both `chart_footer` and `chart_footer_documents`.
  - `class FooterDocument(BaseModel): document_id: int; amount: Decimal; currency: str | None; date: date | None; amount_kind: str | None`
  - `async def chart_footer_documents(session, rule, *, bucket: str, amount_kind: str | None, currency, since, until, facets_in_rule) -> list[FooterDocument]`
  - `GET /api/spending/{chart_id}/footer/{bucket}` → `FooterDocumentsOut`

- [ ] **Step 1: Write the failing test**

The shape below is the one the prototype executed against Postgres (spec §3.3);
it is the reason this route deduplicates and sums.

Append to `tests/test_chart_footer.py`:

```python
@pytest.mark.asyncio
async def test_a_split_document_appears_once_with_its_rows_summed(
    session, document, facets
) -> None:
    """Executed against Postgres before this was planned: a document split
    across spend lines emits **two** rows into one bucket while
    `_Group.documents` — a set — reports one. So the list deduplicates, and a
    listed document's amount is the sum of its rows in that bucket.

    Rendering one row's amount would print `60.00` under a footer reading
    `100.00`: a number that appears nowhere in the accounting.
    """
    from library.charts.footer import chart_footer_documents

    doc = await document(
        amount_total=Decimal("100.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
    )
    await replace_lines(
        session,
        doc.id,
        [
            LineInput(amount=Decimal("60.00"), note="a", labels={}),
            LineInput(amount=Decimal("40.00"), note="b", labels={}),
        ],
    )
    await session.commit()
    rule = Rule(all=[Clause(facet="category", values=["software"])])

    rows = await chart_footer_documents(
        session,
        rule,
        bucket="uncategorised",
        amount_kind=None,
        currency="EUR",
        since=None,
        until=None,
        facets_in_rule={"category"},
    )

    assert [(r.document_id, r.amount) for r in rows] == [(doc.id, Decimal("100.00"))]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bucket", "kind", "make"),
    [
        ("uncategorised", None, {"amount_kind": AmountKind.PAYMENT_MADE}),
        ("unclassified", None, {"amount_kind": None}),
        ("undated", None, {"amount_kind": AmountKind.PAYMENT_MADE, "document_date": None}),
        ("excluded", "coverage_limit", {"amount_kind": "coverage_limit"}),
    ],
)
async def test_the_list_length_equals_the_footer_s_count(
    session, document, facets, bucket: str, kind: str | None, make: dict
) -> None:
    """The invariant this route exists to hold: what the footer counts is what
    the panel lists. §8's "the panel must add up to the bar", one level down.

    Two documents so an off-by-one is visible; the same document twice would
    pass under a broken deduplication.
    """
    from library.charts.footer import chart_footer_documents

    defaults = {"document_date": date(2026, 4, 1), "currency": "EUR"}
    for _ in range(2):
        await document(amount_total=Decimal("10.00"), **{**defaults, **make})
    await session.commit()
    rule = Rule(all=[Clause(facet="category", values=["software"])])
    shared = {
        "currency": "EUR",
        "since": None,
        "until": None,
        "facets_in_rule": {"category"},
    }

    footer = await chart_footer(session, rule, **shared)
    rows = await chart_footer_documents(
        session, rule, bucket=bucket, amount_kind=kind, **shared
    )

    group = (
        next(g for g in footer.excluded if g.amount_kind == kind)
        if bucket == "excluded"
        else getattr(footer, bucket)
    )
    assert group is not None, f"the fixture did not land in {bucket}"
    assert len(rows) == group.documents
    assert sum((r.amount for r in rows), Decimal(0)) == group.amount


@pytest.mark.asyncio
async def test_an_empty_bucket_lists_nothing_rather_than_raising(session, facets) -> None:
    from library.charts.footer import chart_footer_documents

    rows = await chart_footer_documents(
        session,
        Rule(),
        bucket="unaccounted",
        amount_kind=None,
        currency="EUR",
        since=None,
        until=None,
        facets_in_rule=set(),
    )
    assert rows == []
```

Append to `tests/test_api_spending.py`:

```python
@pytest.mark.asyncio
async def test_the_footer_route_lists_the_documents_behind_a_count(
    client, session, document, facets
) -> None:
    await document(
        amount_total=Decimal("89.20"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 4, 1),
        title="Kestrel Test Utilities statement",
    )
    await session.commit()
    chart = (
        await client.post(
            "/api/spending",
            json={
                "name": "Software",
                "display_currency": "EUR",
                "rule": {"all": [{"facet": "category", "values": ["software"]}]},
            },
        )
    ).json()
    data = (await client.get(f"/api/spending/{chart['id']}/data")).json()
    assert data["footer"]["uncategorised"]["documents"] == 1

    body = (
        await client.get(
            f"/api/spending/{chart['id']}/footer/uncategorised",
            params={"currency": data["currency"]},
        )
    ).json()

    assert [d["title"] for d in body["documents"]] == ["Kestrel Test Utilities statement"]
    assert body["documents"][0]["amount"] == "89.20"


@pytest.mark.asyncio
async def test_an_unknown_footer_bucket_is_a_422_naming_it(client, facets) -> None:
    chart = (
        await client.post(
            "/api/spending", json={"name": "All", "display_currency": "EUR"}
        )
    ).json()
    response = await client.get(f"/api/spending/{chart['id']}/footer/nonsense")
    assert response.status_code == 422
    assert "nonsense" in response.json()["detail"]


@pytest.mark.asyncio
async def test_the_footer_route_caps_its_limit_at_100(client, facets) -> None:
    chart = (
        await client.post(
            "/api/spending", json={"name": "All", "display_currency": "EUR"}
        )
    ).json()
    response = await client.get(
        f"/api/spending/{chart['id']}/footer/uncategorised", params={"limit": 101}
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run and watch it fail**

```bash
uv run pytest tests/test_chart_footer.py tests/test_api_spending.py -q -k "split_document_appears or list_length or empty_bucket or footer_route or unknown_footer"
```

Expected: FAIL — `ImportError: cannot import name 'chart_footer_documents'`.

- [ ] **Step 3: Extract the shared row fetch**

Read `chart_footer` in `src/library/charts/footer.py`. It builds the statement,
binds the parameters and iterates the rows. Move everything up to and including
the `session.execute(...)` into one private function, and have `chart_footer`
call it. **Do not copy the statement.** The whole reason this route is cheap is
that the classification stays in one place; a second `text(_CLASSIFY_SQL...)`
call site is a second thing that can drift, and it would fail *open* — the count
and the list would disagree only on the branch neither test exercised.

```python
async def _classified_rows(
    session: AsyncSession,
    rule: Rule,
    *,
    currency: str,
    since: date | None,
    until: date | None,
    facets_in_rule: set[str],
) -> Sequence[Any]:
    """Every row the rule touched, each in exactly one bucket.

    The one execution of `_CLASSIFY_SQL`. `chart_footer` aggregates what this
    returns; `chart_footer_documents` filters it. Two callers, one `CASE` — so
    a bucket the aggregate reports and a bucket the list can open are the same
    bucket by construction rather than by a test comparing two copies.
    """
```

Its body is the code you just moved. Preserve the existing bind construction
exactly — `summable`, `refund`, `facets`, `since`, `until` and the rule's own
binds.

- [ ] **Step 4: Add `chart_footer_documents`**

```python
class FooterDocument(BaseModel):
    """One document behind a footer bucket.

    `amount` is the **sum of this document's rows in this bucket**, not one
    row's: a document split across spend lines emits one row per line, and a
    `100.00` document split `60.00`/`40.00` with neither line labelled emits
    two — proved against Postgres before this was written. Rendering a single
    row's amount would print a number the footer never reports.
    """

    document_id: int
    amount: Decimal
    currency: str | None
    date: date | None
    amount_kind: str | None


async def chart_footer_documents(
    session: AsyncSession,
    rule: Rule,
    *,
    bucket: str,
    amount_kind: str | None,
    currency: str,
    since: date | None,
    until: date | None,
    facets_in_rule: set[str],
) -> list[FooterDocument]:
    """The documents behind one footer bucket, deduplicated by document.

    `len(...)` equals the bucket's reported `documents` and the amounts sum to
    its reported `amount`, because both come from the same rows `chart_footer`
    aggregated. `amount_kind` selects one group out of `excluded`, which is a
    list of groups rather than a single figure; it is ignored for every other
    bucket, which has exactly one group.

    Ordering is by descending absolute amount then document id — the largest
    contributor first, and stable across calls.
    """
    rows = await _classified_rows(
        session,
        rule,
        currency=currency,
        since=since,
        until=until,
        facets_in_rule=facets_in_rule,
    )
    merged: dict[int, FooterDocument] = {}
    for row in rows:
        if row.bucket != bucket:
            continue
        if bucket == "excluded" and amount_kind is not None and row.amount_kind != amount_kind:
            continue
        existing = merged.get(row.document_id)
        if existing is None:
            merged[row.document_id] = FooterDocument(
                document_id=row.document_id,
                amount=row.amount,
                currency=row.currency,
                date=row.date,
                amount_kind=row.amount_kind,
            )
        else:
            merged[row.document_id] = existing.model_copy(
                update={"amount": existing.amount + row.amount}
            )
    return sorted(merged.values(), key=lambda d: (-abs(d.amount), d.document_id))
```

**Signs.** `chart_footer` accounts summable amounts *signed* through
`AMOUNT_SIGN` and reports magnitudes for kinds that never enter a total. Read
how it does that and apply the identical treatment here, or the sums in
`test_the_list_length_equals_the_footer_s_count` will not match for a bucket
containing a refund. Do not re-derive the rule — call whatever `footer.py`
already uses.

- [ ] **Step 5: Add the route**

In `src/library/api/spending.py`:

```python
#: The buckets `_CLASSIFY_SQL` can put a row in that the footer reports. Named
#: here rather than derived from `FooterOut`'s fields so an unknown bucket is a
#: 422 the owner can read, and so `unaccounted` is openable: a bug signal you
#: cannot open is not a signal.
_FOOTER_BUCKETS = frozenset(
    {"excluded", "unclassified", "uncategorised", "undated", "unaccounted"}
)


class FooterDocumentOut(BaseModel):
    """One document behind a footer count. `amount` is this document's total
    within the bucket, summed across its spend lines."""

    id: int
    title: str | None
    date: date | None
    amount: Decimal
    currency: str | None
    amount_kind: str | None


class FooterDocumentsOut(BaseModel):
    bucket: str
    documents: list[FooterDocumentOut]


@router.get(
    "/spending/{chart_id}/footer/{bucket}", summary="The documents behind a footer count"
)
async def chart_footer_bucket(
    chart_id: int,
    bucket: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    amount_kind: Annotated[
        str | None, Query(description="Selects one group out of `excluded`.")
    ] = None,
    since: Annotated[date | None, Query(alias="from")] = None,
    until: Annotated[date | None, Query(alias="to")] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FooterDocumentsOut:
    """§9.4 calls uncategorised money "a visible task". This is what makes it
    one: without it every footer count is a number with nowhere to go.

    Takes the same window arguments as `/data` and resolves them the same way,
    so the list answers the question the footer counted.
    """
    if bucket not in _FOOTER_BUCKETS:
        raise _unprocessable(
            f"unknown footer bucket '{bucket}'; use one of {sorted(_FOOTER_BUCKETS)}"
        )
    chart = await _load_chart(session, chart_id)
    query = await _resolve_query(
        session, chart, grain=None, split=None, currency=currency, since=since, until=until
    )
    rows = await chart_footer_documents(
        session,
        query.rule,
        bucket=bucket,
        amount_kind=amount_kind,
        currency=query.currency,
        since=query.since,
        until=query.until,
        facets_in_rule=query.facets_in_rule,
    )
    page = rows[offset : offset + limit]
    titles = dict(
        (
            await session.execute(
                select(Document.id, Document.title).where(
                    Document.id.in_([row.document_id for row in page])
                )
            )
        ).all()
    )
    return FooterDocumentsOut(
        bucket=bucket,
        documents=[
            FooterDocumentOut(
                id=row.document_id,
                title=titles.get(row.document_id),
                date=row.date,
                amount=_money(row.amount),
                currency=row.currency,
                amount_kind=row.amount_kind,
            )
            for row in page
        ],
    )
```

Note `grain=None, split=None` on `_resolve_query`: the footer takes no grain and
no split axis, and passing a chart's default split would be inert at best.
Import `chart_footer_documents` from `library.charts.footer`.

`unconvertible` is deliberately **not** in `_FOOTER_BUCKETS`: it is not a
`_CLASSIFY_SQL` bucket at all but a merge of two separately-reported lists
(docs/charts.md §5), so listing its documents needs `Unconvertible` to carry
document ids — an engine change, out of scope. Record it as a known limit in
Task 8 rather than approximating it.

- [ ] **Step 6: Run the tests and watch them pass**

```bash
uv run pytest tests/test_chart_footer.py tests/test_api_spending.py -q
```

Expected: PASS, including every pre-existing footer test — that is the check
that the extraction in Step 3 preserved `chart_footer`'s behaviour exactly.

- [ ] **Step 7: Mutation-check the deduplication and the shared fetch**

Three mutations, each restored:

1. Return `[FooterDocument(...) for row in rows if row.bucket == bucket]` with
   no merging, and confirm both
   `test_a_split_document_appears_once_with_its_rows_summed` and
   `test_the_list_length_equals_the_footer_s_count` go **red**.
2. In `_classified_rows`, change one `CASE` branch's bucket name in
   `_CLASSIFY_SQL` and confirm the aggregate tests **and** the list tests both
   move. If only one moves, the extraction did not actually share.
3. Delete the `amount_kind` filter and confirm the `excluded` parametrisation
   goes red once a second excluded kind is present. If the fixture has only one
   excluded kind, add a second — a filter with nothing to filter is untested.

- [ ] **Step 8: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add -A && git commit -m "feat(charts): drill through to the documents behind a footer count"
```

---

## Task 7: `GET /api/facets/counts`

**Files:**
- Modify: `src/library/api/facets.py` (new route, beside `list_facets`)
- Test: `tests/test_api_facets.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `GET /api/facets/counts` → `{"counts": [{"facet_key": str, "value_key": str, "documents": int, "first_date": date | None, "last_date": date | None}]}`, ordered by `documents` descending.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api_facets.py`:

```python
@pytest.mark.asyncio
async def test_counts_are_ordered_by_document_count(client, session, document, facets) -> None:
    """The empty state proposes questions worth asking, so the busiest values
    come first (§10.4)."""
    for _ in range(2):
        await document(
            amount_total=Decimal("10.00"),
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=date(2026, 1, 5),
            labels={"category": "software"},
        )
    await document(
        amount_total=Decimal("30.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 2, 2),
        labels={"category": "services"},
    )
    await session.commit()

    counts = (await client.get("/api/facets/counts")).json()["counts"]

    category = [c for c in counts if c["facet_key"] == "category"]
    assert [(c["value_key"], c["documents"]) for c in category] == [
        ("software", 2),
        ("services", 1),
    ]


@pytest.mark.asyncio
async def test_counts_carry_the_date_span(client, session, document, facets) -> None:
    """"15 documents in `software` over 3 months" needs both ends."""
    for day in (date(2026, 1, 5), date(2026, 3, 9)):
        await document(
            amount_total=Decimal("10.00"),
            amount_kind=AmountKind.PAYMENT_MADE,
            document_date=day,
            labels={"category": "software"},
        )
    await session.commit()

    counts = (await client.get("/api/facets/counts")).json()["counts"]

    software = next(c for c in counts if c["value_key"] == "software")
    assert software["first_date"] == "2026-01-05"
    assert software["last_date"] == "2026-03-09"


@pytest.mark.asyncio
async def test_a_value_with_no_money_behind_it_is_absent(
    client, session, document, facets
) -> None:
    """Reading `spend_facts` rather than `document_labels` does this for free:
    the view requires `amount_total IS NOT NULL` and its join to `payments`
    excludes soft-deleted documents. Proposing a chart of a value the archive
    has no amounts for is exactly the noise §10.4 replaces."""
    await document(document_date=date(2026, 2, 2), labels={"category": "supplies"})
    await document(
        amount_total=Decimal("99.00"),
        amount_kind=AmountKind.PAYMENT_MADE,
        document_date=date(2026, 2, 2),
        labels={"category": "accountancy"},
        deleted=True,
    )
    await session.commit()

    counts = (await client.get("/api/facets/counts")).json()["counts"]

    assert {c["value_key"] for c in counts} == set()
```

- [ ] **Step 2: Run and watch it fail**

```bash
uv run pytest tests/test_api_facets.py -q -k counts
```

Expected: FAIL — 404, or 422 if `counts` is captured by `{facet_key}`.

- [ ] **Step 3: Add the route**

In `src/library/api/facets.py`. **Declare it before any `/facets/{facet_key}`
route** so the literal path is not swallowed by the parameter — the same trap
`/ask/new` before `/ask/:threadId` solves in the router.

```python
#: Counted over `spend_facts`, not `document_labels`, and that choice does the
#: filtering for free: the view requires `amount_total IS NOT NULL` and its
#: join to `payments` excludes soft-deleted documents, so neither an amountless
#: nor a deleted document can put a moneyless proposal in front of the owner.
#: `is_canonical` is the one filter that is not free and so is explicit: a
#: merged twin is a second row for money already counted once.
_FACET_COUNTS_SQL = """
SELECT lbl.key AS facet_key,
       lbl.value AS value_key,
       count(DISTINCT sf.document_id) AS documents,
       min(sf.date) AS first_date,
       max(sf.date) AS last_date
FROM spend_facts sf
CROSS JOIN LATERAL jsonb_each_text(sf.labels) AS lbl(key, value)
WHERE sf.is_canonical
GROUP BY lbl.key, lbl.value
ORDER BY documents DESC, lbl.key, lbl.value
"""


class FacetValueCount(BaseModel):
    facet_key: str
    value_key: str
    documents: int
    first_date: date | None
    last_date: date | None


class FacetCountsOut(BaseModel):
    counts: list[FacetValueCount]


@router.get("/facets/counts", summary="Document counts per facet value")
async def facet_counts(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FacetCountsOut:
    """What the empty state proposes charts from (§10.4).

    A separate route rather than counts on `GET /api/facets`, so
    `DocumentFilterBar` — which loads the vocabulary on every document list
    render — does not start paying for an aggregate it never reads.
    """
    rows = (await session.execute(text(_FACET_COUNTS_SQL))).mappings().all()
    return FacetCountsOut(counts=[FacetValueCount(**dict(row)) for row in rows])
```

Add `from sqlalchemy import text` and `from datetime import date` if absent.

- [ ] **Step 4: Run and watch it pass**

```bash
uv run pytest tests/test_api_facets.py -q
```

Expected: PASS, including every pre-existing facets test — that is the check
that `/facets/counts` did not shadow `/facets/{facet_key}` or vice versa.

- [ ] **Step 5: Mutation-check `is_canonical`**

Delete `WHERE sf.is_canonical` and confirm a test goes red. If none does, that
is a gap: add one with two documents from the same sender, same amount, same
day, which the payment rules merge — the count must be 1, not 2. Restore the
filter.

- [ ] **Step 6: Commit**

```bash
uv run ruff format . && uv run ruff check . && uv run mypy
git add -A && git commit -m "feat(charts): document counts per facet value"
```

---

## Task 8: documentation, journal, and the PR

**Files:**
- Modify: `docs/charts.md` (§11's route table, §13's known limits)
- Modify: `docs/api.md` (the spending and facets sections)
- Modify: `docs/facets.md` (colour on a value)
- Create: `journal/260830-spending-view-backend.md`
- Modify: `docs/superpowers/plans/2026-08-30-spending-view-backend.md` (tick the boxes)

- [ ] **Step 1: Update `docs/charts.md`**

Add the three new spending routes to §11's table:

| method | path | notes |
| --- | --- | --- |
| `GET` | `/api/spending/{id}` | one saved question |
| `GET` | `/api/spending/{id}/footer/{bucket}` | the documents behind a footer count; `limit` ≤ 100 |

Add to §11 the `splits` field on `DataOut` and the `label`/`colour` on
`CellOutBody`, and **remove** "A sender split emits ids, not names" from §13's
known limits — it is fixed. Add in its place:

> - **`unconvertible` has no drill-through.** Every other footer bucket lists
>   its documents; `unconvertible` is not a `_CLASSIFY_SQL` bucket but a merge
>   of two separately-reported lists (§5), so listing it needs `Unconvertible`
>   to carry document ids and merge as a union — the same engine change the
>   upper-bound `documents` count already wants.

- [ ] **Step 2: Update `docs/api.md` and `docs/facets.md`**

`docs/api.md`: the three new spending routes, `PATCH /api/senders/{id}`,
`GET /api/facets/counts`, and the `colour` field on `GET /api/facets` and
`GET /api/senders`. `docs/facets.md`: a value now carries an optional `colour`,
null meaning a derived palette slot, set through the value patch route.

Both files carry the repository's stamp convention. Update **Last updated** and
**Last verified** with today's date and a method line naming the modules you
actually read and the tests that cover the claims. Do not write "verified" for
anything you did not read.

- [ ] **Step 3: Write the journal entry**

Create `journal/260830-spending-view-backend.md`. H1 is a clean title with no
number or date (`# The spending view's backend`). Cover: the two prototypes and
what executing them changed (the split-document row count, and that reading
`spend_facts` filters moneyless proposals for free); the decision to reuse
`_CLASSIFY_SQL` rather than write a second query, and what the mutation check
proved; anything that surprised you during implementation.

**No real sender names, amounts or addresses.**

- [ ] **Step 4: Regenerate the journal index**

```bash
uv run python scripts/build_journal_index.py
```

- [ ] **Step 5: Run everything**

```bash
uv run pytest -q
uv run ruff format --check . && uv run ruff check .
uv run mypy
uv run python scripts/check_docs.py --max-violations 0
```

All four must pass before the PR. Report actual output; do not summarise a run
you did not watch finish.

- [ ] **Step 6: Grep for leaked real values**

```bash
git diff main --stat
git diff main | grep -nEi '[0-9]{2,}\.[0-9]{2}|@|[A-Z]{2}[0-9]{2} ?[A-Z]{3}' | head -40
```

Read every hit. Any real sender name, amount, address or registration is a
blocker — GitGuardian does not catch this class.

- [ ] **Step 7: Open the PR**

```bash
git push -u origin <branch>
gh pr create --title "feat(charts): the spending view's backend surface" --body "..."
```

The body states what shipped, links the spec and this plan, and names the two
prototypes and their results.

- [ ] **Step 8: Wait for CI, then merge and deploy**

`backend` takes ~16–18 minutes and is not hanging. Before `make deploy`, confirm
the **`promote`** job actually succeeded — `gh run watch` can exit 0 while the
run is still in progress. Try the plain `gh pr merge --squash` first; the
unattributed-changes rule has blocked agent PRs before but did not fire on #99
or #121.

After merging, expect `docs-stamps` to go red on `main` if the squash-merge
crossed midnight UTC (issue #126); the fix is a follow-up PR that re-verifies
and re-stamps, not a force-push.

---

## Self-Review

**Spec coverage.** §3.1 → Task 4. §3.2 → Task 5. §3.3 → Task 6. §3.4 → Task 7.
§3.5 → Tasks 1–3. §3.6's testing posture is folded into every task's mutation
step and the Global Constraints. §2.5's fallback is a 4b concern (the renderer
derives the slot); 4a's job is only to make `colour` nullable and readable,
which Tasks 1–3 do.

**Placeholders.** None. Three steps deliberately say "read the existing code
and reuse it rather than writing a second copy" — Task 3's document count, Task
6's sign handling, Task 6's `_resolve` helper name. Those are instructions to
avoid a duplicate, not gaps: each names the exact function to read and what
would go wrong if a second copy were written.

**Type consistency.** `chart_footer_documents` returns `list[FooterDocument]`
in Task 6 and is consumed as `row.document_id` / `row.amount` by the route in
the same task. `_resolve_splits` returns `list[SplitValueOut]` in Task 5 and is
consumed by `_data_out` and `chart_cell_data` in the same task.
`VocabularyValue.colour` is added in Task 2 and read by Task 3's `get_value` and
Task 5's `_colour_of`. `SplitValueOut` is the one name used throughout;
`SplitOut` and `SplitLabelOut` appear nowhere.
