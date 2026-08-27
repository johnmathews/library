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

**One chunk per document, by construction.** Every ``body`` is shorter than
``embedding_chunk_chars`` (1800), so each document produces exactly one content
chunk. ``tests/test_recall_scenarios.py`` asserts this. It keeps document-level
recall unambiguous: a document is retrieved or it is not, with no question of
which of its chunks won.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Ceiling every ``RecallDoc.body`` must stay under so each document yields
#: exactly one chunk. Mirrors ``Settings.embedding_chunk_chars``; the structural
#: test imports the real setting and asserts this does not drift above it.
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


def _sender(name: str) -> str:
    return f"{name} {FIXTURE_SUFFIX}"


# --- Case 1: a clause inside a long contract -----------------------------------
#
# The spec's own motivating example for #5. The target is one 2019 mortgage
# contract; the distractors are the SAME sender's mortgage paperwork from
# adjacent years, all of which discuss repayment in similar language. Unscoped,
# the right year has to win on content alone.

_MORTGAGE: tuple[RecallDoc, ...] = (
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
        marker="mortgage-2017-offer",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="contract",
        document_date=date(2017, 2, 3),
        title="Mortgage offer — provisional terms",
        body=(
            "This provisional offer sets out indicative terms only and does not "
            "constitute an agreement. Early repayment conditions will be stated "
            "in full in the final agreement. The indicative fixed period is ten "
            "years and the indicative rate is held for ninety days from the date "
            "of this letter."
        ),
    ),
    RecallDoc(
        marker="mortgage-2021-statement",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2021, 1, 9),
        title="Annual mortgage statement",
        body=(
            "Opening balance, scheduled repayments received, and interest "
            "charged for the year. One voluntary repayment was received in "
            "March and applied to the principal. No compensation charge was "
            "raised. The remaining fixed period is stated on page two."
        ),
    ),
    RecallDoc(
        marker="mortgage-2019-insurance",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2019, 6, 14),
        title="Buildings insurance requirement",
        body=(
            "As a condition of the agreement dated this month, the property "
            "must be insured for its full reinstatement value for the duration "
            "of the loan. Evidence of cover must be provided annually. This "
            "letter does not vary any repayment term of the agreement."
        ),
    ),
)


# --- Case 2: the chunk that names nothing --------------------------------------
#
# THE case finding #6 exists for. The target's body is a bare figures block that
# never names its sender, its date or what kind of document it is — all of that
# lives only in the metadata. Without a context header the chunk cannot match a
# question that names the sender. Expected to FAIL at baseline and to pass once
# #6 lands; that delta is the measurement.

_BARE_FIGURES: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="energy-2024-annual-bare",
        sender_name=_sender("Northwind Energy"),
        kind_slug="utility-bill",
        document_date=date(2024, 11, 4),
        title="Annual statement",
        body=(
            "Period total                 412,80\n"
            "Instalments received         360,00\n"
            "Balance due                   52,80\n"
            "Meter reading start          18422\n"
            "Meter reading end            21067\n"
            "Standing charge included in the period total."
        ),
    ),
    RecallDoc(
        marker="energy-2023-annual-bare",
        sender_name=_sender("Northwind Energy"),
        kind_slug="utility-bill",
        document_date=date(2023, 11, 6),
        title="Annual statement",
        body=(
            "Period total                 388,15\n"
            "Instalments received         372,00\n"
            "Balance due                   16,15\n"
            "Meter reading start          15980\n"
            "Meter reading end            18422\n"
            "Standing charge included in the period total."
        ),
    ),
    RecallDoc(
        marker="water-2024-annual-bare",
        sender_name=_sender("Clearbrook Water"),
        kind_slug="utility-bill",
        document_date=date(2024, 10, 22),
        title="Annual statement",
        body=(
            "Period total                 141,20\n"
            "Instalments received         132,00\n"
            "Balance due                    9,20\n"
            "Meter reading start           0641\n"
            "Meter reading end             0718"
        ),
    ),
)


# --- Case 3: same words, different kind ----------------------------------------
#
# Exercises #5's `kind` filter. All four documents talk about the same boiler in
# similar language; only the kind distinguishes what the user is asking for.

_BOILER: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="boiler-warranty",
        sender_name=_sender("Halden Heating"),
        kind_slug="warranty",
        document_date=date(2022, 4, 18),
        title="Boiler warranty certificate",
        body=(
            "The appliance identified below is covered against defects in "
            "materials and workmanship for seven years from the installation "
            "date. Cover is conditional on an annual service being carried out "
            "by an approved engineer. This warranty does not cover damage "
            "caused by limescale, incorrect pressure, or third-party parts."
        ),
    ),
    RecallDoc(
        marker="boiler-invoice",
        sender_name=_sender("Halden Heating"),
        kind_slug="invoice",
        document_date=date(2022, 4, 18),
        title="Boiler supply and installation",
        body=(
            "Supply and installation of one condensing boiler including flue "
            "kit, system flush and commissioning. Labour two days. The seven "
            "year warranty is registered with the manufacturer on your behalf "
            "and the certificate follows separately."
        ),
    ),
    RecallDoc(
        marker="boiler-manual",
        sender_name=_sender("Halden Heating"),
        kind_slug="manual",
        document_date=date(2022, 4, 18),
        title="Boiler user instructions",
        body=(
            "Setting the system pressure, resetting after a lockout, and the "
            "annual service schedule. Operating outside the stated pressure "
            "range may invalidate the warranty. Keep this booklet with the "
            "appliance for the life of the installation."
        ),
    ),
    RecallDoc(
        marker="boiler-service-letter",
        sender_name=_sender("Halden Heating"),
        kind_slug="letter",
        document_date=date(2023, 4, 2),
        title="Annual service due",
        body=(
            "Your appliance is approaching its annual service date. An annual "
            "service by an approved engineer is a condition of the seven year "
            "warranty. Please book within the next thirty days to keep cover "
            "in force."
        ),
    ),
)


# --- Case 4: breadth ------------------------------------------------------------
#
# Twelve documents mention the same term. At the shipped top_k of 10 recall is
# capped at 10/12 = 0.83 BY CONSTRUCTION — no retrieval improvement can make this
# case pass at k=10, which is the point: it is unanswerable until #7 lets the
# model ask for more. Scored at k=12 so the case CAN pass, and the eval reports
# the k it used so a reader sees why this one differs.

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


# --- Case 5: the same term across years ----------------------------------------
#
# Exercises #5's date filters. Four near-identical parking notices; only the
# issue year separates them, and their bodies deliberately do not state it.

_PARKING: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"parking-{year}",
        sender_name=_sender("Civic Parking Office"),
        kind_slug="parking-ticket",
        document_date=date(year, 9, 17),
        title="Penalty charge notice",
        body=(
            "A penalty charge notice has been issued in respect of the vehicle "
            "described below, which was observed parked in a controlled zone "
            "without a valid permit displayed. The reduced amount applies if "
            "paid within fourteen days. Representations may be made in writing."
        ),
    )
    for year in (2021, 2022, 2023, 2024)
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
CORPUS: tuple[RecallDoc, ...] = (
    _MORTGAGE + _BARE_FIGURES + _BOILER + _SOLAR + _PARKING + _CONTROL + _FILLER
)


CASES: tuple[RecallCase, ...] = (
    RecallCase(
        name="control-unique-term",
        question="What was done to the kiln?",
        expected_markers=("control-kiln",),
        why=(
            "Control. A distinctive term in exactly one document with no near "
            "neighbour. Must pass at baseline; a failure here means the "
            "embedder, the seeding or the harness is broken and no other "
            "result should be believed."
        ),
    ),
    RecallCase(
        name="contract-clause",
        question="What does my mortgage contract say about repaying early?",
        expected_markers=("mortgage-2019-contract",),
        why=(
            "The spec's motivating example for #5. Three same-sender "
            "distractors discuss repayment in similar language; only one "
            "states the actual terms."
        ),
    ),
    RecallCase(
        name="sender-named-bare-chunk",
        question="What did Northwind Energy bill me for in 2024?",
        expected_markers=("energy-2024-annual-bare",),
        why=(
            "THE case for #6. The target's body is a figures block naming "
            "neither its sender nor its year — both live only in metadata — so "
            "a question naming the sender cannot match it on content. Expected "
            "to fail at baseline and to pass once contextual headers land; that "
            "delta is the measurement #6 is justified by."
        ),
    ),
    RecallCase(
        name="kind-scoped",
        question="Show me the warranty for the boiler.",
        expected_markers=("boiler-warranty",),
        why=(
            "Exercises #5's kind filter. Four documents about the same boiler "
            "all mention the warranty; only one IS the warranty."
        ),
    ),
    RecallCase(
        name="date-scoped",
        question="What parking penalty did I get in 2022?",
        expected_markers=("parking-2022",),
        why=(
            "Exercises #5's date filters. Four near-identical notices whose "
            "bodies deliberately never state their year, so content alone "
            "cannot separate them — only the metadata filter can."
        ),
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
