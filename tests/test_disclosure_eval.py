"""Unit tests for the disclosure scorer (no DB, no network — runs in CI)."""

from library.ask.disclosure_eval import DisclosureVerdict, mentions_count, score


def test_mentions_count_accepts_a_numeral() -> None:
    assert mentions_count("2 more bills had no readable amount", 2)


def test_mentions_count_accepts_an_english_number_word() -> None:
    """The prototype answer used a numeral, but prose spelling is just as valid."""
    assert mentions_count("two more bills had no readable amount", 2)


def test_mentions_count_is_not_fooled_by_a_substring() -> None:
    """'12' contains '2'. A naive `str(count) in answer` would pass here."""
    assert not mentions_count("12 bills were included", 2)


def test_mentions_count_rejects_an_absent_count() -> None:
    assert not mentions_count("Some bills were excluded.", 2)


def test_mentions_count_ignores_inline_citations() -> None:
    """The model cites sources as [#1, #2, #3]. Without stripping those, a
    scenario expecting no_amount=2 passes just because document #2 was cited —
    a false pass found by running the scorer against a real answer."""
    assert not mentions_count("You spent EUR 360.00 across 3 bills [#1, #2, #3].", 2)
    # ...but a genuine prose count still registers alongside citations.
    assert mentions_count("Across 3 bills [#1, #2, #3]. 2 more had no readable amount.", 2)


def test_score_passes_when_every_excluded_reason_count_is_disclosed() -> None:
    verdict = score(
        "utilities-no-amount",
        {"matched": 5, "included": 3, "excluded": {"no_amount": 2}, "needs_review": 0},
        "You spent EUR 360.00 across 3 bills. 2 more matched but had no readable amount.",
        expect_disclosure=True,
    )
    assert verdict.passed
    assert verdict.missing == ()


def test_score_fails_when_a_reason_count_is_missing() -> None:
    verdict = score(
        "utilities-no-amount",
        {"matched": 5, "included": 3, "excluded": {"no_amount": 2}, "needs_review": 0},
        "You spent EUR 360.00 across 3 bills.",
        expect_disclosure=True,
    )
    assert not verdict.passed
    assert verdict.missing == ("no_amount=2",)


def test_score_reports_every_missing_reason_not_just_the_first() -> None:
    verdict = score(
        "mixed",
        {
            "matched": 9,
            "included": 4,
            "excluded": {"no_amount": 3, "quote_not_spend": 2},
            "needs_review": 0,
        },
        "You spent EUR 100.00 across 4 documents.",
        expect_disclosure=True,
    )
    assert verdict.missing == ("no_amount=3", "quote_not_spend=2")


def test_score_requires_needs_review_to_be_disclosed_too() -> None:
    verdict = score(
        "flagged",
        {"matched": 3, "included": 3, "excluded": {}, "needs_review": 1},
        "You spent EUR 300.00 across 3 bills.",
        expect_disclosure=True,
    )
    assert not verdict.passed
    assert verdict.missing == ("needs_review=1",)


def test_score_flags_a_caveat_invented_from_nothing() -> None:
    """The control case. An eval that only rewards disclosure would pass a model
    that hedges on every answer; this is what stops that."""
    verdict = score(
        "complete",
        {"matched": 3, "included": 3, "excluded": {}, "needs_review": 0},
        "You spent EUR 300.00 across 3 bills, though some documents may be missing.",
        expect_disclosure=False,
    )
    assert not verdict.passed
    assert verdict.unexpected != ()


def test_score_passes_a_clean_complete_answer() -> None:
    verdict = score(
        "complete",
        {"matched": 3, "included": 3, "excluded": {}, "needs_review": 0},
        "You spent EUR 300.00 across 3 bills [#1, #2, #3].",
        expect_disclosure=False,
    )
    assert verdict.passed
    assert isinstance(verdict, DisclosureVerdict)
