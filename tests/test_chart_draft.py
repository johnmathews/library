"""Drafting a chart rule from a question, against the closed vocabulary.

The model is stubbed throughout: these test the boundary, not the model. The
vocabulary comes from the `facets` fixture (category / scope / cost_type).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from library.charts.draft import (
    BLANK_TERM,
    DraftedRule,
    DraftError,
    build_draft_prompt,
    draft_rule,
    filter_drafted_rule,
)
from library.charts.query import SENDER_SPLIT
from library.charts.rule import rule_predicate
from library.config import Settings
from library.facets.vocabulary import add_alias, load_vocabulary
from tests.conftest import StubAnthropic


@pytest.mark.asyncio
async def test_a_drafted_value_outside_the_vocabulary_is_dropped_and_reported(
    session: AsyncSession, facets: dict[str, tuple[str, ...]], stub_anthropic: StubAnthropic
) -> None:
    """§7.5: the vocabulary is never auto-extended. A rule that silently
    kept an invented value would resolve to zero rows and read as "you
    spent nothing on that", which is worse than an error."""
    stub_anthropic.returns(
        rule={"all": [{"facet": "category", "op": "in", "values": ["services", "cryptocurrency"]}]},
        proposed_split="scope",
    )
    result = await draft_rule(session, "money I spend on services")
    assert result.rule.all[0].values == ["services"]
    assert result.unknown_terms == ["cryptocurrency"]
    assert result.proposed_split == "scope"


@pytest.mark.asyncio
async def test_a_drafted_facet_outside_the_vocabulary_drops_the_whole_clause(
    session: AsyncSession, facets: dict[str, tuple[str, ...]], stub_anthropic: StubAnthropic
) -> None:
    stub_anthropic.returns(
        rule={"all": [{"facet": "vibe", "op": "in", "values": ["good"]}]},
        proposed_split=None,
    )
    result = await draft_rule(session, "money I spend on good vibes")
    assert result.rule.all == []
    assert "vibe" in result.unknown_terms


@pytest.mark.asyncio
async def test_a_clause_left_with_no_values_is_dropped_not_left_empty(
    session: AsyncSession, facets: dict[str, tuple[str, ...]], stub_anthropic: StubAnthropic
) -> None:
    """An empty `values` list raises RuleError in Task 4, so leaving one
    behind turns a drafting miss into a 500 at query time."""
    stub_anthropic.returns(
        rule={"all": [{"facet": "category", "op": "in", "values": ["nonsense"]}]},
        proposed_split=None,
    )
    result = await draft_rule(session, "money I spend on nonsense")
    assert result.rule.all == []
    # The drop is only safe because the rule it produces is translatable.
    assert rule_predicate(result.rule) == ("TRUE", {})


@pytest.mark.asyncio
async def test_the_backend_uses_messages_parse_not_messages_create(
    session: AsyncSession, facets: dict[str, tuple[str, ...]], stub_anthropic: StubAnthropic
) -> None:
    """Asserted on the call shape, not on the output. #108 and #116 both
    passed every behavioural test while using the wrong call."""
    stub_anthropic.returns(rule={"all": []}, proposed_split=None)
    await draft_rule(session, "everything")
    assert stub_anthropic.used == "parse", (
        "the API backend must use messages.parse, not messages.create"
    )
    # The sequence, not just the last call: `used` is overwritten, so a
    # create-then-parse implementation would satisfy the assertion above.
    assert [method for method, _ in stub_anthropic.calls] == ["parse"]
    kwargs = stub_anthropic.calls[0][1]
    assert kwargs["output_format"] is DraftedRule
    # Pins the stated non-negotiable: no new `*_model` setting. A mutant adding
    # `settings.chart_draft_model` defaulted to an already-priced model passes
    # every other test in this file, and the MODEL_PRICING_USD_PER_MTOK boot
    # check stays quiet because the model itself is priced.
    assert kwargs["model"] == Settings().extraction_model


@pytest.mark.asyncio
async def test_an_alias_of_a_known_value_is_not_reported_unknown(
    session: AsyncSession, facets: dict[str, tuple[str, ...]], stub_anthropic: StubAnthropic
) -> None:
    """A legitimate synonym must resolve to its canonical key, or every
    question phrased in the archive's own alias vocabulary reads as
    unexpressible."""
    await add_alias(session, "category", "software", "saas")
    await session.commit()
    stub_anthropic.returns(
        rule={"all": [{"facet": "category", "op": "in", "values": ["SaaS"]}]},
        proposed_split=None,
    )
    result = await draft_rule(session, "money I spend on tools")
    assert result.rule.all[0].values == ["software"]
    assert result.unknown_terms == []


@pytest.mark.asyncio
async def test_a_negated_clause_keeps_its_operator(
    session: AsyncSession, facets: dict[str, tuple[str, ...]], stub_anthropic: StubAnthropic
) -> None:
    stub_anthropic.returns(
        rule={"all": [{"facet": "scope", "op": "not_in", "values": ["business"]}]},
        proposed_split=SENDER_SPLIT,
    )
    result = await draft_rule(session, "money that is not business spending")
    assert result.rule.all[0].op == "not_in"
    assert result.proposed_split == SENDER_SPLIT


@pytest.mark.asyncio
async def test_the_subscription_backend_fails_loudly(
    session: AsyncSession, facets: dict[str, tuple[str, ...]], stub_anthropic: StubAnthropic
) -> None:
    """Only the API backend can use `messages.parse`. The alternative to an
    error is an empty rule, and an empty rule means *all spending* — so a
    silent failure would answer a narrow question with the whole archive."""
    stub_anthropic.returns(rule={"all": []}, proposed_split=None)
    with pytest.raises(DraftError):
        await draft_rule(session, "everything", backend="subscription")
    assert stub_anthropic.used is None


@pytest.mark.asyncio
async def test_a_missing_api_key_raises_rather_than_returning_an_empty_rule(
    session: AsyncSession, facets: dict[str, tuple[str, ...]]
) -> None:
    with pytest.raises(DraftError):
        await draft_rule(session, "everything", settings=Settings(anthropic_api_key=None))


@pytest.mark.asyncio
async def test_the_prompt_carries_the_whole_vocabulary_and_its_aliases(
    session: AsyncSession, facets: dict[str, tuple[str, ...]]
) -> None:
    await add_alias(session, "cost_type", "subscription", "recurring")
    await session.commit()
    prompt = build_draft_prompt(await load_vocabulary(session), "money I spend on services")
    for facet_key, value_keys in facets.items():
        assert facet_key in prompt
        for value_key in value_keys:
            assert value_key in prompt
    assert "recurring" in prompt
    assert "money I spend on services" in prompt


# --- The filter is pure, so the closed-set guarantee is tested without a model.


@pytest.mark.asyncio
async def test_an_unknown_operator_drops_the_clause_rather_than_guessing(
    session: AsyncSession, facets: dict[str, tuple[str, ...]]
) -> None:
    """Reading an unrecognised operator as "in" would invert an exclusion into
    an inclusion, which silently moves money *into* the chart."""
    vocabulary = await load_vocabulary(session)
    drafted = DraftedRule.model_validate(
        {"all": [{"facet": "scope", "op": "excludes", "values": ["business"]}], "split": None}
    )
    result = filter_drafted_rule(drafted, vocabulary)
    assert result.rule.all == []
    assert "excludes" in result.unknown_terms


@pytest.mark.asyncio
async def test_an_unknown_split_is_reported_and_not_proposed(
    session: AsyncSession, facets: dict[str, tuple[str, ...]]
) -> None:
    vocabulary = await load_vocabulary(session)
    drafted = DraftedRule.model_validate({"all": [], "split": "phase_of_the_moon"})
    result = filter_drafted_rule(drafted, vocabulary)
    assert result.proposed_split is None
    assert result.unknown_terms == ["phase_of_the_moon"]


@pytest.mark.asyncio
async def test_a_value_from_the_wrong_facet_is_unknown_on_this_one(
    session: AsyncSession, facets: dict[str, tuple[str, ...]]
) -> None:
    """`business` is a real value — of `scope`, not of `category`. Values are
    resolved within their own facet or the closed set is only half closed."""
    vocabulary = await load_vocabulary(session)
    drafted = DraftedRule.model_validate(
        {"all": [{"facet": "category", "op": "in", "values": ["business"]}], "split": None}
    )
    result = filter_drafted_rule(drafted, vocabulary)
    assert result.rule.all == []
    assert result.unknown_terms == ["business"]


@pytest.mark.asyncio
async def test_a_repeated_value_appears_once(
    session: AsyncSession, facets: dict[str, tuple[str, ...]]
) -> None:
    vocabulary = await load_vocabulary(session)
    drafted = DraftedRule.model_validate(
        {
            "all": [{"facet": "category", "op": "in", "values": ["services", "Services"]}],
            "split": None,
        }
    )
    result = filter_drafted_rule(drafted, vocabulary)
    assert result.rule.all[0].values == ["services"]


@pytest.mark.asyncio
async def test_a_blank_term_is_still_reported(
    session: AsyncSession, facets: dict[str, tuple[str, ...]]
) -> None:
    """A blank value is dropped by the lookup like any other unknown, but has no
    text to report. Without a placeholder the clause vanishes with an empty
    `unknown_terms`, so the rule quietly widens to "all spending" and the caller
    has nothing to branch on — the silent narrowing §12 forbids."""
    vocabulary = await load_vocabulary(session)
    drafted = DraftedRule.model_validate(
        {"all": [{"facet": "category", "op": "in", "values": ["   "]}], "split": None}
    )
    result = filter_drafted_rule(drafted, vocabulary)
    assert result.rule.all == []
    assert result.unknown_terms == [BLANK_TERM]


@pytest.mark.asyncio
async def test_a_blank_facet_is_still_reported(
    session: AsyncSession, facets: dict[str, tuple[str, ...]]
) -> None:
    vocabulary = await load_vocabulary(session)
    drafted = DraftedRule.model_validate(
        {"all": [{"facet": "  ", "op": "in", "values": ["services"]}], "split": None}
    )
    result = filter_drafted_rule(drafted, vocabulary)
    assert result.rule.all == []
    assert result.unknown_terms == [BLANK_TERM]
