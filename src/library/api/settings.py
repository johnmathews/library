"""User settings: per-user display preferences, plus the read-only
instance-wide email-triage configuration view (docs/api.md)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from library import notifications
from library.auth.deps import current_user

# Aliased: the GET /settings route handler below is itself named get_settings.
from library.config import get_settings as get_app_settings
from library.db import get_session
from library.email_ingest import BODY_MIN_CHARS, BODY_MIN_WORDS
from library.email_label import PROMPT_VERSION
from library.models import User
from library.schemas import (
    AppearancePreferences,
    DashboardPreferences,
    EmailTriageAllowlistOut,
    EmailTriageBodySubstanceOut,
    EmailTriageHoldOut,
    EmailTriageLabelOut,
    EmailTriageNoiseFilterOut,
    EmailTriageOut,
    KindColorsPreferences,
    NotificationSettingsIn,
    UserPreferences,
    resolve_preferences,
)

router: APIRouter = APIRouter(tags=["settings"])


@router.get("/settings", response_model=UserPreferences, summary="Your display preferences")
async def get_settings(
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Resolved display preferences (defaults filled when unset)."""
    return resolve_preferences(user.preferences)


@router.get(
    "/settings/email-triage",
    response_model=EmailTriageOut,
    summary="Effective email triage configuration (read-only)",
)
async def get_email_triage(
    user: Annotated[User, Depends(current_user)],
) -> EmailTriageOut:
    """The live email-in triage pipeline configuration, for the Settings tab.

    Instance-wide (not per-user), read-only, and secret-free: never the IMAP
    credentials or host, never the Anthropic key, never the allowlisted
    addresses — only booleans/counts plus the non-secret thresholds. Settings
    are environment-only (server .env + worker restart); see
    docs/runbooks/email-triage.md and docs/ingestion.md ("Email item
    selection", "Held for review") for semantics.
    """
    settings = get_app_settings()
    allowlist = settings.email_allowed_senders
    return EmailTriageOut(
        email_in_configured=bool(settings.email_host),
        poll_minutes=settings.email_poll_minutes,
        held_folder=settings.email_held_folder,
        processed_folder=settings.email_processed_folder,
        hold=EmailTriageHoldOut(
            enabled=settings.email_hold_enabled,
            below_substance=settings.email_hold_below_substance,
            unknown_senders=settings.email_hold_unknown_senders,
        ),
        allowlist=EmailTriageAllowlistOut(
            configured=bool(allowlist),
            count=len(allowlist),
        ),
        noise_filter=EmailTriageNoiseFilterOut(
            enabled=settings.email_filter_noise_enabled,
            tiny_image_max_bytes=settings.email_filter_tiny_image_max_bytes,
            tiny_image_max_edge_px=settings.email_filter_tiny_image_max_edge_px,
        ),
        label=EmailTriageLabelOut(
            enabled=settings.email_label_enabled,
            active=settings.email_label_enabled and settings.anthropic_api_key is not None,
            model=settings.email_label_model,
            daily_budget_usd=settings.email_label_daily_budget_usd,
            body_snippet_chars=settings.email_label_body_snippet_chars,
            prompt_version=PROMPT_VERSION,
        ),
        body_substance=EmailTriageBodySubstanceOut(
            min_words=BODY_MIN_WORDS,
            min_chars=BODY_MIN_CHARS,
        ),
        imap_timeout_seconds=settings.email_imap_timeout_seconds,
    )


@router.put("/settings", response_model=UserPreferences, summary="Update your dashboard fields")
async def put_settings(
    payload: DashboardPreferences,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Persist the dashboard field list. Unknown keys are dropped (200)."""
    # Reassign the whole dict so SQLAlchemy detects the JSONB change; the
    # spread preserves sibling keys (e.g. background_tone).
    user.preferences = {
        **(user.preferences or {}),
        "dashboard_fields": [field.value for field in payload.dashboard_fields],
    }
    await db.commit()
    return resolve_preferences(user.preferences)


@router.put(
    "/settings/appearance",
    response_model=UserPreferences,
    summary="Update your page-canvas tone and tile preview",
)
async def put_appearance(
    payload: AppearancePreferences,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Persist the page-canvas tone + tile preview + dock position. Unknown values default."""
    user.preferences = {
        **(user.preferences or {}),
        "background_tone": payload.background_tone.value,
        "tile_preview": payload.tile_preview.value,
        "dock_position": payload.dock_position.value,
    }
    await db.commit()
    return resolve_preferences(user.preferences)


@router.put(
    "/settings/kind-colors",
    response_model=UserPreferences,
    summary="Update your per-kind tile border colours",
)
async def put_kind_colors(
    payload: KindColorsPreferences,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Replace the per-kind colour overrides. Malformed entries are dropped; an
    empty map resets every kind to its built-in default."""
    user.preferences = {
        **(user.preferences or {}),
        "kind_colors": payload.kind_colors,
    }
    await db.commit()
    return resolve_preferences(user.preferences)


@router.put(
    "/settings/notifications",
    response_model=UserPreferences,
    summary="Update your Pushover notification settings",
)
async def put_notifications(
    payload: NotificationSettingsIn,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Persist per-user Pushover credentials + event subscriptions.

    Secrets are write-only: an omitted/blank ``pushover_app_token`` or
    ``pushover_user_key`` keeps the stored value (so saving only ``events``
    never wipes credentials). When ``enabled`` is set, both credentials must be
    present (422 otherwise) and are verified against Pushover's validation
    endpoint so a typo is caught at save time rather than silently dropping
    every future push. The response never echoes the raw secrets.
    """
    existing = (user.preferences or {}).get("notifications") or {}
    app_token = payload.pushover_app_token or existing.get("pushover_app_token")
    user_key = payload.pushover_user_key or existing.get("pushover_user_key")
    device = payload.pushover_device  # non-secret, echoed back → authoritative

    if payload.enabled and not (app_token and user_key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A Pushover app token and user key are both required to enable notifications.",
        )
    if payload.enabled:
        validation = await notifications.validate_pushover(
            app_token=app_token, user_key=user_key, device=device
        )
        if not validation.valid:
            reason = "; ".join(validation.errors) or "unknown error"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Pushover rejected these credentials: {reason}",
            )

    block: dict[str, object] = {
        "enabled": payload.enabled,
        "events": [event.value for event in payload.events],
        "email_forward_addresses": payload.email_forward_addresses,
    }
    # Store only non-empty secrets (no empty-string noise in the JSONB).
    if app_token:
        block["pushover_app_token"] = app_token
    if user_key:
        block["pushover_user_key"] = user_key
    if device:
        block["pushover_device"] = device

    user.preferences = {**(user.preferences or {}), "notifications": block}
    await db.commit()
    return resolve_preferences(user.preferences)
