"""Tests for the /healthz container healthcheck endpoint."""

from fastapi.testclient import TestClient

import library


def test_healthz_returns_200(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200


def test_healthz_body_shape(client: TestClient) -> None:
    body: dict[str, str] = client.get("/healthz").json()
    assert set(body.keys()) == {"status", "version", "git_sha", "ocr_models"}
    assert body["status"] == "ok"
    # Reported unconditionally, not only when broken: "ok" is what lets a
    # deploy check distinguish a healthy image from an older one that predates
    # the key entirely. `claude_credentials` can be absent because it is
    # conditional on credentials existing; the OCR weights always apply.
    assert body["ocr_models"] == "ok"


def test_healthz_version_matches_package(client: TestClient) -> None:
    body: dict[str, str] = client.get("/healthz").json()
    assert body["version"] == library.__version__


def test_healthz_reports_git_sha(client: TestClient, monkeypatch) -> None:
    """git_sha reflects the image's baked-in build commit (settings.git_sha)."""
    import library.app as app_module
    from library.config import get_settings

    pinned = get_settings().model_copy(update={"git_sha": "deadbee"})
    monkeypatch.setattr(app_module, "get_settings", lambda: pinned)

    body: dict[str, str] = client.get("/healthz").json()
    assert body["git_sha"] == "deadbee"


def test_healthz_git_sha_is_none_when_unset(client: TestClient) -> None:
    """Unbuilt/dev contexts (no GIT_SHA build-arg) report a null git_sha, not a crash."""
    body: dict[str, str] = client.get("/healthz").json()
    assert body["git_sha"] is None


def test_main_module_exposes_app() -> None:
    from fastapi import FastAPI

    from library.main import app

    assert isinstance(app, FastAPI)


def test_healthz_degrades_when_the_ocr_weights_are_absent(
    client: TestClient, monkeypatch, tmp_path
) -> None:
    """A deploy whose image lost `COPY models/` says so, at deploy time.

    This is the whole point of the key (GH #109). `photo.get_engine()` is lazy
    and `lru_cache`d, so without this the first symptom of a weightless image
    is one failed photo ingest, hours later — every healthcheck green until
    then. Degraded, not a 500: the API is genuinely serving, and only the photo
    OCR path is affected.
    """
    from library.ocr import weights

    monkeypatch.setattr(weights, "MODEL_DIR", tmp_path)
    weights.pinned_models.cache_clear()
    try:
        response = client.get("/healthz")
    finally:
        weights.pinned_models.cache_clear()

    assert response.status_code == 200
    body: dict[str, str] = response.json()
    assert body["status"] == "degraded"
    assert body["ocr_models"] == "missing"
    # Names the paths, so the fix is obvious from the healthcheck alone.
    assert str(tmp_path) in body["ocr_models_detail"]


def test_healthz_is_ok_again_after_the_degraded_test(client: TestClient) -> None:
    """No pollution: the test above monkeypatches a module global and a cache.

    Deliberately last in the file and deliberately order-dependent — that is
    what it checks. `weights.pinned_models` is `lru_cache`d and keyed on
    nothing, so a missed `cache_clear()` in the degraded test would leave every
    later test in the session reading `/healthz` as degraded.
    """
    body: dict[str, str] = client.get("/healthz").json()
    assert body["ocr_models"] == "ok"
    assert body["status"] == "ok"
