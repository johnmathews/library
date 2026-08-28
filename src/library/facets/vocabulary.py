"""Reads and writes over the facet vocabulary. No LLM, no network."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import DocumentLabel, Facet, FacetValue, FacetValueAlias


class UnknownFacetError(ValueError):
    """Raised when a facet key is not in the vocabulary."""


class UnknownValueError(ValueError):
    """Raised when a value key is not in the named facet.

    This is the closed-set guarantee's enforcement point: callers cannot create
    a value by naming one, only by going through ``create_value``.
    """


@dataclass(frozen=True, slots=True)
class VocabularyValue:
    id: int
    key: str
    label: str
    parent_id: int | None
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VocabularyFacet:
    id: int
    key: str
    label: str
    ordinal: int
    values: tuple[VocabularyValue, ...]

    def value(self, key: str) -> VocabularyValue | None:
        return next((v for v in self.values if v.key == key), None)


async def load_vocabulary(session: AsyncSession) -> tuple[VocabularyFacet, ...]:
    """The whole vocabulary, ordered by facet then value ordinal then key.

    Loaded whole rather than per-facet: it is a few dozen rows, it is needed in
    full by both the labelling prompt and the API, and one query beats N.
    """
    facet_rows = (await session.execute(select(Facet).order_by(Facet.ordinal, Facet.key))).scalars()
    value_rows = (
        await session.execute(select(FacetValue).order_by(FacetValue.ordinal, FacetValue.key))
    ).scalars()
    alias_rows = (await session.execute(select(FacetValueAlias))).scalars()

    aliases: dict[int, list[str]] = {}
    for row in alias_rows:
        aliases.setdefault(row.facet_value_id, []).append(row.alias)

    by_facet: dict[int, list[VocabularyValue]] = {}
    for row in value_rows:
        by_facet.setdefault(row.facet_id, []).append(
            VocabularyValue(
                id=row.id,
                key=row.key,
                label=row.label,
                parent_id=row.parent_id,
                aliases=tuple(sorted(aliases.get(row.id, ()))),
            )
        )
    return tuple(
        VocabularyFacet(
            id=facet.id,
            key=facet.key,
            label=facet.label,
            ordinal=facet.ordinal,
            values=tuple(by_facet.get(facet.id, ())),
        )
        for facet in facet_rows
    )


async def _resolve(
    session: AsyncSession, facet_key: str, value_key: str | None
) -> tuple[int, int | None]:
    """``(facet_id, value_id)``; ``value_id`` is None when ``value_key`` is None."""
    facet_id = (
        await session.execute(select(Facet.id).where(Facet.key == facet_key))
    ).scalar_one_or_none()
    if facet_id is None:
        raise UnknownFacetError(facet_key)
    if value_key is None:
        return facet_id, None
    value_id = (
        await session.execute(
            select(FacetValue.id).where(
                FacetValue.facet_id == facet_id, FacetValue.key == value_key
            )
        )
    ).scalar_one_or_none()
    if value_id is None:
        raise UnknownValueError(f"{facet_key}={value_key}")
    return facet_id, value_id


async def document_labels(session: AsyncSession, document_id: int) -> dict[str, str]:
    """``{facet_key: value_key}`` for one document. Absent facets are absent keys."""
    rows = await session.execute(
        select(Facet.key, FacetValue.key)
        .join(DocumentLabel, DocumentLabel.facet_id == Facet.id)
        .join(FacetValue, FacetValue.id == DocumentLabel.facet_value_id)
        .where(DocumentLabel.document_id == document_id)
    )
    return dict(rows.all())


async def set_document_label(
    session: AsyncSession, document_id: int, facet_key: str, value_key: str | None
) -> None:
    """Set (or, with ``value_key=None``, clear) one document's value for one facet.

    An upsert rather than delete-then-insert: the composite primary key means a
    second value for the same facet is a conflict, not a second row, so the
    at-most-one rule is upheld by the database whichever path callers take.
    """
    facet_id, value_id = await _resolve(session, facet_key, value_key)
    if value_id is None:
        await session.execute(
            delete(DocumentLabel).where(
                DocumentLabel.document_id == document_id, DocumentLabel.facet_id == facet_id
            )
        )
        return
    statement = (
        pg_insert(DocumentLabel)
        .values(document_id=document_id, facet_id=facet_id, facet_value_id=value_id)
        .on_conflict_do_update(
            index_elements=["document_id", "facet_id"], set_={"facet_value_id": value_id}
        )
    )
    await session.execute(statement)
