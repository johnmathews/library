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

### The two test idioms, and which one a test uses

This repository has **two** shapes of test and they do not mix. Using the wrong
one is the most likely way to waste a round on this plan.

**Engine-level** (`tests/test_chart_footer.py`, `tests/test_chart_query.py`,
`tests/test_split_colour.py`): `async def`, the `session` fixture, and the
`document` / `facets` fixtures from `tests/conftest.py`. `asyncio_mode = "auto"`,
so no `@pytest.mark.asyncio` is needed. `document(...)` takes
`amount_total`, `amount_kind`, `document_date`, `currency`, `labels`, `deleted`,
`sender`, `title`. `facets` creates `FIXTURE_VOCABULARY`.

**API-level** (`tests/test_api_spending.py`, `tests/test_api_facets.py`):
**synchronous** `def`, the `api_client: TestClient` fixture, and seeding through
a **separate engine** — `api_client` drives the app on its own loop, so the test
cannot share the `session` fixture's. Never write `await api_client.get(...)`;
`TestClient` is synchronous.

The helpers already in those files, to be reused rather than rewritten:

```python
# tests/test_api_spending.py
def _run[T](database_url: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T
def _seed_vocabulary(database_url, facet="category", values=("software", "services")) -> None
def _seed_document(database_url, *, amount: str | None = None, kind: AmountKind | None = None,
                   day: date | None = MARCH, currency: str | None = "EUR",
                   labels: Mapping[str, str] | None = None) -> int
def _save_chart(api_client, name, rule, *, default_split=None) -> int
SOFTWARE_RULE: dict[str, object]
MARCH = date(2026, 3, 1)

# tests/test_api_facets.py
def _make_facet(api_client) -> str        # a fresh, uniquely-keyed facet
def _run[T](api_database_url, op) -> T
```

Two consequences:

- **Chart and facet names must be unique per test.** Both suites share one
  database and list endpoints default to 25 rows, so a list assertion is scoped
  by a name the test invented, never by a count. Follow the existing `api-...`
  prefix convention in `test_api_spending.py`.
- **`_seed_document` has no `sender=` parameter yet.** Task 5 needs one; add it
  to the existing helper (resolving or creating a `Sender` by name inside
  `work`) rather than writing a second seeding function.

Where a task below shows a test written against `session`/`document` but names
an HTTP route, it is an **API-level** test: seed with these helpers and call
`api_client` synchronously. The assertions are the requirement; the seeding
idiom is the one above.

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


These two are **API-level** — synchronous, `api_client`, uniquely-keyed
fixtures. Put them in `tests/test_api_facets.py`, not in
`tests/test_split_colour.py`:

```python
def test_get_facets_returns_colour(api_client: TestClient, api_database_url: str) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})

    async def paint(session: AsyncSession) -> None:
        await session.execute(
            text(
                "UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'alpha' "
                "AND facet_id = (SELECT id FROM facets WHERE key = :facet)"
            ),
            {"facet": key},
        )

    _run(api_database_url, paint)

    facet = next(f for f in api_client.get("/api/facets").json()["facets"] if f["key"] == key)
    colours = {v["key"]: v["colour"] for v in facet["values"]}
    assert colours == {"alpha": "#1f77b4", "beta": None}


def test_get_senders_returns_colour(api_client: TestClient, api_database_url: str) -> None:
    name = f"Corvus Test Supply {uuid.uuid4()}"

    async def seed(session: AsyncSession) -> None:
        session.add(Sender(name=name, colour="#d62728"))

    _run(api_database_url, seed)

    rows = api_client.get("/api/senders").json()
    assert [r["colour"] for r in rows if r["name"] == name] == ["#d62728"]
```

`_make_facet` and `_run` already exist in `tests/test_api_facets.py`; add
`Sender` and `text` to its imports. Check the exact path and body shape of the
create-value route (`@router.post("/facets/{facet_key}/values"...)` at ~line 147
of `src/library/api/facets.py`) and match it — do not guess.

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


def _colour(api_client: TestClient, facet_key: str, value_key: str) -> str | None:
    facet = next(
        f for f in api_client.get("/api/facets").json()["facets"] if f["key"] == facet_key
    )
    return next(v["colour"] for v in facet["values"] if v["key"] == value_key)


def _labelled(api_client: TestClient, facet_key: str, value_key: str) -> str:
    facet = next(
        f for f in api_client.get("/api/facets").json()["facets"] if f["key"] == facet_key
    )
    return next(v["label"] for v in facet["values"] if v["key"] == value_key)


def test_a_value_s_colour_can_be_set_without_renaming_it(api_client: TestClient) -> None:
    """Setting a colour must not force a rename. `label` is optional on the
    patch for the same reason `colour` is: an absent field is left alone."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})

    response = api_client.patch(
        f"/api/facets/{key}/values/alpha", json={"colour": "#1f77b4"}
    )

    assert response.status_code == 200, response.text
    assert _colour(api_client, key, "alpha") == "#1f77b4"
    assert _labelled(api_client, key, "alpha") == "Alpha"


def test_an_explicit_null_clears_a_colour_and_an_absent_field_does_not(
    api_client: TestClient,
) -> None:
    """The `model_fields_set` distinction, which is the whole reason this is not
    one nullable field: "clear it" and "do not touch it" are different requests
    that both look like None."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.patch(f"/api/facets/{key}/values/alpha", json={"colour": "#1f77b4"})

    api_client.patch(f"/api/facets/{key}/values/alpha", json={"label": "Alpha renamed"})
    assert _colour(api_client, key, "alpha") == "#1f77b4", "an absent colour is left alone"

    api_client.patch(f"/api/facets/{key}/values/alpha", json={"colour": None})
    assert _colour(api_client, key, "alpha") is None, "an explicit null clears it"


@pytest.mark.parametrize("colour", COLOUR_PATTERN_REJECTS)
def test_a_malformed_colour_is_a_422_not_a_500(api_client: TestClient, colour: str) -> None:
    """Refused by the request model, so the database CHECK is defence in depth
    rather than the error path the owner sees."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})

    response = api_client.patch(f"/api/facets/{key}/values/alpha", json={"colour": colour})

    assert response.status_code == 422


def test_patching_an_unknown_value_is_still_a_404(api_client: TestClient) -> None:
    """The behaviour the route had before it became a patch, preserved."""
    key = _make_facet(api_client)
    response = api_client.patch(f"/api/facets/{key}/values/absent", json={"label": "X"})
    assert response.status_code == 404


def _seed_sender(api_database_url: str, name: str) -> int:
    async def work(session: AsyncSession) -> int:
        sender = Sender(name=name)
        session.add(sender)
        await session.flush()
        return sender.id

    return _run(api_database_url, work)


def test_a_sender_s_colour_can_be_set_and_cleared(
    api_client: TestClient, api_database_url: str
) -> None:
    sender_id = _seed_sender(api_database_url, f"Corvus Test Supply {uuid.uuid4()}")

    set_response = api_client.patch(f"/api/senders/{sender_id}", json={"colour": "#d62728"})
    assert set_response.status_code == 200, set_response.text
    assert set_response.json()["colour"] == "#d62728"

    clear_response = api_client.patch(f"/api/senders/{sender_id}", json={"colour": None})
    assert clear_response.json()["colour"] is None


def test_patching_an_unknown_sender_is_a_404(api_client: TestClient) -> None:
    assert api_client.patch("/api/senders/999999", json={"colour": "#d62728"}).status_code == 404


@pytest.mark.parametrize("colour", COLOUR_PATTERN_REJECTS)
def test_a_malformed_sender_colour_is_a_422(
    api_client: TestClient, api_database_url: str, colour: str
) -> None:
    sender_id = _seed_sender(api_database_url, f"Corvus Test Supply {uuid.uuid4()}")
    assert api_client.patch(f"/api/senders/{sender_id}", json={"colour": colour}).status_code == 422
```

All API-level: synchronous, `api_client`, `_make_facet` for a uniquely-keyed
facet. They live in `tests/test_api_facets.py`.

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
def test_one_chart_can_be_read_by_id(api_client: TestClient) -> None:
    """The workspace loads one chart. Without this it has to page the list
    looking for a row, which breaks the moment there are more than `limit`."""
    chart_id = _save_chart(api_client, "api-read-by-id", SOFTWARE_RULE, default_split="cost_type")

    response = api_client.get(f"/api/spending/{chart_id}")

    assert response.status_code == 200, response.text
    listed = api_client.get("/api/spending?limit=100").json()["charts"]
    assert response.json() == next(c for c in listed if c["id"] == chart_id)


def test_reading_an_unknown_chart_is_a_404(api_client: TestClient) -> None:
    response = api_client.get("/api/spending/999999")
    assert response.status_code == 404
    assert "999999" in response.json()["detail"]
```

`_save_chart` validates the split against the vocabulary, so seed it first if
`cost_type` is not present — read `_seed_vocabulary`'s default values and pass a
split that exists, or call `_save_chart` with `default_split=None`.

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


def _seed_sender_document(
    database_url: str, *, sender: str, amount: str, day: date = MARCH
) -> tuple[int, int]:
    """A document from a named sender; returns `(document_id, sender_id)`.

    Extends `_seed_document`'s job rather than replacing it: add a `sender=`
    parameter to that helper and call it from here, so there is one definition
    of "seed a document for the spending API".
    """


def test_a_facet_split_resolves_value_keys_to_display_labels(
    api_client: TestClient, api_database_url: str
) -> None:
    """`spend_facts.labels` maps a facet key to a value *key*, so an unresolved
    legend reads `software`. §2.3: the legend carries names."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )
    chart_id = _save_chart(api_client, "api-splits-facet", {}, default_split="category")

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    assert {s["value"]: s["label"] for s in body["splits"]} == {"software": "Software"}


def test_a_sender_split_resolves_ids_to_names(
    api_client: TestClient, api_database_url: str
) -> None:
    """The engine emits `CAST(sf.sender_id AS text)`, so without resolution the
    legend reads `41`. The id stays as `value` because `/cell` round-trips it."""
    name = f"{VENDOR_A} {uuid.uuid4()}"
    _seed_sender_document(api_database_url, sender=name, amount="10.00")
    chart_id = _save_chart(api_client, "api-splits-sender", {}, default_split="sender")

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    named = [s for s in body["splits"] if s["label"] == name]
    assert len(named) == 1
    assert named[0]["value"].isdigit(), "value stays the id /cell must be sent back"


def test_the_unlabelled_bucket_is_named_by_the_axis(
    api_client: TestClient, api_database_url: str
) -> None:
    """`split_value` is null both for "no value for this facet" and for "no
    sender". A client cannot invent either name; the API supplies it."""
    _seed_vocabulary(api_database_url)
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    facet_chart = _save_chart(api_client, "api-splits-null-facet", {}, default_split="category")
    sender_chart = _save_chart(api_client, "api-splits-null-sender", {}, default_split="sender")

    by_facet = api_client.get(f"/api/spending/{facet_chart}/data").json()
    by_sender = api_client.get(f"/api/spending/{sender_chart}/data").json()

    assert (None, "Uncategorised") in [(s["value"], s["label"]) for s in by_facet["splits"]]
    assert (None, "No sender") in [(s["value"], s["label"]) for s in by_sender["splits"]]


def test_a_split_value_carries_its_stored_colour_and_null_when_unset(
    api_client: TestClient, api_database_url: str
) -> None:
    _seed_vocabulary(api_database_url)
    for value in ("software", "services"):
        _seed_document(
            api_database_url,
            amount="10.00",
            kind=AmountKind.PAYMENT_MADE,
            labels={"category": value},
        )

    async def paint(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'")
        )

    _run(api_database_url, paint)
    chart_id = _save_chart(api_client, "api-splits-colour", {}, default_split="category")

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    colours = {s["value"]: s["colour"] for s in body["splits"]}
    assert colours["software"] == "#1f77b4"
    assert colours["services"] is None


def test_a_split_value_whose_row_vanished_falls_back_to_the_raw_value(
    api_client: TestClient, api_database_url: str
) -> None:
    """Facet values are deletable at runtime and a saved chart can rot. A
    rotted legend entry is a legible defect; a 500 on every chart in range is
    not — the same failure the `sorted()` over a null currency caused."""
    name = f"{VENDOR_A} {uuid.uuid4()}"
    document_id, _sender_id = _seed_sender_document(
        api_database_url, sender=name, amount="10.00"
    )

    async def orphan(session: AsyncSession) -> None:
        # Break the reference the way runtime deletion would, without going
        # through a route that would refuse it.
        await session.execute(
            text("UPDATE documents SET sender_id = 999999 WHERE id = :id"),
            {"id": document_id},
        )

    _run(api_database_url, orphan)
    chart_id = _save_chart(api_client, "api-splits-orphan", {}, default_split="sender")

    response = api_client.get(f"/api/spending/{chart_id}/data")

    assert response.status_code == 200, response.text
    assert "999999" in [s["label"] for s in response.json()["splits"]]


def test_a_cell_carries_its_own_label_and_colour(
    api_client: TestClient, api_database_url: str
) -> None:
    """So a drilled panel can title itself without re-reading /data."""
    _seed_vocabulary(api_database_url)
    _seed_document(
        api_database_url,
        amount="10.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={"category": "software"},
    )

    async def paint(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'")
        )

    _run(api_database_url, paint)
    chart_id = _save_chart(api_client, "api-cell-label", SOFTWARE_RULE, default_split="category")
    data = api_client.get(f"/api/spending/{chart_id}/data").json()
    cell = next(c for c in data["cells"] if c["split_value"] == "software")

    body = api_client.get(
        f"/api/spending/{chart_id}/cell",
        params={
            "period": cell["period"],
            "split_value": cell["split_value"],
            "grain": data["grain"],
            "split": data["split"],
            "currency": data["currency"],
        },
    ).json()

    assert body["label"] == "Software"
    assert body["colour"] == "#1f77b4"


def test_an_unsplit_chart_has_no_split_values(
    api_client: TestClient, api_database_url: str
) -> None:
    """`split_value` is null for an unsplit chart too, and that is not a bucket
    needing a name — it is the absence of an axis."""
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE)
    chart_id = _save_chart(api_client, "api-splits-none", {})

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    assert body["split"] is None
    assert body["splits"] == []


def test_the_legend_order_matches_the_cells(
    api_client: TestClient, api_database_url: str
) -> None:
    """De-duplication preserves the engine's ordering. A `set()` would not, and
    a legend ordered differently from the chart is a legend that mislabels it."""
    _seed_vocabulary(api_database_url)
    for value in ("software", "services"):
        _seed_document(
            api_database_url,
            amount="10.00",
            kind=AmountKind.PAYMENT_MADE,
            labels={"category": value},
        )
    chart_id = _save_chart(api_client, "api-splits-order", {}, default_split="category")

    body = api_client.get(f"/api/spending/{chart_id}/data").json()

    first_seen: list[str | None] = []
    for cell in body["cells"]:
        if cell["split_value"] not in first_seen:
            first_seen.append(cell["split_value"])
    assert [s["value"] for s in body["splits"]] == first_seen
```

All API-level: synchronous, `api_client`, the existing `_seed_*` / `_save_chart`
helpers. `_save_chart(api_client, name, {})` saves an empty rule — the "all
spending" shape, which is what a split test wants so the split axis is the only
variable.

**`_seed_sender_document`'s body is deliberately left for you to write**, and
the way to write it is to add a `sender: str | None = None` parameter to the
existing `_seed_document` and have it resolve-or-create a `Sender` by name
inside its `work` coroutine, returning both ids. Writing a second seeding
function beside `_seed_document` is what this codebase deletes rather than
tests — there must be one definition of "seed a document for the spending API".

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
def test_the_footer_route_lists_the_documents_behind_a_count(
    api_client: TestClient, api_database_url: str
) -> None:
    _seed_vocabulary(api_database_url)
    document_id = _seed_document(
        api_database_url, amount="89.20", kind=AmountKind.PAYMENT_MADE
    )
    chart_id = _save_chart(api_client, "api-footer-drill", SOFTWARE_RULE)
    data = api_client.get(f"/api/spending/{chart_id}/data").json()
    counted = data["footer"]["uncategorised"]["documents"]

    body = api_client.get(
        f"/api/spending/{chart_id}/footer/uncategorised",
        params={"currency": data["currency"]},
    ).json()

    assert len(body["documents"]) == counted
    listed = {d["id"]: d for d in body["documents"]}
    assert document_id in listed
    assert listed[document_id]["amount"] == "89.20"


def test_an_unknown_footer_bucket_is_a_422_naming_it(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-footer-bucket-422", {})
    response = api_client.get(f"/api/spending/{chart_id}/footer/nonsense")
    assert response.status_code == 422
    assert "nonsense" in response.json()["detail"]


def test_the_footer_route_caps_its_limit_at_100(api_client: TestClient) -> None:
    chart_id = _save_chart(api_client, "api-footer-limit", {})
    response = api_client.get(
        f"/api/spending/{chart_id}/footer/uncategorised", params={"limit": 101}
    )
    assert response.status_code == 422


def test_the_footer_route_and_the_footer_count_agree_after_a_window_narrows(
    api_client: TestClient, api_database_url: str
) -> None:
    """The route takes /data's window arguments and must resolve them the same
    way, or the list answers a different question from the count above it."""
    _seed_vocabulary(api_database_url)
    _seed_document(api_database_url, amount="10.00", kind=AmountKind.PAYMENT_MADE, day=MARCH)
    _seed_document(
        api_database_url,
        amount="20.00",
        kind=AmountKind.PAYMENT_MADE,
        day=date(2026, 6, 1),
    )
    chart_id = _save_chart(api_client, "api-footer-window", SOFTWARE_RULE)
    window = {"from": "2026-03-01", "to": "2026-03-31"}

    data = api_client.get(f"/api/spending/{chart_id}/data", params=window).json()
    body = api_client.get(
        f"/api/spending/{chart_id}/footer/uncategorised",
        params={**window, "currency": data["currency"]},
    ).json()

    assert len(body["documents"]) == data["footer"]["uncategorised"]["documents"]
```

The first three are API-level in `tests/test_api_spending.py`. The engine-level
tests above them belong in `tests/test_chart_footer.py` and use `session` /
`document` / `replace_lines`, which that file already imports.

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
These are **API-level**, and they go in `tests/test_api_spending.py` rather than
`tests/test_api_facets.py` — that is where `_seed_document` and `_seed_vocabulary`
live, and where a document with an amount can be seeded at all.

```python
def test_counts_are_ordered_by_document_count(
    api_client: TestClient, api_database_url: str
) -> None:
    """The empty state proposes questions worth asking, so the busiest values
    come first (§10.4)."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("alpha", "beta"))
    for _ in range(2):
        _seed_document(
            api_database_url,
            amount="10.00",
            kind=AmountKind.PAYMENT_MADE,
            labels={facet: "alpha"},
        )
    _seed_document(
        api_database_url, amount="30.00", kind=AmountKind.PAYMENT_MADE, labels={facet: "beta"}
    )

    counts = api_client.get("/api/facets/counts").json()["counts"]

    mine = [c for c in counts if c["facet_key"] == facet]
    assert [(c["value_key"], c["documents"]) for c in mine] == [("alpha", 2), ("beta", 1)]


def test_counts_carry_the_date_span(api_client: TestClient, api_database_url: str) -> None:
    """"15 documents in `software` over 3 months" needs both ends."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("alpha",))
    for day in (date(2026, 1, 5), date(2026, 3, 9)):
        _seed_document(
            api_database_url,
            amount="10.00",
            kind=AmountKind.PAYMENT_MADE,
            day=day,
            labels={facet: "alpha"},
        )

    counts = api_client.get("/api/facets/counts").json()["counts"]

    alpha = next(c for c in counts if c["facet_key"] == facet)
    assert alpha["first_date"] == "2026-01-05"
    assert alpha["last_date"] == "2026-03-09"


def test_a_value_with_no_money_behind_it_is_absent(
    api_client: TestClient, api_database_url: str
) -> None:
    """Reading `spend_facts` rather than `document_labels` does this for free:
    the view requires `amount_total IS NOT NULL` and its join to `payments`
    excludes soft-deleted documents. Proposing a chart of a value the archive
    has no amounts for is exactly the noise §10.4 replaces."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("amountless", "deleted"))
    _seed_document(api_database_url, amount=None, labels={facet: "amountless"})
    deleted_id = _seed_document(
        api_database_url,
        amount="99.00",
        kind=AmountKind.PAYMENT_MADE,
        labels={facet: "deleted"},
    )

    async def soft_delete(session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE documents SET deleted_at = now() WHERE id = :id"), {"id": deleted_id}
        )

    _run(api_database_url, soft_delete)

    counts = api_client.get("/api/facets/counts").json()["counts"]

    assert [c for c in counts if c["facet_key"] == facet] == []


def test_a_merged_pair_counts_once(api_client: TestClient, api_database_url: str) -> None:
    """`is_canonical` is the one filter reading `spend_facts` does not give for
    free: a merged twin is a second row for money already counted once."""
    facet = f"counts-{uuid.uuid4().hex[:8]}"
    _seed_vocabulary(api_database_url, facet=facet, values=("alpha",))
    name = f"Corvus Test Assurance {uuid.uuid4()}"
    for _ in range(2):
        _seed_sender_document(
            api_database_url, sender=name, amount="10.00", labels={facet: "alpha"}
        )

    counts = api_client.get("/api/facets/counts").json()["counts"]

    alpha = next(c for c in counts if c["facet_key"] == facet)
    assert alpha["documents"] == 1, "two documents, one payment, one canonical row"
```

The last test depends on the payment rules actually merging the pair. Read
`docs/money-facts.md` §5 and confirm what the rules require — same sender, same
amount, same or near date, and a non-null `amount_kind` — and shape the fixture
so the merge genuinely happens. **Assert the merge before asserting the count**:
if the pair does not merge, `documents == 2` is correct and the test would be
green while testing nothing. Extend `_seed_sender_document` with a `labels=`
parameter (it forwards to `_seed_document`, which already takes one).

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
