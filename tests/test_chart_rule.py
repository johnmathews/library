"""Rule -> SQL predicate. Pure; no database.

The injection cases are not paranoia: facet and value keys reach this module
from an LLM draft (Task 8) as well as from the owner, so they are untrusted
input by construction.
"""

from __future__ import annotations

import pytest

from library.charts.rule import Clause, Rule, RuleError, rule_predicate


def test_an_empty_rule_matches_everything() -> None:
    """The seeded "All spending" chart is an empty rule (spec §10.1)."""
    sql, params = rule_predicate(Rule())
    assert sql == "TRUE"
    assert params == {}


def test_a_single_in_clause_reads_the_labels_column() -> None:
    sql, params = rule_predicate(Rule(all=[Clause(facet="category", op="in", values=["software"])]))
    assert sql == "(sf.labels->>:f0 = ANY(:v0))"
    assert params == {"f0": "category", "v0": ["software"]}


def test_clauses_are_anded() -> None:
    sql, params = rule_predicate(
        Rule(
            all=[
                Clause(facet="category", op="in", values=["software"]),
                Clause(facet="cost_type", op="in", values=["subscription", "usage"]),
            ]
        )
    )
    assert sql == "(sf.labels->>:f0 = ANY(:v0)) AND (sf.labels->>:f1 = ANY(:v1))"
    assert params["v1"] == ["subscription", "usage"]


def test_not_in_excludes_unlabelled_rows_too() -> None:
    """A row with no value for the facet has `labels->>facet IS NULL`, and
    `NULL <> ANY(...)` is NULL, not TRUE — so a naive negation drops every
    unlabelled row from a `not_in` result AND from its complement. Both
    would then under-report, and §9.4's footer would be the only place the
    money appeared.
    """
    sql, _ = rule_predicate(Rule(all=[Clause(facet="scope", op="not_in", values=["business"])]))
    assert sql == "(sf.labels->>:f0 IS NULL OR NOT (sf.labels->>:f0 = ANY(:v0)))"


def test_a_clause_with_no_values_is_rejected() -> None:
    with pytest.raises(RuleError):
        rule_predicate(Rule(all=[Clause(facet="category", op="in", values=[])]))


def test_facet_and_value_keys_are_bound_never_interpolated() -> None:
    """Keys arrive from an LLM draft as well as from the owner."""
    sql, params = rule_predicate(
        Rule(all=[Clause(facet="'; DROP TABLE documents; --", op="in", values=["x"])])
    )
    assert "DROP TABLE" not in sql
    assert params["f0"] == "'; DROP TABLE documents; --"
