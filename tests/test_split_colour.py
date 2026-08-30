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


@pytest.mark.asyncio
async def test_the_vocabulary_carries_each_value_s_colour(session: AsyncSession, facets) -> None:
    """`load_vocabulary` is what the spending router already loads to validate a
    rule, so reading colour from it is what makes split resolution free."""
    from library.facets.vocabulary import load_vocabulary

    await session.execute(text("UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'software'"))
    await session.commit()
    vocabulary = await load_vocabulary(session)
    category = next(f for f in vocabulary if f.key == "category")
    assert category.value("software").colour == "#1f77b4"
    assert category.value("services").colour is None
