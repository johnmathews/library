"""The spending API: the chart engine's thirteen routes (spec §9.6, §8.4).

Thin by design. Every number here is computed by `library.charts.query`,
`library.charts.footer`, `library.charts.draft` or `library.spend_lines`; this
module parses, validates, calls them, and serialises. **No router builds SQL**
against `spend_facts` — the two invariants those modules hold (the total is
invariant across split changes, and the drill-through sums to the bar) live in
one place precisely so a second copy of the query cannot drift from them.

At `/api/spending` rather than §9.6's `/api/charts`, **permanently**. The
original note here promised this router would take `/api/charts` once the legacy
series stack that owned those thirteen routes was deleted. That stack was deleted
on 2026-08-31 and the promise is withdrawn: `/charts` is the name the redesign
replaced. Those routes answered "what did one `(sender, kind, currency)` series
do over time" — a question this engine deliberately does not ask — so inheriting
the prefix would carry the discarded model's vocabulary into the surface that
replaced it, and would break every stored link and client for nothing. The SPA
route stays `/charts` because that is the URL users have bookmarked; the
API/frontend asymmetry is accepted knowingly. See docs/charts.md §11.

Four things this module is responsible for that nothing underneath it can be:

* **`facets_in_rule`** (`_ChartQuery.facets_in_rule`). `chart_footer` trusts its
  caller for the set of facets the rule names; passing an empty set for a
  facet-bearing rule switches off §9.4's headline guarantee — uncategorised
  money stops being reported — silently, with no error and no test in
  `footer.py` able to notice.
* **`/cell` asking `/data`'s exact question** (`_SharedArgs`). `chart_series`
  and `chart_cell` share their predicate internally, but only if they are given
  the same arguments. The argument set is built once per request and unpacked
  into both, so a drift is a type error rather than a wrong panel.
* **The whole footer** (`_footer_out`). Eight fields, including `unclassified`
  (money with an amount and an undecided kind) and `unaccounted` (the live
  `ELSE`). Dropping either restores the bug it was added for: money that appears
  in no line of the accounting at all.
* **An empty rule means ALL SPENDING** (`draft_chart`). A draft whose clauses
  were all dropped comes back as `Rule(all=[])` plus `unknown_terms`, and
  previewing it would answer a narrow question with the whole archive's total.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from library.charts.draft import MAX_QUESTION_CHARS, DraftError, draft_rule
from library.charts.footer import (
    UNACCOUNTED,
    UNCATEGORISED,
    UNCLASSIFIED,
    UNDATED,
    ExcludedGroup,
    Footer,
    chart_footer,
    chart_footer_documents,
)
from library.charts.query import (
    SENDER_SPLIT,
    CellPayment,
    Series,
    Unconvertible,
    chart_cell,
    chart_series,
    period_start,
)
from library.charts.rule import Rule, RuleError, rule_predicate
from library.db import get_session
from library.facets.vocabulary import VocabularyFacet, VocabularyValue, load_vocabulary
from library.models import Chart, Document, Facet, FacetValue, Grain, LineLabel, Sender, SpendLine
from library.spend_lines import (
    AllocationError,
    LineInput,
    clear_lines,
    commit_allocation,
    replace_lines,
)

router: APIRouter = APIRouter(tags=["spending"])

#: Money is quantised **at the serialiser only**. The engine returns unquantised
#: `Decimal`s on purpose: rounding per cell would break `sum(cells) == total`,
#: which is the one thing §2.5 asks the headline and the drawing to agree on.
#: A zero is quantised too — `Decimal("0")` renders as `"0"`, and a footer line
#: reading `0` beside one reading `12.50` looks like two different kinds of
#: number.
_CENTS = Decimal("0.01")

#: `unknown_terms` is unbounded, model-authored text (§7.5's proposal). It is
#: reported, so it is also capped: a term is a facet or value key, and a model
#: that returns a paragraph is telling us something is wrong, not naming a facet.
MAX_UNKNOWN_TERMS = 20
MAX_TERM_CHARS = 120


def _money(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


# --- request and response models --------------------------------------------


class ChartIn(BaseModel):
    """A saved question (§9.1)."""

    name: str = Field(min_length=1, max_length=120)
    question_text: str = Field(default="", max_length=MAX_QUESTION_CHARS)
    rule: Rule = Rule()
    default_grain: Grain = Grain.MONTH
    default_split: str | None = None
    display_currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    ordinal: int = 0


class ChartPatch(BaseModel):
    """Every field optional; an absent field is left alone.

    `default_split` is genuinely nullable, so "clear the split axis" and "do not
    touch the split axis" cannot both be `None` in one field — the caller sets
    it to `null` to clear, and the sentinel below tells the two apart.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    question_text: str | None = Field(default=None, max_length=MAX_QUESTION_CHARS)
    rule: Rule | None = None
    default_grain: Grain | None = None
    default_split: str | None = None
    display_currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    ordinal: int | None = None


class ChartOut(BaseModel):
    id: int
    name: str
    question_text: str
    rule: Rule
    default_grain: Grain
    default_split: str | None
    display_currency: str
    ordinal: int


class ChartListOut(BaseModel):
    charts: list[ChartOut]


class CellOut(BaseModel):
    """One (time bucket, split bucket) point. `split_value` is null both when
    the chart has no split axis and when the row carries no value for it."""

    period: date
    split_value: str | None
    total: Decimal
    payments: int


class SplitValueOut(BaseModel):
    """One bucket of the split axis, resolved for display (§2.3).

    `value` is exactly what `/cell` must be sent back — the sender id as text,
    or the facet value key, never the label. Resolution is a display concern
    and lives here rather than in the engine, which keeps `split_value` stable
    across a rename (docs/charts.md §4).

    A **list** rather than a mapping: the unlabelled bucket's `value` is
    `null`, which cannot be a JSON object key, and it is the bucket whose name
    a client is least able to invent — it means "no value for this facet" under
    a facet split and "no sender" under `split=sender`.

    `colour` is a stored override; `null` means the client derives a stable
    palette slot from `value` (§2.5).
    """

    value: str | None
    label: str
    colour: str | None


class UnconvertibleOut(BaseModel):
    """Money the chart could not express in its display currency (§9.3).

    Merged from two sources that report **different rows**: `query.py`'s (rows
    that would have entered the total) and `footer.py`'s (rows it accounts for).
    After the merge the amount mixes signed nets with magnitudes, so it means
    "money the chart could not express", not "the net missing from the total" —
    and `documents` must be rendered beside it: an unconvertible payment and an
    equal unconvertible refund net to `amount == 0.00, documents == 2`, which
    would otherwise print as "nothing missing" while two documents are
    unrepresented.

    `documents` is an **upper bound** on the distinct documents behind the
    amount, never an understatement. Exactly one shape can be counted twice: a
    document split across spend lines with one line counted (reported by
    `query.py`) and another uncategorised (reported by `footer.py`), in a
    rateless currency. The other footer buckets are properties of the document
    rather than of a line, so they cannot co-occur with a counted line. Making
    it exact needs `Unconvertible` to carry document ids and merge as a union,
    which is an engine change. The field's purpose — stopping `amount == 0.00`
    from reading as "nothing missing" — is unaffected.

    `currency` is null when the amount carries no currency at all.
    """

    currency: str | None
    amount: Decimal
    documents: int


class ExcludedGroupOut(BaseModel):
    """Money the total did not count, and why. `amount_kind` is the reason, not
    always a kind: `unclassified`, `undated`, `uncategorised` and `unaccounted`
    name their own group."""

    amount_kind: str
    amount: Decimal
    documents: int


class FooterOut(BaseModel):
    """§9.4's accounting, in full. All eight fields, always present.

    An absent footer field and an empty one are different claims, and only one
    of them is "nothing was excluded", so the keys are never conditional.

    `unclassified` and `uncategorised` belong under **needs attention**, not
    under "excluded from the total": excluded means "correctly not spending",
    an undecided kind or a missing label means "not yet decided". `unaccounted`
    should always be null; if it is not, the classification has a hole and this
    is the money in it.
    """

    netted_refunds: Decimal
    refund_count: int
    excluded: list[ExcludedGroupOut]
    unclassified: ExcludedGroupOut | None
    uncategorised: ExcludedGroupOut | None
    undated: ExcludedGroupOut | None
    unaccounted: ExcludedGroupOut | None
    unconvertible: list[UnconvertibleOut]


class DataOut(BaseModel):
    """A chart's answer plus the accounting for what its total did not count.

    `grain`, `split`, `currency`, `since` and `until` echo the **resolved**
    arguments, not the request's: a client drilling into a cell sends them back
    to `/cell` verbatim and is then provably asking the same question the bar
    answered.

    `payments` and `documents` count what reached the total; the footer's own
    `documents` counts canonical rows in each group. The two are not a
    partition of the archive and must not be added.
    """

    chart_id: int | None
    grain: Grain
    split: str | None
    currency: str
    since: date | None
    until: date | None
    cells: list[CellOut]
    #: Every split bucket in `cells`, resolved for display (§2.3). Empty when
    #: the chart has no split axis.
    splits: list[SplitValueOut]
    total: Decimal
    payments: int
    documents: int
    footer: FooterOut


class CellDocumentOut(BaseModel):
    """One document behind a payment in a drilled-into cell (§9.5).

    `amount` and `currency` are optional because the columns are: a hand-made
    MERGE override can pull an amountless document into a group, and that is
    precisely the merge this panel exists to expose.
    """

    id: int
    title: str | None
    date: date | None
    amount: Decimal | None
    currency: str | None
    amount_kind: str | None
    reference: str | None
    is_canonical: bool


class CellPaymentOut(BaseModel):
    """One payment inside a cell. `total` is its contribution to the bar;
    summing `documents[].amount` instead would double a merged pair."""

    payment_id: int
    total: Decimal
    documents: list[CellDocumentOut]


class CellOutBody(BaseModel):
    """One cell's payments, and the bar they add up to.

    `total` is `/data`'s number for this cell, and the payments are rounded to
    sum to it exactly (`_rendered_shares`) — the panel is where a wrong merge is
    noticed, so a panel that does not add up to the bar it opened is the one
    thing it must never be.
    """

    period: date
    split_value: str | None
    total: Decimal
    payments: list[CellPaymentOut]
    #: This cell's split bucket, resolved for display — so a drilled panel can
    #: title itself without re-reading `/data`. `""` for an **unsplit** chart
    #: (`_resolve_splits` returns `[]`, so there is no bucket to resolve) —
    #: never a placeholder string, and distinct from an empty `label` on a
    #: real bucket, which cannot occur.
    label: str
    colour: str | None


class PreviewIn(BaseModel):
    """A rule to answer without saving it.

    `DraftIn`'s sibling, and the difference is the whole point: that one takes a
    *question* and spends a model call turning it into a rule, this one takes a
    rule the caller already has. The rule editor needs the second — it is
    showing the owner what their own edit would do, and asking a model to
    re-derive a rule the owner just typed would be both slower and less
    faithful.

    `display_currency` is required rather than defaulted for the same reason it
    is on `DraftIn`: a defaulted currency renders a plausible figure in the
    wrong denomination, which is worse than refusing.

    Extras are **forbidden**. A preview whose window silently failed to arrive
    still returns 200 and a plausible-looking chart — just one answering a
    different question — so a misspelled or renamed field has to be a 422 rather
    than a default. This is the one schema in the module where a dropped field
    is indistinguishable from a working feature.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    rule: Rule
    display_currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    grain: Grain = Grain.MONTH
    #: `""` means "no split axis", matching `/data`'s query-parameter reading.
    #: There is no saved chart to default from here, so unlike `/data` an absent
    #: key and an empty string may safely collapse to the same thing.
    split: str | None = None
    #: Aliased to `from`/`to`, the names `/data` uses for the same window on the
    #: query string — the editor previews the range the workspace is showing, so
    #: it forwards the toolbar's own arguments and they have to be spelled the
    #: same. `extra="forbid"` above is what makes that safe: without it a body
    #: sending `from`/`to` against fields named `since`/`until` is silently
    #: dropped and every preview is answered over all time, which looks like a
    #: working feature.
    since: date | None = Field(default=None, alias="from")
    until: date | None = Field(default=None, alias="to")


class DraftIn(BaseModel):
    """A question to draft a rule for.

    `question` is capped rather than truncated. `draft.py` trims at 500
    characters silently, and a question is the owner's *intent*, not evidence —
    a silently shortened intent drafts a rule for a question nobody asked.
    """

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    display_currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    grain: Grain = Grain.MONTH
    since: date | None = None
    until: date | None = None


class DraftOut(BaseModel):
    """A drafted rule, everything the vocabulary could not express, and a preview.

    `rule` and `preview` are **null** when every clause was dropped: an empty
    `Rule` matches every row, so previewing one would answer "money I spend on
    good vibes" with the whole archive's total — the most confidently wrong
    answer this feature can give. A null rule also cannot be round-tripped into
    a save, which is what keeps a failed draft out of the charts table (§7.5:
    say what cannot be expressed and propose the addition, never approximate).

    `expressible` is false whenever anything was dropped, including when a
    surviving rule is still previewed: a preview built from part of a question
    is an approximation, and the client has to say so.
    """

    question: str
    expressible: bool
    rule: Rule | None
    proposed_split: str | None
    unknown_terms: list[str]
    message: str | None
    preview: DataOut | None


class SpendLineOut(BaseModel):
    id: int
    amount: Decimal
    note: str | None
    #: facet key -> value key, for the facets this line overrides.
    labels: dict[str, str]


class AllocationIn(BaseModel):
    lines: list[LineInput]


class AllocationOut(BaseModel):
    """A document's whole allocation. `lines == []` means unsplit — the common
    case, and the one `spend_facts` synthesises a single row for."""

    document_id: int
    amount_total: Decimal | None
    lines: list[SpendLineOut]


# --- the one argument set both /data and /cell are asked ---------------------


class _SharedArgs(TypedDict):
    """Every argument `chart_series` and `chart_cell` must agree on.

    A `TypedDict` rather than a `dict[str, Any]` so unpacking it into both calls
    is checked: adding an argument to the engine's shared predicate without
    adding it here fails `mypy` instead of quietly giving the panel a different
    question from the bar.
    """

    grain: Grain
    split: str | None
    currency: str
    since: date | None
    until: date | None


@dataclass(frozen=True, slots=True)
class _ChartQuery:
    """One resolved request. Built once, read by `/data`, `/cell` and `/draft`."""

    rule: Rule
    grain: Grain
    split: str | None
    currency: str
    since: date | None
    until: date | None
    #: The vocabulary this request was validated against. Kept so split
    #: resolution reads the labels already loaded rather than loading them
    #: again — and reads the *same* ones the rule was validated against, so a
    #: value deleted mid-request cannot be valid in one half and missing in the
    #: other.
    vocabulary: tuple[VocabularyFacet, ...]

    def shared(self) -> _SharedArgs:
        return {
            "grain": self.grain,
            "split": self.split,
            "currency": self.currency,
            "since": self.since,
            "until": self.until,
        }

    @property
    def facets_in_rule(self) -> set[str]:
        """The facets the rule names — `chart_footer`'s uncategorised gate.

        Derived here because `chart_footer` cannot derive it: it takes the set
        from its caller and trusts it, so an empty set for a facet-bearing rule
        turns off the reporting of unlabelled money with no error at all.
        """
        return {clause.facet for clause in self.rule.all}


# --- helpers -----------------------------------------------------------------


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def _load_chart(session: AsyncSession, chart_id: int) -> Chart:
    chart = await session.get(Chart, chart_id)
    if chart is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no chart with id {chart_id}"
        )
    return chart


def _chart_out(chart: Chart) -> ChartOut:
    return ChartOut(
        id=chart.id,
        name=chart.name,
        question_text=chart.question_text,
        rule=_chart_rule(chart),
        default_grain=chart.default_grain,
        default_split=chart.default_split,
        display_currency=chart.display_currency,
        ordinal=chart.ordinal,
    )


def _chart_rule(chart: Chart) -> Rule:
    """The stored rule, or a 422 naming the chart.

    Rules are validated at write, so this only fires for a row edited outside
    the API — which still deserves a named error rather than a 500.
    """
    try:
        return Rule.model_validate(chart.rule)
    except ValidationError as exc:
        raise _unprocessable(
            f"chart {chart.id} has an unreadable rule: {exc.error_count()} error(s)"
        ) from exc


def _validate_rule(rule: Rule, vocabulary: tuple[VocabularyFacet, ...]) -> None:
    """Refuse a rule the vocabulary cannot express, naming what is missing.

    §12: a rule referencing a deleted facet value renders an **error naming the
    value**, never an empty chart — an empty chart is indistinguishable from
    "you spent nothing on that", which is the failure this whole feature exists
    to remove. Checked on the read path as well as the write path, because the
    vocabulary can lose a value after the chart was saved.

    `rule_predicate` is called for its own refusal (`RuleError` on a clause with
    no values), which would otherwise surface as a 500 from inside the engine.
    """
    try:
        rule_predicate(rule)
    except RuleError as exc:
        raise _unprocessable(str(exc)) from exc
    by_key = {facet.key: facet for facet in vocabulary}
    for clause in rule.all:
        facet = by_key.get(clause.facet)
        if facet is None:
            raise _unprocessable(f"unknown facet '{clause.facet}' in this chart's rule")
        missing = [value for value in clause.values if facet.value(value) is None]
        if missing:
            raise _unprocessable(
                f"unknown value(s) {sorted(missing)} for facet "
                f"'{clause.facet}' in this chart's rule"
            )
    _refuse_unmatchable_conjunction(rule)


def _refuse_unmatchable_conjunction(rule: Rule) -> None:
    """Refuse two `in` clauses on one facet: the rule can never match anything.

    A document carries at most one value per facet, so `category in [a] AND
    category in [b]` is empty for every row. That is the §12 failure in its
    purest form — the chart renders "you spent nothing", which is
    indistinguishable from an honest zero.

    **Refused, not merged.** Rewriting it to `category in [a, b]` turns the AND
    into an OR, which answers a *different question from the one the owner
    asked* and moves money into the chart. `draft.py` refuses an unrecognised
    operator rather than guessing for the same reason. Where a merge is what
    the owner wants, the editor offers it as a visible action they take.

    Deliberately narrow: only multiple `in` clauses on one facet. Two `not_in`
    clauses are a legitimate intersection of exclusions, and a mixed pair is
    unanswerable only if the exclusion covers the inclusion — set arithmetic
    rather than a shape check, and not what this guards.
    """
    seen: set[str] = set()
    for clause in rule.all:
        if clause.op != "in":
            continue
        if clause.facet in seen:
            raise _unprocessable(
                f"facet '{clause.facet}' appears in two 'in' clauses; a document carries at "
                f"most one value per facet, so this rule can never match anything"
            )
        seen.add(clause.facet)


def _validate_split(split: str | None, vocabulary: tuple[VocabularyFacet, ...]) -> None:
    """`sender` is a real column (§9.2); anything else must be a live facet."""
    if split is None or split == SENDER_SPLIT:
        return
    if all(facet.key != split for facet in vocabulary):
        raise _unprocessable(
            f"unknown split axis '{split}': use '{SENDER_SPLIT}' or a facet in the vocabulary"
        )


async def _resolve_query(
    session: AsyncSession,
    chart: Chart,
    *,
    grain: Grain | None,
    split: str | None,
    currency: str | None,
    since: date | None,
    until: date | None,
) -> _ChartQuery:
    """Resolve one request against a chart's defaults, refusing what it cannot ask.

    The empty string is how a client asks for **no** split axis on a chart that
    defaults to one; an omitted `split` takes the chart's default.
    """
    rule = _chart_rule(chart)
    resolved_split = chart.default_split if split is None else (split or None)
    vocabulary = await load_vocabulary(session)
    _validate_rule(rule, vocabulary)
    _validate_split(resolved_split, vocabulary)
    if since is not None and until is not None and since > until:
        raise _unprocessable(f"'from' ({since}) is after 'to' ({until})")
    return _ChartQuery(
        rule=rule,
        grain=grain if grain is not None else chart.default_grain,
        split=resolved_split,
        currency=(currency or chart.display_currency).upper(),
        since=since,
        until=until,
        vocabulary=vocabulary,
    )


#: What the `null` split bucket is called, per axis. It is a real bucket — an
#: unlabelled row lands in it rather than being dropped, which is the
#: mechanical basis of the total's invariance (docs/charts.md §4) — so it needs
#: a name, and only the API knows which one.
_NO_SENDER = "No sender"
_UNLABELLED = "Uncategorised"

#: `senders.id` is Postgres `int4` (`models.py`'s `Sender.id`); a client-
#: supplied id above this can never be a real row and would raise from
#: asyncpg's own range check rather than resolving to the fallback.
_MAX_SENDER_ID = 2**31 - 1


async def _resolve_splits(
    session: AsyncSession, query: _ChartQuery, values: list[str | None]
) -> list[SplitValueOut]:
    """Label and colour each split bucket present in a result.

    Returns `[]` for an unsplit chart: there is no axis, so there are no
    buckets to name — distinct from a split axis whose only bucket is the
    unlabelled one.

    A `value` whose sender row or facet value has since been deleted resolves
    to itself. Facet values are deletable at runtime and a saved chart can rot;
    a rotted legend entry is a legible defect, while raising here would be a
    500 on every chart in range of one such row.
    """
    if query.split is None:
        return []

    if query.split == SENDER_SPLIT:
        # `values` can come straight from `/cell`'s `split_value` query
        # parameter, which is client-controlled and never validated as an id
        # (§9.5: `/cell` takes whatever `/data` handed back, but a client can
        # send anything). `int()` itself is the only reliable judge of "is
        # this a number" — `str.isdigit()` is not a safe gate in front of it:
        # it is `True` for characters `int()` rejects (superscripts and other
        # Unicode category-No "digits", e.g. `"²"`), so filtering on it first
        # still lets `int()` raise. Parsing defensively and bounding the
        # result against `senders.id`'s `int4` range (`_MAX_SENDER_ID`) is
        # what actually keeps a junk id from reaching `int()`'s `ValueError`
        # or asyncpg's own range check — both unhandled 500s on a route with
        # no other validation.
        #
        # Keyed by the **parsed** id, not the raw string: `int()` accepts
        # forms `senders.id` never renders (`"007"`, fullwidth or other
        # non-ASCII decimal digits), and a client sending one of those for a
        # real sender must still get that sender's name, not a raw-string
        # fallback that only fires for a mismatch of spelling, not of value.
        numeric: dict[str, int] = {}
        for value in values:
            if value is None:
                continue
            try:
                parsed = int(value)
            except ValueError:
                continue
            if 0 <= parsed <= _MAX_SENDER_ID:
                numeric[value] = parsed
        rows = (
            await session.execute(
                select(Sender.id, Sender.name, Sender.colour).where(Sender.id.in_(numeric.values()))
            )
        ).all()
        by_id = {row.id: (row.name, row.colour) for row in rows}
        results = []
        for value in values:
            if value is None:
                results.append(SplitValueOut(value=None, label=_NO_SENDER, colour=None))
                continue
            sender_id = numeric.get(value)
            name, colour = (
                (value, None) if sender_id is None else by_id.get(sender_id, (value, None))
            )
            results.append(SplitValueOut(value=value, label=name, colour=colour))
        return results

    facet = next((f for f in query.vocabulary if f.key == query.split), None)
    by_key = {v.key: v for v in facet.values} if facet is not None else {}
    return [
        SplitValueOut(
            value=value,
            label=_UNLABELLED if value is None else _label_of(by_key.get(value), value),
            colour=None if value is None else _colour_of(by_key.get(value)),
        )
        for value in values
    ]


def _label_of(value: VocabularyValue | None, fallback: str) -> str:
    return fallback if value is None else value.label


def _colour_of(value: VocabularyValue | None) -> str | None:
    return None if value is None else value.colour


def _merge_unconvertible(
    from_total: list[Unconvertible], from_footer: list[Unconvertible]
) -> list[UnconvertibleOut]:
    """One line per currency across both reports.

    `query.py` reports rows that would have entered the total; `footer.py`
    reports the rows it accounts for. They are different rows, so both are
    needed — but reporting the same currency twice would read as two separate
    problems. The `None` key (an amount with no currency at all) merges like any
    other and sorts last.
    """
    amounts: dict[str | None, Decimal] = {}
    documents: dict[str | None, int] = {}
    for entry in [*from_total, *from_footer]:
        amounts[entry.currency] = amounts.get(entry.currency, Decimal(0)) + entry.amount
        documents[entry.currency] = documents.get(entry.currency, 0) + entry.documents
    return [
        UnconvertibleOut(currency=code, amount=_money(amounts[code]), documents=documents[code])
        for code in sorted(amounts, key=lambda code: (code is None, code or ""))
    ]


def _group(entry: ExcludedGroup | None) -> ExcludedGroupOut | None:
    """One footer group, quantised. `None` in, `None` out — an empty group is
    not a report, and rendering it as a zero line would invent a claim."""
    if entry is None:
        return None
    return ExcludedGroupOut(
        amount_kind=entry.amount_kind, amount=_money(entry.amount), documents=entry.documents
    )


def _footer_out(footer: Footer, from_total: list[Unconvertible]) -> FooterOut:
    """All eight fields (§9.4), quantised, `None` where nothing landed.

    `unclassified` (money with an amount and an undecided `amount_kind`) and
    `unaccounted` (the classifier's live `ELSE`) are not optional extras: before
    they existed, an unclassified document appeared in **no** footer line at
    all, on the class of document the archive has most of.
    """
    return FooterOut(
        netted_refunds=_money(footer.netted_refunds),
        refund_count=footer.refund_count,
        excluded=[
            ExcludedGroupOut(
                amount_kind=entry.amount_kind,
                amount=_money(entry.amount),
                documents=entry.documents,
            )
            for entry in footer.excluded
        ],
        unclassified=_group(footer.unclassified),
        uncategorised=_group(footer.uncategorised),
        undated=_group(footer.undated),
        unaccounted=_group(footer.unaccounted),
        unconvertible=_merge_unconvertible(from_total, footer.unconvertible),
    )


def _data_out(
    chart_id: int | None,
    query: _ChartQuery,
    series: Series,
    footer: Footer,
    splits: list[SplitValueOut],
) -> DataOut:
    """Serialise a chart's answer so the headline is the drawing (§2.5).

    `chart_series` guarantees `total == sum(cells)` **unquantised**, and
    `fx.convert` does not round — so quantising the two independently breaks the
    promise on the wire: two cells of `10.005` render as `"10.01"` and `"10.01"`
    beneath a headline of `"20.01"`. The cells are therefore rendered first and
    the headline is their sum, which makes the rounded total the rounded drawing
    by construction. The engine's own invariant is untouched; only what the
    client reads is reconciled.
    """
    cells = [
        CellOut(
            period=cell.period,
            split_value=cell.split_value,
            total=_money(cell.total),
            payments=cell.payments,
        )
        for cell in series.cells
    ]
    return DataOut(
        chart_id=chart_id,
        grain=query.grain,
        split=query.split,
        currency=query.currency,
        since=query.since,
        until=query.until,
        cells=cells,
        splits=splits,
        # `_money` of an already-2dp sum is a no-op for the invariant and gives
        # the empty chart its `"0.00"`: `Decimal(0)` renders as `"0"`.
        total=_money(sum((cell.total for cell in cells), Decimal(0))),
        payments=series.payments,
        documents=series.documents,
        footer=_footer_out(footer, series.unconvertible),
    )


def _rendered_shares(values: list[Decimal]) -> list[Decimal]:
    """Round parts to cents so they still sum to the rounded whole.

    §9.5's promise is that the panel adds up to the bar the owner clicked, and
    rounding each payment on its own breaks it by a cent: two contributions of
    `10.005` each render as `10.01` under a bar of `20.01`. The residual is
    given to (or taken from) the values whose rounding moved them furthest —
    the standard largest-remainder allocation — so the parts sum to the same
    number `/data` renders for that cell, which is `_money` of the same sum.
    """
    target = _money(sum(values, Decimal(0)))
    rendered = [_money(value) for value in values]
    residual = target - sum(rendered, Decimal(0))
    if not residual:
        return rendered
    step = _CENTS if residual > 0 else -_CENTS
    # Rounded down the most first when cents are owed, rounded up the most first
    # when cents are owing; ties break on position, so the order is stable.
    direction = -1 if residual > 0 else 1
    order = sorted(
        range(len(values)),
        key=lambda index: (rendered[index] - values[index]) * direction,
        reverse=True,
    )
    for index in order[: int(abs(residual) / _CENTS)]:
        rendered[index] += step
    return rendered


async def _answer(session: AsyncSession, chart_id: int | None, query: _ChartQuery) -> DataOut:
    """The chart's numbers and the accounting for what they did not count.

    Both halves are asked the same question: `chart_series` gets the shared
    argument set, `chart_footer` gets the same window, currency and — derived
    here, because nothing below can derive it — the facets the rule names.
    """
    series = await chart_series(session, query.rule, **query.shared())
    footer = await chart_footer(
        session,
        query.rule,
        currency=query.currency,
        since=query.since,
        until=query.until,
        facets_in_rule=query.facets_in_rule,
    )
    # Manual de-duplication rather than `set()`: it preserves the engine's
    # ordering, and the legend must match the order the chart draws its cells
    # in, not an arbitrary hash order.
    seen: list[str | None] = []
    for cell in series.cells:
        if cell.split_value not in seen:
            seen.append(cell.split_value)
    splits = await _resolve_splits(session, query, seen)
    return _data_out(chart_id, query, series, footer, splits)


# --- charts ------------------------------------------------------------------


@router.get("/spending", summary="Saved spending questions")
async def list_charts(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChartListOut:
    rows = (
        (
            await session.execute(
                select(Chart).order_by(Chart.ordinal, Chart.name).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return ChartListOut(charts=[_chart_out(chart) for chart in rows])


@router.get("/spending/{chart_id}", summary="One saved question")
async def get_chart(
    chart_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> ChartOut:
    """One chart by id. The workspace loads through this rather than paging the
    list, which would stop finding a chart as soon as there are more than
    `limit` of them."""
    return _chart_out(await _load_chart(session, chart_id))


@router.post("/spending", status_code=status.HTTP_201_CREATED, summary="Save a question")
async def create_chart(
    body: ChartIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> ChartOut:
    vocabulary = await load_vocabulary(session)
    _validate_rule(body.rule, vocabulary)
    _validate_split(body.default_split, vocabulary)
    await _require_free_name(session, body.name, chart_id=None)
    chart = Chart(
        name=body.name,
        question_text=body.question_text,
        rule=body.rule.model_dump(),
        default_grain=body.default_grain,
        default_split=body.default_split,
        display_currency=body.display_currency.upper(),
        ordinal=body.ordinal,
    )
    session.add(chart)
    try:
        await session.commit()
    except IntegrityError as exc:  # a name taken between the check and the commit
        await session.rollback()
        raise _duplicate_name(body.name) from exc
    return _chart_out(chart)


@router.patch("/spending/{chart_id}", summary="Edit a saved question")
async def update_chart(
    chart_id: int, body: ChartPatch, session: Annotated[AsyncSession, Depends(get_session)]
) -> ChartOut:
    chart = await _load_chart(session, chart_id)
    fields = body.model_fields_set
    vocabulary = await load_vocabulary(session)
    if body.rule is not None:
        _validate_rule(body.rule, vocabulary)
        chart.rule = body.rule.model_dump()
    if "default_split" in fields:
        _validate_split(body.default_split, vocabulary)
        chart.default_split = body.default_split
    if body.name is not None:
        await _require_free_name(session, body.name, chart_id=chart_id)
        chart.name = body.name
    if body.question_text is not None:
        chart.question_text = body.question_text
    if body.default_grain is not None:
        chart.default_grain = body.default_grain
    if body.display_currency is not None:
        chart.display_currency = body.display_currency.upper()
    if body.ordinal is not None:
        chart.ordinal = body.ordinal
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _duplicate_name(chart.name) from exc
    return _chart_out(chart)


@router.delete(
    "/spending/{chart_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved question",
)
async def delete_chart(
    chart_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    chart = await _load_chart(session, chart_id)
    await session.delete(chart)
    await session.commit()


def _duplicate_name(name: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=f"a chart named '{name}' already exists"
    )


async def _require_free_name(session: AsyncSession, name: str, *, chart_id: int | None) -> None:
    statement = select(Chart.id).where(Chart.name == name)
    if chart_id is not None:
        statement = statement.where(Chart.id != chart_id)
    if (await session.execute(statement)).first() is not None:
        raise _duplicate_name(name)


# --- data and drill-through --------------------------------------------------


@router.get("/spending/{chart_id}/data", summary="A chart's answer, with its footer")
async def chart_data(
    chart_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    grain: Annotated[Grain | None, Query(description="Overrides the chart's default.")] = None,
    split: Annotated[
        str | None,
        Query(description="A facet key or `sender`; the empty string means no split axis."),
    ] = None,
    since: Annotated[date | None, Query(alias="from", description="Inclusive lower bound.")] = None,
    until: Annotated[date | None, Query(alias="to", description="Inclusive upper bound.")] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
) -> DataOut:
    chart = await _load_chart(session, chart_id)
    query = await _resolve_query(
        session, chart, grain=grain, split=split, currency=currency, since=since, until=until
    )
    return await _answer(session, chart_id, query)


@router.get("/spending/{chart_id}/cell", summary="The payments behind one cell")
async def chart_cell_data(
    chart_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    period: Annotated[date, Query(description="The cell's period, at the grain's boundary.")],
    split_value: Annotated[
        str | None, Query(description="Omit for the unlabelled bucket / an unsplit chart.")
    ] = None,
    grain: Annotated[Grain | None, Query()] = None,
    split: Annotated[str | None, Query()] = None,
    since: Annotated[date | None, Query(alias="from")] = None,
    until: Annotated[date | None, Query(alias="to")] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
) -> CellOutBody:
    """The cell's payments, each with its documents (§9.5).

    Takes **every** argument `/data` takes and resolves them the same way, so
    the panel provably lists the rows the bar summed: `_ChartQuery.shared()` is
    unpacked into `chart_cell` exactly as `/data` unpacks it into
    `chart_series`. A client that echoes `/data`'s response fields back here
    cannot open a cell of a different chart than the one it drew.
    """
    chart = await _load_chart(session, chart_id)
    query = await _resolve_query(
        session, chart, grain=grain, split=split, currency=currency, since=since, until=until
    )
    start = await period_start(session, query.grain, period)
    if start != period:
        # Never `[]`. `chart_cell` filters `date_trunc(grain, date) = period`,
        # so a mid-bucket period matches nothing — and an empty panel under a
        # non-empty bar reads as "you spent nothing here", which is the silence
        # §12 exists to remove.
        raise _unprocessable(
            f"period {period} is not the start of a {query.grain.value}; use {start}"
        )
    resolved = await _resolve_splits(session, query, [split_value])
    bucket = resolved[0] if resolved else None
    payments: list[CellPayment] = await chart_cell(
        session,
        query.rule,
        split_value=split_value,
        period=period,
        **query.shared(),
    )
    rendered = _rendered_shares([payment.total for payment in payments])
    return CellOutBody(
        period=period,
        split_value=split_value,
        total=_money(sum(rendered, Decimal(0))),
        label=bucket.label if bucket else "",
        colour=bucket.colour if bucket else None,
        payments=[
            CellPaymentOut(
                payment_id=payment.payment_id,
                total=share,
                documents=[
                    CellDocumentOut(
                        id=document.id,
                        title=document.title,
                        date=document.date,
                        amount=None if document.amount is None else _money(document.amount),
                        currency=document.currency,
                        amount_kind=document.amount_kind,
                        reference=document.reference,
                        is_canonical=document.is_canonical,
                    )
                    for document in payment.documents
                ],
            )
            for payment, share in zip(payments, rendered, strict=True)
        ],
    )


#: The buckets `_CLASSIFY_SQL` can put a row in that the footer reports. Named
#: here rather than derived from `FooterOut`'s fields so an unknown bucket is a
#: 422 the owner can read, and so `unaccounted` is openable: a bug signal you
#: cannot open is not a signal. Built from `footer.py`'s own constants rather
#: than re-spelled string literals, so a rename there cannot leave this set
#: stale and the renamed bucket un-drillable behind a 422 naming a bucket the
#: engine no longer has. `unconvertible` is deliberately absent: it is not a
#: `_CLASSIFY_SQL` bucket at all but a merge of two separately-reported lists
#: (docs/charts.md §5), so listing its documents would need `Unconvertible` to
#: carry document ids — an engine change, out of scope.
_FOOTER_BUCKETS = frozenset({"excluded", UNCLASSIFIED, UNCATEGORISED, UNDATED, UNACCOUNTED})


class FooterDocumentOut(BaseModel):
    """One document behind a footer count. `amount` is this document's total
    within the bucket, summed across its spend lines."""

    id: int
    title: str | None
    date: date | None
    amount: Decimal
    currency: str | None
    amount_kind: str | None


class FooterDocumentsOut(BaseModel):
    """`total` is the bucket's full size, taken **before** paging: a bucket
    with more documents than `limit` still returns only a page of them, and
    without `total` a client cannot tell a complete list from the first 100 of
    340 — the steady state for `uncategorised` on a real archive, not an edge
    case (§9.4 calls it "a visible task" precisely because it tends to be
    large)."""

    bucket: str
    total: int
    documents: list[FooterDocumentOut]


@router.get("/spending/{chart_id}/footer/{bucket}", summary="The documents behind a footer count")
async def chart_footer_bucket(
    chart_id: int,
    bucket: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    amount_kind: Annotated[
        str | None, Query(description="Selects one group out of `excluded`.")
    ] = None,
    since: Annotated[date | None, Query(alias="from")] = None,
    until: Annotated[date | None, Query(alias="to")] = None,
    currency: Annotated[str | None, Query(pattern=r"^[A-Za-z]{3}$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FooterDocumentsOut:
    """§9.4 calls uncategorised money "a visible task". This is what makes it
    one: without it every footer count is a number with nowhere to go.

    Takes the same window arguments as `/data` and resolves them the same way,
    so the list answers the question the footer counted. `split` is left to
    the chart's default (`_resolve_query`'s `split=None`) rather than cleared:
    `chart_footer` itself takes no split, but inheriting the default still
    validates it exactly as `/data` does, so a chart whose split axis names a
    facet deleted at runtime is a 422 from both routes rather than a footer
    panel that opens under a chart that will not draw.
    """
    if bucket not in _FOOTER_BUCKETS:
        raise _unprocessable(
            f"unknown footer bucket '{bucket}'; use one of {sorted(_FOOTER_BUCKETS)}"
        )
    if bucket == "excluded" and amount_kind is None:
        # `excluded` is a *list* of groups, one per kind (§9.4) — unlike every
        # other bucket, which has exactly one. Without `amount_kind` naming
        # which group, `row.amount_kind == amount_kind` can never match (an
        # excluded row always carries a kind) and the route would return an
        # empty page indistinguishable from "that group is genuinely empty",
        # three lines below a bucket check that already 422s and names the
        # problem back to the caller.
        raise _unprocessable("bucket 'excluded' needs '?amount_kind' naming which group")
    chart = await _load_chart(session, chart_id)
    query = await _resolve_query(
        session, chart, grain=None, split=None, currency=currency, since=since, until=until
    )
    rows = await chart_footer_documents(
        session,
        query.rule,
        bucket=bucket,
        amount_kind=amount_kind,
        currency=query.currency,
        since=query.since,
        until=query.until,
        facets_in_rule=query.facets_in_rule,
    )
    total = len(rows)
    page = rows[offset : offset + limit]
    titles: dict[int, str | None] = dict(
        (
            await session.execute(
                select(Document.id, Document.title).where(
                    Document.id.in_([row.document_id for row in page])
                )
            )
        )
        .tuples()
        .all()
    )
    return FooterDocumentsOut(
        bucket=bucket,
        total=total,
        documents=[
            FooterDocumentOut(
                id=row.document_id,
                title=titles.get(row.document_id),
                date=row.date,
                amount=_money(row.amount),
                currency=row.currency,
                amount_kind=row.amount_kind,
            )
            for row in page
        ],
    )


# --- preview -----------------------------------------------------------------


@router.post("/spending/preview", summary="An unsaved rule's answer")
async def preview_rule(
    body: PreviewIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> DataOut:
    """Answer a rule that has not been saved, so an edit can be seen before it
    is applied.

    Assembled entirely from parts `/data` and `/spending/draft` already use —
    `_answer` takes a nullable chart id precisely because a preview has no chart
    behind it — so this route adds a surface, not a capability.

    Two lines here are easy to lose when reading this beside `draft_chart`,
    and both matter:

    * **`_validate_rule` is called.** `draft_chart` does not call it, and is
      right not to: `filter_drafted_rule` has already guaranteed that every
      surviving clause names vocabulary that exists. A rule arriving from a
      client carries no such guarantee, and an unvalidated one answers "you
      spent nothing" instead of naming the facet or value that is wrong — the
      §12 failure this feature exists to avoid.
    * **the window is refused when reversed**, as `_resolve_query` does for
      `/data`. `draft_chart` omits that check; preview follows `/data`.

    No model is called and nothing is written.
    """
    split = body.split or None
    vocabulary = await load_vocabulary(session)
    _validate_rule(body.rule, vocabulary)
    _validate_split(split, vocabulary)
    if body.since is not None and body.until is not None and body.since > body.until:
        raise _unprocessable(f"'from' ({body.since}) is after 'to' ({body.until})")
    query = _ChartQuery(
        rule=body.rule,
        grain=body.grain,
        split=split,
        currency=body.display_currency.upper(),
        since=body.since,
        until=body.until,
        vocabulary=vocabulary,
    )
    return await _answer(session, None, query)


# --- drafting ----------------------------------------------------------------


@router.post("/spending/draft", summary="Draft a rule from a question")
async def draft_chart(
    body: DraftIn, session: Annotated[AsyncSession, Depends(get_session)]
) -> DraftOut:
    """Turn a question into a rule against the closed vocabulary, and preview it.

    The branch that matters is on `unknown_terms`, **before** any preview.
    `filter_drafted_rule` drops what the vocabulary cannot express, and when it
    drops everything the result is `Rule(all=[])` — which matches every row. A
    preview built from that would answer a question about one thing with the
    whole archive's total, and would look entirely plausible.
    """
    try:
        result = await draft_rule(session, body.question)
    except DraftError as exc:
        # Never degrade to an empty rule: that is "all spending".
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    unknown = [term[:MAX_TERM_CHARS] for term in result.unknown_terms[:MAX_UNKNOWN_TERMS]]
    collapsed = not result.rule.all and bool(result.unknown_terms)
    message: str | None = None
    if unknown:
        message = "not in the vocabulary: " + ", ".join(unknown)
        if collapsed:
            message += (
                " — nothing in this question could be expressed, so no rule is "
                "proposed (an empty rule would mean all spending)"
            )
    if collapsed:
        return DraftOut(
            question=body.question,
            expressible=False,
            rule=None,
            proposed_split=result.proposed_split,
            unknown_terms=unknown,
            message=message,
            preview=None,
        )
    vocabulary = await load_vocabulary(session)
    _validate_split(result.proposed_split, vocabulary)
    query = _ChartQuery(
        rule=result.rule,
        grain=body.grain,
        split=result.proposed_split,
        currency=body.display_currency.upper(),
        since=body.since,
        until=body.until,
        vocabulary=vocabulary,
    )
    return DraftOut(
        question=body.question,
        expressible=not unknown,
        rule=result.rule,
        proposed_split=result.proposed_split,
        unknown_terms=unknown,
        message=message,
        preview=await _answer(session, None, query),
    )


# --- spend lines (§8.4) ------------------------------------------------------


async def _require_document(session: AsyncSession, document_id: int) -> Document:
    document = await session.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no document with id {document_id}"
        )
    return document


async def _allocation_body(session: AsyncSession, document: Document) -> AllocationOut:
    """Read an allocation back from the database.

    Re-read rather than serialised from the objects `replace_lines` returned:
    a committed instance's attributes are refreshed lazily, and a lazy refresh
    on an async session raises `MissingGreenlet` rather than returning a row.
    """
    lines = (
        (
            await session.execute(
                select(SpendLine).where(SpendLine.document_id == document.id).order_by(SpendLine.id)
            )
        )
        .scalars()
        .all()
    )
    labels: dict[int, dict[str, str]] = {}
    if lines:
        label_rows = await session.execute(
            select(LineLabel.line_id, Facet.key, FacetValue.key)
            .join(Facet, Facet.id == LineLabel.facet_id)
            .join(FacetValue, FacetValue.id == LineLabel.facet_value_id)
            .where(LineLabel.line_id.in_([line.id for line in lines]))
        )
        for line_id, facet_key, value_key in label_rows:
            labels.setdefault(line_id, {})[facet_key] = value_key
    return AllocationOut(
        document_id=document.id,
        amount_total=None if document.amount_total is None else _money(document.amount_total),
        lines=[
            SpendLineOut(
                id=line.id,
                amount=_money(line.amount),
                note=line.note,
                labels=labels.get(line.id, {}),
            )
            for line in lines
        ],
    )


async def _commit_allocation(session: AsyncSession) -> None:
    """Commit an allocation, translating the sum triggers' refusal into a 400.

    The SQLSTATE check and the re-raise of everything else live in
    `spend_lines.commit_allocation`, because the same pair of triggers refuses
    an `amount_total` edit from `api/documents.py` — one definition, two
    writers, so neither can drift into answering a 500 where the other answers
    a named 400.
    """
    try:
        await commit_allocation(session, refusal="the spend lines do not sum to the document total")
    except AllocationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/documents/{document_id}/spend-lines", summary="A document's allocation")
async def get_spend_lines(
    document_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> AllocationOut:
    document = await _require_document(session, document_id)
    return await _allocation_body(session, document)


@router.put("/documents/{document_id}/spend-lines", summary="Replace a document's allocation")
async def put_spend_lines(
    document_id: int,
    body: AllocationIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AllocationOut:
    """Replace the whole allocation (§8.4). Partial writes have no meaning.

    Every refusal `spend_lines.py` can name — the scale, the sum, an unknown
    facet or value — is an `AllocationError` and becomes a 400.
    """
    document = await _require_document(session, document_id)
    try:
        await replace_lines(session, document_id, body.lines)
    except AllocationError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _commit_allocation(session)
    return await _allocation_body(session, document)


@router.delete(
    "/documents/{document_id}/spend-lines",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Return a document to unsplit",
)
async def delete_spend_lines(
    document_id: int, session: Annotated[AsyncSession, Depends(get_session)]
) -> None:
    await _require_document(session, document_id)
    await clear_lines(session, document_id)
    await _commit_allocation(session)
