"""The facet REST surface, exercised through the app."""

import asyncio
import hashlib
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.api.facets import KEY_MAX_LENGTH, KEY_PATTERN
from library.models import (
    Document,
    DocumentSource,
    DocumentStatus,
    Facet,
    FacetValueSuggestion,
    Sender,
)

pytestmark = pytest.mark.integration


def _make_facet(api_client: TestClient) -> str:
    """Create a fresh, uniquely-keyed facet to work in.

    Deliberately not the shipped vocabulary: it is shared across the whole
    integration suite and this file must not depend on or assert against it.
    """
    key = f"api-{uuid.uuid4().hex[:8]}"
    response = api_client.post("/api/facets", json={"key": key, "label": "Api"})
    assert response.status_code == 201, response.text
    return key


def _run[T](api_database_url: str, op: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run an async DB operation against the API test DB in the main thread.

    Nothing in the HTTP surface creates a ``FacetValueSuggestion`` row (that is
    the labeller's job, exercised elsewhere) so the suggestion tests below seed
    one directly. Mirrors ``tests/test_admin_api.py``'s ``_run_service``: a
    short-lived NullPool engine against the same database ``api_client`` reads
    from, run in the main thread rather than TestClient's event-loop thread.
    """

    async def _body() -> T:
        engine = create_async_engine(api_database_url, poolclass=NullPool)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await op(session)
        finally:
            await engine.dispose()

    return asyncio.run(_body())


def _seed_suggestion(
    api_database_url: str, facet_key: str, document_id: int, suggested_label: str
) -> int:
    """Insert a pending suggestion directly against ``facet_key``; returns its id."""

    async def _op(session: AsyncSession) -> int:
        facet_id = (
            await session.execute(select(Facet.id).where(Facet.key == facet_key))
        ).scalar_one()
        suggestion = FacetValueSuggestion(
            facet_id=facet_id,
            document_id=document_id,
            suggested_label=suggested_label,
            reason="the labeller thought this document belonged in this facet",
        )
        session.add(suggestion)
        await session.flush()
        await session.commit()
        return suggestion.id

    return _run(api_database_url, _op)


def _seed_deleted_document(api_database_url: str) -> int:
    """A soft-deleted document, to prove labels are refused for trashed rows."""

    async def _op(session: AsyncSession) -> int:
        marker = f"facets-deleted:{uuid.uuid4()}"
        document = Document(
            sha256=hashlib.sha256(marker.encode()).hexdigest(),
            mime_type="application/pdf",
            source=DocumentSource.UPLOAD,
            status=DocumentStatus.INDEXED,
            deleted_at=datetime.now(UTC),
        )
        session.add(document)
        await session.flush()
        await session.commit()
        return document.id

    return _run(api_database_url, _op)


def test_the_vocabulary_lists_facets_and_values(api_client: TestClient) -> None:
    key = _make_facet(api_client)
    assert (
        api_client.post(
            f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"}
        ).status_code
        == 201
    )
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert [v["key"] for v in facet["values"]] == ["alpha"]


def test_setting_and_reading_a_documents_labels(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    put = api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}}
    )
    assert put.status_code == 200, put.text
    assert (
        api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key]
        == "alpha"
    )


def test_a_null_clears_a_label(api_client: TestClient, seeded_document_id: int) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: None}})
    assert key not in api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"]


def test_an_unknown_value_is_rejected_with_422_not_created(
    api_client: TestClient, seeded_document_id: int
) -> None:
    """The closed set holds at the API boundary too, not only in the labeller."""
    key = _make_facet(api_client)
    response = api_client.put(
        f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "invented"}}
    )
    assert response.status_code == 422
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert facet["values"] == []


def test_deleting_a_value_in_use_returns_409(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    assert api_client.delete(f"/api/facets/{key}/values/alpha").status_code == 409


def test_merge_moves_labels_and_reports_the_count(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    response = api_client.post(f"/api/facets/{key}/values/alpha/merge", json={"into": "beta"})
    assert response.status_code == 200
    assert response.json()["moved"] == 1
    assert (
        api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key]
        == "beta"
    )


def test_a_dry_run_merge_reports_the_count_without_moving_anything(
    api_client: TestClient, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})
    response = api_client.post(
        f"/api/facets/{key}/values/alpha/merge", json={"into": "beta", "dry_run": True}
    )
    assert response.json()["moved"] == 1
    # nothing moved: the label and the source value both survive
    assert (
        api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key]
        == "alpha"
    )
    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert "alpha" in {v["key"] for v in facet["values"]}


def test_anonymous_access_is_refused(anon_client: TestClient) -> None:
    assert anon_client.get("/api/facets").status_code in (401, 403)


def test_a_pending_suggestion_is_listed(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "Gamma Label")
    body = api_client.get("/api/facet-suggestions").json()
    row = next(s for s in body["suggestions"] if s["id"] == suggestion_id)
    assert row["facet"] == key
    assert row["suggested_label"] == "Gamma Label"
    assert row["document_id"] == seeded_document_id


def test_accepting_a_suggestion_creates_the_value_and_labels_the_document(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    """The one sanctioned path that widens the closed vocabulary."""
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "Gamma Label")
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/accept")
    assert response.status_code == 200, response.text
    assert response.json() == {"facet": key, "value": "gamma-label"}

    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    assert "gamma-label" in {v["key"] for v in facet["values"]}

    labels = api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"]
    assert labels[key] == "gamma-label"


def test_accepting_a_suggestion_whose_derived_key_already_exists_is_409(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    api_client.post(
        f"/api/facets/{key}/values", json={"key": "gamma-label", "label": "Gamma Label"}
    )
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "Gamma Label")
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/accept")
    assert response.status_code == 409

    body = api_client.get("/api/facets").json()
    facet = next(f for f in body["facets"] if f["key"] == key)
    values = [v for v in facet["values"] if v["key"] == "gamma-label"]
    assert len(values) == 1
    assert values[0]["label"] == "Gamma Label"


def test_dismissing_a_suggestion_removes_it_from_the_pending_list(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "Gamma Label")
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/dismiss")
    assert response.status_code == 200, response.text
    ids = {s["id"] for s in api_client.get("/api/facet-suggestions").json()["suggestions"]}
    assert suggestion_id not in ids


def test_creating_a_duplicate_facet_key_is_409(api_client: TestClient) -> None:
    key = _make_facet(api_client)
    response = api_client.post("/api/facets", json={"key": key, "label": "Api"})
    assert response.status_code == 409, response.text


def test_creating_a_duplicate_value_key_is_409(api_client: TestClient) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    response = api_client.post(
        f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha Duplicate"}
    )
    assert response.status_code == 409, response.text


def test_merging_a_value_into_itself_is_409_and_destroys_nothing(api_client: TestClient) -> None:
    """A self-merge is a mistake, not an intent.

    Unguarded it returned ``200 {"moved": 0}`` while the copy-then-delete
    re-pointed the value's aliases onto its own id, deleted them, and deleted
    the value — a silent data loss behind a success code.
    """
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values/alpha/aliases", json={"alias": "first letter"})

    response = api_client.post(f"/api/facets/{key}/values/alpha/merge", json={"into": "alpha"})
    assert response.status_code == 409, response.text

    facet = next(f for f in api_client.get("/api/facets").json()["facets"] if f["key"] == key)
    value = next(v for v in facet["values"] if v["key"] == "alpha")
    assert value["aliases"] == ["first letter"]


def test_merging_an_in_use_value_into_itself_is_409_not_500(
    api_client: TestClient, seeded_document_id: int
) -> None:
    """With the value in use the same path raised an uncaught IntegrityError."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.put(f"/api/documents/{seeded_document_id}/labels", json={"labels": {key: "alpha"}})

    response = api_client.post(f"/api/facets/{key}/values/alpha/merge", json={"into": "alpha"})
    assert response.status_code == 409, response.text
    assert (
        api_client.get(f"/api/documents/{seeded_document_id}/labels").json()["labels"][key]
        == "alpha"
    )


def test_a_dry_run_merge_into_an_unknown_value_is_404(api_client: TestClient) -> None:
    """The dry run resolves both sides, so it fails on everything the merge would."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    response = api_client.post(
        f"/api/facets/{key}/values/alpha/merge", json={"into": "nonexistent", "dry_run": True}
    )
    assert response.status_code == 404, response.text


def test_a_dry_run_merge_into_itself_is_409(api_client: TestClient) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    response = api_client.post(
        f"/api/facets/{key}/values/alpha/merge", json={"into": "alpha", "dry_run": True}
    )
    assert response.status_code == 409, response.text


def test_accepting_a_punctuation_heavy_suggestion_derives_a_contract_shaped_key(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    """``accept`` is the only route that widens the vocabulary, so the key it
    derives must satisfy the same ``^[a-z0-9_-]+$`` contract POST enforces."""
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(
        api_database_url, key, seeded_document_id, "EV charging (home)!"
    )
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/accept")
    assert response.status_code == 200, response.text
    assert response.json() == {"facet": key, "value": "ev-charging-home"}
    assert re.fullmatch(KEY_PATTERN, response.json()["value"])
    # The label keeps the human wording; only the key is sanitised.
    facet = next(f for f in api_client.get("/api/facets").json()["facets"] if f["key"] == key)
    value = next(v for v in facet["values"] if v["key"] == "ev-charging-home")
    assert value["label"] == "EV charging (home)!"


def test_accepting_an_over_long_suggestion_truncates_the_key_rather_than_500ing(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    """``facet_values.key`` is VARCHAR(64); over-long raised DBAPIError — which
    is not IntegrityError, so the 409 handler missed it and it became a 500."""
    label = "Vehicle " * 30  # 240 characters, far past the 64-character key limit
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, label)
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/accept")
    assert response.status_code == 200, response.text
    derived = response.json()["value"]
    assert len(derived) <= KEY_MAX_LENGTH
    assert re.fullmatch(KEY_PATTERN, derived)


def test_accepting_a_suggestion_with_no_usable_characters_is_422(
    api_client: TestClient, api_database_url: str, seeded_document_id: int
) -> None:
    key = _make_facet(api_client)
    suggestion_id = _seed_suggestion(api_database_url, key, seeded_document_id, "!!! ??? ***")
    response = api_client.post(f"/api/facet-suggestions/{suggestion_id}/accept")
    assert response.status_code == 422, response.text
    assert "!!! ??? ***" in response.json()["detail"]


def test_putting_labels_on_an_unknown_document_is_404(api_client: TestClient) -> None:
    """Every sibling document router 404s; the FK violation was a 500."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    response = api_client.put("/api/documents/999999999/labels", json={"labels": {key: "alpha"}})
    assert response.status_code == 404, response.text


def test_putting_labels_on_a_deleted_document_is_404(
    api_client: TestClient, api_database_url: str
) -> None:
    """A trashed document must not gain labels."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    document_id = _seed_deleted_document(api_database_url)
    response = api_client.put(
        f"/api/documents/{document_id}/labels", json={"labels": {key: "alpha"}}
    )
    assert response.status_code == 404, response.text


def test_get_facets_returns_colour(api_client: TestClient, api_database_url: str) -> None:
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.post(f"/api/facets/{key}/values", json={"key": "beta", "label": "Beta"})

    async def paint(session: AsyncSession) -> None:
        await session.execute(
            text(
                "UPDATE facet_values SET colour = '#1f77b4' WHERE key = 'alpha' "
                "AND facet_id = (SELECT id FROM facets WHERE key = :facet)"
            ),
            {"facet": key},
        )
        await session.commit()

    _run(api_database_url, paint)

    facet = next(f for f in api_client.get("/api/facets").json()["facets"] if f["key"] == key)
    colours = {v["key"]: v["colour"] for v in facet["values"]}
    assert colours == {"alpha": "#1f77b4", "beta": None}


def test_get_senders_returns_colour(api_client: TestClient, api_database_url: str) -> None:
    name = f"Corvus Test Supply {uuid.uuid4()}"

    async def seed(session: AsyncSession) -> None:
        session.add(Sender(name=name, colour="#d62728"))
        await session.commit()

    _run(api_database_url, seed)

    rows = api_client.get("/api/senders").json()
    assert [r["colour"] for r in rows if r["name"] == name] == ["#d62728"]


COLOUR_PATTERN_REJECTS = ["1f77b4", "#1f7", "#gggggg", "rebeccapurple"]


def _colour(api_client: TestClient, facet_key: str, value_key: str) -> str | None:
    facet = next(f for f in api_client.get("/api/facets").json()["facets"] if f["key"] == facet_key)
    return next(v["colour"] for v in facet["values"] if v["key"] == value_key)


def _labelled(api_client: TestClient, facet_key: str, value_key: str) -> str:
    facet = next(f for f in api_client.get("/api/facets").json()["facets"] if f["key"] == facet_key)
    return next(v["label"] for v in facet["values"] if v["key"] == value_key)


def test_a_value_s_colour_can_be_set_without_renaming_it(api_client: TestClient) -> None:
    """Setting a colour must not force a rename. `label` is optional on the
    patch for the same reason `colour` is: an absent field is left alone."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})

    response = api_client.patch(f"/api/facets/{key}/values/alpha", json={"colour": "#1f77b4"})

    assert response.status_code == 200, response.text
    assert _colour(api_client, key, "alpha") == "#1f77b4"
    assert _labelled(api_client, key, "alpha") == "Alpha"


def test_an_explicit_null_clears_a_colour_and_an_absent_field_does_not(
    api_client: TestClient,
) -> None:
    """The `model_fields_set` distinction, which is the whole reason this is not
    one nullable field: "clear it" and "do not touch it" are different requests
    that both look like None."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})
    api_client.patch(f"/api/facets/{key}/values/alpha", json={"colour": "#1f77b4"})

    api_client.patch(f"/api/facets/{key}/values/alpha", json={"label": "Alpha renamed"})
    assert _colour(api_client, key, "alpha") == "#1f77b4", "an absent colour is left alone"

    api_client.patch(f"/api/facets/{key}/values/alpha", json={"colour": None})
    assert _colour(api_client, key, "alpha") is None, "an explicit null clears it"


@pytest.mark.parametrize("colour", COLOUR_PATTERN_REJECTS)
def test_a_malformed_colour_is_a_422_not_a_500(api_client: TestClient, colour: str) -> None:
    """Refused by the request model, so the database CHECK is defence in depth
    rather than the error path the owner sees."""
    key = _make_facet(api_client)
    api_client.post(f"/api/facets/{key}/values", json={"key": "alpha", "label": "Alpha"})

    response = api_client.patch(f"/api/facets/{key}/values/alpha", json={"colour": colour})

    assert response.status_code == 422


def test_patching_an_unknown_value_is_still_a_404(api_client: TestClient) -> None:
    """The behaviour the route had before it became a patch, preserved."""
    key = _make_facet(api_client)
    response = api_client.patch(f"/api/facets/{key}/values/absent", json={"label": "X"})
    assert response.status_code == 404


def _seed_sender(api_database_url: str, name: str) -> int:
    async def work(session: AsyncSession) -> int:
        sender = Sender(name=name)
        session.add(sender)
        await session.flush()
        await session.commit()
        return sender.id

    return _run(api_database_url, work)


def test_a_sender_s_colour_can_be_set_and_cleared(
    api_client: TestClient, api_database_url: str
) -> None:
    sender_id = _seed_sender(api_database_url, f"Corvus Test Supply {uuid.uuid4()}")

    set_response = api_client.patch(f"/api/senders/{sender_id}", json={"colour": "#d62728"})
    assert set_response.status_code == 200, set_response.text
    assert set_response.json()["colour"] == "#d62728"

    clear_response = api_client.patch(f"/api/senders/{sender_id}", json={"colour": None})
    assert clear_response.json()["colour"] is None


def test_patching_an_unknown_sender_is_a_404(api_client: TestClient) -> None:
    assert api_client.patch("/api/senders/999999", json={"colour": "#d62728"}).status_code == 404


@pytest.mark.parametrize("colour", COLOUR_PATTERN_REJECTS)
def test_a_malformed_sender_colour_is_a_422(
    api_client: TestClient, api_database_url: str, colour: str
) -> None:
    sender_id = _seed_sender(api_database_url, f"Corvus Test Supply {uuid.uuid4()}")
    assert api_client.patch(f"/api/senders/{sender_id}", json={"colour": colour}).status_code == 422
