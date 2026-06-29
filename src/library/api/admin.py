"""Admin-only views: system/infra context, architecture docs, test coverage,
and user management.

The whole router is gated by ``require_admin`` (attached at include level in
app.py), so every endpoint here is admin-only; ``current_user`` still runs
first, so anonymous requests get 401 and merely non-admin requests get 403.
See docs/admin.md.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import library
from library.auth.passwords import hash_password
from library.auth.service import revoke_all_credentials
from library.config import Settings, get_settings
from library.db import get_session
from library.models import Document, DocumentStatus, User
from library.schemas import RecipientOut
from library.taxonomy import (
    UNSET,
    _Unset,
    reassign_and_delete_recipient,
    rename_recipient,
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


class CoverageInfo(BaseModel):
    """Test coverage, read from the CI-baked summary (see docs/admin.md)."""

    available: bool
    backend: CoverageSide | None = None
    frontend: CoverageSide | None = None
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
    """Create a user (optionally admin). Mirrors the `library user add` CLI."""
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
    # Three-state from the raw query string: absent (UNSET), explicit-null, or an id.
    reassign_to: int | None | _Unset
    if "reassign_to" not in request.query_params:
        reassign_to = UNSET
    else:
        raw = request.query_params["reassign_to"]
        if raw == "" or raw.lower() == "null":
            reassign_to = None
        else:
            try:
                reassign_to = int(raw)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="reassign_to must be an integer, empty, or 'null'",
                ) from None

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
