"""The shared JSON-envelope stripper.

Extracted from `facets/labeller.py` after the same production failure occurred
a second time, in `money/backfill.py`: a model told to return bare JSON wrapped
it in a ```json fence, `json.loads` raised, and the whole run silently decided
nothing. Both callers now share this, and both use `messages.parse` on their
API path so the fence cannot arise there at all.
"""

import pytest

from library.llm.envelope import strip_json_envelope


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
        ('```\n{"a": 1}\n```', '{"a": 1}'),
        ('{"a": 1}', '{"a": 1}'),
        ('  {"a": 1}  ', '{"a": 1}'),
        ('Here you go:\n{"a": 1}\nhope that helps', '{"a": 1}'),
        ('```json\n{"a": {"b": 2}}\n```', '{"a": {"b": 2}}'),
    ],
)
def test_an_envelope_is_stripped_down_to_the_json(payload: str, expected: str) -> None:
    assert strip_json_envelope(payload) == expected


def test_text_with_no_json_object_is_returned_as_is() -> None:
    """Never raises: the caller's json.loads stays the validity check."""
    assert strip_json_envelope("I could not tell.") == "I could not tell."


def test_an_empty_payload_is_returned_as_is() -> None:
    assert strip_json_envelope("") == ""
