"""Admin-only currency normalisation.

Currency is a free-text ``CHAR(3)`` code carried by ``documents``. Renaming a
code is a single ``UPDATE documents`` from ``from_code`` to ``to_code``.

``fx_rates`` (reference data mapping a currency to its USD rate) is never
mutated by a rename — two currencies must not share rate rows. If ``to_code``
has no ``fx_rates`` row, a warning is returned so the admin knows FX
conversion for the renamed code is unavailable.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, text
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
class NormalizeResult:
    """Outcome of :func:`normalize_currency`.

    - ``done`` — the rename ran; ``counts`` holds ``documents`` (rows changed)
      and ``fx_rate_missing`` flags a missing ``fx_rates`` row for ``to_code``.
    - ``invalid_source`` / ``invalid_target`` — a code was not ``^[A-Z]{3}$``.
    - ``same_code`` — source and target are equal after normalising.
    """

    status: Literal["done", "invalid_source", "invalid_target", "same_code"]
    from_code: str = ""
    to_code: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    fx_rate_missing: bool = False


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


async def normalize_currency(
    session: AsyncSession, from_code_raw: str, to_code_raw: str
) -> NormalizeResult:
    """Rename currency ``from_code`` -> ``to_code`` across the whole store.

    Validates both codes, refuses a no-op, then rewrites ``documents`` and
    leaves ``fx_rates`` untouched (flagging a missing target rate). Commits and
    returns the row count.
    """
    from_code = normalize_currency_code(from_code_raw)
    if from_code is None:
        return NormalizeResult(status="invalid_source")
    to_code = normalize_currency_code(to_code_raw)
    if to_code is None:
        return NormalizeResult(status="invalid_target")
    if from_code == to_code:
        return NormalizeResult(status="same_code", from_code=from_code, to_code=to_code)

    params = {"from_code": from_code, "to_code": to_code}
    counts: dict[str, int] = {}

    async def _run(sql: str) -> int:
        # `AsyncSession.execute` is typed as returning `Result`, which has no
        # `rowcount`; every statement here is DML, so the runtime object is a
        # `CursorResult`. Narrowed rather than ignored so the attribute stays checked.
        result = cast(CursorResult[Any], await session.execute(text(sql), params))
        return result.rowcount

    counts["documents"] = await _run(
        "UPDATE documents SET currency = :to_code WHERE currency = :from_code"
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
