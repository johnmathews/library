"""Reference-entity (recipient / sender / kind) rename, merge, create, delete.

The three families share a rename/merge/reassign-delete shape but differ enough
(kinds are slug-keyed and never merge) that the handlers are kept explicit rather
than parameterised — correctness over DRY.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from library.api.admin._base import (
    ReferenceCreateIn,
    _acquire_admin_lock,
    _reassign_to_int,
    _reassign_to_slug,
    router,
)
from library.db import get_session
from library.schemas import KindOut, RecipientOut, SenderOut
from library.taxonomy import (
    create_recipient,
    create_sender,
    reassign_and_delete_kind,
    reassign_and_delete_recipient,
    reassign_and_delete_sender,
    rename_kind,
    rename_recipient,
    rename_sender,
)

# ---------------------------------------------------------- recipient management


class RecipientRenameIn(BaseModel):
    """Body of PATCH /api/admin/recipients/{id}."""

    name: Annotated[str, StringConstraints(max_length=255)]
    merge: bool = Field(
        default=False,
        description="Confirm merging into an existing recipient on a name collision.",
    )


class RecipientRenameConflict(BaseModel):
    """409 body when a rename would collide with an existing recipient.

    The client warns the user, then re-PATCHes with ``merge=true`` to merge this
    recipient into ``target_id``.
    """

    detail: str
    target_id: int
    target_name: str
    target_document_count: int


class RecipientDeleteConflict(BaseModel):
    """409 body when deleting an in-use recipient without a reassignment target."""

    detail: str
    document_count: int


@router.patch(
    "/recipients/{recipient_id}",
    response_model=RecipientOut,
    summary="Rename (or merge) a recipient",
    responses={
        400: {"description": "Empty name"},
        404: {"description": "Unknown recipient"},
        409: {
            "model": RecipientRenameConflict,
            "description": "Name collides with another recipient",
        },
    },
)
async def rename_recipient_route(
    recipient_id: int,
    payload: RecipientRenameIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecipientOut | JSONResponse:
    """Rename a recipient; on a case-insensitive name collision, merge when confirmed.

    Without ``merge`` a collision returns 409 carrying the target's id/name/count
    (the client warns, then retries with ``merge=true``, which reassigns this
    recipient's documents to the target and deletes this recipient).
    """
    await _acquire_admin_lock(session)
    result = await rename_recipient(session, recipient_id, payload.name, payload.merge)
    if result.status == "empty_name":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="name must not be empty"
        )
    if result.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="recipient not found")
    if result.status == "collision":
        assert result.recipient is not None
        # Flat 409 body (matches RecipientRenameConflict): the conflict fields sit
        # at the top level alongside `detail`, so the client reads them straight
        # off `ApiError.body` without a FastAPI HTTPException envelope nesting them.
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"a recipient named {result.recipient.name!r} already exists; "
                    "retry with merge=true to merge into it"
                ),
                "target_id": result.recipient.id,
                "target_name": result.recipient.name,
                "target_document_count": result.document_count,
            },
        )
    assert result.recipient is not None
    return RecipientOut(id=result.recipient.id, name=result.recipient.name)


@router.delete(
    "/recipients/{recipient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # The success path is an empty 204; the 409 branch returns a JSONResponse
    # directly. Pin response_model to None so FastAPI does not infer a body model
    # from the `None | JSONResponse` return annotation (a 204 may carry no body).
    response_model=None,
    summary="Delete a recipient, reassigning its documents",
    responses={
        400: {"description": "Self-reassignment"},
        404: {"description": "Unknown recipient or reassignment target"},
        409: {"model": RecipientDeleteConflict, "description": "Recipient in use; no target given"},
    },
)
async def delete_recipient_route(
    recipient_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None | JSONResponse:
    """Delete a recipient. If it still has documents, a ``reassign_to`` target is
    required: ``?reassign_to=<id>`` moves them, ``?reassign_to=`` (empty/null)
    nulls them, and omitting it entirely on an in-use recipient returns 409.
    """
    reassign_to = _reassign_to_int(request)
    await _acquire_admin_lock(session)
    result = await reassign_and_delete_recipient(session, recipient_id, reassign_to)
    if result.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="recipient not found")
    if result.status == "target_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="reassignment target not found"
        )
    if result.status == "self_reassign":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="cannot reassign a recipient to itself"
        )
    if result.status == "in_use":
        # Flat 409 body (matches RecipientDeleteConflict): `document_count` sits at
        # the top level alongside `detail` for the client to read off
        # `ApiError.body` directly (no HTTPException envelope nesting it).
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"recipient has {result.document_count} document(s); "
                    "provide reassign_to to move them before deleting"
                ),
                "document_count": result.document_count,
            },
        )
    return None


@router.post(
    "/recipients",
    response_model=RecipientOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a recipient",
    responses={422: {"description": "Empty name"}},
)
async def create_recipient_route(
    payload: ReferenceCreateIn,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RecipientOut:
    """Create a recipient; a case-insensitive name match returns the existing one
    (``200``) instead of a duplicate, a new one is ``201``."""
    await _acquire_admin_lock(session)
    result = await create_recipient(session, payload.name)
    if result.status == "empty_name":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="recipient name must not be empty",
        )
    assert result.entity is not None
    if result.status == "exists":
        response.status_code = status.HTTP_200_OK
    return RecipientOut(id=result.entity.id, name=result.entity.name)


# ------------------------------------------------------------- sender management


class SenderRenameIn(BaseModel):
    """Body of PATCH /api/admin/senders/{id}."""

    name: Annotated[str, StringConstraints(max_length=255)]
    merge: bool = Field(
        default=False,
        description="Confirm merging into an existing sender on a name collision.",
    )


class SenderRenameConflict(BaseModel):
    """409 body when a sender rename would collide with another sender."""

    detail: str
    target_id: int
    target_name: str
    target_document_count: int


class SenderDeleteConflict(BaseModel):
    """409 body when deleting an in-use sender without a reassignment target."""

    detail: str
    document_count: int


@router.post(
    "/senders",
    response_model=SenderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a sender",
    responses={422: {"description": "Empty name"}},
)
async def create_sender_route(
    payload: ReferenceCreateIn,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SenderOut:
    """Create a sender; a case-insensitive name match returns the existing one
    (``200``) instead of a duplicate, a new one is ``201``."""
    await _acquire_admin_lock(session)
    result = await create_sender(session, payload.name)
    if result.status == "empty_name":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="sender name must not be empty",
        )
    assert result.entity is not None
    if result.status == "exists":
        response.status_code = status.HTTP_200_OK
    return SenderOut(id=result.entity.id, name=result.entity.name)


@router.patch(
    "/senders/{sender_id}",
    response_model=SenderOut,
    summary="Rename (or merge) a sender",
    responses={
        400: {"description": "Empty name"},
        404: {"description": "Unknown sender"},
        409: {"model": SenderRenameConflict, "description": "Name collides with another sender"},
    },
)
async def rename_sender_route(
    sender_id: int,
    payload: SenderRenameIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SenderOut | JSONResponse:
    """Rename a sender; on a case-insensitive collision, merge when confirmed
    (mirrors the recipient rename/merge contract)."""
    await _acquire_admin_lock(session)
    result = await rename_sender(session, sender_id, payload.name, payload.merge)
    if result.status == "empty_name":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="name must not be empty"
        )
    if result.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sender not found")
    if result.status == "collision":
        assert result.sender is not None
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"a sender named {result.sender.name!r} already exists; "
                    "retry with merge=true to merge into it"
                ),
                "target_id": result.sender.id,
                "target_name": result.sender.name,
                "target_document_count": result.document_count,
            },
        )
    assert result.sender is not None
    return SenderOut(id=result.sender.id, name=result.sender.name)


@router.delete(
    "/senders/{sender_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a sender, reassigning its documents",
    responses={
        400: {"description": "Self-reassignment"},
        404: {"description": "Unknown sender or reassignment target"},
        409: {"model": SenderDeleteConflict, "description": "Sender in use; no target given"},
    },
)
async def delete_sender_route(
    sender_id: int,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None | JSONResponse:
    """Delete a sender. If it still has documents, ``?reassign_to=<id>`` moves
    them, ``?reassign_to=`` (empty/null) nulls them, and omitting it on an in-use
    sender returns 409."""
    reassign_to = _reassign_to_int(request)
    await _acquire_admin_lock(session)
    result = await reassign_and_delete_sender(session, sender_id, reassign_to)
    if result.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sender not found")
    if result.status == "target_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="reassignment target not found"
        )
    if result.status == "self_reassign":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="cannot reassign a sender to itself"
        )
    if result.status == "in_use":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"sender has {result.document_count} document(s); "
                    "provide reassign_to to move them before deleting"
                ),
                "document_count": result.document_count,
            },
        )
    return None


# --------------------------------------------------------------- kind management


class KindRenameIn(BaseModel):
    """Body of PATCH /api/admin/kinds/{slug} — the display name only (slug is immutable)."""

    name: Annotated[str, StringConstraints(max_length=255)]


class KindRenameConflict(BaseModel):
    """409 body when a kind rename would collide with another kind's name."""

    detail: str
    target_slug: str
    target_name: str


class KindDeleteConflict(BaseModel):
    """409 body when deleting an in-use kind without a reassignment target."""

    detail: str
    document_count: int


@router.patch(
    "/kinds/{slug}",
    response_model=KindOut,
    summary="Rename a kind's display name (slug is immutable)",
    responses={
        400: {"description": "Empty name"},
        404: {"description": "Unknown kind"},
        409: {"model": KindRenameConflict, "description": "Name collides with another kind"},
    },
)
async def rename_kind_route(
    slug: str,
    payload: KindRenameIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KindOut | JSONResponse:
    """Rename a kind's display name. The slug is a stable identifier and never
    changes. A name collision with another kind is refused (no kind-merge)."""
    await _acquire_admin_lock(session)
    result = await rename_kind(session, slug, payload.name)
    if result.status == "empty_name":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="name must not be empty"
        )
    if result.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kind not found")
    if result.status == "collision":
        assert result.kind is not None
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"a kind named {result.kind.name!r} already exists; pick a different name"
                ),
                "target_slug": result.kind.slug,
                "target_name": result.kind.name,
            },
        )
    assert result.kind is not None
    return KindOut(slug=result.kind.slug, name=result.kind.name)


@router.delete(
    "/kinds/{slug}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a kind, reassigning its documents",
    responses={
        400: {"description": "Self-reassignment"},
        404: {"description": "Unknown kind or reassignment target"},
        409: {"model": KindDeleteConflict, "description": "Kind in use; no target given"},
    },
)
async def delete_kind_route(
    slug: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None | JSONResponse:
    """Delete a kind. If it still has documents, ``?reassign_to=<slug>`` moves
    them onto another kind, ``?reassign_to=`` (empty/null) nulls them, and
    omitting it on an in-use kind returns 409."""
    reassign_to = _reassign_to_slug(request)
    await _acquire_admin_lock(session)
    result = await reassign_and_delete_kind(session, slug, reassign_to)
    if result.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kind not found")
    if result.status == "target_not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="reassignment target not found"
        )
    if result.status == "self_reassign":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="cannot reassign a kind to itself"
        )
    if result.status == "in_use":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"kind has {result.document_count} document(s); "
                    "provide reassign_to to move them before deleting"
                ),
                "document_count": result.document_count,
            },
        )
    return None
