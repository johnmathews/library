from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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
    # Force a real read from Postgres rather than the identity map: without
    # this, `select(Chart)` returns the exact Python object just constructed
    # above, and `stored.rule` is the identical dict rather than anything
    # deserialised from JSONB.
    session.expire_all()
    stored = (await session.execute(select(Chart))).scalar_one()
    assert Rule.model_validate(stored.rule) == rule


@pytest.mark.asyncio
async def test_a_chart_needs_no_split_axis(session) -> None:
    """`default_split` is nullable: a chart with no split is one series, and
    that is the shape of the seeded "All spending" card before the owner
    picks an axis."""
    session.add(
        Chart(
            name="chart-b",
            question_text="everything",
            rule={},
            default_grain=Grain.MONTH,
            display_currency="EUR",
        )
    )
    await session.flush()
    stored = (await session.execute(select(Chart))).scalar_one()
    assert stored.default_split is None


@pytest.mark.asyncio
async def test_two_charts_may_not_share_a_name(session) -> None:
    session.add(
        Chart(
            name="dup",
            question_text="a",
            rule={},
            default_grain=Grain.MONTH,
            display_currency="EUR",
        )
    )
    await session.flush()
    session.add(
        Chart(
            name="dup",
            question_text="b",
            rule={},
            default_grain=Grain.MONTH,
            display_currency="EUR",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()
