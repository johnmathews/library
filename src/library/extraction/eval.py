"""Pure scoring for the extraction-quality harness (stdlib + models only).

Two ground-truth sources combine here:
- the corrections flywheel (real labels on user-reviewed documents), and
- the LLM judge (coverage on sampled documents).
No DB or network access — callers load documents / run the judge and pass the
results in.
"""

from collections import Counter
from collections.abc import Iterable
from typing import Any

from library.extraction.judge import JudgeResult
from library.models import Document


def flywheel_accuracy(documents: Iterable[Document]) -> dict[str, tuple[int, int]]:
    """Field -> (correct, total) over user-reviewed documents.

    A document is "reviewed" if it has any ``extra["corrections"]``. For such a
    document, every field in ``extra["extraction"]["fields_set"]`` counts toward
    the total; a field with a correction record counts as wrong, otherwise
    correct.
    """
    correct: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for document in documents:
        extra = document.extra or {}
        corrections = extra.get("corrections")
        if not corrections:
            continue
        corrected = {c["field"] for c in corrections}
        fields_set = (extra.get("extraction") or {}).get("fields_set", [])
        for field in fields_set:
            total[field] += 1
            if field not in corrected:
                correct[field] += 1
    return {field: (correct[field], total[field]) for field in total}


def judge_agreement(results: Iterable[JudgeResult]) -> dict[str, tuple[int, int]]:
    """Field -> (agree, total) where agree means the judge said 'correct'."""
    agree: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for result in results:
        for verdict in result.verdicts:
            total[verdict.field] += 1
            if verdict.verdict == "correct":
                agree[verdict.field] += 1
    return {field: (agree[field], total[field]) for field in total}


def _ratio(pair: tuple[int, int] | None) -> float | None:
    if pair is None or pair[1] == 0:
        return None
    return pair[0] / pair[1]


def combine(
    flywheel: dict[str, tuple[int, int]],
    agreement: dict[str, tuple[int, int]],
) -> dict[str, dict[str, Any]]:
    """Merge both sources into a per-field summary dict."""
    fields = set(flywheel) | set(agreement)
    out: dict[str, dict[str, Any]] = {}
    for field in sorted(fields):
        fw = flywheel.get(field)
        ag = agreement.get(field)
        out[field] = {
            "flywheel_accuracy": _ratio(fw),
            "flywheel_n": fw[1] if fw else 0,
            "judge_agreement": _ratio(ag),
            "judge_n": ag[1] if ag else 0,
            "n": (fw[1] if fw else 0) + (ag[1] if ag else 0),
        }
    return out


def version_distribution(documents: Iterable[Document]) -> dict[str, int]:
    """'<prompt_version>|<model>' -> count across the documents' extraction blobs."""
    counts: Counter[str] = Counter()
    for document in documents:
        extraction = (document.extra or {}).get("extraction") or {}
        version = extraction.get("prompt_version") or "unknown"
        model = extraction.get("model") or "unknown"
        counts[f"{version}|{model}"] += 1
    return dict(counts)


def modal_version(distribution: dict[str, int]) -> tuple[str, str]:
    """The most common (prompt_version, model) pair; ('unknown','unknown') if empty."""
    if not distribution:
        return ("unknown", "unknown")
    top = max(distribution.items(), key=lambda kv: kv[1])[0]
    version, _, model = top.partition("|")
    return (version, model)
