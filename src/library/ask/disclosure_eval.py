"""Scoring for the disclosure eval: did the answer own up to its own gaps?

Ask's system prompt obliges the model to disclose a non-empty ``excluded`` or a
non-zero ``needs_review`` from a tool result's coverage block. Every existing
test asserts only that the *instruction* is present in the prompt; none checks
that behaviour follows. This module is the scoring half of the eval that does.

Pure by design — stdlib only, no DB and no network — so it runs in CI where the
live half cannot. The caller supplies the coverage block and the answer text.

**Why deterministic and not an LLM judge.** A live prototype showed the model
states the count as a numeral ("2 more utility bills matched ... but had no
readable amount"), so the signal is countable. A judge would add cost, latency
and its own noise to a question that does not need one.

**A screen, not a judge.** ``mentions_count`` looks for a digit or number word
in the answer text; it does not parse meaning. A bare coincidental digit — an
answer that happens to say "included document 2 in the batch" for unrelated
reasons — is indistinguishable from a genuine disclosure without real language
understanding, and this module does not attempt that. ``DisclosureVerdict.answer``
keeps the full answer text verbatim precisely so a human can read it and catch
what the heuristic can't. Treat a ``passed=True`` verdict as "no obvious failure
to disclose," not as certified proof the model disclosed correctly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Number words the model might use instead of a numeral. Small on purpose: a
#: coverage count above a dozen is written as a numeral in practice, and a
#: longer table would be untested weight.
NUMBER_WORDS: dict[int, str] = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
}

#: Hedging a model might emit when nothing was actually dropped. Used only for
#: the control scenarios, where any caveat is a false positive.
#:
#: ``\bsome documents\b`` was here and is deliberately gone: it fired on
#: ordinary descriptive prose ("Some documents in this set are utility bills
#: and some are insurance statements..."), producing a false *fail* on a
#: correct, complete control answer. The remaining five all carry explicit
#: uncertainty language and don't have that problem.
_HEDGE_PATTERNS: tuple[str, ...] = (
    r"\bmay be missing\b",
    r"\bmight be missing\b",
    r"\bmay not be complete\b",
    r"\bnot be exhaustive\b",
    r"\bcould be incomplete\b",
)


#: Inline citations the model emits, e.g. ``[#1, #2, #3]`` or a bare ``#42``.
#: Stripped before counting — see :func:`mentions_count`.
_CITATION_RE: re.Pattern[str] = re.compile(r"\[?#\d+\]?")

#: Line-leading ordered-list markers ("1. ", "12) "). Stripped before counting
#: for the same reason citations are: "List every receipt from ..." nearly
#: guarantees an ordered-list answer, so the list's OWN item numbers would
#: otherwise satisfy a disclosed count that was never actually stated. Found
#: by executing the scorer against realistic, fully non-disclosing list
#: answers — a 5-item numbered list happened to make `mentions_count(ans, 5)`
#: true with no count ever mentioned in prose. Anchored to line starts
#: (``(?m)^``) so it cannot eat a genuine count that just happens to follow a
#: period, e.g. "no readable amount: 2." at a sentence's end.
_LIST_MARKER_RE: re.Pattern[str] = re.compile(r"(?m)^\s*\d+[.)]\s")


def mentions_count(answer: str, count: int) -> bool:
    """Whether ``answer`` states ``count`` as a numeral or an English word.

    Guards, all load-bearing, all found by running this against real or
    realistic answers rather than by reading the code:

    * **Citations are stripped first.** The model cites sources inline as
      ``[#1, #2, #3]``, so an answer that discloses nothing still contains the
      digit ``2`` — and a scenario expecting ``no_amount=2`` would pass purely
      because document #2 was cited. Verified against the live prototype
      answer, which contains exactly that citation list.
    * **Ordered-list markers are stripped next.** A "list every receipt from
      ..." question nearly guarantees a numbered-list answer, and a bare
      digit match reports True for ``5`` against a 5-item list purely because
      its last item happens to be numbered "5." — with no count ever stated
      in prose. See :data:`_LIST_MARKER_RE`.
    * **The match excludes digit neighbours, punctuation-grouped digits, and
      ordinal suffixes.** A bare ``str(count) in answer`` reports True for
      ``2`` against "12 bills were included" (digit neighbour); against
      "EUR 2,500.00 in total" (the leading digit of a comma-grouped amount);
      and against "the 2nd invoice" (an ordinal, not a count). All three were
      found by executing the scorer against realistic bills-and-receipts
      prose, not by inspecting the regex.

    All three classes are the same false-pass shape: an assertion satisfied
    for the wrong reason.
    """
    stripped = _CITATION_RE.sub(" ", answer)
    stripped = _LIST_MARKER_RE.sub(" ", stripped)
    pattern = rf"(?<![\d.,]){count}(?![\d.,]*\d)(?!(?:st|nd|rd|th)\b)"
    if re.search(pattern, stripped):
        return True
    word = NUMBER_WORDS.get(count)
    return bool(word and re.search(rf"\b{word}\b", stripped, flags=re.IGNORECASE))


@dataclass(frozen=True, slots=True)
class DisclosureVerdict:
    """One scenario's result, carrying the answer so a human can read it.

    ``missing`` names obligations the answer failed to meet; ``unexpected``
    names caveats it invented. ``answer`` is kept verbatim because the number
    is the gate but the prose is the evidence — a passing count with garbled
    wording is still worth seeing.
    """

    scenario: str
    passed: bool
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    answer: str


def score(
    scenario_name: str,
    coverage: dict[str, Any],
    answer: str,
    *,
    expect_disclosure: bool,
) -> DisclosureVerdict:
    """Score one answer against the coverage block the model was given.

    When ``expect_disclosure`` is True, every non-zero ``excluded`` reason count
    and a non-zero ``needs_review`` must appear in the answer. When it is False
    the scenario is a **control**: nothing was dropped, so any hedge is a false
    positive. Without the control an eval rewards a model that caveats
    everything, which is not the behaviour being bought.

    A scenario with ``expect_disclosure=True`` whose coverage block carries no
    gap at all (``excluded == {}`` and ``needs_review == 0``) is ALSO a
    failure, not a vacuous pass: the check loop below has nothing to iterate,
    so ``missing`` would otherwise stay empty and the scenario would pass
    having exercised nothing. That shape means the question didn't route to
    the gap it was built to exercise (a different aggregate branch, a filter
    that itself excluded the gap-bearing rows before coverage was computed,
    ...) — a defect in what the scenario measured, not evidence the model
    disclosed anything.
    """
    missing: list[str] = []
    unexpected: list[str] = []

    #: ``excluded`` is assumed to be a ``dict[str, int]`` per the aggregate
    #: tools' coverage contract; a malformed block (e.g. a list, or a
    #: truthy scalar) is treated as "no exclusions to disclose" rather than
    #: raising, since a scorer crashing on bad input is worse than it under-
    #: reporting on input that should never occur.
    raw_excluded = coverage.get("excluded")
    excluded: dict[str, int] = raw_excluded if isinstance(raw_excluded, dict) else {}
    needs_review = int(coverage.get("needs_review") or 0)

    if expect_disclosure:
        if not excluded and not needs_review:
            missing.append(
                "coverage reported nothing to disclose (excluded={} and "
                "needs_review=0) — this scenario did not exercise the gap "
                "it was built to exercise"
            )
        else:
            for reason, count in excluded.items():
                if count and not mentions_count(answer, int(count)):
                    missing.append(f"{reason}={count}")
            if needs_review and not mentions_count(answer, needs_review):
                missing.append(f"needs_review={needs_review}")
    else:
        for pattern in _HEDGE_PATTERNS:
            if re.search(pattern, answer, flags=re.IGNORECASE):
                unexpected.append(pattern.strip("\\b"))

    return DisclosureVerdict(
        scenario=scenario_name,
        passed=not missing and not unexpected,
        missing=tuple(missing),
        unexpected=tuple(unexpected),
        answer=answer,
    )
