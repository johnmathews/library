"""The vocabulary this feature ships with, and an idempotent seeder.

Derived from the shape of the existing free-form tags, not migrated from them:
the tags are the drift this vocabulary replaces, so they inform which
dimensions exist and never which value a document gets.

``vehicle``, ``property`` and ``person`` are declared as facets with **no
values**. Their values name real vehicles, addresses and people; this
repository is public, so they are created at runtime through
``vocabulary.create_value`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Facet, FacetValue, FacetValueAlias


@dataclass(frozen=True, slots=True)
class SeedValue:
    key: str
    label: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SeedFacet:
    key: str
    label: str
    ordinal: int
    values: tuple[SeedValue, ...]


SEED_VOCABULARY: tuple[SeedFacet, ...] = (
    SeedFacet(
        key="category",
        label="Category",
        ordinal=0,
        values=(
            SeedValue(
                "accountancy",
                "Accountancy",
                ("accounting", "bookkeeping", "fiscal services", "tax advice", "tax preparation"),
            ),
            SeedValue("tax", "Tax", ("tax assessment", "tax return", "vat", "corporation tax")),
            SeedValue(
                "vehicle-service",
                "Vehicle service",
                (
                    "auto repair",
                    "car service",
                    "vehicle maintenance",
                    "oil change",
                    "roadworthiness",
                ),
            ),
            SeedValue("ev-charging", "EV charging", ("charging", "electric vehicle charging")),
            SeedValue("insurance", "Insurance", ("premium", "policy", "cover")),
            SeedValue("healthcare", "Healthcare", ("medical", "dental", "dentist", "treatment")),
            SeedValue(
                "software", "Software", ("saas", "subscription software", "cloud services", "api")
            ),
            SeedValue("energy", "Energy", ("electricity", "gas", "utilities", "utility bill")),
            SeedValue(
                "housing", "Housing", ("property maintenance", "real estate", "installation")
            ),
            SeedValue("parking", "Parking", ("parking session",)),
            SeedValue("fines", "Fines", ("traffic fine", "penalty", "parking violation")),
            SeedValue("pension", "Pension", ("retirement", "portfolio")),
            SeedValue("banking", "Banking", ("bank charges", "money transfer")),
            SeedValue("travel", "Travel", ("accommodation", "booking", "camping")),
        ),
    ),
    SeedFacet(
        key="scope",
        label="Scope",
        ordinal=1,
        values=(
            SeedValue("business", "Business", ("company", "work")),
            SeedValue("personal", "Personal", ("private", "household", "family")),
        ),
    ),
    SeedFacet(
        key="cost_type",
        label="Cost type",
        ordinal=2,
        values=(
            SeedValue("subscription", "Subscription", ("recurring plan", "monthly plan")),
            SeedValue("usage", "Usage", ("metered", "credits", "pay as you go")),
            SeedValue("one-off", "One-off", ("one time", "single purchase")),
        ),
    ),
    SeedFacet(key="vehicle", label="Vehicle", ordinal=3, values=()),
    SeedFacet(key="property", label="Property", ordinal=4, values=()),
    SeedFacet(key="person", label="Person", ordinal=5, values=()),
)


async def seed_vocabulary(session: AsyncSession) -> int:
    """Create any missing seed facets, values and aliases. Returns values created.

    Additive and idempotent: it never updates or deletes, so a value the owner
    has renamed or a facet they have extended survives a re-seed untouched.
    """
    created = 0
    for seed_facet in SEED_VOCABULARY:
        facet_id = (
            await session.execute(select(Facet.id).where(Facet.key == seed_facet.key))
        ).scalar_one_or_none()
        if facet_id is None:
            facet = Facet(key=seed_facet.key, label=seed_facet.label, ordinal=seed_facet.ordinal)
            session.add(facet)
            await session.flush()
            facet_id = facet.id
        for ordinal, seed_value in enumerate(seed_facet.values):
            value_id = (
                await session.execute(
                    select(FacetValue.id).where(
                        FacetValue.facet_id == facet_id, FacetValue.key == seed_value.key
                    )
                )
            ).scalar_one_or_none()
            if value_id is not None:
                continue
            value = FacetValue(
                facet_id=facet_id, key=seed_value.key, label=seed_value.label, ordinal=ordinal
            )
            session.add(value)
            await session.flush()
            created += 1
            for alias in seed_value.aliases:
                session.add(FacetValueAlias(facet_value_id=value.id, alias=alias))
    return created
