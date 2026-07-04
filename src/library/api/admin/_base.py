"""Shared router + module-scope helpers for the admin package.

This tiny module holds the single ``APIRouter`` and the cross-cutting helpers
(advisory-lock keys/acquirer, the three-state ``?reassign_to`` parsers, and the
reference-entity create body) that the ``users``/``taxonomy``/``fx`` submodules
hang their routes on. It exists to break the import cycle: ``__init__`` imports
the submodules for their decorator side effects, and each submodule imports
``router`` (and helpers) from *here* rather than from ``__init__``.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from library.taxonomy import UNSET, _Unset

router: APIRouter = APIRouter(prefix="/admin", tags=["admin"])

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


class ReferenceCreateIn(BaseModel):
    """Body of the reference-entity create endpoints (recipients, senders)."""

    name: Annotated[str, StringConstraints(max_length=255)]
