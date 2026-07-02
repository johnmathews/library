"""Admin-only views: system/infra context, architecture docs, test coverage,
and user management.

The whole router is gated by ``require_admin`` (attached at include level in
app.py), so every endpoint here is admin-only; ``current_user`` still runs
first, so anonymous requests get 401 and merely non-admin requests get 403.
See docs/admin.md.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import library
from library.auth.deps import current_user
from library.auth.passwords import hash_password
from library.auth.service import revoke_all_credentials
from library.config import Settings, get_settings
from library.currencies import list_currencies_in_use, normalize_currency
from library.db import get_session
from library.extraction.apply import get_or_create_user_recipient
from library.fx_admin import list_fx_status, seed_fx_rate, seed_fx_rate_live
from library.fx_api import FxApiError
from library.models import Document, DocumentStatus, User
from library.schemas import KindOut, RecipientOut, SenderOut
from library.taxonomy import (
    UNSET,
    _Unset,
    create_recipient,
    create_sender,
    reassign_and_delete_kind,
    reassign_and_delete_recipient,
    reassign_and_delete_sender,
    rename_kind,
    rename_recipient,
    rename_sender,
)

router: APIRouter = APIRouter(prefix="/admin", tags=["admin"])

# Settings fields safe to surface in the system view: operational knobs only,
# never secrets (API keys, passwords) or internal URLs (db, embedder, mail host).
_SAFE_CONFIG_FIELDS: tuple[str, ...] = (
    "environment",
    "max_upload_bytes",
    "session_ttl_days",
    "cookie_secure",
    "ocr_languages",
    "extraction_enabled",
    "extraction_model",
    "extraction_daily_budget_usd",
    "markdown_enabled",
    "markdown_model",
    "markdown_daily_budget_usd",
    "embedding_enabled",
    "embedding_model_name",
    "ask_model",
)

# Static deployment topology (the docker-compose services); descriptive context
# for the admin, kept in sync with docker-compose.yml + docs/architecture.md.
_DEPLOYMENT_SERVICES: list[dict[str, str]] = [
    {"name": "api", "role": "FastAPI web server + this admin surface"},
    {"name": "worker", "role": "Procrastinate job worker (OCR, extract, markdown, embed)"},
    {"name": "db", "role": "PostgreSQL 17 + pgvector (metadata, queue, embeddings)"},
    {"name": "embedder", "role": "text-embeddings-inference sidecar (bge-m3)"},
    {"name": "migrate", "role": "one-shot Alembic upgrade on deploy"},
]

# Markdown docs surfaced read-only in the Architecture view (from settings.docs_dir).
_ARCHITECTURE_DOCS: tuple[str, ...] = ("architecture.md", "ingestion.md")

# Transaction-scoped advisory-lock key serialising admin-role mutations, so the
# last-active-admin guard is race-safe: concurrent demote/deactivate requests
# would each otherwise see one remaining admin (READ COMMITTED) and both commit.
_ADMIN_MUTATION_LOCK_KEY: int = 0x4C49_4241  # "LIBA"

# Distinct advisory-lock key for currency normalisation. It serialises the
# read-then-write of the collision pre-check + multi-table rewrite so two
# concurrent renames can't interleave; kept separate from the reference-entity
# lock so a currency rename doesn't block unrelated sender/kind edits.
_CURRENCY_MUTATION_LOCK_KEY: int = 0x4C49_4243  # "LIBC"


async def _acquire_admin_lock(session: AsyncSession) -> None:
    """Serialise admin mutations via a transaction-scoped advisory lock.

    Shared by the user-role mutations and the reference-entity CRUD
    (senders/kinds/recipients) so concurrent admin edits (e.g. two merges into
    the same target) can't interleave read-then-write. Released automatically at
    commit/rollback; the reference services commit internally, ending the lock.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMIN_MUTATION_LOCK_KEY}
    )


def _reassign_to_int(request: Request) -> int | None | _Unset:
    """Three-state ``?reassign_to`` for id-keyed entities (recipients, senders).

    Absent -> :data:`UNSET`; empty or ``null`` -> ``None`` (null the documents);
    else an int id. A non-integer value is a 422.
    """
    if "reassign_to" not in request.query_params:
        return UNSET
    raw = request.query_params["reassign_to"]
    if raw == "" or raw.lower() == "null":
        return None
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reassign_to must be an integer, empty, or 'null'",
        ) from None


def _reassign_to_slug(request: Request) -> str | None | _Unset:
    """Three-state ``?reassign_to`` for slug-keyed entities (kinds).

    Absent -> :data:`UNSET`; empty or ``null`` -> ``None``; else the target slug.
    """
    if "reassign_to" not in request.query_params:
        return UNSET
    raw = request.query_params["reassign_to"]
    if raw == "" or raw.lower() == "null":
        return None
    return raw


class DbStats(BaseModel):
    """Aggregate counts over the library's content and job queue."""

    documents_total: int = Field(description="Non-deleted documents.")
    documents_deleted: int
    documents_by_status: dict[str, int] = Field(description="Non-deleted documents per status.")
    users_total: int
    users_active: int
    jobs_total: int
    jobs_active: int = Field(description="Jobs in 'todo' or 'doing'.")
    extraction_cost_usd_total: float = Field(description="Summed Claude extraction spend.")


class SystemInfo(BaseModel):
    """Version, build, deployment, redacted config, and live DB stats."""

    version: str
    git_sha: str | None
    deployment: list[dict[str, str]]
    config: dict[str, Any]
    stats: DbStats


class CoverageFile(BaseModel):
    """One file's line-coverage percentage (for the worst-offenders list)."""

    path: str
    pct: float


class CoverageSide(BaseModel):
    """One side (backend/frontend) of the coverage summary.

    The per-file fields are optional and default to empty so older baked
    summaries (totals-only) still validate.
    """

    pct: float | None
    threshold: float | None = None
    files_total: int | None = None
    files_below_gate: int | None = None
    worst_files: list[CoverageFile] = []


class CoverageTestType(BaseModel):
    """One CI test type (mirrors a job in .github/workflows/ci.yml).

    ``has_coverage`` is true for the two unit suites that report line coverage
    (backend/frontend, detailed in the matching ``CoverageSide``); false for
    e2e and compose-smoke, which are pass/fail gates with no line coverage.
    """

    key: str
    label: str
    runner: str
    has_coverage: bool
    description: str


class CoverageInfo(BaseModel):
    """Test coverage, read from the CI-baked summary (see docs/admin.md)."""

    available: bool
    backend: CoverageSide | None = None
    frontend: CoverageSide | None = None
    # Empty for older baked summaries that predate the test-type enumeration.
    test_types: list[CoverageTestType] = []
    generated_at: str | None = None
    git_sha: str | None = None


class ArchitectureDoc(BaseModel):
    """One rendered-on-the-client markdown doc."""

    name: str
    title: str
    markdown: str


class ArchitectureOut(BaseModel):
    """The architecture/ingestion docs available in this deployment."""

    docs: list[ArchitectureDoc]


class AdminUserOut(BaseModel):
    """A user as seen by an admin (no secrets)."""

    id: int
    username: str
    display_name: str
    is_admin: bool
    is_active: bool
    created_at: datetime


class AdminUserCreate(BaseModel):
    """Body of POST /api/admin/users."""

    username: Annotated[str, StringConstraints(min_length=1, max_length=150)]
    password: Annotated[str, StringConstraints(min_length=1)]
    display_name: str = ""
    is_admin: bool = False


class AdminUserUpdate(BaseModel):
    """Body of PATCH /api/admin/users/{id}; only provided fields change."""

    is_admin: bool | None = None
    is_active: bool | None = None


def _user_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at,
    )


def _safe_config(settings: Settings) -> dict[str, Any]:
    """The secret-free operational subset of settings for the system view."""
    return {field: getattr(settings, field) for field in _SAFE_CONFIG_FIELDS}


async def _db_stats(session: AsyncSession) -> DbStats:
    by_status_rows = (
        await session.execute(
            select(Document.status, func.count())
            .where(Document.deleted_at.is_(None))
            .group_by(Document.status)
        )
    ).all()
    by_status = {str(status_value): count for status_value, count in by_status_rows}
    documents_total = sum(by_status.values())
    documents_deleted = (
        await session.execute(
            select(func.count()).select_from(Document).where(Document.deleted_at.is_not(None))
        )
    ).scalar_one()

    users_total = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    users_active = (
        await session.execute(select(func.count()).select_from(User).where(User.is_active))
    ).scalar_one()

    jobs_total = (
        await session.execute(text("SELECT count(*) FROM procrastinate_jobs"))
    ).scalar_one()
    jobs_active = (
        await session.execute(
            text("SELECT count(*) FROM procrastinate_jobs WHERE status IN ('todo', 'doing')")
        )
    ).scalar_one()

    extraction_cost = (
        await session.execute(
            text(
                "SELECT COALESCE(sum((extra -> 'extraction' ->> 'cost_usd')::float8), 0) "
                "FROM documents WHERE extra ? 'extraction'"
            )
        )
    ).scalar_one()

    # Stable ordering by the pipeline's natural progression for the UI.
    ordered = {
        member.value: by_status.get(member.value, 0)
        for member in DocumentStatus
        if member.value in by_status
    }
    return DbStats(
        documents_total=documents_total,
        documents_deleted=documents_deleted,
        documents_by_status=ordered,
        users_total=users_total,
        users_active=users_active,
        jobs_total=jobs_total,
        jobs_active=jobs_active,
        extraction_cost_usd_total=round(float(extraction_cost), 4),
    )


@router.get("/system", response_model=SystemInfo, summary="System & infra context")
async def system_info(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SystemInfo:
    """App version + build, deployment topology, redacted config, and live stats."""
    settings = get_settings()
    return SystemInfo(
        version=library.__version__,
        git_sha=settings.git_sha,
        deployment=_DEPLOYMENT_SERVICES,
        config=_safe_config(settings),
        stats=await _db_stats(session),
    )


def _doc_title(markdown: str, fallback: str) -> str:
    """First level-1 heading, else the filename."""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


@router.get("/architecture", response_model=ArchitectureOut, summary="Architecture docs")
async def architecture() -> ArchitectureOut:
    """The architecture/ingestion markdown docs baked into this deployment.

    Missing files are skipped (a slim image may omit docs/); the client renders
    the returned markdown.
    """
    docs_dir: Path = get_settings().docs_dir
    docs: list[ArchitectureDoc] = []
    for name in _ARCHITECTURE_DOCS:
        path = docs_dir / name
        try:
            markdown = path.read_text(encoding="utf-8")
        except OSError:
            continue  # missing/unreadable (e.g. a slim image) → skip, degrade gracefully
        docs.append(ArchitectureDoc(name=name, title=_doc_title(markdown, name), markdown=markdown))
    return ArchitectureOut(docs=docs)


@router.get("/coverage", response_model=CoverageInfo, summary="Test coverage")
async def coverage() -> CoverageInfo:
    """Backend + frontend coverage from the CI-baked summary; unavailable in dev."""
    path: Path = get_settings().coverage_summary_path
    if not path.is_file():
        return CoverageInfo(available=False)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return CoverageInfo(available=False)
    return CoverageInfo(
        available=True,
        backend=CoverageSide.model_validate(data["backend"]) if data.get("backend") else None,
        frontend=CoverageSide.model_validate(data["frontend"]) if data.get("frontend") else None,
        test_types=[CoverageTestType.model_validate(t) for t in data.get("test_types") or []],
        generated_at=data.get("generated_at"),
        git_sha=data.get("git_sha"),
    )


@router.get("/users", response_model=list[AdminUserOut], summary="List users")
async def list_users(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AdminUserOut]:
    """Every user, oldest first — roles and active state, never secrets."""
    users = (await session.execute(select(User).order_by(User.id))).scalars().all()
    return [_user_out(user) for user in users]


@router.post(
    "/users",
    response_model=AdminUserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    responses={409: {"description": "Username already exists"}},
)
async def create_user(
    payload: AdminUserCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserOut:
    """Create a user (optionally admin), auto-linking a recipient.

    Mirrors the `library user add` CLI. A recipient named by the user's display
    name (falling back to the username) is created and linked via ``user_id`` so
    documents addressed to either name resolve to it (see docs/admin.md §1.2.4).
    """
    existing = (
        await session.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"username already exists: {payload.username!r}",
        )
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        is_admin=payload.is_admin,
    )
    session.add(user)
    await session.flush()  # assign user.id before linking the recipient
    await get_or_create_user_recipient(session, user)
    await session.commit()
    await session.refresh(user)
    return _user_out(user)


async def _active_admin_count(session: AsyncSession) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(User).where(User.is_admin, User.is_active)
        )
    ).scalar_one()


@router.patch(
    "/users/{user_id}",
    response_model=AdminUserOut,
    summary="Update a user's role / active state",
    responses={
        404: {"description": "Unknown user"},
        409: {"description": "Would remove the last active admin"},
    },
)
async def update_user(
    user_id: int,
    payload: AdminUserUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserOut:
    """Promote/demote admin and activate/deactivate.

    Guards against locking everyone out: the change cannot drop the number of
    active admins to zero. Deactivating a user also revokes their sessions and
    tokens (mirrors the `library user disable` CLI).
    """
    # Serialise concurrent admin-role mutations so the last-admin count below is
    # race-safe (released automatically at commit/rollback).
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMIN_MUTATION_LOCK_KEY}
    )
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    provided = payload.model_dump(exclude_unset=True)
    was_active_admin = user.is_admin and user.is_active
    if "is_admin" in provided and provided["is_admin"] is not None:
        user.is_admin = provided["is_admin"]
    if "is_active" in provided and provided["is_active"] is not None:
        user.is_active = provided["is_active"]

    # If this user *was* the/an active admin and is losing that status, make
    # sure at least one active admin remains.
    now_active_admin = user.is_admin and user.is_active
    if was_active_admin and not now_active_admin and await _active_admin_count(session) == 0:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot remove the last active admin",
        )

    await session.commit()
    if not user.is_active:
        await revoke_all_credentials(session, user.id)
    await session.refresh(user)
    return _user_out(user)


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a user",
    responses={
        400: {"description": "Cannot delete your own account"},
        404: {"description": "Unknown user"},
        409: {"description": "Would remove the last active admin"},
    },
)
async def delete_user(
    user_id: int,
    current: Annotated[User, Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Permanently delete a user.

    Guards (checked under the same advisory lock as role changes so the count is
    race-safe): deleting the **last active admin** is refused (409), and deleting
    **your own account** is refused (400). The last-admin check runs first, so a
    sole admin trying to remove themselves gets the clearer 409.

    The deleted user's linked recipient survives — ``recipients.user_id`` is
    ``ON DELETE SET NULL``, so it is merely unlinked and documents addressed to
    that person stay addressed. Sessions and API tokens cascade away with the row.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADMIN_MUTATION_LOCK_KEY}
    )
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    if user.is_admin and user.is_active and await _active_admin_count(session) <= 1:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot delete the last active admin",
        )
    if user.id == current.id:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot delete your own account",
        )

    await session.delete(user)
    await session.commit()


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


class ReferenceCreateIn(BaseModel):
    """Body of the reference-entity create endpoints (recipients, senders)."""

    name: Annotated[str, StringConstraints(max_length=255)]


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


# ----------------------------------------------------------- currency management


class CurrencyInUse(BaseModel):
    """One currency code with the number of (non-deleted) documents using it."""

    code: str
    document_count: int


class CurrencyNormalizeIn(BaseModel):
    """Body of POST /api/admin/currencies/normalize."""

    from_code: Annotated[str, StringConstraints(max_length=8)]
    to_code: Annotated[str, StringConstraints(max_length=8)]


class CurrencyNormalizeOut(BaseModel):
    """Result of a successful currency normalisation."""

    from_code: str
    to_code: str
    counts: dict[str, int]
    # True when ``to_code`` has no fx_rates row, so FX conversion for it is
    # unavailable until a rate is seeded (fx_rates is never mutated by a rename).
    fx_rate_missing: bool


class CurrencyConflictItem(BaseModel):
    """One user-authored override that blocks a currency rename."""

    table: str
    sender_id: int | None
    kind_id: int | None


class CurrencyOverrideConflict(BaseModel):
    """409 body when a rename would collide with user-authored series overrides.

    The rename is refused and nothing is changed; the admin resolves the listed
    overrides first (no user data is dropped).
    """

    detail: str
    conflicts: list[CurrencyConflictItem]


@router.get(
    "/currencies",
    response_model=list[CurrencyInUse],
    summary="List the distinct currency codes in use",
)
async def list_currencies_route(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[CurrencyInUse]:
    """Distinct currency codes across non-deleted documents, with counts."""
    rows = await list_currencies_in_use(session)
    return [CurrencyInUse(code=row.code, document_count=row.document_count) for row in rows]


@router.post(
    "/currencies/normalize",
    response_model=CurrencyNormalizeOut,
    summary="Rename/normalise a currency code across the whole store (series-aware)",
    responses={
        400: {"description": "Source and target are the same code"},
        409: {
            "model": CurrencyOverrideConflict,
            "description": "Refused: would collide with user-authored series overrides",
        },
        422: {"description": "A code is not a 3-letter currency code"},
    },
)
async def normalize_currency_route(
    payload: CurrencyNormalizeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrencyNormalizeOut | JSONResponse:
    """Rename currency ``from_code`` to ``to_code`` everywhere it appears.

    Rewrites documents, authored series and suggestions, merges/cleans the
    series-insight cache, and updates the series override tables — but refuses
    (409) if that would collide with a user-authored override, and never touches
    ``fx_rates`` (a missing target rate is reported in ``fx_rate_missing``). See
    docs/api.md and the currencies module for the full policy.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CURRENCY_MUTATION_LOCK_KEY}
    )
    result = await normalize_currency(session, payload.from_code, payload.to_code)
    if result.status in ("invalid_source", "invalid_target"):
        field = "from_code" if result.status == "invalid_source" else "to_code"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field} must be a 3-letter currency code",
        )
    if result.status == "same_code":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="from_code and to_code are the same",
        )
    if result.status == "override_conflict":
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": (
                    f"renaming {result.from_code} to {result.to_code} would collide with "
                    f"{len(result.conflicts)} user-authored series override(s); resolve them first"
                ),
                "conflicts": [
                    {"table": c.table, "sender_id": c.sender_id, "kind_id": c.kind_id}
                    for c in result.conflicts
                ],
            },
        )
    return CurrencyNormalizeOut(
        from_code=result.from_code,
        to_code=result.to_code,
        counts=result.counts,
        fx_rate_missing=result.fx_rate_missing,
    )


# ------------------------------------------------------------------- FX rates


class FxRateStatus(BaseModel):
    """One in-use currency and whether it has a seeded FX rate.

    ``is_base`` is USD (rate 1.0, always convertible, never seeded). ``rate_to_base``
    / ``as_of`` carry the latest seeded row when ``has_rate`` is true.
    """

    code: str
    document_count: int
    is_base: bool
    has_rate: bool
    rate_to_base: Decimal | None = None
    as_of: date | None = None


class FxRateSeedIn(BaseModel):
    """Body of POST /api/admin/fx-rates.

    ``source="live"`` fetches the current USD-per-unit rate from the provider;
    ``source="manual"`` requires ``rate_to_base`` (USD per one unit). ``as_of``
    defaults to today.
    """

    currency: Annotated[str, StringConstraints(max_length=8)]
    source: Literal["live", "manual"] = "live"
    rate_to_base: Decimal | None = Field(default=None, gt=0)
    as_of: date | None = None


class FxRateSeedOut(BaseModel):
    """The seeded FX row."""

    currency: str
    as_of: date
    rate_to_base: Decimal


@router.get(
    "/fx-rates",
    response_model=list[FxRateStatus],
    summary="Report the FX-rate seeding status of every in-use currency",
)
async def list_fx_rates_route(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[FxRateStatus]:
    """Per in-use currency: document count and whether an FX rate is seeded."""
    rows = await list_fx_status(session)
    return [
        FxRateStatus(
            code=row.code,
            document_count=row.document_count,
            is_base=row.is_base,
            has_rate=row.has_rate,
            rate_to_base=row.rate_to_base,
            as_of=row.as_of,
        )
        for row in rows
    ]


@router.post(
    "/fx-rates",
    response_model=FxRateSeedOut,
    summary="Seed an FX rate for a currency (live fetch or manual entry)",
    responses={
        422: {"description": "Not a 3-letter code, USD (the base), or manual with no rate"},
        502: {"description": "The live FX provider failed or does not list the currency"},
    },
)
async def seed_fx_rate_route(
    payload: FxRateSeedIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FxRateSeedOut:
    """Seed (upsert) one ``fx_rates`` row so conversion for ``currency`` resolves.

    Live source fetches the USD-per-unit rate; manual source uses ``rate_to_base``.
    USD is refused (the implicit base). See docs/admin.md and the fx modules.
    """
    if payload.source == "manual":
        if payload.rate_to_base is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="rate_to_base is required when source is manual",
            )
        result = await seed_fx_rate(session, payload.currency, payload.rate_to_base, payload.as_of)
    else:
        try:
            result = await seed_fx_rate_live(session, payload.currency, settings=settings)
        except FxApiError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"live FX lookup failed: {exc}",
            ) from exc

    if result.status == "invalid_code":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="currency must be a 3-letter currency code",
        )
    if result.status == "is_base":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="USD is the base currency (rate 1.0) and needs no seeded rate",
        )
    if result.status == "unsupported":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"the live FX provider does not list {result.currency}; enter a rate manually",
        )
    assert result.as_of is not None and result.rate_to_base is not None
    return FxRateSeedOut(
        currency=result.currency, as_of=result.as_of, rate_to_base=result.rate_to_base
    )
