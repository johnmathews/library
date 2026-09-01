"""User settings: per-user display preferences, plus the read-only
instance-wide email-triage configuration view (docs/api.md)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from library import notifications
from library.auth.deps import current_user

# Aliased: the GET /settings route handler below is itself named get_settings.
from library.config import get_settings as get_app_settings
from library.db import get_session
from library.email_ingest import BODY_MIN_CHARS, BODY_MIN_WORDS, SKIP_TRACE_REASONS
from library.email_label import PROMPT_VERSION
from library.llm import backends as llm_backends
from library.models import EmailSelectionTrace, User
from library.schemas import (
    AppearancePreferences,
    AskProfilePreferences,
    DashboardPreferences,
    EmailRecentSkipOut,
    EmailRecentSkipsOut,
    EmailSkipDecisionOut,
    EmailTriageAllowlistOut,
    EmailTriageBodySubstanceOut,
    EmailTriageHoldOut,
    EmailTriageLabelOut,
    EmailTriageNoiseFilterOut,
    EmailTriageOut,
    KindColorsPreferences,
    LLMBackendIn,
    LLMBackendsOut,
    LLMSurfaceOut,
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
            decoration_max_bytes=settings.email_filter_decoration_max_bytes,
            decoration_max_edge_px=settings.email_filter_decoration_max_edge_px,
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


#: How many recent skip-trace rows the settings tab shows. Deliberately small:
#: this is a "did the pipeline just eat my attachment?" glance, not a browser.
RECENT_SKIPS_LIMIT: int = 20


@router.get(
    "/settings/email-triage/recent-skips",
    response_model=EmailRecentSkipsOut,
    summary="Recently skipped email items (read-only)",
)
async def get_email_triage_recent_skips(
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> EmailRecentSkipsOut:
    """The last 20 emails whose selection skipped at least one item, newest first.

    Backed by ``email_selection_traces`` (one row per email with any
    filtered/dropped item, however quiet — see ``library.email_ingest``).
    A sibling of the pure config snapshot above rather than a field on it:
    these are DB rows that change per poll, not environment configuration.
    Each row's stored decision list is filtered down to the actual skips
    (reason in ``SKIP_TRACE_REASONS``) so the payload stays compact — the
    ingested siblings and body bookkeeping entries stay in the row for a
    deeper dig via the database. Secret-free: sender/subject/filenames only.
    """
    rows = (
        (
            await db.execute(
                select(EmailSelectionTrace)
                .order_by(EmailSelectionTrace.created_at.desc(), EmailSelectionTrace.id.desc())
                .limit(RECENT_SKIPS_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return EmailRecentSkipsOut(
        recent_skips=[
            EmailRecentSkipOut(
                id=row.id,
                message_id=row.message_id,
                subject=row.subject,
                from_address=row.from_address,
                created_at=row.created_at,
                decisions=[
                    EmailSkipDecisionOut(
                        kind=str(item.get("kind") or ""),
                        filename=item.get("filename"),
                        reason=item.get("reason"),
                        detail=item.get("detail"),
                    )
                    for item in row.decisions
                    if isinstance(item, dict) and item.get("reason") in SKIP_TRACE_REASONS
                ],
            )
            for row in rows
        ]
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
        "phone_columns": payload.phone_columns,
        "hide_summary_mobile": payload.hide_summary_mobile,
    }
    await db.commit()
    return resolve_preferences(user.preferences)


@router.put(
    "/settings/ask-profile",
    response_model=UserPreferences,
    summary="Update the notes Ask reads about you",
)
async def put_ask_profile(
    payload: AskProfilePreferences,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> UserPreferences:
    """Persist the "About you" notes the Ask prompt carries (docs/ask.md §1.2).

    Blank text clears them. Over-long text is a ``422`` (see
    ``AskProfilePreferences``), never silently cut.
    """
    user.preferences = {
        **(user.preferences or {}),
        "ask_profile": payload.ask_profile,
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

    # One branch, not two: the credentials are only known non-empty inside the
    # branch that raised on the empty case, and splitting it across two separate
    # `if payload.enabled` blocks threw that away (for the reader as much as for
    # the type checker).
    if payload.enabled:
        if not (app_token and user_key):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "A Pushover app token and user key are both required to enable notifications."
                ),
            )
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


#: Human-facing copy for each switchable surface. Lives here rather than in
#: ``BACKEND_SURFACES`` so the resolution layer stays free of presentation.
_SURFACE_COPY: dict[str, tuple[str, str]] = {
    "ask": (
        "Ask",
        "The question-answering tool loop and the model that names new "
        "conversations. The subscription backend removes the per-token bill but "
        "spends subscription quota — roughly 135k tokens for a two-tool turn, "
        "because the Claude Code preamble is re-sent on each step of the loop.",
    ),
}


async def _llm_backends_payload(db: AsyncSession, user: User) -> LLMBackendsOut:
    """Build the LLM backend section for ``user``."""
    settings = get_app_settings()
    resolved = await llm_backends.get_backends(db, settings)
    overrides = {row.key for row in await llm_backends.list_overrides(db)}
    status, detail = await llm_backends.credential_health(settings)

    surfaces = []
    for surface, backend in resolved.items():
        label, description = _SURFACE_COPY.get(surface, (surface, ""))
        surfaces.append(
            LLMSurfaceOut(
                surface=surface,
                label=label,
                description=description,
                backend=backend,
                default=llm_backends.config_default(surface, settings),
                overridden=f"llm_backend.{surface}" in overrides,
            )
        )

    return LLMBackendsOut(
        surfaces=surfaces,
        credentials_status=status,
        credentials_detail=detail,
        api_key_configured=settings.anthropic_api_key is not None,
        editable=user.is_admin,
    )


@router.get(
    "/settings/llm-backends",
    response_model=LLMBackendsOut,
    summary="Which LLM backend each surface uses (instance-wide)",
)
async def get_llm_backends(
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> LLMBackendsOut:
    """Readable by any signed-in user — it explains why Ask behaves as it does.

    Secret-free: reports whether an API key is configured, never the key, and
    the credential status without the tokens behind it. Writing is admin-only
    (see the PUT below), which ``editable`` tells the client so it can render
    the controls read-only rather than let a non-admin discover it via a 403.
    """
    return await _llm_backends_payload(db, user)


@router.put(
    "/settings/llm-backends/{surface}",
    response_model=LLMBackendsOut,
    summary="Set one surface's LLM backend (admin only)",
)
async def put_llm_backend(
    surface: str,
    payload: LLMBackendIn,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> LLMBackendsOut:
    """Switch a surface between the metered API and the Claude subscription.

    Takes effect on the next request — no restart. The backend is validated
    before it is stored, so an admin who enables the subscription without
    provisioning credentials is told at the moment they try, rather than the
    next person to ask a question discovering it as a failed query.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin can change the LLM backend.",
        )
    try:
        await llm_backends.set_backend(
            db, surface, payload.backend, get_app_settings(), user_id=user.id
        )
    except llm_backends.UnknownSurfaceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except llm_backends.BackendUnavailableError as exc:
        # 409, not 400: the request is well-formed and the value is legal — the
        # server just isn't in a state where it can be honoured yet.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return await _llm_backends_payload(db, user)


@router.delete(
    "/settings/llm-backends/{surface}",
    response_model=LLMBackendsOut,
    summary="Revert one surface to the deployed default (admin only)",
)
async def delete_llm_backend(
    surface: str,
    db: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(current_user)],
) -> LLMBackendsOut:
    """Drop the override so the surface follows the environment again."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an admin can change the LLM backend.",
        )
    try:
        await llm_backends.clear_backend(db, surface)
    except llm_backends.UnknownSurfaceError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await _llm_backends_payload(db, user)
