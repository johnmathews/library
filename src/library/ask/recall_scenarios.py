"""A synthetic corpus and the retrieval cases scored against it.

The counterpart to ``disclosure_scenarios``. That module seeds metadata-only
documents to drive coverage arithmetic; this one seeds documents with **body
text**, because retrieval is what is being measured and a document with no text
has nothing to retrieve on.

**Everything here is invented.** This repository is public. No sender, amount,
date or sentence below resembles anything real, and every sender name carries a
``(recall-eval fixture)`` suffix so it cannot collide with a real archive's
senders and a human skimming query logs can tell at a glance the rows are
synthetic.

**One shared haystack, many cases.** Unlike the disclosure scenarios — which
seed and roll back per scenario, because each one's coverage arithmetic must not
see the others' rows — every case here is scored against the SAME corpus, seeded
once. That is deliberate: one case's near-miss distractors are another case's
noise, and a haystack that shrinks to six documents per question makes recall@10
meaningless (ten slots, six documents, everything is retrieved).

**Difficulty is the whole design.** A corpus of obviously-distinct documents
scores recall 1.0 at baseline and can therefore never show an improvement. Every
case ships with hand-authored near-miss distractors: same sender, same kind,
adjacent dates, overlapping vocabulary. ``docs/ask.md`` records the acceptance
criterion this corpus is held to — if baseline recall@10 comes out at or above
0.90, the corpus is too easy and gets harder before any retrieval change is
measured against it.

**Chunk counts are declared, and documents may span several chunks.** This
reverses a deliberate earlier decision — every body used to be shorter than
``embedding_chunk_chars`` so that each document produced exactly one chunk —
and the reversal is worth explaining, because the reason originally written
down for the invariant was not the reason it was load-bearing.

The stated reason was that one chunk per document "keeps document-level recall
unambiguous: a document is retrieved or it is not, with no question of which of
its chunks won." That was never true of the code. ``semantic_search`` collapses
to one row per document (``DISTINCT ON (document_id) ORDER BY distance``) and
the eval scores ``hit.document.id``, so document-level recall was unambiguous
for any chunk count.

The invariant *was* load-bearing, for two reasons nobody wrote down:

1. **The blind floor assumed it.** Ranking is by nearest chunk, so a document
   with ``c`` chunks gets ``c`` draws under a null retriever. The old floor
   formula modelled documents as exchangeable, which is exactly right while
   every document is one chunk and wrong — in the passing direction — as soon
   as one is not. See ``recall_eval.blind_recall``.
2. **The ANN prefetch budget assumed it.** ``semantic_search`` prefetches
   ``top_k * 5 * VECTOR_CANDIDATE_FANOUT`` *chunks* before collapsing. A
   201-chunk corpus sits under that window, so on a clean stack the vector leg
   is an exact global argmax rather than the approximate retriever that ships.

Issue #106 needs multi-chunk fixtures to measure whether chunk count biases
retrieval at all, so the invariant had to go. Both real reasons are now
enforced instead of assumed: ``RecallDoc.chunks`` is declared and checked
against the real chunker, and the floor is weighted by it.

**The rule for anyone adding fixtures, which is the opposite of the obvious
one: crowders long, expected documents no longer than their crowders.**
Lengthening the *expected* documents raises the blind floor — three 5-chunk
expected documents among single-chunk crowders sit at 0.70 — so it makes a case
easier while looking harder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: This module's mirror of ``Settings.embedding_chunk_chars`` — the size of one
#: chunk. No longer a ceiling on ``RecallDoc.body``: bodies may exceed it, and
#: what they must do instead is declare the resulting ``chunks`` count, which
#: the structural test checks against the real chunker. Kept because it is still
#: the number every body length is reasoned about in, and because the structural
#: test asserts it has not drifted above the real setting.
MAX_BODY_CHARS: int = 1800

#: Suffix on every synthetic sender. See the module docstring.
FIXTURE_SUFFIX: str = "(recall-eval fixture)"


@dataclass(frozen=True, slots=True)
class RecallDoc:
    """One synthetic document in the shared haystack.

    ``marker`` is the stable handle a case refers to; the CLI maps markers to
    the database ids it just inserted, so cases never hard-code ids.
    """

    marker: str
    sender_name: str
    kind_slug: str
    document_date: date
    title: str
    body: str

    #: How many chunks this body is asserted to produce. **Declared, not
    #: derived** — ``tests/test_recall_scenarios.py`` checks the declaration
    #: against the real chunker, and a derived value would only ever agree with
    #: itself. It cannot be computed from ``len(body)`` either: the packer works
    #: in whole words so each boundary drifts, and ``str.split`` collapses
    #: whitespace, so a 220-character column-padded body chunks as 158.
    chunks: int = 1

    #: Case names this document is authored to compete in, for cases where it
    #: shares neither sender nor title with the expected documents. The blind
    #: floor infers competition from shared metadata, which is how this corpus
    #: places its near-misses — but a fixture that is long *purely to crowd*
    #: matches neither, would count for nothing, and the floor would come out
    #: too pessimistic. Competition that cannot be inferred is declared.
    crowds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecallCase:
    """One question, and the documents that must come back for it.

    ``k`` is the rank cut recall is measured at. It defaults to the shipped
    ``retrieve_top_k`` so most cases measure what Ask actually does today;
    ``breadth-many-mentions`` overrides it deliberately (see its comment).

    ``why`` records what the case exists to exercise, so a future reader can
    tell a case that regressed from a case that was never load-bearing.
    """

    name: str
    question: str
    expected_markers: tuple[str, ...]
    why: str
    k: int = 10

    #: For a case whose answer lives in one PASSAGE of a long document: the
    #: sentence that states it. Declared so the structural tests can hold two
    #: properties that are otherwise unenforceable — that the answer lands in
    #: exactly one chunk (the 200-character overlap will duplicate a sentence
    #: placed near a boundary, which both falsifies "one chunk" and doubles that
    #: chunk's draws), and that it appears in no other document, so a miss
    #: cannot score as a hit. Empty for cases with no single answer sentence.
    answer_needle: str = ""


def _sender(name: str) -> str:
    return f"{name} {FIXTURE_SUFFIX}"


# --- Case 1: a clause inside a long contract -----------------------------------
#
# The spec's own motivating example for #5. The target is one 2019 mortgage
# contract; the distractors are the SAME sender's mortgage paperwork from
# adjacent years, all of which discuss repayment in similar language. Unscoped,
# the right year has to win on content alone.

#: The three documents that actually STATE early-repayment terms. Everything
#: else from this sender talks around the subject without setting out the terms,
#: which is what makes the case a retrieval problem rather than a keyword one.
_MORTGAGE_ANSWERS: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="mortgage-2019-contract",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="contract",
        document_date=date(2019, 6, 11),
        title="Mortgage agreement — fixed ten-year term",
        body=(
            "Clause 7 — Early repayment. The borrower may repay up to fifteen "
            "per cent of the original principal in any calendar year without "
            "penalty. Repayments beyond that threshold attract a compensation "
            "charge calculated on the difference between the contract rate and "
            "the prevailing reinvestment rate for the remaining fixed period. "
            "No compensation is due where repayment follows the sale of the "
            "property, the death of a borrower, or the expiry of the fixed term."
        ),
    ),
    RecallDoc(
        marker="mortgage-2020-variation",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="contract",
        document_date=date(2020, 8, 3),
        title="Deed of variation — rate switch",
        body=(
            "This deed varies the agreement dated as recorded in the schedule. "
            "Clause 7 is replaced in its entirety. The borrower may repay up to "
            "ten per cent of the outstanding balance in any calendar year "
            "without penalty. Repayments beyond that threshold attract a "
            "compensation charge of three per cent of the amount repaid in the "
            "first two years of the varied period and two per cent thereafter. "
            "All other clauses continue in force unamended."
        ),
    ),
    RecallDoc(
        marker="mortgage-2024-remortgage",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="contract",
        document_date=date(2024, 3, 15),
        title="Remortgage agreement — five-year fixed",
        body=(
            "Clause 9 — Repayment before the end of the fixed period. The "
            "borrower may repay up to twenty per cent of the outstanding "
            "balance in any calendar year without charge. Amounts repaid above "
            "that allowance attract a compensation charge of four per cent "
            "falling by one percentage point in each subsequent year of the "
            "fixed period. The charge does not apply on the sale of the "
            "property or on the expiry of the fixed period."
        ),
    ),
)

#: Near-miss correspondence from the same sender. Each mentions repaying, the
#: balance or the agreement, and none states the terms. Cycled over years so the
#: pool is several times the ten-slot cut: at a pool of thirteen a retriever
#: that ranked at RANDOM would already score 0.77, which is why the first
#: hardening pass failed to move the number (see the 2026-08-27 journal).
_MORTGAGE_ROUTINE: tuple[tuple[str, str, str, str], ...] = (
    (
        "annual-statement",
        "letter",
        "Annual mortgage statement",
        "Your statement for the year is enclosed, showing the opening balance, the "
        "payments received, the interest applied and the closing balance. If you "
        "are considering repaying early, contact us for a redemption figure.",
    ),
    (
        "overpayment",
        "letter",
        "Overpayment received",
        "We have applied the overpayment you sent to the outstanding balance. It "
        "fell within the annual allowance described in your agreement, so no "
        "compensation charge applies. The term has shortened accordingly.",
    ),
    (
        "redemption-quote",
        "letter",
        "Redemption figure",
        "The figure below is valid for thirty days and assumes repayment in full "
        "on the date shown. It comprises the balance, interest to that date, an "
        "administration fee and a compensation charge calculated under your "
        "agreement. This letter quotes the figure; it does not restate the terms.",
    ),
    (
        "porting",
        "letter",
        "Moving your mortgage to a new property",
        "If you move and take this mortgage with you, the product and its "
        "remaining fixed period may be carried across. Where the new borrowing is "
        "smaller the reduction is treated as a repayment and may attract a charge.",
    ),
    (
        "arrears",
        "letter",
        "Missed payment",
        "One monthly payment has not reached us. Please pay as soon as possible to "
        "avoid arrears being recorded. If your circumstances have changed, contact "
        "us to discuss a temporary reduction or an extension of the term.",
    ),
    (
        "direct-debit",
        "letter",
        "Change to your direct debit",
        "The amount collected each month is changing because the interest rate "
        "applied to your balance has changed. No action is needed; the new amount "
        "will be taken on the usual date.",
    ),
    (
        "rate-review",
        "letter",
        "Your fixed period is ending",
        "The fixed period on your product ends shortly. Unless you choose a new "
        "product the balance will move to the variable rate. Repaying at or after "
        "the end of the fixed period carries no charge.",
    ),
    (
        "insurance",
        "letter",
        "Buildings insurance requirement",
        "The agreement requires buildings insurance for the full reinstatement "
        "value throughout the term. Evidence of cover must be provided on request. "
        "Failure to maintain cover is a breach of the agreement.",
    ),
    (
        "illustration",
        "letter",
        "Illustration of borrowing costs",
        "This illustration shows what the borrowing would cost under the product "
        "discussed. It explains in general terms that repaying early may result in "
        "a compensation charge, without stating any figure for it.",
    ),
    (
        "offer",
        "contract",
        "Mortgage offer — provisional terms",
        "This provisional offer sets out indicative terms only and does not "
        "constitute an agreement. Early repayment conditions will be stated in "
        "full in the final agreement.",
    ),
    (
        "consent-to-let",
        "letter",
        "Consent to let",
        "We agree to the property being let for a period of twelve months. The "
        "agreement continues on its existing terms and the balance is unaffected. "
        "Consent must be renewed if the letting continues beyond that period.",
    ),
    (
        "valuation",
        "letter",
        "Valuation for lending purposes",
        "A valuation has been carried out for lending purposes only. It is not a "
        "survey and no opinion is offered on the condition of the property. The "
        "figure is used to set the ratio of the loan to the value.",
    ),
    (
        "complaint",
        "letter",
        "Response to your complaint",
        "Thank you for your complaint about the time taken to answer your call. We "
        "have upheld it and applied a credit to your account. This does not change "
        "the balance outstanding or the terms of the agreement.",
    ),
    (
        "payment-holiday",
        "letter",
        "Payment deferral agreed",
        "We have agreed to defer three monthly payments. Interest continues to be "
        "charged on the balance during the deferral and the deferred amounts are "
        "added to what you owe. The term is extended to absorb them.",
    ),
    (
        "product-transfer",
        "letter",
        "Your new product has started",
        "The product recorded in your paperwork has taken effect. Your first "
        "payment under it is due on the date shown. Any charge arising on closing "
        "the previous product has been accounted for in the completion statement.",
    ),
    (
        "address-change",
        "letter",
        "Change of correspondence address",
        "We have updated the address we write to. Statements and notices about the "
        "balance will be sent there from now on. The property charged under the "
        "agreement is unchanged.",
    ),
    (
        "term-extension",
        "letter",
        "Extension of the mortgage term",
        "The term has been extended, which reduces the monthly payment and "
        "increases the total interest paid over the life of the loan. The product "
        "and its remaining fixed period are unchanged.",
    ),
    (
        "fee-notice",
        "invoice",
        "Administration fee",
        "A fee has been applied for producing the documentation you requested. It "
        "is charged separately from the balance and is payable within thirty days. "
        "It is not a charge arising from repaying the loan.",
    ),
    (
        "interest-notice",
        "letter",
        "How interest is applied",
        "Interest is calculated daily on the balance and applied monthly. Payments "
        "received are credited on the day they reach us, which is why a payment "
        "made earlier in the month reduces the interest charged for that month.",
    ),
    (
        "credit-report",
        "letter",
        "What we report about your account",
        "We share the status of the account with credit reference agencies each "
        "month, including the balance and whether payments were made on time. "
        "Repaying the loan is reported as a settled account.",
    ),
)

_MORTGAGE: tuple[RecallDoc, ...] = _MORTGAGE_ANSWERS + tuple(
    RecallDoc(
        marker=f"mortgage-{slug}-{year}",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug=kind,
        document_date=date(year, 1 + (index * 5) % 12, 1 + (index * 7) % 28),
        title=title,
        body=body,
    )
    for index, (slug, kind, title, body) in enumerate(_MORTGAGE_ROUTINE)
    for year in (2018 + index % 3, 2021 + index % 3)
)


# --- Case 3: a sender named in the question, absent from the text ----------------
#
# THE case for #6. Forty identically titled annual statements whose bodies are
# figures blocks naming neither sender nor year. Content cannot distinguish a
# Northwind statement from any other, because the distinguishing facts live only
# in metadata. Three of the forty are the answer, and a blind retriever scores
# 0.25 at k=10, so the case has real room to move.

_BARE_FIGURE_SENDERS: tuple[tuple[str, int], ...] = (
    ("Northwind Energy", 3),
    ("Clearbrook Water", 6),
    ("Ironbridge Gas", 6),
    ("Cavendish Power", 6),
    ("Thistledown Utilities", 5),
    ("Marlow Energy", 5),
    ("Kingsway Water", 5),
    ("Ravensmere Power", 4),
)


def _bare_figures_body(seed: int) -> str:
    """A figures block that names neither its sender nor its year."""
    total = 180 + (seed * 37) % 420
    instalments = total - (seed * 11) % 40
    start = 4000 + (seed * 913) % 20000
    return (
        f"Period total                 {total},{(seed * 7) % 100:02d}\n"
        f"Instalments received         {instalments},00\n"
        f"Balance due                   {total - instalments},{(seed * 7) % 100:02d}\n"
        f"Meter reading start          {start}\n"
        f"Meter reading end            {start + 900 + (seed * 53) % 2400}\n"
        "Standing charge included in the period total."
    )


_BARE_FIGURES: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"bare-{sender.split()[0].lower()}-{2019 + offset}",
        sender_name=_sender(sender),
        kind_slug="utility-bill",
        document_date=date(2019 + offset, 8 + (index + offset) % 5, 1 + (index * 3 + offset) % 27),
        title="Annual statement",
        body=_bare_figures_body(index * 13 + offset),
    )
    for index, (sender, count) in enumerate(_BARE_FIGURE_SENDERS)
    for offset in range(count)
)


# --- Case 4: one kind among many documents about the same object -----------------
#
# Thirteen documents about the same boiler, from the same installer, nearly all
# of which mention the warranty. Two of them ARE warranties. The cluster is
# larger than the ten-slot cut so the case can lose recall.

#: The two documents that ARE warranties. Everything else from this installer
#: mentions the warranty without being one.
_BOILER_ANSWERS: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="boiler-warranty",
        sender_name=_sender("Halden Heating"),
        kind_slug="warranty",
        document_date=date(2022, 4, 18),
        title="Boiler warranty certificate",
        body=(
            "This certificate warrants the appliance against defects in "
            "manufacture for seven years from the date of commissioning, "
            "provided an annual service is carried out by a registered engineer "
            "and recorded in the service log. It covers parts and labour for "
            "the heat exchanger and the main circuit board, and parts only for "
            "the pump and the diverter valve."
        ),
    ),
    RecallDoc(
        marker="boiler-warranty-extension",
        sender_name=_sender("Halden Heating"),
        kind_slug="warranty",
        document_date=date(2029, 4, 18),
        title="Extended warranty certificate",
        body=(
            "This certificate extends cover on the appliance for a further "
            "three years from the expiry of the original term, on the same "
            "conditions. Cover remains conditional on an annual service by a "
            "registered engineer. It covers parts and labour for the heat "
            "exchanger and parts only for all other components."
        ),
    ),
)

#: Near-miss paperwork about the same appliance from the same installer. Nearly
#: every one mentions the warranty; none is one.
_BOILER_ROUTINE: tuple[tuple[str, str, str, str], ...] = (
    (
        "invoice",
        "invoice",
        "Boiler supply and installation",
        "Supply and installation of a condensing combination boiler, including a "
        "magnetic system filter and a full system flush. The warranty certificate "
        "is issued separately on commissioning and registration.",
    ),
    (
        "quote",
        "quote",
        "Quotation for a replacement boiler",
        "Estimate for removing the existing appliance and installing a condensing "
        "combination boiler. The price includes commissioning and registration, "
        "which is what activates the manufacturer warranty.",
    ),
    (
        "manual",
        "manual",
        "Boiler user instructions",
        "How to set the timer, adjust the flow temperature and repressurise the "
        "system. Servicing must be carried out annually by a registered engineer; "
        "failure to do so will invalidate the warranty supplied.",
    ),
    (
        "thermostat",
        "manual",
        "Programmable thermostat instructions",
        "Pairing, scheduling and holiday mode for the wireless thermostat supplied "
        "with the installation. The thermostat carries its own two year warranty "
        "from its manufacturer, separate from the cover on the appliance.",
    ),
    (
        "registration",
        "letter",
        "Appliance registered",
        "The appliance has been registered with the manufacturer and the competent "
        "persons scheme. Registration is what brings the warranty into force. Your "
        "certificate has been issued separately.",
    ),
    (
        "flue",
        "certificate",
        "Flue and combustion check",
        "Combustion analysis was carried out and the readings recorded fall within "
        "the manufacturer tolerance. This record is required evidence should a "
        "warranty claim be made on the heat exchanger.",
    ),
    (
        "service-due",
        "letter",
        "Annual service due",
        "Your appliance is due its annual service. Booking it keeps the warranty "
        "valid; a lapse of more than twelve months between services ends cover "
        "even where the appliance has not failed.",
    ),
    (
        "service-record",
        "certificate",
        "Annual service record",
        "The annual service was carried out and the appliance left in working "
        "order. The service log has been updated, which preserves the cover held "
        "on the appliance. No remedial work was required.",
    ),
    (
        "parts",
        "receipt",
        "Replacement expansion vessel",
        "Supply of a replacement expansion vessel and fitting during the annual "
        "visit. This part falls outside the parts and labour cover on the "
        "appliance and is charged separately.",
    ),
    (
        "noise",
        "letter",
        "Response to your enquiry about a noise",
        "The engineer found no fault on inspection and the reading taken was "
        "within tolerance. No claim has been made against the cover on the "
        "appliance and it remains in force.",
    ),
    (
        "pressure",
        "letter",
        "Losing pressure",
        "A slow loss of pressure is usually a small leak on the system rather than "
        "a fault in the appliance. Leaks on pipework are not covered by the "
        "warranty on the appliance itself.",
    ),
    (
        "filter",
        "invoice",
        "System filter clean",
        "The magnetic filter was removed, cleaned and refitted, and inhibitor was "
        "topped up. Keeping the system clean is a condition of the cover held on "
        "the appliance.",
    ),
    (
        "radiator",
        "invoice",
        "Radiator replacement",
        "One radiator was replaced and the system rebalanced. Radiators are not "
        "part of the appliance and carry no cover under its warranty.",
    ),
    (
        "gas-safety",
        "certificate",
        "Gas safety record",
        "All appliances at the property were checked and found safe. This record "
        "is separate from the service that maintains the appliance warranty, "
        "though both were carried out on the same visit.",
    ),
    (
        "callout",
        "invoice",
        "Emergency call-out",
        "Attendance outside normal hours to restart the appliance after a "
        "lock-out. The call-out fee applies regardless of the cover held, which "
        "meets the cost of parts and labour only.",
    ),
    (
        "water-quality",
        "letter",
        "System water test",
        "A sample of the system water was tested and the inhibitor level was low. "
        "Correcting it protects the heat exchanger, which is the component the "
        "warranty covers for the longest period.",
    ),
    (
        "controls",
        "invoice",
        "Zone valve replacement",
        "A failed zone valve was replaced. The valve is part of the heating system "
        "rather than the appliance and is therefore outside the appliance cover.",
    ),
    (
        "survey",
        "letter",
        "Pre-installation survey",
        "The survey confirms the flue route, the gas supply and the position of "
        "the appliance. The installation must follow it for the manufacturer "
        "warranty to be valid.",
    ),
    (
        "handover",
        "letter",
        "Handover notes",
        "The system was demonstrated and the paperwork handed over, including the "
        "benchmark record, the user instructions and the warranty certificate. "
        "Keep them together with the service log.",
    ),
)

_BOILER: tuple[RecallDoc, ...] = _BOILER_ANSWERS + tuple(
    RecallDoc(
        marker=f"boiler-{slug}-{year}",
        sender_name=_sender("Halden Heating"),
        kind_slug=kind,
        document_date=date(year, 1 + (index * 5) % 12, 1 + (index * 3) % 28),
        title=title,
        body=body,
    )
    for index, (slug, kind, title, body) in enumerate(_BOILER_ROUTINE)
    for year in (2022 + index % 2, 2024 + index % 2)
)


_SOLAR: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"solar-{index:02d}",
        sender_name=_sender(sender),
        kind_slug=kind,
        document_date=date(2023, month, day),
        title=title,
        body=body,
    )
    for index, (sender, kind, month, day, title, body) in enumerate(
        (
            (
                "Solaris Install",
                "quote",
                1,
                12,
                "Quotation for a rooftop array",
                "Indicative pricing for a fourteen panel rooftop array with a single "
                "inverter, scaffolding, and grid registration. Valid ninety days.",
            ),
            (
                "Solaris Install",
                "invoice",
                3,
                2,
                "Rooftop array — first stage",
                "First stage payment for the rooftop array covering panels, mounting "
                "rail and scaffolding hire. Balance due on commissioning.",
            ),
            (
                "Solaris Install",
                "invoice",
                4,
                19,
                "Rooftop array — final stage",
                "Final stage payment for the rooftop array following commissioning "
                "and grid registration. Includes the inverter and its isolator.",
            ),
            (
                "Solaris Install",
                "certificate",
                4,
                21,
                "Array commissioning certificate",
                "Certifies that the rooftop array was commissioned and tested, and "
                "that the installation complies with the applicable wiring rules.",
            ),
            (
                "Solaris Install",
                "warranty",
                4,
                21,
                "Panel performance warranty",
                "Panel output is warranted not to fall below eighty five per cent of "
                "nominal within twenty five years of the array's commissioning date.",
            ),
            (
                "Solaris Install",
                "manual",
                4,
                21,
                "Inverter operating notes",
                "Reading the inverter display, interpreting fault codes, and the "
                "shutdown sequence for the rooftop array before any roof work.",
            ),
            (
                "Gridline Networks",
                "letter",
                5,
                8,
                "Grid connection registered",
                "Your rooftop array has been registered for export. Metering will "
                "record import and export separately from the date below.",
            ),
            (
                "Gridline Networks",
                "invoice",
                7,
                1,
                "Network charges",
                "Quarterly network charges. Export from your rooftop array is "
                "credited separately and shown on the statement overleaf.",
            ),
            (
                "Harbour Insurance",
                "letter",
                5,
                30,
                "Policy amended",
                "Your buildings policy has been amended to note the rooftop array. "
                "No change to the premium arises from this amendment.",
            ),
            (
                "Meridian Mortgages",
                "letter",
                6,
                14,
                "Consent to alterations",
                "Consent is given for the rooftop array described in your request. "
                "The alteration does not affect the security or the fixed period.",
            ),
            (
                "Solaris Install",
                "receipt",
                8,
                3,
                "Bird protection mesh",
                "Supply and fitting of perimeter mesh to the rooftop array to "
                "prevent nesting beneath the panels.",
            ),
            (
                "Solaris Install",
                "letter",
                11,
                27,
                "First season output",
                "A summary of the rooftop array's output across its first season, "
                "with monthly generation and export figures.",
            ),
        ),
        start=1,
    )
)


# --- Case 5: a year that exists only in metadata ---------------------------------
#
# Forty identically titled notices from one sender whose bodies deliberately
# never state their year. Content alone cannot separate them, so only a metadata
# filter or a contextual header can. Three fall in the year asked about.

_PARKING_CONTRAVENTIONS: tuple[str, ...] = (
    "a controlled zone without a valid permit displayed",
    "a residents bay while displaying an expired permit",
    "a loading bay outside the permitted loading hours",
    "a marked disabled bay without a valid badge on display",
    "a suspended bay signed as out of use for works",
    "a footway where waiting is prohibited at any time",
    "a bay reserved for permit holders of another zone",
    "a single yellow line during the restricted period",
    "a school entrance marking during the restricted period",
    "a taxi rank during its hours of operation",
    "a bus stop clearway during its hours of operation",
    "a pedestrian crossing zig-zag marking",
)

#: (year, how many notices that year). 2022 is deliberately the sparse year, so
#: the case expects three documents out of a pool of forty.
_PARKING_YEARS: tuple[tuple[int, int], ...] = (
    (2019, 7),
    (2020, 7),
    (2021, 7),
    (2022, 3),
    (2023, 8),
    (2024, 8),
)

_PARKING: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"parking-{year}{chr(ord('a') + nth)}",
        sender_name=_sender("Civic Parking Office"),
        kind_slug="parking-ticket",
        document_date=date(year, 1 + (nth * 5) % 12, 1 + (nth * 9) % 27),
        title="Penalty charge notice",
        body=(
            "A penalty charge notice has been issued in respect of the vehicle "
            "described below, which was observed parked in "
            f"{_PARKING_CONTRAVENTIONS[(nth + year) % len(_PARKING_CONTRAVENTIONS)]}. "
            "The reduced amount applies if paid within fourteen days of the "
            "date of service. Representations may be made in writing to the "
            "address shown, and an appeal to the independent adjudicator "
            "follows only after a notice of rejection has been issued."
        ),
    )
    for year, count in _PARKING_YEARS
    for nth in range(count)
)


# --- Case 6: the control --------------------------------------------------------
#
# One distinctive term in exactly one document, with no near neighbour anywhere
# in the corpus. This case must pass at baseline. If it does not, the embedder,
# the seeding or the eval harness is broken — not the retrieval design — and no
# other case's result should be believed until it is fixed. Mirrors the role
# `complete-no-gaps` plays in the disclosure scenarios.

_CONTROL: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="control-kiln",
        sender_name=_sender("Ashgrove Pottery"),
        kind_slug="receipt",
        document_date=date(2024, 2, 29),
        title="Kiln element replacement",
        body=(
            "Replacement of three spiral kiln elements and one thermocouple, "
            "including recalibration of the controller against a reference "
            "probe. The kiln was fired to a test schedule before collection."
        ),
    ),
)


# --- Filler ---------------------------------------------------------------------
#
# Bulk noise so the haystack is large enough for recall@10 to discriminate. These
# are never any case's expected answer; they exist to occupy ranks. Generated
# rather than hand-written because their only requirements are "plausible
# archive prose" and "does not collide with a case's vocabulary" — the cases'
# own distractors above are where the difficulty is deliberately placed.

_FILLER_SUBJECTS: tuple[tuple[str, str, str], ...] = (
    ("Lakeside Dental", "invoice", "Routine examination and a small filling to one molar."),
    ("Lakeside Dental", "letter", "A reminder that a routine examination is now due."),
    ("Vellum Books", "receipt", "Three secondhand hardbacks and a reading light."),
    ("Copperfield Removals", "quote", "Estimate for a two-room move including packing materials."),
    ("Copperfield Removals", "invoice", "Two-room move completed, including packing materials."),
    ("Harbour Insurance", "contract", "Contents policy schedule for the coming year."),
    ("Harbour Insurance", "letter", "Confirmation that the contents policy renewed automatically."),
    ("Fenwick Council", "letter", "Notice of the residents parking scheme consultation."),
    ("Fenwick Council", "invoice", "Annual local charge, payable in ten monthly instalments."),
    ("Orchard Vets", "invoice", "Annual vaccination and a general health check."),
    ("Orchard Vets", "receipt", "Flea and worming treatment collected from reception."),
    (
        "Stonebridge Gym",
        "contract",
        "Membership terms, including the notice period for cancellation.",
    ),
    ("Stonebridge Gym", "receipt", "Monthly membership payment."),
    ("Larkspur Travel", "ticket", "Return rail tickets with seat reservations both ways."),
    ("Larkspur Travel", "receipt", "Booking fee and seat reservation charges."),
    ("Kestrel Broadband", "invoice", "Monthly broadband and line rental."),
    ("Kestrel Broadband", "letter", "Notice of a change to the fair usage policy."),
    ("Thornbury Garage", "invoice", "Annual service, oil change and two new tyres."),
    ("Thornbury Garage", "certificate", "Roadworthiness test passed with no advisories."),
    ("Millrace Storage", "contract", "Terms for a small self-storage unit let monthly."),
    ("Millrace Storage", "invoice", "Monthly storage unit charge."),
    ("Bramble Landscaping", "quote", "Estimate for replacing a fence and re-turfing a lawn."),
    ("Bramble Landscaping", "invoice", "Fence replacement and re-turfing completed."),
    ("Aldergate Opticians", "invoice", "Eye examination and one pair of single-vision lenses."),
    ("Aldergate Opticians", "certificate", "Prescription record following an eye examination."),
)

# --- Crowders: long documents whose job is to compete for candidate slots -------
#
# Issue #106 asks for "fixtures that are long purely to crowd". These are they.
#
# They exist for two measurable reasons, neither of which the single-chunk corpus
# could serve:
#
# 1. **The corpus has to outgrow the ANN prefetch window.** `semantic_search`
#    prefetches `top_k * 5 * VECTOR_CANDIDATE_FANOUT` CHUNKS before collapsing to
#    one row per document — 300 at the breadth case's k=12. A 201-chunk corpus
#    sits under that, so on a clean stack the vector leg is an exact global argmax
#    and the eval scores a retriever that is not the one that ships. The deployed
#    archive (1300 chunks) binds; CI did not.
# 2. **Chunk count has to vary before it can be measured.** Whether a document's
#    chunk count buys it rank — the open question behind #105 and #106 — cannot be
#    asked of a corpus with no variation in it.
#
# They are LONG and they are CROWDERS, never expected. That ordering is the whole
# design and it is the opposite of the obvious reading of #106: ranking is
# max-over-chunks, so a document with c chunks gets c draws under a null
# retriever. Lengthening the EXPECTED documents raises the blind floor (three
# 5-chunk expected among single-chunk crowders sit at 0.70, against a 0.35
# ceiling) and makes a case easier while looking harder. Lengthening the crowders
# lowers it, which is what difficulty means here.
#
# Subject matter is installation and building work: the breadth case asks "find
# every document about the solar panel installation", so documents about OTHER
# installation programmes are genuine near-misses on the word that carries the
# query. They share no sender and no title with the solar block, so the blind
# floor cannot infer the competition — hence the explicit `crowds`.

#: Length that yields exactly `_CROWDER_CHUNKS` chunks, chosen mid-band rather
#: than at a threshold: 6795 characters is already 5 chunks and 8596 is 6, so
#: 7400 sits far from both edges and survives the +/-80 boundary guard.
_CROWDER_TARGET_CHARS: int = 7400

#: Declared, and checked against the real chunker by the structural test. Every
#: crowder is truncated to the same length, so one constant covers them all.
_CROWDER_CHUNKS: int = 5

_CROWDER_PARAGRAPHS: tuple[str, ...] = (
    "The installation programme is divided into stages, and each stage is signed "
    "off in writing before the next begins. Access equipment is erected first, "
    "then fixings are set out against the survey drawing, and only then is any "
    "connection made to the existing system.",
    "Access equipment remains in place for the duration of the works. The "
    "scaffold is inspected weekly and a record of each inspection is kept with "
    "the site paperwork. Nothing is fixed to the structure until that record "
    "shows a current inspection.",
    "The electrical connection is carried out by a qualified engineer working to "
    "the current wiring rules. Circuits are tested before energising, readings "
    "are recorded against the schedule, and the schedule is issued with the "
    "completion paperwork rather than separately.",
    "Materials are held at the depot until the week of installation so that "
    "nothing is stored on site longer than necessary. Deliveries are booked "
    "against the programme, and any change to the delivery date is confirmed in "
    "writing because it moves every later stage with it.",
    "Weather delays are absorbed into the programme where possible. Work at "
    "height stops when wind speeds exceed the threshold set out in the method "
    "statement, and the day is recorded as lost rather than being charged.",
    "On completion the system is demonstrated to the occupier and the paperwork "
    "is handed over as one bundle: the commissioning record, the test schedule, "
    "the operating instructions and the maintenance recommendations. Keeping "
    "them together is what makes a later claim straightforward.",
    "Maintenance is recommended annually. The recommendation is not a condition "
    "of anything issued here, and it is set out so that the intervals are known "
    "at handover rather than discovered later.",
    "Any variation to the agreed scope is quoted separately before it is carried "
    "out. Work already completed is invoiced at the agreed stage rate regardless "
    "of whether a variation is subsequently agreed.",
    "The survey drawing governs the layout. Where site conditions differ from "
    "the drawing, the difference is recorded with a photograph and the layout is "
    "reissued before the affected stage proceeds.",
    "Waste is removed from site at the end of each stage and disposed of under "
    "the relevant duty of care. Transfer notes are retained and are available on "
    "request for two years from the date of the works.",
    "Payment is due against the stage schedule. Retention, where it applies, is "
    "released once the completion paperwork has been issued and any outstanding "
    "items on the snagging list have been closed.",
    "Notice of the intended start date is given at least ten working days in "
    "advance. Where access to a neighbouring property is required, that notice "
    "is served separately and the works cannot begin until it has been "
    "acknowledged.",
)

_CROWDER_SUBJECTS: tuple[tuple[str, str], ...] = (
    ("letter", "Loft conversion programme"),
    ("quote", "Estimate for a full rewire"),
    ("invoice", "Rewiring works, first stage"),
    ("letter", "Heat pump installation programme"),
    ("invoice", "Heat pump installation, balance due"),
    ("certificate", "Electrical installation condition report"),
    ("letter", "Roof covering replacement programme"),
    ("invoice", "Roof covering replacement"),
    ("quote", "Estimate for replacement windows"),
    ("invoice", "Replacement windows fitted"),
    ("letter", "Electric vehicle charger installation"),
    ("invoice", "Electric vehicle charger, supply and fit"),
    ("quote", "Estimate for a rear extension"),
    ("letter", "Rear extension programme"),
    ("invoice", "Rear extension, second stage"),
    ("certificate", "Completion record for the extension"),
    ("letter", "Damp proofing programme"),
    ("invoice", "Damp proofing works"),
    ("quote", "Estimate for external wall insulation"),
    ("invoice", "External wall insulation fitted"),
    ("letter", "Drainage renewal programme"),
    ("invoice", "Drainage renewal works"),
    ("quote", "Estimate for a garage conversion"),
    ("invoice", "Garage conversion, final stage"),
)


def _crowder_body(offset: int) -> str:
    """Deterministic prose of exactly ``_CROWDER_TARGET_CHARS`` characters.

    Paragraphs are cycled from ``offset`` and truncated on a word boundary to a
    fixed length, which is what lets a single declared ``chunks`` constant cover
    every crowder and keeps them all clear of a chunk threshold.

    **They are rotations of one another, not 24 independent documents.** The
    paragraph pool is far smaller than the target length, so each body repeats
    its sources two or three times and any two crowders share roughly 94% of
    their sentences. That is enough for what they are for — occupying candidate
    slots with plausible non-answers — and it is measurably stable (three eval
    runs across two stacks returned identical rankings). But it does mean their
    embeddings are near-identical, so the ORDER among them is an arbitrary
    tie-break, and adding more distinct paragraph material would be a real
    improvement rather than a cosmetic one.
    """
    parts: list[str] = []
    length = 0
    index = offset
    while length < _CROWDER_TARGET_CHARS:
        paragraph = _CROWDER_PARAGRAPHS[index % len(_CROWDER_PARAGRAPHS)]
        parts.append(paragraph)
        length += len(paragraph) + 1
        index += 1
    body = " ".join(parts)[:_CROWDER_TARGET_CHARS]
    return body[: body.rfind(" ")]


_CROWDERS: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"crowder-{index:02d}",
        sender_name=_sender("Halcyon Property Services"),
        kind_slug=kind,
        document_date=date(2021 + (index % 4), 1 + (index % 12), 1 + (index % 27)),
        title=title,
        body=_crowder_body(index),
        chunks=_CROWDER_CHUNKS,
        crowds=("breadth-many-mentions",),
    )
    for index, (kind, title) in enumerate(_CROWDER_SUBJECTS, start=1)
)


_FILLER: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"filler-{index:02d}",
        sender_name=_sender(sender),
        kind_slug=kind,
        # Spread across 2021-2024 so no case's date filter accidentally isolates
        # the whole filler set into or out of its range.
        document_date=date(2021 + (index % 4), 1 + (index % 12), 1 + (index % 27)),
        title=summary.rstrip(".").split(",")[0],
        body=(
            f"{summary} This document was issued in the ordinary course and "
            "requires no action. Payment terms, where they apply, are thirty "
            "days from the date shown. Retain for your records."
        ),
    )
    for index, (sender, kind, summary) in enumerate(_FILLER_SUBJECTS, start=1)
)


#: The whole haystack, seeded once and shared by every case.
# --- Case 7: an answer buried in one passage of a long document ------------------
#
# The case issue #106 asks for, built the way the blind floor says it has to be
# rather than the way the issue describes it.
#
# #106 says: "cases whose answer lives in a specific passage of a long document".
# Read literally that makes the EXPECTED documents the long ones — and measured,
# that is unbuildable. Ranking is max-over-chunks, so chunks are draws: two
# 5-chunk expected documents among twenty single-chunk crowders have a blind
# floor of 0.92. A retriever ranking at random would score 92% and the case would
# look hard while measuring nothing.
#
# So the long documents are the CROWDERS and the expected documents are the
# SHORTER ones — three chunks against five. The retrieval problem is still
# "reach a document whose answer is one passage among several", which is what
# #106 wants; what changes is which side carries the length. Floor: 0.2516.
#
# The answer sentence sits in the middle chunk, 2,400 characters in, placed by
# measurement rather than arithmetic: the 200-character overlap carries a
# sentence within ~200 characters of a boundary into TWO chunks, which would both
# falsify "the answer is in one chunk" and give that answer two draws instead of
# one. `answer_needle` lets the structural tests hold that.

#: Length yielding exactly three chunks, mid-band. Shorter than a crowder, which
#: is the whole point — see the note above.
_AGREEMENT_TARGET_CHARS: int = 4200
_AGREEMENT_CHUNKS: int = 3

#: Where the answer sentence starts. Measured: at 2,400 characters it lands in
#: chunk 1 of 3 and in no other; at 1,800 or 3,400 it drifts to a neighbour.
_NEEDLE_OFFSET: int = 2400

#: The sentence that answers the question, and nothing else in the corpus says
#: it. Deliberately specific — ninety days, served before the anniversary — so
#: the crowders can discuss notice at length without accidentally answering.
CANCELLATION_NEEDLE: str = (
    "Cancellation requires ninety days written notice served before the "
    "anniversary date, and no refund of the annual charge arises where notice "
    "is served after it."
)

#: Crowder prose for the passage case. Several of these paragraphs discuss
#: notice, cancellation and termination AT LENGTH, with different periods and
#: about different things — which is the point.
#:
#: The first version of these crowders never mentioned notice at all, and the
#: case scored 1.00 in both environments: the question's own vocabulary
#: ("notice", "cancel", "maintenance agreement") appeared in exactly two
#: documents, so finding them was a lexical exercise and no retrieval quality
#: was being measured. A case that cannot fail at baseline can register a
#: regression but can never show an improvement, which is most of what this
#: corpus exists for.
#:
#: This is the corpus's standing technique — "hand-authored near-miss
#: distractors: same sender, same kind, adjacent dates, overlapping vocabulary"
#: — applied where it was missing.
_AGREEMENT_PARAGRAPHS: tuple[str, ...] = (
    "The agreement sets out the scope of the maintenance service, the response "
    "times that apply to each category of fault, and the circumstances in which "
    "an attendance is chargeable rather than included in the annual charge.",
    "Either party may give notice to vary the schedule of covered equipment. "
    "Thirty days notice is required, and the revised schedule takes effect from "
    "the start of the following month rather than immediately.",
    "Notice of a change to the call-out rate is given in writing sixty days "
    "before it applies. A customer who does not wish to accept the revised rate "
    "may say so in the same period, and the previous rate is held until the "
    "anniversary.",
    "Cancellation of an individual visit requires two working days notice. A "
    "visit cancelled with less notice than that is chargeable at the standard "
    "attendance rate whether or not the engineer has set out.",
    "Termination for non-payment follows a separate route and is not subject to "
    "the notice arrangements described elsewhere in this agreement. Fourteen "
    "days is allowed to remedy an overdue balance before cover is suspended.",
    "The maintenance agreement renews on its anniversary unless it has been "
    "brought to an end beforehand. Renewal carries the schedule of charges "
    "current at that date, which is issued in advance.",
    "Where the property changes hands the agreement may be transferred rather "
    "than cancelled. The incoming owner confirms acceptance in writing and no "
    "notice period arises on a transfer.",
    "A suspension is not a cancellation. Cover may be suspended by agreement for "
    "up to three months, during which no charge accrues and no notice is "
    "required to resume.",
    "Records of every attendance are retained for six years. Copies are provided "
    "on request, and are provided automatically when an agreement ends for any "
    "reason.",
    "Response times are measured from the time a fault is reported rather than "
    "from the time it occurs. Out of hours reports are timed from the start of "
    "the next working day unless the fault is an emergency.",
)


def _agreement_prose(offset: int) -> str:
    """Cycled crowder prose, so no two agreements read identically."""
    parts: list[str] = []
    length = 0
    index = offset
    while length < _CROWDER_TARGET_CHARS + _AGREEMENT_PROSE_HEADROOM:
        paragraph = _AGREEMENT_PARAGRAPHS[index % len(_AGREEMENT_PARAGRAPHS)]
        parts.append(paragraph)
        length += len(paragraph) + 1
        index += 1
    return " ".join(parts)


#: Extra prose generated beyond the longest body that consumes it, so
#: `_agreement_body` can slice a window *after* the needle without running off
#: the end. Needed because the answer bodies read `prose[len(before):...]` rather
#: than restarting at zero.
_AGREEMENT_PROSE_HEADROOM: int = 400


def _agreement_body(needle: str | None, offset: int = 0) -> str:
    """A maintenance-agreement body, optionally with the answer buried in it."""
    prose = _agreement_prose(offset)
    if needle is None:
        filler = prose[:_CROWDER_TARGET_CHARS]
        return filler[: filler.rfind(" ")]
    before = prose[:_NEEDLE_OFFSET]
    before = before[: before.rfind(" ") + 1]
    remaining = _AGREEMENT_TARGET_CHARS - len(before) - len(needle) - 1
    if remaining <= 0:  # pragma: no cover - guarded by the chunk-count test
        raise ValueError(
            f"needle of {len(needle)} chars leaves no room in a "
            f"{_AGREEMENT_TARGET_CHARS}-char body after {len(before)} chars of lead-in"
        )
    # CONTINUE the prose past the needle rather than restarting it. Slicing
    # `prose[:remaining]` here re-read from index 0, so every answer document
    # carried its own first ~1,600 characters twice — invisible to the chunk-count
    # guard (the length is the same either way) and quietly doubling the lexical
    # weight of whatever happened to open the document.
    after = prose[len(before) : len(before) + remaining]
    return before + needle + " " + after[: after.rfind(" ")]


_AGREEMENT_ANSWERS: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"agreement-{slug}",
        sender_name=_sender("Verity Maintenance"),
        kind_slug="contract",
        document_date=date(2023, month, 14),
        title=title,
        body=_agreement_body(CANCELLATION_NEEDLE, offset),
        chunks=_AGREEMENT_CHUNKS,
    )
    for offset, (slug, month, title) in enumerate(
        (
            ("boiler", 3, "Maintenance agreement — boiler cover"),
            ("electrical", 9, "Maintenance agreement — electrical cover"),
            ("plumbing", 6, "Maintenance agreement — plumbing cover"),
        )
    )
)

#: Same sender, same shape, longer, and they never state the notice period. They
#: join the case's candidate pool by sender anyway; `crowds` is declared so the
#: "crowders are actually long" guard covers them too.
_AGREEMENT_CROWDERS: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"agreement-other-{index:02d}",
        sender_name=_sender("Verity Maintenance"),
        kind_slug="contract" if index % 3 else "letter",
        document_date=date(2021 + (index % 4), 1 + (index % 12), 1 + (index % 27)),
        title=title,
        body=_agreement_body(None, index),
        chunks=_CROWDER_CHUNKS,
        crowds=("passage-buried-clause",),
    )
    for index, title in enumerate(
        (
            "Maintenance agreement — annual review",
            "Maintenance agreement — schedule of charges",
            "Maintenance agreement — response times",
            "Maintenance agreement — parts and labour",
            "Maintenance agreement — out of hours cover",
            "Maintenance agreement — excluded works",
            "Maintenance agreement — access arrangements",
            "Maintenance agreement — payment terms",
            "Maintenance agreement — assignment and transfer",
            "Maintenance agreement — variation of scope",
            "Maintenance agreement — subcontracted work",
            "Maintenance agreement — complaints procedure",
            "Maintenance agreement — data and records",
            "Maintenance agreement — health and safety",
            "Maintenance agreement — insurance and liability",
            "Maintenance agreement — force majeure",
            "Maintenance agreement — dispute resolution",
            "Maintenance agreement — governing terms",
            "Maintenance agreement — service levels",
            "Maintenance agreement — reporting",
            "Maintenance agreement — spare parts holding",
            "Maintenance agreement — engineer competence",
            "Maintenance agreement — site attendance",
            "Maintenance agreement — annual statement",
        ),
        start=1,
    )
)

_AGREEMENTS: tuple[RecallDoc, ...] = _AGREEMENT_ANSWERS + _AGREEMENT_CROWDERS


CORPUS: tuple[RecallDoc, ...] = (
    _MORTGAGE
    + _BARE_FIGURES
    + _BOILER
    + _SOLAR
    + _PARKING
    + _CONTROL
    + _FILLER
    + _CROWDERS
    + _AGREEMENTS
)


CASES: tuple[RecallCase, ...] = (
    RecallCase(
        name="control-unique-term",
        question="What was done to the kiln?",
        expected_markers=("control-kiln",),
        why=(
            "Control, and the ONE case deliberately left saturated. A "
            "distinctive term in exactly one document with no near neighbour. "
            "It must score 1.00 at baseline; a failure here means the embedder, "
            "the seeding or the harness is broken and no other result should be "
            "believed. Every other case is built to be able to fail."
        ),
    ),
    RecallCase(
        name="contract-clause",
        question="What does my mortgage contract say about repaying early?",
        expected_markers=(
            "mortgage-2019-contract",
            "mortgage-2020-variation",
            "mortgage-2024-remortgage",
        ),
        why=(
            "The spec's motivating example for #5. Thirteen documents from the "
            "same sender discuss early repayment; only these three STATE the "
            "terms — the rest quote a figure, describe the idea in general, or "
            "mention it in passing. Three expected among thirteen candidates, "
            "so the case loses recall gradually rather than all at once."
        ),
    ),
    RecallCase(
        name="sender-named-bare-chunk",
        question="What has Northwind Energy billed me for?",
        expected_markers=(
            "bare-northwind-2019",
            "bare-northwind-2020",
            "bare-northwind-2021",
        ),
        why=(
            "THE case for #6. Thirteen identically titled annual statements "
            "whose bodies are figures blocks naming neither sender nor year, so "
            "content cannot distinguish a Northwind statement from any other. "
            "The sender exists only in metadata, which is exactly what a "
            "contextual header puts into the embedded text."
        ),
    ),
    RecallCase(
        name="kind-scoped",
        question="Show me the warranty for the boiler.",
        expected_markers=("boiler-warranty", "boiler-warranty-extension"),
        why=(
            "Exercises #5's kind filter. Thirteen documents about the same "
            "boiler from the same installer, and nearly all of them mention the "
            "warranty — the service reminder, the manual, the registration "
            "letter, the parts receipt. Only two ARE warranties."
        ),
    ),
    RecallCase(
        name="date-scoped",
        question="What parking penalties did I get in 2022?",
        expected_markers=("parking-2022a", "parking-2022b", "parking-2022c"),
        why=(
            "Exercises #5's date filters. Thirteen notices from one sender, all "
            "identically titled, whose bodies deliberately never state their "
            "year — so content alone cannot separate them and only the metadata "
            "filter or a contextual header can. Three of the thirteen fall in "
            "the year asked about."
        ),
    ),
    RecallCase(
        name="passage-buried-clause",
        question="What notice do I have to give to cancel the maintenance agreement?",
        expected_markers=(
            "agreement-boiler",
            "agreement-electrical",
            "agreement-plumbing",
        ),
        why=(
            "THE case for #106. Three agreements state the notice period in one "
            "passage each; twenty-four longer agreements from the same sender "
            "discuss every other term at length and never state it. Built with "
            "the CROWDERS long and the expected documents shorter, which is the "
            "opposite of the issue's wording: chunks are draws under "
            "max-over-chunks ranking, so long expected documents raise the blind "
            "floor to 0.92 and the case measures nothing. This way it is 0.25."
        ),
        answer_needle=CANCELLATION_NEEDLE,
    ),
    RecallCase(
        name="breadth-many-mentions",
        question="Find every document about the solar panel installation.",
        expected_markers=tuple(f"solar-{index:02d}" for index in range(1, 13)),
        why=(
            "Exercises #7. Twelve documents mention the array, so at the "
            "shipped top_k of 10 recall is capped at 0.83 BY CONSTRUCTION and "
            "no retrieval improvement can make it pass. Scored at k=12 so the "
            "case can pass once the model is able to ask for that depth."
        ),
        k=12,
    ),
)
