"""Controlled label vocabulary: facets, values, aliases, and document labels.

Six modules, split by what each is allowed to touch. ``vocabulary`` reads the
vocabulary and writes both the vocabulary itself (create/rename/alias/merge/
delete) and document labels, and never calls an LLM; ``seed`` declares the
vocabulary this feature ships with and loads it idempotently; ``labeller``
builds a prompt and calls the model without writing; ``apply`` is the only
module that both reads a model call and writes against it; ``backfill`` and
``recipients`` add further label-population passes. The vocabulary/labeller/
apply split is deliberate: it is what lets the closed-set guarantee be tested
without a model.
"""

from library.facets.seed import seed_vocabulary
from library.facets.vocabulary import (
    MergeIntoSelfError,
    UnknownFacetError,
    UnknownValueError,
    ValueInUseError,
    VocabularyFacet,
    VocabularyValue,
    add_alias,
    count_labels,
    create_facet,
    create_value,
    delete_value,
    document_labels,
    load_vocabulary,
    merge_values,
    rename_value,
    set_document_label,
)

__all__ = [
    "MergeIntoSelfError",
    "UnknownFacetError",
    "UnknownValueError",
    "ValueInUseError",
    "VocabularyFacet",
    "VocabularyValue",
    "add_alias",
    "count_labels",
    "create_facet",
    "create_value",
    "delete_value",
    "document_labels",
    "load_vocabulary",
    "merge_values",
    "rename_value",
    "seed_vocabulary",
    "set_document_label",
]
