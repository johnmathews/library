"""Admin-only, series-aware currency normalisation.

Currency is a free-text ``CHAR(3)`` code carried by documents AND baked into
series identity (see ``models.py``): ``series_insights``,
``series_membership_overrides``, ``series_meta_overrides``, ``authored_series``
and ``authored_series_suggestions`` all store a currency, and three of those
have UNIQUE constraints whose tuple includes it (with
``postgresql_nulls_not_distinct=True``). Renaming a code therefore cannot be a
single ``UPDATE documents`` — it must rewrite every table and cope with
collisions on the constrained ones.

Policy (settled with the user):

- ``documents``, ``authored_series``, ``authored_series_suggestions`` — plain
  ``UPDATE`` (no currency in any unique tuple).
- ``series_insights`` — a recomputable cache. On collision with an existing
  ``(sender, kind, to_code)`` row, the ``from_code`` row is DELETED (the
  survivor wins; the insight regenerates on next indexing); the rest are
  updated.
- ``series_membership_overrides`` / ``series_meta_overrides`` — user-authored
  data (pins, titles/descriptions). If renaming would collide with an existing
  ``to_code`` row in either table, the WHOLE operation is REFUSED up front
  (``override_conflict``) and nothing is mutated. No user data is ever deleted.
- ``fx_rates`` — reference data, never mutated (two currencies must not share
  rate rows). If ``to_code`` has no ``fx_rates`` row, a warning is returned so
  the admin knows FX conversion for the renamed code is unavailable.

Collision detection uses ``IS NOT DISTINCT FROM`` for ``sender_id``/``kind_id``
so it matches the constraints' NULLS-NOT-DISTINCT semantics (a NULL sender and a
NULL kind collide with another NULL/NULL row).
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CODE_RE = re.compile(r"[A-Z]{3}")


def normalize_currency_code(value: str) -> str | None:
    """Uppercase + validate an ISO-4217-shaped code (exactly three letters).

    Mirrors the extraction validator (``extraction/schema.py``): trims,
    uppercases, and accepts only ``^[A-Z]{3}$`` (no closed allow-list). Returns
    ``None`` for anything else.
    """
    code = value.strip().upper()
    return code if _CODE_RE.fullmatch(code) else None


@dataclass(frozen=True)
class CurrencyCount:
    """One currency code with the number of (non-deleted) documents using it."""

    code: str
    document_count: int


@dataclass(frozen=True)
class OverrideConflict:
    """A user-authored series override that blocks a rename (see module docstring)."""

    table: str
    sender_id: int | None
    kind_id: int | None


@dataclass(frozen=True)
class NormalizeResult:
    """Outcome of :func:`normalize_currency`.

    - ``done`` — the rename ran; ``counts`` holds per-table rows changed and
      ``fx_rate_missing`` flags a missing ``fx_rates`` row for ``to_code``.
    - ``invalid_source`` / ``invalid_target`` — a code was not ``^[A-Z]{3}$``.
    - ``same_code`` — source and target are equal after normalising.
    - ``override_conflict`` — refused; ``conflicts`` lists the colliding
      user-authored overrides and nothing was mutated.
    """

    status: Literal["done", "invalid_source", "invalid_target", "same_code", "override_conflict"]
    from_code: str = ""
    to_code: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    fx_rate_missing: bool = False
    conflicts: list[OverrideConflict] = field(default_factory=list)


async def list_currencies_in_use(session: AsyncSession) -> list[CurrencyCount]:
    """Distinct currency codes on non-deleted documents, with counts, by code."""
    rows = (
        await session.execute(
            text(
                "SELECT currency, COUNT(*) AS n FROM documents "
                "WHERE deleted_at IS NULL AND currency IS NOT NULL "
                "GROUP BY currency ORDER BY currency"
            )
        )
    ).all()
    return [CurrencyCount(code=code, document_count=count) for code, count in rows]


# The override tables whose UNIQUE tuple includes currency and hold user data:
# a rename that would collide here is refused rather than dropping rows.
_OVERRIDE_TABLES = ("series_meta_overrides", "series_membership_overrides")


async def _override_conflicts(
    session: AsyncSession, from_code: str, to_code: str
) -> list[OverrideConflict]:
    """Override rows on ``from_code`` whose rename would collide with a ``to_code`` row.

    Matches the constraints' NULLS-NOT-DISTINCT semantics via
    ``IS NOT DISTINCT FROM`` on sender/kind, and (for membership) equal
    ``document_id``.
    """
    conflicts: list[OverrideConflict] = []
    for table in _OVERRIDE_TABLES:
        doc_clause = (
            " AND o2.document_id = o.document_id" if table == "series_membership_overrides" else ""
        )
        rows = (
            await session.execute(
                text(
                    f"SELECT o.sender_id, o.kind_id FROM {table} o "
                    "WHERE o.currency = :from_code AND EXISTS ("
                    f"SELECT 1 FROM {table} o2 WHERE "
                    "o2.sender_id IS NOT DISTINCT FROM o.sender_id "
                    "AND o2.kind_id IS NOT DISTINCT FROM o.kind_id"
                    f"{doc_clause} AND o2.currency = :to_code)"
                ),
                {"from_code": from_code, "to_code": to_code},
            )
        ).all()
        conflicts.extend(
            OverrideConflict(table=table, sender_id=sender_id, kind_id=kind_id)
            for sender_id, kind_id in rows
        )
    return conflicts


async def normalize_currency(
    session: AsyncSession, from_code_raw: str, to_code_raw: str
) -> NormalizeResult:
    """Rename currency ``from_code`` -> ``to_code`` across the whole store.

    Validates both codes, refuses a no-op and any user-override collision, then
    (in this transaction) rewrites documents + authored series + suggestions,
    merges/cleans the ``series_insights`` cache, updates the override tables, and
    leaves ``fx_rates`` untouched (flagging a missing target rate). Commits and
    returns per-table counts. See the module docstring for the full policy.
    """
    from_code = normalize_currency_code(from_code_raw)
    if from_code is None:
        return NormalizeResult(status="invalid_source")
    to_code = normalize_currency_code(to_code_raw)
    if to_code is None:
        return NormalizeResult(status="invalid_target")
    if from_code == to_code:
        return NormalizeResult(status="same_code", from_code=from_code, to_code=to_code)

    conflicts = await _override_conflicts(session, from_code, to_code)
    if conflicts:
        return NormalizeResult(
            status="override_conflict",
            from_code=from_code,
            to_code=to_code,
            conflicts=conflicts,
        )

    params = {"from_code": from_code, "to_code": to_code}
    counts: dict[str, int] = {}

    async def _run(sql: str) -> int:
        result = await session.execute(text(sql), params)
        return result.rowcount

    # Plain updates (currency is in no unique tuple here).
    counts["documents"] = await _run(
        "UPDATE documents SET currency = :to_code WHERE currency = :from_code"
    )
    counts["authored_series"] = await _run(
        "UPDATE authored_series SET currency = :to_code WHERE currency = :from_code"
    )
    counts["authored_series_suggestions"] = await _run(
        "UPDATE authored_series_suggestions SET signature_currency = :to_code "
        "WHERE signature_currency = :from_code"
    )

    # series_insights: cache. Drop from_code rows that would collide with an
    # existing to_code bucket (survivor kept), then move the rest.
    counts["series_insights_merged"] = await _run(
        "DELETE FROM series_insights si WHERE si.currency = :from_code AND EXISTS ("
        "SELECT 1 FROM series_insights s2 WHERE "
        "s2.sender_id IS NOT DISTINCT FROM si.sender_id "
        "AND s2.kind_id IS NOT DISTINCT FROM si.kind_id AND s2.currency = :to_code)"
    )
    counts["series_insights"] = await _run(
        "UPDATE series_insights SET currency = :to_code WHERE currency = :from_code"
    )

    # Override tables: pre-checked collision-free, so plain updates are safe.
    counts["series_membership_overrides"] = await _run(
        "UPDATE series_membership_overrides SET currency = :to_code WHERE currency = :from_code"
    )
    counts["series_meta_overrides"] = await _run(
        "UPDATE series_meta_overrides SET currency = :to_code WHERE currency = :from_code"
    )

    fx_row = (
        await session.execute(
            text("SELECT 1 FROM fx_rates WHERE currency = :to_code LIMIT 1"),
            {"to_code": to_code},
        )
    ).first()

    await session.commit()
    return NormalizeResult(
        status="done",
        from_code=from_code,
        to_code=to_code,
        counts=counts,
        fx_rate_missing=fx_row is None,
    )
