"""Unit tests for the Ask-title backfill placeholder detection."""

from scripts.backfill_ask_titles import is_placeholder_title


def test_truncated_question_is_the_placeholder() -> None:
    question = "Tell me about the Document number 12345 and its full contents"
    # A fresh thread stores the (stripped, truncated) question as its title.
    assert is_placeholder_title(question[:120], question) is True


def test_placeholder_is_stripped_and_capped_at_120() -> None:
    assert is_placeholder_title("q", "  q  ") is True
    long_question = "x" * 200
    assert is_placeholder_title(long_question[:120], long_question) is True
    # The untruncated question is not what gets stored, so it is not a match.
    assert is_placeholder_title(long_question, long_question) is False


def test_regenerated_or_renamed_title_is_not_placeholder() -> None:
    question = "Where are my tax returns filed?"
    assert is_placeholder_title("Tax return filing location", question) is False
