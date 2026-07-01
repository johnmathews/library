"""Unit tests for series statistics (pure helpers, no DB)."""

from datetime import date
from decimal import Decimal

import pytest

from library.series import (
    Distribution,
    SeriesSignature,
    _Member,
    classify_cadence,
    compare_reference,
    compute_trend,
    decode_series_id,
    derive_signature,
    distribution,
    encode_series_id,
    odd_ones_out,
    year_over_year,
)


def _member(
    document_id: int,
    *,
    sender_id: int | None = 1,
    kind_id: int | None = 2,
    currency: str | None = "EUR",
    sender: str | None = "Sender",
    kind: str | None = "utility-bill",
) -> _Member:
    """A minimal ``_Member`` for signature/odd-one-out tests (identity only)."""
    return _Member(
        document_id=document_id,
        sender=sender,
        kind=kind,
        document_date=date(2025, 1, document_id % 28 + 1),
        amount=Decimal("100.00"),
        currency=currency,
        sender_id=sender_id,
        kind_id=kind_id,
        title=f"doc-{document_id}",
    )


def test_encode_series_id_with_currency() -> None:
    assert encode_series_id(7, 2, "EUR") == "7-2-EUR"


def test_encode_series_id_null_currency() -> None:
    assert encode_series_id(7, 2, None) == "7-2-none"


def test_decode_series_id_roundtrip() -> None:
    assert decode_series_id("7-2-EUR") == (7, 2, "EUR")
    assert decode_series_id("7-2-none") == (7, 2, None)


def test_decode_series_id_rejects_malformed() -> None:
    for bad in ("", "7-2", "a-2-EUR", "7-2-EUR-extra", "not-a-series"):
        with pytest.raises(ValueError):
            decode_series_id(bad)


def test_distribution_basic() -> None:
    d = distribution([Decimal("100"), Decimal("150"), Decimal("200")])
    assert d == Distribution(
        count=3,
        mean=Decimal("150.00"),
        median=Decimal("150.00"),
        stdev=Decimal("50.00"),
        minimum=Decimal("100.00"),
        maximum=Decimal("200.00"),
    )


def test_distribution_single_value_zero_stdev() -> None:
    d = distribution([Decimal("42")])
    assert d.count == 1
    assert d.stdev == Decimal("0.00")
    assert d.median == Decimal("42.00")


def test_classify_cadence_monthly() -> None:
    dates = [date(2025, 1, 5), date(2025, 2, 4), date(2025, 3, 6), date(2025, 4, 5)]
    assert classify_cadence(dates) == "monthly"


def test_classify_cadence_quarterly() -> None:
    dates = [date(2025, 1, 1), date(2025, 4, 1), date(2025, 7, 1)]
    assert classify_cadence(dates) == "quarterly"


def test_classify_cadence_yearly() -> None:
    dates = [date(2023, 6, 1), date(2024, 6, 1), date(2025, 6, 1)]
    assert classify_cadence(dates) == "yearly"


def test_classify_cadence_irregular() -> None:
    dates = [date(2025, 1, 1), date(2025, 1, 10), date(2025, 9, 1)]
    assert classify_cadence(dates) == "irregular"


def test_classify_cadence_too_few() -> None:
    assert classify_cadence([date(2025, 1, 1)]) == "irregular"


def _dist(values: list[str]) -> Distribution:
    return distribution([Decimal(v) for v in values])


def test_compare_reference_higher() -> None:
    dist = _dist(["100", "100", "100"])  # median 100, stdev 0
    cmp = compare_reference(Decimal("130"), dist, typical_pct=0.10)
    assert cmp.verdict == "higher"
    assert cmp.delta == Decimal("30.00")
    assert cmp.z_score is None  # stdev 0
    assert round(cmp.vs_median_pct, 3) == 0.30


def test_compare_reference_typical_within_pct() -> None:
    dist = _dist(["100", "100", "100"])
    cmp = compare_reference(Decimal("108"), dist, typical_pct=0.10)
    assert cmp.verdict == "typical"  # 8% <= 10% band


def test_compare_reference_typical_within_stdev() -> None:
    dist = _dist(["100", "150", "200"])  # median 150, stdev 50
    cmp = compare_reference(Decimal("180"), dist, typical_pct=0.01)
    assert cmp.verdict == "typical"  # within 1 stdev even though >1% of median
    assert cmp.z_score is not None


def test_compare_reference_lower() -> None:
    dist = _dist(["100", "100", "100"])
    cmp = compare_reference(Decimal("50"), dist, typical_pct=0.10)
    assert cmp.verdict == "lower"


def test_compute_trend_rising() -> None:
    pts = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 2, 1), Decimal("120")),
        (date(2025, 3, 1), Decimal("140")),
    ]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "rising"
    assert round(trend.change_pct, 2) == 0.40


def test_compute_trend_flat() -> None:
    pts = [
        (date(2025, 1, 1), Decimal("100")),
        (date(2025, 2, 1), Decimal("101")),
        (date(2025, 3, 1), Decimal("102")),
    ]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "flat"


def test_compute_trend_none_when_single() -> None:
    assert compute_trend([(date(2025, 1, 1), Decimal("100"))], flat_pct=0.05) is None


def test_year_over_year_match() -> None:
    pts = [
        (date(2024, 3, 1), Decimal("100"), 11),
        (date(2025, 1, 1), Decimal("130"), 12),
        (date(2025, 3, 5), Decimal("150"), 13),
    ]
    yoy = year_over_year(pts, reference_date=date(2025, 3, 5), cadence="monthly")
    assert yoy is not None
    assert yoy.document_id == 11
    assert yoy.prior_value == Decimal("100.00")
    assert round(yoy.change_pct, 2) == 0.50


def test_year_over_year_no_match() -> None:
    pts = [(date(2025, 1, 1), Decimal("130"), 12), (date(2025, 3, 5), Decimal("150"), 13)]
    assert year_over_year(pts, reference_date=date(2025, 3, 5), cadence="monthly") is None


def test_compute_trend_rising_from_zero() -> None:
    """first_amount==0 must not force 'flat'; direction should be 'rising'."""
    pts = [
        (date(2025, 1, 1), Decimal("0")),
        (date(2025, 2, 1), Decimal("100")),
        (date(2025, 3, 1), Decimal("200")),
    ]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "rising"


def test_compute_trend_falling() -> None:
    """Clearly downward series returns 'falling'."""
    pts = [
        (date(2025, 1, 1), Decimal("300")),
        (date(2025, 2, 1), Decimal("200")),
        (date(2025, 3, 1), Decimal("100")),
    ]
    trend = compute_trend(pts, flat_pct=0.05)
    assert trend is not None and trend.direction == "falling"


def test_year_over_year_zero_reference() -> None:
    """ref_value==Decimal('0.00') must not be falsy-skipped; change_pct == -1.0."""
    pts = [
        (date(2024, 3, 1), Decimal("100"), 20),
        (date(2025, 3, 1), Decimal("0.00"), 21),
    ]
    yoy = year_over_year(pts, reference_date=date(2025, 3, 1), cadence="monthly")
    assert yoy is not None
    assert yoy.change_pct == -1.0


def test_derive_signature_empty_is_none() -> None:
    assert derive_signature([]) is None


def test_derive_signature_homogeneous_dominance_one() -> None:
    members = [_member(i) for i in range(1, 5)]
    sig = derive_signature(members)
    assert sig is not None
    assert (sig.sender_id, sig.kind_id, sig.currency) == (1, 2, "EUR")
    assert sig.member_count == 4
    assert sig.dominant_count == 4
    assert sig.dominance == 1.0


def test_derive_signature_mixed_picks_dominant() -> None:
    members = [
        _member(1),
        _member(2),
        _member(3),
        _member(4, sender_id=9),  # odd one out
    ]
    sig = derive_signature(members)
    assert sig is not None
    assert (sig.sender_id, sig.kind_id, sig.currency) == (1, 2, "EUR")
    assert sig.member_count == 4
    assert sig.dominant_count == 3
    assert sig.dominance == 0.75


def test_derive_signature_null_currency_bucket() -> None:
    members = [_member(i, currency=None) for i in range(1, 4)]
    sig = derive_signature(members)
    assert sig is not None
    assert sig.currency is None
    assert sig.dominance == 1.0


def test_odd_ones_out_classifies_axis() -> None:
    signature = SeriesSignature(
        sender_id=1,
        kind_id=2,
        currency="EUR",
        member_count=6,
        dominant_count=3,
        dominance=0.5,
    )
    members = [
        _member(1),  # matches -> excluded
        _member(2, sender_id=9),  # sender axis
        _member(3, kind_id=8),  # kind axis
        _member(4, currency="GBP"),  # currency axis
        _member(5, sender_id=9, kind_id=8),  # differs on both -> sender wins (first axis)
    ]
    odd = odd_ones_out(members, signature)
    by_id = {member.document_id: axis for member, axis, _reason in odd}
    assert 1 not in by_id  # the matching member is not odd
    assert by_id[2] == "sender"
    assert by_id[3] == "kind"
    assert by_id[4] == "currency"
    assert by_id[5] == "sender"


def test_odd_ones_out_reason_is_grounded_and_never_hallucinates() -> None:
    """Regression: the reason must name ONLY real sender/kind/currency values.

    An LLM previously wrote the reason and invented a sender ("De Hooge Waerder")
    that appeared in none of the documents. The reason is now deterministic, so it
    can only ever contain values actually present on the members.
    """
    # Dominant identity is Waternet (sender_id 1); the odd member is Vitens.
    members = [
        _member(1, sender="Waternet"),
        _member(2, sender="Waternet"),
        _member(3, sender="Vitens", sender_id=9),  # odd one out on sender
    ]
    signature = derive_signature(members)
    assert signature is not None
    odd = odd_ones_out(members, signature)
    assert len(odd) == 1
    member, axis, reason = odd[0]
    assert member.document_id == 3
    assert axis == "sender"
    # Grounded: names the odd member's REAL sender and the REAL usual sender.
    assert reason == "This document is from Vitens, unlike the rest of the series (Waternet)."

    # Anti-hallucination guarantee: every capitalised word in the reason is a real
    # value from the members (or a sentence-start word), never an invented name.
    real_values = {"Waternet", "Vitens"}
    sentence_starts = {"This"}
    proper_nouns = {
        word.strip(".,()")
        for word in reason.split()
        if word[:1].isupper() and word.strip(".,()") not in sentence_starts
    }
    assert proper_nouns <= real_values


def test_odd_ones_out_reason_handles_missing_sender() -> None:
    """A member with no sender must not provoke an invented name."""
    members = [
        _member(1, sender="Waternet"),
        _member(2, sender="Waternet"),
        _member(3, sender=None, sender_id=None),  # no sender set
    ]
    signature = derive_signature(members)
    assert signature is not None
    odd = odd_ones_out(members, signature)
    assert len(odd) == 1
    _member_row, axis, reason = odd[0]
    assert axis == "sender"
    assert reason == "This document has no sender set, unlike the rest of the series (Waternet)."
