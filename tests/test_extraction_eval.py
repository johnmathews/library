"""Unit tests for the pure eval scoring functions."""

from library.extraction.eval import (
    combine,
    flywheel_accuracy,
    judge_agreement,
    modal_version,
    version_distribution,
)
from library.extraction.judge import FieldVerdict, JudgeResult
from library.models import Document


def _doc(extra: dict) -> Document:
    # Normal constructor (see Task 1 note on data descriptors).
    return Document(extra=extra)


def test_flywheel_accuracy_counts_corrected_as_wrong() -> None:
    docs = [
        _doc(
            {
                "extraction": {"fields_set": ["amount_total", "title"]},
                "corrections": [{"field": "amount_total"}],
            }
        ),
        _doc(
            {
                "extraction": {"fields_set": ["amount_total", "title"]},
                "corrections": [{"field": "title"}],
            }
        ),
    ]
    acc = flywheel_accuracy(docs)
    assert acc["amount_total"] == (1, 2)  # one correct, two total
    assert acc["title"] == (1, 2)


def test_flywheel_ignores_docs_without_corrections() -> None:
    docs = [_doc({"extraction": {"fields_set": ["title"]}})]  # never reviewed
    assert flywheel_accuracy(docs) == {}


def test_judge_agreement() -> None:
    results = [
        JudgeResult(
            verdicts=[
                FieldVerdict(field="title", verdict="correct", note=None),
                FieldVerdict(field="amount_total", verdict="wrong", note=None),
            ]
        ),
        JudgeResult(verdicts=[FieldVerdict(field="title", verdict="correct", note=None)]),
    ]
    agree = judge_agreement(results)
    assert agree["title"] == (2, 2)
    assert agree["amount_total"] == (0, 1)


def test_combine_merges_fields() -> None:
    combined = combine({"title": (1, 2)}, {"title": (2, 2), "amount_total": (0, 1)})
    assert combined["title"]["flywheel_accuracy"] == 0.5
    assert combined["title"]["judge_agreement"] == 1.0
    assert combined["amount_total"]["flywheel_accuracy"] is None


def test_version_distribution_and_modal() -> None:
    docs = [
        _doc({"extraction": {"prompt_version": "v1", "model": "haiku"}}),
        _doc({"extraction": {"prompt_version": "v1", "model": "haiku"}}),
        _doc({"extraction": {"prompt_version": "v2", "model": "sonnet"}}),
    ]
    dist = version_distribution(docs)
    assert dist["v1|haiku"] == 2
    assert modal_version(dist) == ("v1", "haiku")
