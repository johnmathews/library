"""Synthetic scenarios that drive the disclosure eval's live command.

Each :class:`Scenario` names a small set of synthetic documents to seed, a
natural-language question expected to route to the coverage-carrying
``query_documents`` tool (see ``ask/engine.py``), and whether the tool's
``coverage`` block is expected to force a disclosure in the answer.
``library.cli.eval_disclosure`` seeds each scenario's documents
(``session.add`` + ``flush``, never ``commit`` — see that command's
docstring), drives ``run_ask`` for real, and scores the answer with
``library.ask.disclosure_eval.score``.

Every sender name, amount, date and currency below is invented — this
repository is public, and none of it need resemble anything real. Sender
names carry a "(disclosure-eval fixture)" suffix so they cannot collide with
a real archive's senders and so a human skimming query logs can tell at a
glance these documents are synthetic.

The exclusion-reason strings used below (``no_amount``, ``quote_not_spend``,
``over_limit``) are the exact keys ``structured_query.py`` writes into
``Coverage.excluded`` — verified against that code, not assumed from the
eval's own brief. Two further keys exist there and are **not** exercised by any
scenario here — ``not_summable_kind`` and ``duplicate_payment``, both added
with #136 — because seeding either needs a shape ``SeedDoc`` does not yet
express (a second document sharing a payment). See ``docs/ask.md``'s
"What it measures, exactly".
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import date, timedelta

from library.models import AmountKind, ReviewStatus
from library.structured_query import query_documents

#: `structured_query.query_documents`'s default page size (the `list`
#: aggregate's `over_limit` exclusion only fires once matched documents
#: exceed this). Introspected rather than re-typed as a literal `50` so a
#: future change to the real default cannot silently detune
#: `list-truncation` below into seeding too few documents to trigger it.
#: Verified against the source on 2026-08-27: `structured_query.py` declares
#: ``limit: int = 50`` on `query_documents` (and `list_documents`, which it
#: delegates to).
_LIST_DEFAULT_LIMIT: int = inspect.signature(query_documents).parameters["limit"].default


@dataclass(frozen=True, slots=True)
class SeedDoc:
    """One synthetic document to seed for a :class:`Scenario`.

    Only the columns the eval's questions route on — ``structured_query.py``'s
    filters (sender, kind, date, amount) — plus ``currency``, which every
    document needs a real value for but which no scenario here filters or
    groups on. ``source`` is fixed to ``DocumentSource.UPLOAD`` by the CLI
    command for every seeded row — it plays no part in any scenario's
    routing, so it is not worth varying here.
    """

    sender_name: str
    kind_slug: str
    document_date: date
    amount: str | None
    currency: str | None = "EUR"
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    title: str | None = None
    #: What the amount MEANS. Not optional in practice: since #136
    #: ``sum_amount`` totals only ``SUMMABLE_AMOUNT_KINDS``, so a seeded amount
    #: left undecided is excluded as ``not_summable_kind`` and every spend
    #: scenario here would total nothing — while still running, and still
    #: scoring. ``payment_due`` is the default because these fixtures are bills
    #: and invoices; a ``quote`` overrides it with ``estimate``, which is the
    #: kind ``sum_amount`` totals when the caller asks about quotes.
    amount_kind: AmountKind = AmountKind.PAYMENT_DUE


@dataclass(frozen=True, slots=True)
class Scenario:
    """One eval case: seed ``docs``, ask ``question``, check disclosure."""

    name: str
    question: str
    docs: tuple[SeedDoc, ...]
    #: Whether ``coverage.excluded``/``needs_review`` on the tool result this
    #: question should elicit is expected to be non-empty/non-zero. ``False``
    #: marks a *control* scenario (see ``complete-no-gaps``): nothing was
    #: dropped, so any hedge in the answer is a false positive.
    expect_disclosure: bool


def _receipts(sender: str, count: int) -> tuple[SeedDoc, ...]:
    """``count`` amount-bearing receipts from ``sender``, one per day from
    2025-01-01, for the `list-truncation` scenario — every row must have a
    distinct, valid date and an amount so nothing is dropped for any reason
    OTHER than the list's page-size limit."""
    start = date(2025, 1, 1)
    return tuple(
        SeedDoc(
            sender_name=sender,
            kind_slug="receipt",
            document_date=start + timedelta(days=i),
            amount="12.50",
            title=f"Receipt {i + 1} (disclosure-eval fixture)",
        )
        for i in range(count)
    )


SCENARIOS: tuple[Scenario, ...] = (
    # Exercises `sum_amount`'s `no_amount` exclusion (structured_query.py):
    # extraction found no total on 2 of the 5 matching bills. This is the
    # scenario the live prototype ran before this eval existed; it is known to
    # produce spontaneous disclosure ("2 more ... had no readable amount").
    Scenario(
        name="utilities-no-amount",
        question=(
            "How much did I spend on utility bills from Aurora Utilities "
            "(disclosure-eval fixture) in 2025?"
        ),
        docs=(
            SeedDoc(
                "Aurora Utilities (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 1, 15),
                "40.00",
            ),
            SeedDoc(
                "Aurora Utilities (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 4, 15),
                "60.00",
            ),
            SeedDoc(
                "Aurora Utilities (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 7, 15),
                "260.00",
            ),
            SeedDoc(
                "Aurora Utilities (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 2, 20),
                None,
            ),
            SeedDoc(
                "Aurora Utilities (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 9, 20),
                None,
            ),
        ),
        expect_disclosure=True,
    ),
    # Exercises `sum_amount`'s `quote_not_spend` exclusion: quotes are not
    # actual expenditure, so a total that doesn't explicitly ask for quotes
    # must drop them and say so.
    Scenario(
        name="spend-excludes-quotes",
        question="What is my total spend with Ledger Movers (disclosure-eval fixture)?",
        docs=(
            SeedDoc(
                "Ledger Movers (disclosure-eval fixture)", "invoice", date(2025, 3, 1), "150.00"
            ),
            SeedDoc(
                "Ledger Movers (disclosure-eval fixture)", "invoice", date(2025, 6, 1), "225.00"
            ),
            SeedDoc(
                "Ledger Movers (disclosure-eval fixture)",
                "quote",
                date(2025, 2, 1),
                "500.00",
                amount_kind=AmountKind.ESTIMATE,
            ),
            SeedDoc(
                "Ledger Movers (disclosure-eval fixture)",
                "quote",
                date(2025, 5, 1),
                "475.00",
                amount_kind=AmountKind.ESTIMATE,
            ),
            SeedDoc(
                "Ledger Movers (disclosure-eval fixture)",
                "quote",
                date(2025, 8, 1),
                "610.00",
                amount_kind=AmountKind.ESTIMATE,
            ),
        ),
        expect_disclosure=True,
    ),
    # Exercises `Coverage.needs_review`: two of the four amounts summed are
    # flagged untrustworthy by the archive's validator, and the model must
    # say so even though the documents stay counted `included`.
    #
    # Deliberately seeds TWO flagged documents, not one: `NUMBER_WORDS[1]` is
    # "one", and a bare `\bone\b` match makes `needs_review=1` satisfiable by
    # any answer that happens to contain the word "one" anywhere ("Aurora is
    # one of your suppliers.") for reasons unrelated to disclosure — found by
    # executing the scorer against realistic prose, not by reading the regex.
    # A count of 2 removes that collision without touching `NUMBER_WORDS`
    # itself, which stays correct: "one document was flagged" is legitimate
    # prose that should still be credited when the expected count really is 1.
    Scenario(
        name="flagged-amounts",
        question=(
            "What is my total spend on utility bills from Brightwater Aqua "
            "(disclosure-eval fixture)?"
        ),
        docs=(
            SeedDoc(
                "Brightwater Aqua (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 1, 10),
                "80.00",
            ),
            SeedDoc(
                "Brightwater Aqua (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 4, 10),
                "95.00",
            ),
            SeedDoc(
                "Brightwater Aqua (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 7, 10),
                "10000.00",
                review_status=ReviewStatus.NEEDS_REVIEW,
            ),
            SeedDoc(
                "Brightwater Aqua (disclosure-eval fixture)",
                "utility-bill",
                date(2025, 10, 10),
                "120.00",
                review_status=ReviewStatus.NEEDS_REVIEW,
            ),
        ),
        expect_disclosure=True,
    ),
    # Exercises `list_documents`'s `over_limit` exclusion: more matching
    # receipts than the tool's page size, so a "list them all" answer cannot
    # actually list them all.
    Scenario(
        name="list-truncation",
        question="List every receipt from Voltway Records (disclosure-eval fixture).",
        docs=_receipts("Voltway Records (disclosure-eval fixture)", _LIST_DEFAULT_LIMIT + 5),
        expect_disclosure=True,
    ),
    # Control: nothing is dropped for any reason, so a correct answer has
    # nothing to disclose. Without this, an eval built only from the four
    # scenarios above would equally reward a model that hedges on every
    # answer regardless of whether anything was actually incomplete.
    Scenario(
        name="complete-no-gaps",
        question="What is my total spend with Ledger & Co Books (disclosure-eval fixture)?",
        docs=(
            SeedDoc(
                "Ledger & Co Books (disclosure-eval fixture)", "invoice", date(2025, 1, 5), "100.00"
            ),
            SeedDoc(
                "Ledger & Co Books (disclosure-eval fixture)", "invoice", date(2025, 4, 5), "100.00"
            ),
            SeedDoc(
                "Ledger & Co Books (disclosure-eval fixture)", "invoice", date(2025, 7, 5), "100.00"
            ),
        ),
        expect_disclosure=False,
    ),
)
