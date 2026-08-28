"""Facet vocabulary and document-label endpoints.

The closed-set rule is enforced here as well as in the labeller: a PUT naming a
value that does not exist is a 422, never an implicit create. Authentication is
enforced at include level in app.py, like every other router.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library.db import get_session
from library.facets import vocabulary
from library.facets.vocabulary import (
    UnknownFacetError,
    UnknownValueError,
    ValueInUseError,
)
from library.models import Facet, FacetValueSuggestion

router: APIRouter = APIRouter(tags=["facets"])

Key = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")]
Label = Annotated[str, StringConstraints(min_length=1, max_length=255)]


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
    await vocabulary.create_facet(session, body.key, body.label, body.ordinal)
    await session.commit()
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
    except UnknownFacetError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet") from exc
    await session.commit()
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet value") from exc
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet value") from exc
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
            return {"moved": moved}
        moved = await vocabulary.merge_values(session, facet_key, value_key, body.into)
    except (UnknownFacetError, UnknownValueError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet value") from exc
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown facet value") from exc
    except ValueInUseError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
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
    for facet_key, value_key in body.labels.items():
        try:
            await vocabulary.set_document_label(session, document_id, facet_key, value_key)
        except UnknownFacetError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown facet: {facet_key}") from exc
        except UnknownValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{facet_key}={value_key} is not in the vocabulary",
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown suggestion")
    suggestion, facet_key = row
    value_key = suggestion.suggested_label.strip().lower().replace(" ", "-")
    vocab = {f.key: f for f in await vocabulary.load_vocabulary(session)}
    if vocab[facet_key].value(value_key) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{facet_key}={value_key} already exists")
    await vocabulary.create_value(session, facet_key, value_key, suggestion.suggested_label)
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown suggestion")
    suggestion.state = "dismissed"
    await session.commit()
    return {"state": "dismissed"}
