"""Unit tests for the disclosure scorer (no DB, no network — runs in CI)."""

import inspect

from library.ask.disclosure_eval import DisclosureVerdict, mentions_count, score
from library.ask.disclosure_scenarios import Scenario


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


def test_mentions_count_ignores_an_ordered_list_items_own_number() -> None:
    """A markdown list's own item numbers are not a disclosed count. Found by
    executing the scorer against a realistic, fully non-disclosing numbered
    list — the previous regex let the trailing '5.' item marker satisfy
    `mentions_count(ans, 5)` with no count ever stated in prose."""
    assert not mentions_count("1. Receipt 55 …\n…\n5. Receipt 51 …", 5)
    assert not mentions_count("Total EUR 360.00:\n1. Jan…\n2. Apr…\n3. Jul…", 2)


def test_mentions_count_still_registers_a_genuine_count_in_a_list_bearing_answer() -> None:
    """The list-marker strip must not blind the scorer to a real count that
    happens to share the answer with a numbered list."""
    assert mentions_count(
        "1. Receipt A\n2. Receipt B\n\n2 more documents had no readable amount.", 2
    )


def test_mentions_count_list_marker_strip_still_accepts_known_safe_strings() -> None:
    """Guards the review verified stay safe: none of these are ordered-list
    markers and must not be affected by the new strip."""
    assert not mentions_count("The amount was 12.50 today.", 5)
    assert not mentions_count("Dated 2025-01-05.", 5)
    assert not mentions_count("Receipt 55 was issued.", 5)
    assert not mentions_count("Receipt 50 was issued.", 5)
    assert not mentions_count("You spent EUR 10,175.00 in total.", 5)


def test_score_fails_a_scenario_expecting_disclosure_but_with_nothing_to_disclose() -> None:
    """FIX 1: a coverage block with excluded={} and needs_review=0 must FAIL
    an `expect_disclosure=True` scenario, not vacuously pass it — the check
    loop having nothing to iterate over means the scenario exercised nothing,
    which is a defect in the scenario, not evidence of correct disclosure."""
    verdict = score(
        "x",
        {"matched": 5, "included": 5, "excluded": {}, "needs_review": 0},
        "You spent EUR 360.00.",
        expect_disclosure=True,
    )
    assert not verdict.passed
    assert verdict.missing != ()


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


# ----------------------------------------------------------------------------
# Scenario data (library.ask.disclosure_scenarios) — pure, so testable in CI
# even though the live command that drives them needs credentials it doesn't.
# ----------------------------------------------------------------------------


def test_scenarios_cover_both_polarities() -> None:
    """At least one control scenario, or the eval only rewards hedging."""
    from library.ask.disclosure_scenarios import SCENARIOS

    assert any(s.expect_disclosure for s in SCENARIOS)
    assert any(not s.expect_disclosure for s in SCENARIOS)


def test_scenario_names_are_unique() -> None:
    from library.ask.disclosure_scenarios import SCENARIOS

    names = [s.name for s in SCENARIOS]
    assert len(names) == len(set(names))


def test_every_scenario_seeds_documents_and_asks_something() -> None:
    from library.ask.disclosure_scenarios import SCENARIOS

    for scenario in SCENARIOS:
        assert scenario.docs, f"{scenario.name} seeds nothing"
        assert scenario.question.strip(), f"{scenario.name} asks nothing"


def _scenario(name: str) -> Scenario:
    from library.ask.disclosure_scenarios import SCENARIOS

    for scenario in SCENARIOS:
        if scenario.name == name:
            return scenario
    raise AssertionError(f"no scenario named {name!r}")


def test_utilities_no_amount_seeds_the_split_the_scenario_name_promises() -> None:
    """A test that just checks 'docs is non-empty' pins nothing about THIS
    scenario's shape; the split (3 with an amount, 2 without) is exactly what
    makes `no_amount=2` the expected disclosure."""
    scenario = _scenario("utilities-no-amount")
    with_amount = [d for d in scenario.docs if d.amount is not None]
    without_amount = [d for d in scenario.docs if d.amount is None]
    assert len(with_amount) == 3
    assert len(without_amount) == 2
    # One sender, one kind — otherwise a sender/kind filter could itself
    # change which documents are 'matched', muddying what's being tested.
    assert {d.sender_name for d in scenario.docs} == {"Aurora Utilities (disclosure-eval fixture)"}
    assert {d.kind_slug for d in scenario.docs} == {"utility-bill"}


def test_spend_excludes_quotes_seeds_amount_bearing_invoices_and_quotes() -> None:
    """`quote_not_spend` only fires for quotes that themselves carry an
    amount — an amountless quote would be counted under `no_amount` instead,
    silently testing the wrong exclusion reason."""
    scenario = _scenario("spend-excludes-quotes")
    invoices = [d for d in scenario.docs if d.kind_slug == "invoice"]
    quotes = [d for d in scenario.docs if d.kind_slug == "quote"]
    assert len(invoices) == 2
    assert len(quotes) == 3
    assert all(d.amount is not None for d in scenario.docs)
    assert {d.sender_name for d in scenario.docs} == {"Ledger Movers (disclosure-eval fixture)"}


def test_flagged_amounts_has_exactly_two_needs_review_documents() -> None:
    """Pinned at two, not one: `NUMBER_WORDS[1]` is "one", so a
    `needs_review=1` scenario is satisfied by any answer containing that
    common English word for reasons unrelated to disclosure. Two removes the
    collision (see the scenario's own docstring in disclosure_scenarios.py)."""
    from library.models import ReviewStatus

    scenario = _scenario("flagged-amounts")
    flagged = [d for d in scenario.docs if d.review_status is ReviewStatus.NEEDS_REVIEW]
    assert len(flagged) == 2
    # Every document must carry an amount: `needs_review` is a count within
    # `included`, so an amountless flagged document would land in `no_amount`
    # instead and never contribute to the count this scenario is testing.
    assert all(d.amount is not None for d in scenario.docs)


def test_list_truncation_seeds_more_documents_than_the_real_query_limit() -> None:
    """Pinned against the actual production default, not a copy of it — if
    `structured_query.query_documents`'s `limit` default ever changes, this
    scenario must still seed enough documents to overflow it."""
    from library.structured_query import query_documents

    real_default_limit = inspect.signature(query_documents).parameters["limit"].default
    scenario = _scenario("list-truncation")
    assert len(scenario.docs) > real_default_limit
    assert all(d.amount is not None for d in scenario.docs), (
        "an amountless document here would also be dropped for no_amount, "
        "muddying which reason the truncation is attributed to"
    )


def test_series_other_currency_seeds_a_dominant_and_a_minority_currency_bucket() -> None:
    """The dominant currency bucket must meet `series_min_documents` (else the
    series is 'insufficient data' rather than a real comparison), and the
    minority bucket is what `other_currency` should report as dropped."""
    from library.config import get_settings

    scenario = _scenario("series-other-currency")
    by_currency: dict[str | None, int] = {}
    for doc in scenario.docs:
        by_currency[doc.currency] = by_currency.get(doc.currency, 0) + 1
    assert sorted(by_currency.values(), reverse=True) == [3, 2]
    dominant_count = max(by_currency.values())
    assert dominant_count >= get_settings().series_min_documents
    # One sender, one kind: otherwise the currency split could be attributed
    # to `other_series_group` instead of `other_currency`.
    assert len({d.sender_name for d in scenario.docs}) == 1
    assert len({d.kind_slug for d in scenario.docs}) == 1


def test_complete_no_gaps_is_a_genuine_control_with_nothing_to_disclose() -> None:
    """A control scenario that itself has a gap (a missing amount, a flagged
    document, a second currency) would make `expect_disclosure=False` wrong,
    not the model. This pins that the control really has nothing to hide."""
    from library.models import ReviewStatus

    scenario = _scenario("complete-no-gaps")
    assert scenario.expect_disclosure is False
    assert all(d.amount is not None for d in scenario.docs)
    assert all(d.review_status is not ReviewStatus.NEEDS_REVIEW for d in scenario.docs)
    assert len({d.currency for d in scenario.docs}) == 1
    assert len({d.kind_slug for d in scenario.docs}) == 1
    assert len({d.sender_name for d in scenario.docs}) == 1
