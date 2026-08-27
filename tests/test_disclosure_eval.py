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


def test_mentions_count_rejects_a_comma_grouped_amount() -> None:
    """A false pass found by execution: the leading digit of a thousands-
    grouped amount is not a disclosed count."""
    assert not mentions_count("You spent EUR 2,500.00 in total.", 2)
    assert not mentions_count("EUR 1,234.00 total", 1)


def test_mentions_count_rejects_a_decimal_amount_digit() -> None:
    """Same false-pass class as the comma case, for a plain decimal amount:
    neither digit of '360.00' is a standalone count of 3 or 6."""
    assert not mentions_count("You spent EUR 360.00 in total.", 3)
    assert not mentions_count("You spent EUR 360.00 in total.", 6)


def test_mentions_count_rejects_an_ordinal_suffix() -> None:
    """'2nd' is an ordinal, not a count of 2 — another false pass found by
    execution against realistic prose."""
    assert not mentions_count("This is the 2nd invoice this month.", 2)
    assert not mentions_count("the 3rd and 2nd bills", 2)


def test_mentions_count_accepts_a_sentence_final_count() -> None:
    """A count at the very end of a sentence, followed only by punctuation,
    must still register — the comma/decimal guard must not overreach."""
    assert mentions_count("no readable amount: 2.", 2)


def test_mentions_count_accepts_a_genuine_count_alongside_an_ordinal() -> None:
    """An ordinal elsewhere in the sentence must not blind the scorer to a
    real, separate count."""
    assert mentions_count("2 bills, plus the 2nd was flagged", 2)


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


def test_score_passes_ordinary_prose_mentioning_documents_in_a_control() -> None:
    """'Some documents' is ordinary descriptive English, not a hedge. A
    correct, complete control answer must not fail just for using it."""
    verdict = score(
        "complete",
        {"matched": 3, "included": 3, "excluded": {}, "needs_review": 0},
        "Some documents in this set are utility bills and some are insurance "
        "statements; all 3 are fully accounted for.",
        expect_disclosure=False,
    )
    assert verdict.passed
    assert verdict.unexpected == ()


def test_score_tolerates_a_malformed_excluded_block() -> None:
    """A coverage block that violates its own shape contract (excluded as a
    list, not a dict) must not crash the scorer -- it degrades to treating
    the block as having nothing to disclose from `excluded`."""
    verdict = score(
        "malformed",
        {"matched": 2, "included": 2, "excluded": ["no_amount"], "needs_review": 1},
        "You spent EUR 50.00 across 2 bills.",
        expect_disclosure=True,
    )
    assert verdict.missing == ("needs_review=1",)
