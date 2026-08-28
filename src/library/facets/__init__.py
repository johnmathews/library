"""Controlled label vocabulary: facets, values, aliases, and document labels.

Three modules with deliberately separate jobs. ``vocabulary`` reads and writes
the vocabulary and never calls an LLM. ``labeller`` builds a prompt and calls
the model and never writes. ``apply`` is the only module that does both. That
split is what lets the closed-set guarantee be tested without a model.
"""

from library.facets.vocabulary import (
    UnknownFacetError,
    UnknownValueError,
    VocabularyFacet,
    VocabularyValue,
    document_labels,
    load_vocabulary,
    set_document_label,
)

__all__ = [
    "UnknownFacetError",
    "UnknownValueError",
    "VocabularyFacet",
    "VocabularyValue",
    "document_labels",
    "load_vocabulary",
    "set_document_label",
]
