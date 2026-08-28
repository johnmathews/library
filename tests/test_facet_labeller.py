"""Prompt construction and closed-set parsing. No network, no database."""

import json

from library.facets.labeller import (
    DocumentFields,
    build_labelling_prompt,
    parse_label_response,
)
from library.facets.vocabulary import VocabularyFacet, VocabularyValue

VOCAB: tuple[VocabularyFacet, ...] = (
    VocabularyFacet(
        id=1,
        key="category",
        label="Category",
        ordinal=0,
        values=(
            VocabularyValue(
                id=10, key="software", label="Software", parent_id=None, aliases=("saas",)
            ),
            VocabularyValue(id=11, key="energy", label="Energy", parent_id=None, aliases=()),
        ),
    ),
    VocabularyFacet(
        id=2,
        key="scope",
        label="Scope",
        ordinal=1,
        values=(
            VocabularyValue(id=20, key="business", label="Business", parent_id=None, aliases=()),
        ),
    ),
)

FIELDS = DocumentFields(
    title="Monthly plan invoice",
    summary="A recurring charge for a hosted tool.",
    sender="Vendor",
    kind="invoice",
    amount="48.00",
    currency="EUR",
    excerpt="Plan renewal. Amount due 48.00 EUR.",
)


def test_prompt_lists_every_allowed_value_and_its_aliases() -> None:
    prompt = build_labelling_prompt(VOCAB, FIELDS)
    assert "category" in prompt and "software" in prompt and "energy" in prompt
    assert "saas" in prompt
    assert "scope" in prompt and "business" in prompt
    assert "Monthly plan invoice" in prompt


def test_a_value_inside_the_vocabulary_is_accepted() -> None:
    payload = json.dumps(
        {
            "labels": [
                {
                    "facet": "category",
                    "value": "software",
                    "confidence": 0.9,
                    "reason": "a hosted tool subscription",
                }
            ]
        }
    )
    proposals = parse_label_response(payload, VOCAB)
    assert len(proposals) == 1
    assert proposals[0].facet_key == "category"
    assert proposals[0].value_key == "software"
    assert proposals[0].suggested_label is None


def test_a_value_outside_the_vocabulary_becomes_unknown_plus_a_suggestion() -> None:
    """The closed-set guarantee. The model cannot widen the vocabulary by naming."""
    payload = json.dumps(
        {
            "labels": [
                {
                    "facet": "category",
                    "value": "telecoms",
                    "confidence": 0.95,
                    "reason": "a phone bill",
                }
            ]
        }
    )
    proposals = parse_label_response(payload, VOCAB)
    assert proposals[0].value_key is None
    assert proposals[0].suggested_label == "telecoms"


def test_an_explicit_unknown_carries_its_suggestion() -> None:
    payload = json.dumps(
        {
            "labels": [
                {
                    "facet": "category",
                    "value": None,
                    "confidence": 0.2,
                    "reason": "cannot tell",
                    "suggest": "telecoms",
                }
            ]
        }
    )
    proposals = parse_label_response(payload, VOCAB)
    assert proposals[0].value_key is None
    assert proposals[0].suggested_label == "telecoms"


def test_an_unknown_facet_is_discarded_entirely() -> None:
    payload = json.dumps(
        {"labels": [{"facet": "not_a_facet", "value": "x", "confidence": 1.0, "reason": "no"}]}
    )
    assert parse_label_response(payload, VOCAB) == []


def test_an_alias_resolves_to_its_value() -> None:
    payload = json.dumps(
        {"labels": [{"facet": "category", "value": "saas", "confidence": 0.8, "reason": "alias"}]}
    )
    proposals = parse_label_response(payload, VOCAB)
    assert proposals[0].value_key == "software"


def test_malformed_json_yields_no_proposals_rather_than_raising() -> None:
    assert parse_label_response("not json at all", VOCAB) == []


def test_confidence_is_clamped_into_zero_one() -> None:
    payload = json.dumps(
        {"labels": [{"facet": "scope", "value": "business", "confidence": 4.2, "reason": "x"}]}
    )
    assert parse_label_response(payload, VOCAB)[0].confidence == 1.0


def test_a_list_valued_facet_does_not_raise() -> None:
    """The model can return any JSON shape. A whole labelling run must not die
    because one entry's `facet` was not a string."""
    payload = json.dumps(
        {"labels": [{"facet": ["nested"], "value": "software", "confidence": 1.0, "reason": "r"}]}
    )
    assert parse_label_response(payload, VOCAB) == []


def test_an_object_valued_facet_does_not_raise() -> None:
    payload = json.dumps(
        {"labels": [{"facet": {"a": 1}, "value": "software", "confidence": 1.0, "reason": "r"}]}
    )
    assert parse_label_response(payload, VOCAB) == []


def test_a_non_string_reason_or_suggestion_is_discarded_not_stringified() -> None:
    payload = json.dumps(
        {
            "labels": [
                {
                    "facet": "category",
                    "value": "software",
                    "confidence": 0.9,
                    "reason": ["not", "a", "string"],
                    "suggest": {"nope": 1},
                }
            ]
        }
    )
    proposal = parse_label_response(payload, VOCAB)[0]
    assert proposal.reason == ""
    assert proposal.suggested_label is None
