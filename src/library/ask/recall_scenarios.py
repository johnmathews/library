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
        marker="mortgage-2018-illustration",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2018, 11, 20),
        title="Illustration of borrowing costs",
        body=(
            "This illustration shows what the borrowing would cost under the "
            "product discussed. It explains in general terms that repaying "
            "early may result in a compensation charge, and that the amount of "
            "any such charge depends on the terms of the agreement eventually "
            "entered into. It states no figures for that charge and creates no "
            "obligation on either party."
        ),
    ),
    RecallDoc(
        marker="mortgage-2021-statement",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2021, 1, 9),
        title="Annual mortgage statement",
        body=(
            "Your statement for the year is enclosed. It shows the opening "
            "balance, the payments received, the interest applied and the "
            "closing balance. Where an overpayment has been made within the "
            "annual allowance it is shown separately. If you are considering "
            "repaying early, contact us for a redemption figure."
        ),
    ),
    RecallDoc(
        marker="mortgage-2022-overpayment",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2022, 5, 30),
        title="Overpayment received",
        body=(
            "We have applied the overpayment you sent to the outstanding "
            "balance. The amount fell within the annual allowance described in "
            "your agreement, so no compensation charge has been applied. Your "
            "monthly payment is unchanged; the term has shortened accordingly."
        ),
    ),
    RecallDoc(
        marker="mortgage-2023-redemption-quote",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2023, 2, 14),
        title="Redemption figure",
        body=(
            "The figure quoted below is valid for thirty days and assumes "
            "repayment in full on the date shown. It comprises the outstanding "
            "balance, interest to the repayment date, an administration fee, "
            "and a compensation charge calculated under the terms of your "
            "agreement. This letter quotes the figure; it does not restate the "
            "terms under which the charge arises."
        ),
    ),
    RecallDoc(
        marker="mortgage-2023-porting",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2023, 6, 5),
        title="Moving your mortgage to a new property",
        body=(
            "If you move and take this mortgage with you, the existing product "
            "and its remaining fixed period may be carried across. Where the "
            "new borrowing is smaller, the reduction is treated as a repayment "
            "and may attract a charge. Where it is larger, the additional "
            "amount is a separate product on its own terms."
        ),
    ),
    RecallDoc(
        marker="mortgage-2020-arrears",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2020, 2, 11),
        title="Missed payment",
        body=(
            "One monthly payment has not reached us. Please make the payment as "
            "soon as possible to avoid arrears being recorded. If your "
            "circumstances have changed, contact us to discuss options, which "
            "may include a temporary reduction in payments or an extension of "
            "the term."
        ),
    ),
    RecallDoc(
        marker="mortgage-2019-insurance",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2019, 6, 14),
        title="Buildings insurance requirement",
        body=(
            "The agreement requires buildings insurance to be maintained for "
            "the full reinstatement value throughout the term. Evidence of "
            "cover must be provided on request. Failure to maintain cover is a "
            "breach of the agreement and may entitle us to arrange insurance "
            "and recover the cost from you."
        ),
    ),
    RecallDoc(
        marker="mortgage-2024-completion",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2024, 3, 28),
        title="Your new product has started",
        body=(
            "The new product recorded in your remortgage paperwork has now "
            "taken effect. Your first payment under it is due on the date "
            "shown. The previous product has been closed and any charge arising "
            "on its closure has already been accounted for in the completion "
            "statement."
        ),
    ),
    RecallDoc(
        marker="mortgage-2022-annual",
        sender_name=_sender("Meridian Mortgages"),
        kind_slug="letter",
        document_date=date(2022, 1, 11),
        title="Annual mortgage statement",
        body=(
            "Your statement for the year is enclosed. Interest was applied "
            "monthly at the contract rate. The closing balance reflects all "
            "payments received up to the statement date. Keep this document "
            "with your records; it is not a redemption figure and cannot be "
            "used to repay the loan."
        ),
    ),
)


# --- Case 3: a sender named in the question, absent from the text ----------------
#
# THE case for #6. Thirteen annual statements whose bodies are figures blocks
# naming neither their sender nor their year, all titled identically. Content
# alone cannot separate a Northwind statement from a Clearbrook one, because the
# distinguishing facts live only in metadata. Three of the thirteen are the
# expected answer, so the case has room to lose recall rather than being a
# single document that either appears in ten slots or does not.

_BARE_FIGURE_STATEMENTS: tuple[tuple[str, int, int, str], ...] = (
    ("Northwind Energy", 2024, 11, "412,80 | 360,00 | 52,80 | 18422 | 21067"),
    ("Northwind Energy", 2023, 11, "388,15 | 372,00 | 16,15 | 15980 | 18422"),
    ("Northwind Energy", 2022, 10, "351,40 | 348,00 | 3,40 | 13655 | 15980"),
    ("Clearbrook Water", 2024, 10, "214,60 | 198,00 | 16,60 | 4471 | 4712"),
    ("Clearbrook Water", 2023, 10, "205,90 | 204,00 | 1,90 | 4238 | 4471"),
    ("Clearbrook Water", 2022, 9, "197,25 | 180,00 | 17,25 | 4011 | 4238"),
    ("Ironbridge Gas", 2024, 12, "509,10 | 480,00 | 29,10 | 22840 | 26115"),
    ("Ironbridge Gas", 2023, 12, "477,55 | 468,00 | 9,55 | 19702 | 22840"),
    ("Ironbridge Gas", 2022, 11, "441,00 | 432,00 | 9,00 | 16833 | 19702"),
    ("Cavendish Power", 2024, 9, "298,45 | 288,00 | 10,45 | 9120 | 10344"),
    ("Cavendish Power", 2023, 9, "281,70 | 276,00 | 5,70 | 8015 | 9120"),
    ("Cavendish Power", 2022, 8, "266,30 | 252,00 | 14,30 | 7002 | 8015"),
    ("Cavendish Power", 2021, 8, "251,05 | 240,00 | 11,05 | 6120 | 7002"),
)


def _bare_figures_body(figures: str) -> str:
    """Render one statement as a figures block that names nothing."""
    total, instalments, balance, start, finish = figures.split(" | ")
    return (
        f"Period total                 {total}\n"
        f"Instalments received         {instalments}\n"
        f"Balance due                   {balance}\n"
        f"Meter reading start          {start}\n"
        f"Meter reading end            {finish}\n"
        "Standing charge included in the period total."
    )


_BARE_FIGURES: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"bare-{sender.split()[0].lower()}-{year}",
        sender_name=_sender(sender),
        kind_slug="utility-bill",
        document_date=date(year, month, 4 + index % 20),
        title="Annual statement",
        body=_bare_figures_body(figures),
    )
    for index, (sender, year, month, figures) in enumerate(_BARE_FIGURE_STATEMENTS)
)


# --- Case 4: one kind among many documents about the same object -----------------
#
# Thirteen documents about the same boiler, from the same installer, nearly all
# of which mention the warranty. Two of them ARE warranties. The cluster is
# larger than the ten-slot cut so the case can lose recall.

_BOILER: tuple[RecallDoc, ...] = (
    RecallDoc(
        marker="boiler-warranty",
        sender_name=_sender("Halden Heating"),
        kind_slug="warranty",
        document_date=date(2022, 4, 18),
        title="Boiler warranty certificate",
        body=(
            "This certificate warrants the appliance against defects in "
            "manufacture for seven years from the date of commissioning, "
            "provided an annual service is carried out by a registered "
            "engineer and recorded in the service log. The warranty covers "
            "parts and labour for the heat exchanger and the main circuit "
            "board, and parts only for the pump and the diverter valve."
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
            "registered engineer. The extension covers parts and labour for "
            "the heat exchanger and parts only for all other components."
        ),
    ),
    RecallDoc(
        marker="boiler-invoice",
        sender_name=_sender("Halden Heating"),
        kind_slug="invoice",
        document_date=date(2022, 4, 18),
        title="Boiler supply and installation",
        body=(
            "Supply and installation of a condensing combination boiler, "
            "including a magnetic system filter, a new pressure relief "
            "discharge and a full system flush. The warranty certificate is "
            "issued separately on commissioning and registration."
        ),
    ),
    RecallDoc(
        marker="boiler-quote",
        sender_name=_sender("Halden Heating"),
        kind_slug="quote",
        document_date=date(2022, 3, 2),
        title="Quotation for a replacement boiler",
        body=(
            "Estimate for removing the existing appliance and installing a "
            "condensing combination boiler. The price includes commissioning "
            "and registration, which is what activates the manufacturer "
            "warranty. The estimate is valid for sixty days."
        ),
    ),
    RecallDoc(
        marker="boiler-manual",
        sender_name=_sender("Halden Heating"),
        kind_slug="manual",
        document_date=date(2022, 4, 18),
        title="Boiler user instructions",
        body=(
            "These instructions describe how to set the timer, adjust the flow "
            "temperature and repressurise the system. Servicing must be carried "
            "out annually by a registered engineer; failure to do so will "
            "invalidate the warranty supplied with the appliance."
        ),
    ),
    RecallDoc(
        marker="boiler-thermostat-manual",
        sender_name=_sender("Halden Heating"),
        kind_slug="manual",
        document_date=date(2022, 4, 19),
        title="Programmable thermostat instructions",
        body=(
            "Pairing, scheduling and holiday mode for the wireless thermostat "
            "supplied with the installation. The thermostat carries its own two "
            "year warranty from the manufacturer, which is separate from the "
            "cover held on the appliance itself."
        ),
    ),
    RecallDoc(
        marker="boiler-registration",
        sender_name=_sender("Halden Heating"),
        kind_slug="letter",
        document_date=date(2022, 4, 25),
        title="Appliance registered",
        body=(
            "The appliance has been registered with the manufacturer and with "
            "the competent persons scheme. Registration is what brings the "
            "warranty into force. Your certificate has been issued separately "
            "and should be kept with the installation paperwork."
        ),
    ),
    RecallDoc(
        marker="boiler-flue-certificate",
        sender_name=_sender("Halden Heating"),
        kind_slug="certificate",
        document_date=date(2022, 4, 18),
        title="Flue and combustion check",
        body=(
            "Combustion analysis was carried out at commissioning and the "
            "readings recorded below fall within the manufacturer tolerance. "
            "This record is required evidence should a warranty claim be made "
            "on the heat exchanger."
        ),
    ),
    RecallDoc(
        marker="boiler-service-letter",
        sender_name=_sender("Halden Heating"),
        kind_slug="letter",
        document_date=date(2023, 4, 2),
        title="Annual service due",
        body=(
            "Your appliance is due its annual service. Booking the service "
            "keeps the warranty valid; a lapse of more than twelve months "
            "between services will end cover even where the appliance has not "
            "failed. Call or reply to arrange a visit."
        ),
    ),
    RecallDoc(
        marker="boiler-service-2023",
        sender_name=_sender("Halden Heating"),
        kind_slug="certificate",
        document_date=date(2023, 5, 11),
        title="Annual service record",
        body=(
            "The annual service was carried out and the appliance left in "
            "working order. The service log has been updated, which preserves "
            "the cover held on the appliance. No remedial work was required."
        ),
    ),
    RecallDoc(
        marker="boiler-service-2024",
        sender_name=_sender("Halden Heating"),
        kind_slug="certificate",
        document_date=date(2024, 5, 8),
        title="Annual service record",
        body=(
            "The annual service was carried out. The expansion vessel was "
            "recharged and the system pressure reset. The service log has been "
            "updated so that cover on the appliance continues uninterrupted."
        ),
    ),
    RecallDoc(
        marker="boiler-parts-receipt",
        sender_name=_sender("Halden Heating"),
        kind_slug="receipt",
        document_date=date(2024, 5, 8),
        title="Replacement expansion vessel",
        body=(
            "Supply of a replacement expansion vessel and fitting during the "
            "annual visit. This part is outside the parts and labour cover held "
            "on the appliance and is therefore charged separately."
        ),
    ),
    RecallDoc(
        marker="boiler-complaint",
        sender_name=_sender("Halden Heating"),
        kind_slug="letter",
        document_date=date(2023, 9, 19),
        title="Response to your enquiry about a noise",
        body=(
            "Thank you for reporting the noise from the appliance. The engineer "
            "found no fault on inspection and the reading taken was within "
            "tolerance. No claim has been made against the cover held on the "
            "appliance and it remains in force."
        ),
    ),
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


# --- Case 5: the same term across years ----------------------------------------
#
# Exercises #5's date filters. Four near-identical parking notices; only the
# issue year separates them, and their bodies deliberately do not state it.

#: Thirteen notices whose bodies deliberately never state their year, so content
#: alone cannot separate them. Three fall in 2022, which is more than a single
#: document that either lands inside ten slots or does not, and the cluster as a
#: whole is larger than the cut so the case has room to lose recall.
_PARKING_NOTICES: tuple[tuple[str, int, int, int, str], ...] = (
    ("a", 2020, 3, 4, "a controlled zone without a valid permit displayed"),
    ("a", 2021, 9, 17, "a controlled zone without a valid permit displayed"),
    ("b", 2021, 11, 2, "a residents bay while displaying an expired permit"),
    ("a", 2022, 9, 17, "a controlled zone without a valid permit displayed"),
    ("b", 2022, 4, 26, "a loading bay outside the permitted loading hours"),
    ("c", 2022, 12, 8, "a marked disabled bay without a valid badge on display"),
    ("a", 2023, 9, 17, "a controlled zone without a valid permit displayed"),
    ("b", 2023, 2, 13, "a suspended bay signed as out of use for works"),
    ("c", 2023, 7, 30, "a footway where waiting is prohibited at any time"),
    ("a", 2024, 9, 17, "a controlled zone without a valid permit displayed"),
    ("b", 2024, 1, 22, "a bay reserved for permit holders of another zone"),
    ("c", 2024, 6, 9, "a single yellow line during the restricted period"),
    ("d", 2024, 10, 15, "a school entrance marking during the restricted period"),
)

_PARKING: tuple[RecallDoc, ...] = tuple(
    RecallDoc(
        marker=f"parking-{year}{suffix}",
        sender_name=_sender("Civic Parking Office"),
        kind_slug="parking-ticket",
        document_date=date(year, month, day),
        title="Penalty charge notice",
        body=(
            "A penalty charge notice has been issued in respect of the vehicle "
            f"described below, which was observed parked in {contravention}. "
            "The reduced amount applies if paid within fourteen days of the "
            "date of service. Representations may be made in writing to the "
            "address shown, and an appeal to the independent adjudicator "
            "follows only after a notice of rejection has been issued."
        ),
    )
    for suffix, year, month, day, contravention in _PARKING_NOTICES
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
            "bare-northwind-2024",
            "bare-northwind-2023",
            "bare-northwind-2022",
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
