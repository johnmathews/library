"""Pushover client tests (W1): send + validate, no network, no database.

The httpx boundary is faked with ``httpx.MockTransport`` (the same strategy as
``test_importer``/``test_embedding_client``), so these are pure unit tests of
request shape and response parsing.
"""

from __future__ import annotations

import httpx
import pytest

from library import notifications
from library.notifications import (
    PushoverResult,
    dispatch_document_completion,
    dispatch_document_notification,
    send_pushover,
    validate_pushover,
)
from library.schemas import (
    NotificationEvent,
    get_notification_credentials,
    resolve_notification_settings,
)


def _enabled_blob(**overrides: object) -> dict[str, object]:
    blob = {
        "notifications": {
            "enabled": True,
            "pushover_app_token": "app",
            "pushover_user_key": "usr",
            "pushover_device": "iphone",
            "events": ["document_success", "processing_error"],
        }
    }
    blob["notifications"].update(overrides)  # type: ignore[union-attr]
    return blob


def test_resolve_notification_settings_defaults_for_empty() -> None:
    out = resolve_notification_settings(None)
    assert out.enabled is False
    assert out.pushover_app_token_set is False
    assert out.pushover_user_key_set is False
    assert out.pushover_device is None
    assert out.events == []


def test_resolve_notification_settings_is_secret_safe() -> None:
    out = resolve_notification_settings(_enabled_blob())
    dumped = out.model_dump()
    assert dumped["pushover_app_token_set"] is True
    assert "pushover_app_token" not in dumped
    assert "pushover_user_key" not in dumped
    assert out.events == [NotificationEvent.DOCUMENT_SUCCESS, NotificationEvent.PROCESSING_ERROR]


def test_get_credentials_returns_when_configured() -> None:
    creds = get_notification_credentials(_enabled_blob())
    assert creds is not None
    assert creds.app_token == "app"
    assert creds.user_key == "usr"
    assert creds.device == "iphone"
    assert NotificationEvent.DOCUMENT_SUCCESS in creds.events


def test_get_credentials_none_when_disabled() -> None:
    assert get_notification_credentials(_enabled_blob(enabled=False)) is None


def test_get_credentials_none_when_missing_key() -> None:
    blob = _enabled_blob()
    del blob["notifications"]["pushover_user_key"]  # type: ignore[union-attr]
    assert get_notification_credentials(blob) is None


def test_get_credentials_none_when_no_events_selected() -> None:
    assert get_notification_credentials(_enabled_blob(events=[])) is None


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_send_success_posts_form_encoded() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"status": 1, "request": "abc-123"},
            headers={"X-Limit-App-Remaining": "7496"},
        )

    async with _client(handler) as client:
        result = await send_pushover(
            app_token="tok",
            user_key="usr",
            message="hello",
            title="Title",
            priority=1,
            client=client,
        )

    assert result.ok is True
    assert result.request_id == "abc-123"
    assert result.app_remaining == 7496
    assert captured["url"] == "https://api.pushover.net/1/messages.json"
    assert "application/x-www-form-urlencoded" in str(captured["content_type"])
    body = str(captured["body"])
    # Form-encoded (data=), not JSON.
    assert "token=tok" in body
    assert "user=usr" in body
    assert "message=hello" in body
    assert "priority=1" in body
    assert "title=Title" in body


async def test_send_omits_empty_optionals() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"status": 1, "request": "r"})

    async with _client(handler) as client:
        await send_pushover(app_token="t", user_key="u", message="m", client=client)

    body = captured["body"]
    assert "title=" not in body
    assert "device=" not in body
    assert "url=" not in body


async def test_send_invalid_user_flagged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "status": 0,
                "user": "invalid",
                "errors": ["user identifier is invalid"],
                "request": "r",
            },
        )

    async with _client(handler) as client:
        result = await send_pushover(app_token="t", user_key="bad", message="m", client=client)

    assert result.ok is False
    assert result.invalid_user is True
    assert result.invalid_token is False
    assert result.errors == ("user identifier is invalid",)


async def test_send_invalid_token_flagged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "status": 0,
                "token": "invalid",
                "errors": ["application token is invalid"],
            },
        )

    async with _client(handler) as client:
        result = await send_pushover(app_token="bad", user_key="u", message="m", client=client)

    assert result.ok is False
    assert result.invalid_token is True
    assert result.invalid_user is False


async def test_send_quota_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"status": 0, "errors": ["message limit reached"]},
            headers={"X-Limit-App-Remaining": "0"},
        )

    async with _client(handler) as client:
        result = await send_pushover(app_token="t", user_key="u", message="m", client=client)

    assert result.ok is False
    assert result.app_remaining == 0
    assert result.errors == ("message limit reached",)


async def test_send_transport_error_is_captured_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with _client(handler) as client:
        result = await send_pushover(app_token="t", user_key="u", message="m", client=client)

    assert result.ok is False
    assert result.errors and "request failed" in result.errors[0]


async def test_validate_success_returns_devices() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"status": 1, "devices": ["iphone", "desktop"]})

    async with _client(handler) as client:
        validation = await validate_pushover(app_token="t", user_key="u", client=client)

    assert validation.valid is True
    assert validation.devices == ("iphone", "desktop")
    assert captured["url"] == "https://api.pushover.net/1/users/validate.json"


async def test_validate_failure_returns_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": 0, "errors": ["user identifier is invalid"]})

    async with _client(handler) as client:
        validation = await validate_pushover(app_token="t", user_key="bad", client=client)

    assert validation.valid is False
    assert validation.errors == ("user identifier is invalid",)


# --- Dispatch logic (W3): fake session + patched send_pushover, no DB/network ---


class _FakeUser:
    def __init__(self, preferences: dict[str, object]) -> None:
        self.preferences = preferences


class _FakeDocument:
    def __init__(self, uploader, *, title=None, filename=None, doc_id=7) -> None:
        self.uploader = uploader
        self.title = title
        self.original_filename = filename
        self.id = doc_id


class _FakeSession:
    def __init__(self, document) -> None:
        self._document = document

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, _model: object, _pk: object):
        return self._document


def _factory_for(document):
    def factory() -> _FakeSession:
        return _FakeSession(document)

    return factory


@pytest.fixture
def captured_sends(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    sends: list[dict[str, object]] = []

    async def _fake_send(**kwargs: object) -> PushoverResult:
        sends.append(kwargs)
        return PushoverResult(ok=True, request_id="r")

    monkeypatch.setattr(notifications, "send_pushover", _fake_send)
    return sends


def _owner(*events: str, **extra: object) -> _FakeUser:
    prefs = {
        "notifications": {
            "enabled": True,
            "pushover_app_token": "app",
            "pushover_user_key": "usr",
            "events": list(events),
            **extra,
        }
    }
    return _FakeUser(prefs)


async def test_completion_sends_success_when_not_needs_review(captured_sends) -> None:
    doc = _FakeDocument(_owner("document_success"), title="Invoice")
    sent = await dispatch_document_completion(_factory_for(doc), 7, needs_review=False)
    assert sent is True
    assert len(captured_sends) == 1
    assert captured_sends[0]["title"] == "Document processed"
    assert captured_sends[0]["priority"] == 0


async def test_completion_sends_needs_review_when_flagged_and_opted_in(captured_sends) -> None:
    doc = _FakeDocument(_owner("document_success", "needs_review"))
    sent = await dispatch_document_completion(_factory_for(doc), 7, needs_review=True)
    assert sent is True
    assert captured_sends[0]["title"] == "Document needs review"


async def test_completion_falls_back_to_success_when_review_not_opted_in(captured_sends) -> None:
    doc = _FakeDocument(_owner("document_success"))  # needs_review NOT enabled
    sent = await dispatch_document_completion(_factory_for(doc), 7, needs_review=True)
    assert sent is True
    assert captured_sends[0]["title"] == "Document processed"


async def test_completion_sends_nothing_when_no_relevant_events(captured_sends) -> None:
    doc = _FakeDocument(_owner("duplicate"))  # neither success nor needs_review
    sent = await dispatch_document_completion(_factory_for(doc), 7, needs_review=True)
    assert sent is False
    assert captured_sends == []


async def test_error_dispatch_uses_high_priority(captured_sends) -> None:
    doc = _FakeDocument(_owner("processing_error"))
    sent = await dispatch_document_notification(
        _factory_for(doc), 7, NotificationEvent.PROCESSING_ERROR
    )
    assert sent is True
    assert captured_sends[0]["title"] == "Processing failed"
    assert captured_sends[0]["priority"] == 1


async def test_duplicate_dispatch_respects_opt_in(captured_sends) -> None:
    doc = _FakeDocument(_owner("document_success"))  # duplicate NOT enabled
    sent = await dispatch_document_notification(_factory_for(doc), 7, NotificationEvent.DUPLICATE)
    assert sent is False
    assert captured_sends == []


async def test_no_owner_sends_nothing(captured_sends) -> None:
    doc = _FakeDocument(uploader=None)
    sent = await dispatch_document_completion(_factory_for(doc), 7, needs_review=False)
    assert sent is False
    assert captured_sends == []


async def test_disabled_owner_sends_nothing(captured_sends) -> None:
    doc = _FakeDocument(_owner("document_success", enabled=False))
    sent = await dispatch_document_completion(_factory_for(doc), 7, needs_review=False)
    assert sent is False
    assert captured_sends == []


async def test_deep_link_url_built_from_base(captured_sends) -> None:
    doc = _FakeDocument(_owner("document_success"), doc_id=42)
    await dispatch_document_completion(
        _factory_for(doc), 42, needs_review=False, document_url_base="https://lib.example.com/"
    )
    assert captured_sends[0]["url"] == "https://lib.example.com/documents/42"


async def test_send_failure_returns_false_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _failing_send(**kwargs: object) -> PushoverResult:
        return PushoverResult(ok=False, errors=("boom",))

    monkeypatch.setattr(notifications, "send_pushover", _failing_send)
    doc = _FakeDocument(_owner("document_success"))
    sent = await dispatch_document_completion(_factory_for(doc), 7, needs_review=False)
    assert sent is False


async def test_loaded_document_dispatch_duplicate(captured_sends) -> None:
    doc = _FakeDocument(_owner("duplicate"), title="Receipt")
    sent = await notifications.dispatch_loaded_document_notification(
        doc, NotificationEvent.DUPLICATE
    )
    assert sent is True
    assert captured_sends[0]["title"] == "Duplicate document"


async def test_loaded_document_dispatch_no_owner(captured_sends) -> None:
    doc = _FakeDocument(uploader=None)
    sent = await notifications.dispatch_loaded_document_notification(
        doc, NotificationEvent.DUPLICATE
    )
    assert sent is False
    assert captured_sends == []
