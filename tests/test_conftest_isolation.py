"""The shared API database must be empty at the start of every test.

Two tests that only make sense as a pair, in this order: the first seeds rows
through the real API, the second asserts none of them survive. Run on `main`
(before the autouse truncation fixture) the second one fails, which is what
makes this a regression test rather than a tautology.

`api_database_url` is session-scoped — one migrated database for the whole API
suite — so without teardown truncation every test inherits its predecessors'
rows. Combined with the default `limit=25` on list endpoints, that made
assertions on totals and on `.first()` silently order-dependent.
"""

from fastapi.testclient import TestClient

from tests.test_documents_api import list_docs, seed_document


def test_a_seeds_documents_into_the_shared_database(
    api_client: TestClient, api_database_url: str
) -> None:
    """Seed through the same path the API suite uses."""
    for index in range(3):
        seed_document(
            api_database_url,
            f"isolation-seed-{index}",
            title=f"Isolation {index}",
            tag_slugs=["isolation-probe"],
        )

    body = list_docs(api_client, tag="isolation-probe")
    assert body["total"] == 3


def test_b_sees_an_empty_database(api_client: TestClient, api_database_url: str) -> None:
    """Nothing from the previous test may still be here.

    Asserts the unfiltered total, not just the tagged one: a tag filter would
    still pass if truncation missed `documents` but cleared `document_tags`.
    """
    assert list_docs(api_client)["total"] == 0
    assert list_docs(api_client, tag="isolation-probe")["total"] == 0
