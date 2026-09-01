"""A chart's rule: which `spend_facts` rows the question is asking about.

Pure — no session, no I/O — so the translation is exhaustively testable
without a database. Facet and value keys are always bound, never
interpolated: they reach here from an LLM draft as well as from the owner.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RuleError(ValueError):
    """The rule cannot be translated into a predicate."""


class Clause(BaseModel):
    """One `facet in/not_in values` test.

    `extra="forbid"` is load-bearing rather than tidiness. `op` has a default,
    so under Pydantic's default `extra="ignore"` a mis-named field — `operator`
    instead of `op` — validates to `op="in"` and an exclusion silently becomes
    an inclusion. `draft.py` refuses an unrecognised operator for the same
    reason, spelled out there: it is the one rewrite that moves money *into* a
    chart. This model is reachable from a browser, so it needs the refusal too.

    Safe to forbid on the read path as well as the write path: every stored
    rule was written by `model_dump()` on this class, and the charts migration
    seeds no rows, so no persisted rule carries a key this would now reject.
    """

    model_config = ConfigDict(extra="forbid")

    facet: str
    op: Literal["in", "not_in"] = "in"
    values: list[str]


class Rule(BaseModel):
    """A conjunction of clauses.

    Forbids extras for the same reason `Clause` does, one level up:
    `DraftedRule` carries a `split` beside its `all`, so a caller posting the
    drafted shape verbatim would otherwise have its split silently discarded.
    """

    model_config = ConfigDict(extra="forbid")

    #: ANDed. Empty matches every row — that is the "All spending" chart.
    all: list[Clause] = []


def rule_predicate(rule: Rule) -> tuple[str, dict[str, object]]:
    """Translate a rule into a SQL fragment over the alias ``sf``."""
    if not rule.all:
        return "TRUE", {}
    fragments: list[str] = []
    params: dict[str, object] = {}
    for index, clause in enumerate(rule.all):
        if not clause.values:
            raise RuleError(f"clause {index} on facet '{clause.facet}' has no values")
        facet_key, values_key = f"f{index}", f"v{index}"
        params[facet_key] = clause.facet
        params[values_key] = list(clause.values)
        member = f"sf.labels->>:{facet_key} = ANY(:{values_key})"
        if clause.op == "in":
            fragments.append(f"({member})")
        else:
            # A row with no value for this facet has labels->>facet IS NULL,
            # and NULL <> ANY(...) is NULL rather than TRUE. Without the
            # explicit IS NULL arm an unlabelled row satisfies neither a
            # `not_in` rule nor its complement, and disappears from both.
            fragments.append(f"(sf.labels->>:{facet_key} IS NULL OR NOT ({member}))")
    return " AND ".join(fragments), params
