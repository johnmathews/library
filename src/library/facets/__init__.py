"""Controlled label vocabulary: facets, values, aliases, and document labels.

This package is growing module by module as the feature is built. So far:
``vocabulary`` reads the vocabulary and writes both the vocabulary itself
(create/rename/alias/merge/delete) and document labels, and never calls an
LLM; ``seed`` declares the vocabulary this feature ships with and loads it
idempotently. Later modules split off further along the same lines:
``labeller`` will build a prompt and call the model without writing;
``apply`` will be the only module that does both reading and writing
against a model call; ``backfill`` and ``recipients`` add further
label-population passes. The vocabulary/labeller/apply split is deliberate:
it is what lets the closed-set guarantee be tested without a model.
"""

from library.facets.vocabulary import (
    UnknownFacetError,
    UnknownValueError,
    ValueInUseError,
    VocabularyFacet,
    VocabularyValue,
    add_alias,
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
    "UnknownFacetError",
    "UnknownValueError",
    "ValueInUseError",
    "VocabularyFacet",
    "VocabularyValue",
    "add_alias",
    "create_facet",
    "create_value",
    "delete_value",
    "document_labels",
    "load_vocabulary",
    "merge_values",
    "rename_value",
    "set_document_label",
]
