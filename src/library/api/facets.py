"""Facet vocabulary and document-label endpoints.

The closed-set rule is enforced here as well as in the labeller: a PUT naming a
value that does not exist is a 422, never an implicit create. The only endpoint
that widens the vocabulary is ``accept_suggestion``, and it is guarded the same
way ``create_facet``/``create_value`` are: a duplicate key is a 409, not an
unhandled ``IntegrityError``. Authentication is enforced at include level in
app.py, like every other router.
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from library.db import get_session
from library.facets import vocabulary
from library.facets.vocabulary import (
    MergeIntoSelfError,
    UnknownFacetError,
    UnknownValueError,
    ValueInUseError,
)
from library.models import Document, Facet, FacetValueSuggestion

router: APIRouter = APIRouter(tags=["facets"])

KEY_PATTERN: str = r"^[a-z0-9_-]+$"
KEY_MAX_LENGTH: int = 64
Key = Annotated[
    str, StringConstraints(min_length=1, max_length=KEY_MAX_LENGTH, pattern=KEY_PATTERN)
]
Label = Annotated[str, StringConstraints(min_length=1, max_length=255)]

_DISALLOWED = re.compile(r"[^a-z0-9_-]+")
_REPEATED_SEPARATORS = re.compile(r"([_-])\1+")


def derive_value_key(label: str) -> str:
    """Turn a free-text suggested label into a key meeting the ``Key`` contract.

    ``accept_suggestion`` is the one route that widens the vocabulary, so key
    hygiene matters most here: a raw ``label.lower().replace(" ", "-")`` happily
    produces ``ev-charging-(home)!`` — which no other route would accept — and a
    label over 64 characters reaches Postgres as a ``DBAPIError`` (not an
    ``IntegrityError``), surfacing as a 500. Returns ``""`` when nothing usable
    is left, which the caller answers with a 422.
    """
    key = label.strip().lower().replace(" ", "-")
    key = _DISALLOWED.sub("", key)
    key = _REPEATED_SEPARATORS.sub(r"\1", key)
    return key.strip("-_")[:KEY_MAX_LENGTH].strip("-_")


class ValueOut(BaseModel):
    key: str
    label: str
    parent_id: int | None
    aliases: list[str]


class FacetOut(BaseModel):
    key: str
    label: str
    ordinal: int
    values: list[ValueOut]


class VocabularyOut(BaseModel):
    facets: list[FacetOut]


class FacetCreate(BaseModel):
    key: Key
    label: Label
    ordinal: int = 0


class ValueCreate(BaseModel):
    key: Key
    label: Label


class ValueRename(BaseModel):
    label: Label


class AliasCreate(BaseModel):
    alias: Annotated[str, StringConstraints(min_length=1, max_length=255)]


class MergeRequest(BaseModel):
    into: Key
    dry_run: bool = False


class LabelsBody(BaseModel):
    labels: dict[str, str | None] = Field(
        description="facet key -> value key; null clears that facet's label."
    )


@router.get("/facets", response_model=VocabularyOut, summary="The whole facet vocabulary")
async def list_facets(session: Annotated[AsyncSession, Depends(get_session)]) -> VocabularyOut:
    facets = await vocabulary.load_vocabulary(session)
    return VocabularyOut(
        facets=[
            FacetOut(
                key=facet.key,
                label=facet.label,
                ordinal=facet.ordinal,
                values=[
                    ValueOut(
                        key=value.key,
                        label=value.label,
                        parent_id=value.parent_id,
                        aliases=list(value.aliases),
                    )
                    for value in facet.values
                ],
            )
            for facet in facets
        ]
    )


@router.post("/facets", status_code=status.HTTP_201_CREATED, summary="Create a facet")
async def create_facet(
    body: FacetCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    try:
        await vocabulary.create_facet(session, body.key, body.label, body.ordinal)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"facet already exists: {body.key!r}",
        ) from exc
    return {"key": body.key}


@router.post(
    "/facets/{facet_key}/values",
    status_code=status.HTTP_201_CREATED,
    summary="Add a value to a facet",
)
async def create_value(
    facet_key: str, body: ValueCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    try:
        await vocabulary.create_value(session, facet_key, body.key, body.label)
        await session.commit()
    except UnknownFacetError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown facet") from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{facet_key}={body.key} already exists",
        ) from exc
    return {"key": body.key}


@router.patch("/facets/{facet_key}/values/{value_key}", summary="Rename a value")
async def rename_value(
    facet_key: str,
    value_key: str,
    body: ValueRename,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    try:
        await vocabulary.rename_value(session, facet_key, value_key, body.label)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown facet value"
        ) from exc
    await session.commit()
    return {"label": body.label}


@router.post("/facets/{facet_key}/values/{value_key}/aliases", summary="Add an alias")
async def add_alias(
    facet_key: str,
    value_key: str,
    body: AliasCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    try:
        await vocabulary.add_alias(session, facet_key, value_key, body.alias)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown facet value"
        ) from exc
    await session.commit()
    return {"alias": body.alias}


@router.post("/facets/{facet_key}/values/{value_key}/merge", summary="Fold one value into another")
async def merge_value(
    facet_key: str,
    value_key: str,
    body: MergeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, int]:
    try:
        if body.dry_run:
            moved = await vocabulary.count_labels(session, facet_key, value_key)
            # Resolve `into` here too, so a dry run answers 404 for an unknown
            # target exactly as the real merge does — a dry run whose only job
            # is to preview a merge must fail on anything the merge would.
            await vocabulary.count_labels(session, facet_key, body.into)
            if value_key == body.into:
                raise MergeIntoSelfError(value_key)
            return {"moved": moved}
        moved = await vocabulary.merge_values(session, facet_key, value_key, body.into)
    except MergeIntoSelfError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{facet_key}={value_key} cannot be merged into itself",
        ) from exc
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown facet value"
        ) from exc
    await session.commit()
    return {"moved": moved}


@router.delete(
    "/facets/{facet_key}/values/{value_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an unused value",
)
async def delete_value(
    facet_key: str, value_key: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    try:
        await vocabulary.delete_value(session, facet_key, value_key)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown facet value"
        ) from exc
    except ValueInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()


@router.get("/documents/{document_id}/labels", summary="One document's facet labels")
async def get_labels(
    document_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, dict[str, str]]:
    return {"labels": await vocabulary.document_labels(session, document_id)}


@router.put("/documents/{document_id}/labels", summary="Set or clear facet labels")
async def put_labels(
    document_id: int,
    body: LabelsBody,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, dict[str, str]]:
    # Without this the FK violation surfaces as an unhandled IntegrityError
    # (a 500). A trashed document is treated as absent: labels must not be
    # written onto something the user has deleted.
    exists = (
        await session.execute(
            select(Document.id).where(Document.id == document_id, Document.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown document")
    for facet_key, value_key in body.labels.items():
        try:
            await vocabulary.set_document_label(session, document_id, facet_key, value_key)
        except UnknownFacetError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown facet: {facet_key}"
            ) from exc
        except UnknownValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{facet_key}={value_key} is not in the vocabulary",
            ) from exc
    await session.commit()
    return {"labels": await vocabulary.document_labels(session, document_id)}


@router.get("/facet-suggestions", summary="Values the labeller wanted but could not use")
async def list_suggestions(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, list[dict[str, object]]]:
    rows = await session.execute(
        select(FacetValueSuggestion, Facet.key)
        .join(Facet, Facet.id == FacetValueSuggestion.facet_id)
        .where(FacetValueSuggestion.state == "pending")
        .order_by(FacetValueSuggestion.created_at)
        .limit(100)
    )
    return {
        "suggestions": [
            {
                "id": suggestion.id,
                "facet": facet_key,
                "suggested_label": suggestion.suggested_label,
                "reason": suggestion.reason,
                "document_id": suggestion.document_id,
            }
            for suggestion, facet_key in rows.all()
        ]
    }


@router.post("/facet-suggestions/{suggestion_id}/accept", summary="Create the suggested value")
async def accept_suggestion(
    suggestion_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    row = (
        await session.execute(
            select(FacetValueSuggestion, Facet.key)
            .join(Facet, Facet.id == FacetValueSuggestion.facet_id)
            .where(FacetValueSuggestion.id == suggestion_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown suggestion")
    suggestion, facet_key = row
    value_key = derive_value_key(suggestion.suggested_label)
    if not value_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"no value key can be derived from {suggestion.suggested_label!r}: "
                "nothing matching [a-z0-9_-] remains"
            ),
        )
    vocab = {f.key: f for f in await vocabulary.load_vocabulary(session)}
    if vocab[facet_key].value(value_key) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"{facet_key}={value_key} already exists"
        )
    try:
        await vocabulary.create_value(session, facet_key, value_key, suggestion.suggested_label)
    except IntegrityError as exc:
        # The pre-check above closes the common case; this closes the race
        # (two accepts for the same derived key landing concurrently) that a
        # pre-check alone cannot.
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"{facet_key}={value_key} already exists"
        ) from exc
    await vocabulary.set_document_label(session, suggestion.document_id, facet_key, value_key)
    suggestion.state = "accepted"
    await session.commit()
    return {"facet": facet_key, "value": value_key}


@router.post("/facet-suggestions/{suggestion_id}/dismiss", summary="Reject a suggestion")
async def dismiss_suggestion(
    suggestion_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    suggestion = await session.get(FacetValueSuggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown suggestion")
    suggestion.state = "dismissed"
    await session.commit()
    return {"state": "dismissed"}
