"""Prompt construction and closed-set parsing. No network, no database."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from library.config import Settings
from library.facets.labeller import (
    MAX_SUGGESTED_LABEL_CHARS,
    DocumentFields,
    LabelParseError,
    LabelResponse,
    build_labelling_prompt,
    label_document,
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


# A `vehicle` value's key is always plain ASCII (the `Key` contract enforced at
# every write path is `[a-z0-9_-]+`), but its display label and aliases are
# free text and can be genuinely non-ASCII, mixed case, or both — e.g. a real
# "Škoda" vehicle value. The model echoes back whatever casing it likes
# ("Skoda", "SKODA", "škoda"...), so matching must casefold rather than
# compare verbatim. Casefolding is case-insensitive, not accent-insensitive:
# it does NOT fold diacritics (`"Skoda".casefold() != "Škoda".casefold()`),
# so an unaccented "Skoda" still needs its own alias distinct from the
# accented "Škoda" one — see `test_casefold_does_not_fold_diacritics` below.
#
# The key below, `koda`, is exactly what `derive_value_key`
# (`src/library/api/facets.py`, the only function that manufactures a
# vehicle/property/person key) would actually produce from the label
# "Škoda": it lower-cases then drops every character outside `[a-z0-9_-]`
# rather than transliterating it, so the leading `š` is deleted rather than
# replaced (confirmed by running `derive_value_key("Škoda")` directly, which
# returns `"koda"`, not `"skoda"`). Neither the key nor a casefold of it
# reads as the marque — only the alias does.
VEHICLE_VOCAB: tuple[VocabularyFacet, ...] = (
    VocabularyFacet(
        id=1,
        key="vehicle",
        label="Vehicle",
        ordinal=0,
        values=(
            VocabularyValue(id=30, key="koda", label="Škoda", parent_id=None, aliases=("Škoda",)),
        ),
    ),
)


@pytest.mark.parametrize("raw_value", ["Škoda", "ŠKODA", "škoda"])
def test_an_alias_resolves_regardless_of_case_including_non_ascii_letters(raw_value: str) -> None:
    """`Škoda`, `ŠKODA` and `škoda` — three casings of the SAME accented
    letters — must all resolve via the alias stored as `Škoda`, without
    needing a hand-enumerated alias per casing variant. This does not
    exercise diacritic folding: every variant here carries the accent."""
    payload = json.dumps(
        {"labels": [{"facet": "vehicle", "value": raw_value, "confidence": 0.9, "reason": "x"}]}
    )
    proposals = parse_label_response(payload, VEHICLE_VOCAB)
    assert proposals[0].value_key == "koda"


def test_a_value_key_differing_only_in_case_resolves() -> None:
    """The model naming the value key itself, but capitalised, must still match."""
    payload = json.dumps(
        {"labels": [{"facet": "vehicle", "value": "Koda", "confidence": 0.9, "reason": "x"}]}
    )
    proposals = parse_label_response(payload, VEHICLE_VOCAB)
    assert proposals[0].value_key == "koda"


def test_casefold_does_not_fold_diacritics() -> None:
    """Documents a real boundary, not a bug: `str.casefold()` folds case, not
    accents. `VEHICLE_VOCAB`'s only alias for `koda` is the accented
    `Škoda`; the model emitting the unaccented `Skoda` matches neither that
    alias (`"skoda" != "škoda"` once both are casefolded) nor the key
    (`"skoda" != "koda"`). It must fall through to `unknown` plus a
    suggestion, exactly like any other out-of-vocabulary value — the
    closed-set guarantee holds either way, it just does not widen the match."""
    payload = json.dumps(
        {"labels": [{"facet": "vehicle", "value": "Skoda", "confidence": 0.9, "reason": "x"}]}
    )
    proposals = parse_label_response(payload, VEHICLE_VOCAB)
    assert proposals[0].value_key is None
    assert proposals[0].suggested_label == "Skoda"


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


def test_an_over_long_suggestion_is_clamped_to_the_column_width() -> None:
    """``facet_value_suggestions.suggested_label`` is VARCHAR(255).

    Nothing between the model and the insert clamps it, so an over-long
    suggestion reaches Postgres as a StringDataRightTruncation — a
    statement-level error that aborts whatever transaction the labeller was
    invited into. Truncate rather than discard: the text is still evidence.
    """
    payload = json.dumps(
        {
            "labels": [
                {
                    "facet": "category",
                    "value": None,
                    "confidence": 0.9,
                    "reason": "a facet value we do not have",
                    "suggest": "z" * 400,
                }
            ]
        }
    )
    proposal = parse_label_response(payload, VOCAB)[0]
    assert proposal.suggested_label is not None
    assert len(proposal.suggested_label) == MAX_SUGGESTED_LABEL_CHARS
    assert proposal.suggested_label == "z" * MAX_SUGGESTED_LABEL_CHARS


def test_an_over_long_out_of_vocabulary_value_is_clamped_too() -> None:
    """The same clamp must cover the value-becomes-a-suggestion fallback."""
    payload = json.dumps(
        {
            "labels": [
                {
                    "facet": "category",
                    "value": "q" * 400,
                    "confidence": 0.9,
                    "reason": "invented",
                }
            ]
        }
    )
    proposal = parse_label_response(payload, VOCAB)[0]
    assert proposal.value_key is None
    assert proposal.suggested_label == "q" * MAX_SUGGESTED_LABEL_CHARS


# --- Envelope tolerance (parse_label_response never sees bare JSON from a
# real model — see library.facets.labeller.label_document's docstring). ---


def test_a_fenced_payload_with_a_json_language_tag_is_parsed() -> None:
    payload = (
        "```json\n"
        + json.dumps(
            {"labels": [{"facet": "category", "value": "energy", "confidence": 0.9, "reason": "x"}]}
        )
        + "\n```"
    )
    proposals = parse_label_response(payload, VOCAB)
    assert len(proposals) == 1
    assert proposals[0].value_key == "energy"


def test_a_fenced_payload_without_a_language_tag_is_parsed() -> None:
    payload = (
        "```\n"
        + json.dumps(
            {"labels": [{"facet": "category", "value": "energy", "confidence": 0.9, "reason": "x"}]}
        )
        + "\n```"
    )
    proposals = parse_label_response(payload, VOCAB)
    assert len(proposals) == 1
    assert proposals[0].value_key == "energy"


def test_leading_prose_before_the_json_object_is_stripped() -> None:
    payload = "Sure, here is the labelling:\n" + json.dumps(
        {"labels": [{"facet": "category", "value": "energy", "confidence": 0.9, "reason": "x"}]}
    )
    proposals = parse_label_response(payload, VOCAB)
    assert len(proposals) == 1
    assert proposals[0].value_key == "energy"


def test_still_garbage_after_stripping_yields_no_proposals() -> None:
    """The fence gets stripped; what remains is still not JSON."""
    assert parse_label_response("```\nnot json at all\n```", VOCAB) == []


# Verbatim capture of a real ``claude-haiku-4-5`` response (via the API
# backend, before the structured-output fix): the model was instructed to
# "Return ONLY a JSON object ... with no prose or code fences" and wrapped its
# otherwise-correct JSON in a ```json fence anyway. `json.loads` on this text
# raises immediately, which is exactly what made
# `library label-archive --limit 5` log "facet labeller returned an
# unparseable payload" for every document while still reporting
# `labelled 5, skipped 0` (fixed separately in the backfill accounting).
REAL_HAIKU_FENCED_PAYLOAD: str = (
    "```json\n"
    "{\n"
    '  "labels": [\n'
    "    {\n"
    '      "facet": "category",\n'
    '      "value": "energy",\n'
    '      "confidence": 0.95,\n'
    '      "reason": "a recurring utility charge",\n'
    '      "suggest": null\n'
    "    },\n"
    "    {\n"
    '      "facet": "scope",\n'
    '      "value": "household",\n'
    '      "confidence": 0.8,\n'
    '      "reason": "billed to the home account, not a business expense",\n'
    '      "suggest": null\n'
    "    },\n"
    "    {\n"
    '      "facet": "urgency",\n'
    '      "value": null,\n'
    '      "confidence": 0.3,\n'
    '      "reason": "no explicit deadline stated in the excerpt",\n'
    '      "suggest": "no-deadline"\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "```"
)

REAL_HAIKU_VOCAB: tuple[VocabularyFacet, ...] = (
    VocabularyFacet(
        id=1,
        key="category",
        label="Category",
        ordinal=0,
        values=(VocabularyValue(id=10, key="energy", label="Energy", parent_id=None, aliases=()),),
    ),
    VocabularyFacet(
        id=2,
        key="scope",
        label="Scope",
        ordinal=1,
        values=(
            VocabularyValue(id=20, key="household", label="Household", parent_id=None, aliases=()),
        ),
    ),
    VocabularyFacet(id=3, key="urgency", label="Urgency", ordinal=2, values=()),
)


def test_the_exact_captured_haiku_fenced_payload_yields_three_proposals() -> None:
    """Feeds `parse_label_response` real model output, not a hand-written
    JSON string — every prior test in this module (and the ones above) only
    ever exercised the shape we assumed the model would return, which is
    exactly the seam that let this ship broken."""
    proposals = parse_label_response(REAL_HAIKU_FENCED_PAYLOAD, REAL_HAIKU_VOCAB)
    assert len(proposals) == 3
    assert proposals[0].facet_key == "category"
    assert proposals[0].value_key == "energy"
    assert proposals[1].facet_key == "scope"
    assert proposals[1].value_key == "household"
    assert proposals[2].facet_key == "urgency"
    assert proposals[2].value_key is None
    assert proposals[2].suggested_label == "no-deadline"


# --- label_document wiring: messages.parse(), not messages.create(). ---


def _make_parse_client(parsed_output: LabelResponse | None) -> SimpleNamespace:
    response = SimpleNamespace(
        parsed_output=parsed_output,
        usage=SimpleNamespace(input_tokens=111, output_tokens=22),
    )
    return SimpleNamespace(messages=SimpleNamespace(parse=AsyncMock(return_value=response)))


async def test_label_document_calls_messages_parse_with_the_label_response_schema() -> None:
    parsed = LabelResponse.model_validate(
        {"labels": [{"facet": "category", "value": "energy", "confidence": 0.9, "reason": "x"}]}
    )
    client = _make_parse_client(parsed)
    settings = Settings(anthropic_api_key="test-key")

    result = await label_document(settings, VOCAB, FIELDS, client=client)

    assert result is not None
    proposals, input_tokens, output_tokens = result
    assert client.messages.parse.await_count == 1
    call = client.messages.parse.await_args
    assert call.kwargs["output_format"] is LabelResponse
    assert proposals[0].value_key == "energy"
    assert (input_tokens, output_tokens) == (111, 22)


async def test_label_document_raises_when_structured_output_is_empty() -> None:
    """Mirrors ``library.extraction.extractor``'s ``_attempt``: a ``None``
    ``parsed_output`` is a hard failure, not a silently-empty result."""
    client = _make_parse_client(None)
    settings = Settings(anthropic_api_key="test-key")

    with pytest.raises(LabelParseError):
        await label_document(settings, VOCAB, FIELDS, client=client)
