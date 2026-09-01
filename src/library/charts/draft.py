"""Draft a chart rule from a plain-language question, against the closed vocabulary.

Spec §9.1: a chart is a saved question, and the model drafts its rule against the
**current** vocabulary. Spec §7.5 and §12: a facet or value the model names that
is not in the vocabulary is dropped and reported, never added — when a question
cannot be expressed in the current vocabulary the system says so rather than
approximating.

Two structural decisions carry that guarantee:

* The API backend uses ``client.messages.parse()`` with a Pydantic
  ``output_format`` (the shape of ``library.facets.labeller``). ``messages.create()``
  plus ``json.loads`` shipped twice in this repository (GH #108, and the amount
  classifier) and was reverted both times — see ``library.llm.envelope``.
* The ``output_format`` schema is deliberately **permissive**; it is not the
  closed-vocabulary gate. The gate is :func:`filter_drafted_rule`, applied to the
  response after it comes back. The prompt is a request; the filter is the
  guarantee. A rule that silently kept an invented value would resolve to zero
  rows and read as "you spent nothing on that", which is worse than an error.

Uses ``settings.extraction_model`` rather than a setting of its own: every
``*_model`` setting needs a matching row in ``MODEL_PRICING_USD_PER_MTOK`` or the
app refuses to boot.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from library.charts.query import SENDER_SPLIT
from library.charts.rule import Clause, Rule
from library.config import LLMBackend, Settings, get_settings
from library.facets.vocabulary import VocabularyFacet, load_vocabulary

logger = logging.getLogger(__name__)

MAX_DRAFT_TOKENS: int = 800
MAX_QUESTION_CHARS: int = 500

#: Reported in place of a term that is blank or whitespace-only. Such a term is
#: dropped by the lookup like any other unknown, but has no text to report — and
#: an unreported drop is exactly the silent narrowing §12 forbids, because the
#: caller would see a rule that quietly widened to "all spending" with an empty
#: `unknown_terms`.
BLANK_TERM: str = "(blank)"

_OPS: tuple[str, ...] = ("in", "not_in")

DRAFT_SYSTEM_PROMPT: str = """\
You turn a plain-language question about household spending into a chart RULE
for "Library", a self-hosted family document archive.

You are given a CLOSED vocabulary of facets. Each facet is one dimension, and a
document takes AT MOST ONE value per facet.

A rule is a list of clauses, ANDed together. Each clause names one facet, an
operator ("in" or "not_in"), and one or more values OF THAT FACET. An empty list
of clauses matches all spending, which is the correct answer to a question that
asks for everything.

You may only name facet keys and value keys that appear in the vocabulary;
aliases listed beside a value also identify it. Never invent a facet or a value:
anything outside the vocabulary is dropped and reported back to the owner as a
proposed addition, so an approximation helps nobody.

Also propose a "split": the key of the one facet whose values you expect to vary
most within the result, or "sender", or null when nothing splits it usefully.

Return ONLY a JSON object of this shape, with no prose or code fences:
{"all": [{"facet": "...", "op": "in"|"not_in", "values": ["..."]}],
 "split": "..."|null}"""


class DraftError(RuntimeError):
    """The rule could not be drafted. Never a silently empty rule.

    An empty ``Rule`` means "all spending", so returning one on failure would
    answer a narrow question with the whole archive's total.
    """


class DraftedClause(BaseModel):
    """One clause exactly as the model may return it.

    Deliberately permissive — this is part of the ``output_format`` for
    ``client.messages.parse()``, not the closed-vocabulary gate. A model naming a
    facet, value or operator outside the vocabulary must still reach
    :func:`filter_drafted_rule` so it becomes a reported ``unknown_term`` rather
    than a rejected response, so nothing here constrains these fields.
    """

    facet: str
    op: str = "in"
    values: list[str] = Field(default_factory=list)


class DraftedRule(BaseModel):
    """Structured-output schema for the drafter (see :class:`DraftedClause`)."""

    all: list[DraftedClause] = Field(default_factory=list)
    split: str | None = None


@dataclass(frozen=True, slots=True)
class DraftResult:
    """A rule the vocabulary can express, plus everything it could not."""

    rule: Rule
    proposed_split: str | None = None
    #: Facet keys, value keys and operators the model named that the vocabulary
    #: does not contain. Ordered as the model produced them, de-duplicated.
    unknown_terms: list[str] = field(default_factory=list)


def build_draft_prompt(vocabulary: Sequence[VocabularyFacet], question: str) -> str:
    lines: list[str] = ["VOCABULARY (choose only from these):"]
    for facet in vocabulary:
        lines.append(f"- {facet.key} ({facet.label}):")
        if not facet.values:
            lines.append("    (no values yet — this facet cannot be used in a rule)")
        for value in facet.values:
            alias_note = f"  [also: {', '.join(value.aliases)}]" if value.aliases else ""
            lines.append(f"    {value.key} — {value.label}{alias_note}")
    lines += [
        "",
        f'SPLIT AXES: "{SENDER_SPLIT}" (see library.charts.query), or any facet key above.',
        "",
        "QUESTION:",
        question[:MAX_QUESTION_CHARS],
    ]
    return "\n".join(lines)


def _resolve_value(facet: VocabularyFacet, raw: str) -> str | None:
    """The canonical value key for ``raw``, matching keys then aliases, or None.

    Casefolded, not ``.lower()``'d, and matched against aliases as well: the
    vocabulary contains non-ASCII display forms, and a model answering with a
    legitimate synonym must not be reported as unknown. Only the *matching* is
    case-insensitive — the stored key is what ends up in the rule.
    """
    folded = raw.strip().casefold()
    if not folded:
        return None
    match = next((v for v in facet.values if v.key.casefold() == folded), None)
    if match is None:
        match = next(
            (v for v in facet.values if any(a.casefold() == folded for a in v.aliases)),
            None,
        )
    return match.key if match is not None else None


def filter_drafted_rule(drafted: DraftedRule, vocabulary: Sequence[VocabularyFacet]) -> DraftResult:
    """Map a drafted rule onto the closed vocabulary, reporting what it dropped.

    Pure, so the closed-set guarantee is tested without a model. Three drops:

    * an unknown **value** — dropped, the clause survives on its other values;
    * an unknown **facet** (or operator) — the whole clause is dropped, because
      nothing about it can be trusted to mean what it says;
    * a clause left with **no values** — dropped entirely. ``rule_predicate``
      raises ``RuleError`` on an empty ``values`` list, so leaving one behind
      would turn a drafting miss into a 500 at query time.
    """
    by_key = {facet.key: facet for facet in vocabulary}
    folded_keys = {facet.key.casefold(): facet for facet in vocabulary}
    unknown: list[str] = []

    def report(term: str) -> None:
        cleaned = term.strip() or BLANK_TERM
        if cleaned not in unknown:
            unknown.append(cleaned)

    clauses: list[Clause] = []
    for drafted_clause in drafted.all:
        facet = by_key.get(drafted_clause.facet) or folded_keys.get(
            drafted_clause.facet.strip().casefold()
        )
        if facet is None:
            report(drafted_clause.facet)
            continue
        op_key = drafted_clause.op.strip().casefold()
        if op_key not in _OPS:
            # Neither operator can be assumed: reading an unknown op as "in"
            # would invert an exclusion into an inclusion, which is the one
            # rewrite that silently moves money into the chart.
            report(drafted_clause.op)
            continue
        op: Literal["in", "not_in"] = "in" if op_key == "in" else "not_in"
        kept: list[str] = []
        for raw_value in drafted_clause.values:
            resolved = _resolve_value(facet, raw_value)
            if resolved is None:
                report(raw_value)
            elif resolved not in kept:
                kept.append(resolved)
        if not kept:
            continue
        clauses.append(Clause(facet=facet.key, op=op, values=kept))

    proposed_split: str | None = None
    raw_split = (drafted.split or "").strip()
    if raw_split:
        if raw_split.casefold() == SENDER_SPLIT:
            proposed_split = SENDER_SPLIT
        elif (split_facet := folded_keys.get(raw_split.casefold())) is not None:
            proposed_split = split_facet.key
        else:
            report(raw_split)

    return DraftResult(rule=Rule(all=clauses), proposed_split=proposed_split, unknown_terms=unknown)


async def draft_rule(
    session: AsyncSession,
    question: str,
    *,
    settings: Settings | None = None,
    client: AsyncAnthropic | None = None,
    backend: LLMBackend = "api",
) -> DraftResult:
    """Draft a chart rule for ``question`` against the vocabulary in ``session``.

    Only the ``"api"`` backend is implemented: it uses ``messages.parse()`` with
    :class:`DraftedRule` as the structured-output schema. The subscription
    backend returns free text and cannot use ``parse()``; rather than adding a
    second, untested tolerant-JSON path whose failure mode is an empty rule (=
    "all spending"), it raises :class:`DraftError`.
    """
    resolved_settings = settings if settings is not None else get_settings()
    if backend != "api":
        raise DraftError(
            f"the {backend} backend cannot draft a rule: structured output "
            "(messages.parse) is API-only"
        )
    vocabulary = await load_vocabulary(session)
    prompt = build_draft_prompt(vocabulary, question)

    async def _call(anthropic: AsyncAnthropic) -> DraftedRule:
        response = await anthropic.messages.parse(
            model=resolved_settings.extraction_model,
            max_tokens=MAX_DRAFT_TOKENS,
            system=DRAFT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=DraftedRule,
        )
        parsed = response.parsed_output
        if parsed is None:
            raise DraftError(f"{resolved_settings.extraction_model} returned no parseable output")
        logger.debug(
            "drafted a chart rule with %d clause(s)",
            len(parsed.all),
        )
        return parsed

    if client is not None:
        drafted = await _call(client)
    elif resolved_settings.anthropic_api_key is None:
        # Loud, unlike the labeller's quiet None: drafting is a foreground
        # request from the owner, so an unrunnable model must reach them.
        raise DraftError("no Anthropic API key is configured; cannot draft a rule")
    else:
        api_key = resolved_settings.anthropic_api_key.get_secret_value()
        async with AsyncAnthropic(api_key=api_key) as owned:
            drafted = await _call(owned)
    return filter_drafted_rule(drafted, vocabulary)
