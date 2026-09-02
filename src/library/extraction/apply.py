"""Run extraction for a document and apply the result to the database.

This is the pipeline-facing half of W6. The invariant it enforces:
**extraction never fails a document.** Disabled feature, missing API key,
blown budget, unusable input, API errors — all end in a skip/failed audit
event and a normal return, so the pipeline continues to ``indexed`` and the
document stays searchable by its OCR text.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy import Numeric, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.config import Settings
from library.extraction.extractor import (
    PROMPT_VERSION,
    ExtractionOutcome,
    ExtractionSkipped,
    extract,
)
from library.extraction.validation import (
    Finding,
    derive_review_status,
    findings_to_payload,
    validate,
)
from library.facets.apply import label_and_apply
from library.models import (
    AmountKind,
    Document,
    DocumentLanguage,
    IngestionEvent,
    Kind,
    Recipient,
    Sender,
    SpendLine,
    Tag,
    User,
)

logger = logging.getLogger(__name__)


# Completion events whose cost counts toward the EXTRACTION daily budget: the
# main extraction pass plus the fill-only repair pass over the markdown layer
# (library.extraction.repair) — repair is extraction spend, not markdown spend.
EXTRACTION_SPEND_EVENTS: tuple[str, ...] = ("extraction_completed", "extraction_repair_completed")


async def todays_spend_usd(
    session: AsyncSession, event: str | tuple[str, ...] = EXTRACTION_SPEND_EVENTS
) -> float:
    """Sum today's (UTC) estimated spend recorded by the given completion event(s).

    Defaults to extraction spend (``extraction_completed`` plus
    ``extraction_repair_completed`` — the repair pass draws on the same budget);
    pass another completion event name (e.g. ``email_label_completed``) to gate
    a different budget on its own daily total.
    """
    events = (event,) if isinstance(event, str) else event
    start_of_day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    statement = select(
        func.coalesce(func.sum(IngestionEvent.detail["cost_usd"].astext.cast(Numeric)), 0)
    ).where(
        IngestionEvent.event.in_(events),
        IngestionEvent.detail.has_key("cost_usd"),
        IngestionEvent.created_at >= start_of_day,
    )
    return float((await session.execute(statement)).scalar_one())


async def upsert_sender(session: AsyncSession, name: str) -> Sender:
    """Find a sender by case-insensitive name match, creating it if new."""
    cleaned = " ".join(name.split())
    existing = (
        await session.execute(select(Sender).where(func.lower(Sender.name) == cleaned.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    sender = Sender(name=cleaned)
    session.add(sender)
    await session.flush()
    return sender


async def get_or_create_user_recipient(session: AsyncSession, user: User) -> Recipient:
    """Return the :class:`Recipient` linked to ``user``, creating/linking if needed.

    A user's recipient is named by their display name, falling back to the
    username when the display name is empty. If a recipient with that name
    already exists but is not yet linked to any user, it is *adopted* (its
    ``user_id`` set) rather than duplicated — keeping ``recipients.name`` unique.
    A name already linked to a *different* user is returned as-is (the name is
    shared) rather than risking a duplicate-name insert.
    """
    existing = (
        await session.execute(select(Recipient).where(Recipient.user_id == user.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    name = (user.display_name or "").strip() or user.username
    by_name = (
        await session.execute(select(Recipient).where(func.lower(Recipient.name) == name.lower()))
    ).scalar_one_or_none()
    if by_name is not None:
        if by_name.user_id is None:
            by_name.user_id = user.id
            await session.flush()
        return by_name

    recipient = Recipient(name=name, user_id=user.id)
    session.add(recipient)
    await session.flush()
    return recipient


async def _match_user(session: AsyncSession, name: str) -> User | None:
    """Find a user whose username or (non-empty) display name equals ``name``.

    Case-insensitive; the lowest user id wins on the (rare) overlap where one
    user's username equals another's display name, for deterministic resolution.
    """
    return (
        (
            await session.execute(
                select(User)
                .where(
                    or_(
                        func.lower(User.username) == name.lower(),
                        and_(
                            User.display_name != "",
                            func.lower(User.display_name) == name.lower(),
                        ),
                    )
                )
                .order_by(User.id)
            )
        )
        .scalars()
        .first()
    )


async def match_existing_recipient(session: AsyncSession, name: str) -> Recipient | None:
    """Resolve a name to an **existing** :class:`Recipient`, without inventing one.

    A name matching a user's **username or display name** (case-insensitive)
    resolves to that user's linked recipient (created/adopted as needed — a user
    is a real, known person, not an invented recipient). Any other name resolves
    to a plain recipient by case-insensitive name match. Returns ``None`` when
    nothing matches; unlike :func:`upsert_recipient` it never creates a new plain
    recipient. The extraction path calls this first (any confidence): a match to
    a known recipient/user is always assigned. Only an UNMATCHED name is then
    created — and only when the extraction is high-confidence (see rung 1 in
    :func:`_apply_outcome`) — so low-confidence guesses still can't seed junk rows.
    """
    cleaned = name.strip()
    user = await _match_user(session, cleaned)
    if user is not None:
        return await get_or_create_user_recipient(session, user)

    return (
        await session.execute(
            select(Recipient).where(func.lower(Recipient.name) == cleaned.lower())
        )
    ).scalar_one_or_none()


async def upsert_recipient(session: AsyncSession, name: str) -> Recipient:
    """Resolve an extracted recipient name to a :class:`Recipient` row.

    A name matching a user's **username or display name** (case-insensitive)
    resolves to that user's linked recipient (created/adopted as needed), so a
    document addressed to either name maps to one recipient. Any other name
    upserts a plain recipient by case-insensitive name match, creating if new.

    This *creates* a recipient when none matches. It backs the **manual** edit
    path (``documents_service.apply_document_update``) unconditionally, and the
    extraction path uses it for a **high-confidence** document-stated name that
    matched no existing recipient (rung 1 in :func:`_apply_outcome`).
    """
    existing = await match_existing_recipient(session, name)
    if existing is not None:
        return existing
    recipient = Recipient(name=name.strip())
    session.add(recipient)
    await session.flush()
    return recipient


async def match_user_by_email(session: AsyncSession, address: str) -> int | None:
    """Resolve an email address to the owning user's id.

    Matches the (lowercased, whitespace-stripped) ``address`` against any user's
    ``preferences.notifications.email_forward_addresses``; returns that user's id
    on the first match, else ``None``. Iterates users in Python rather than a
    JSONB containment query — a personal/family deployment has a handful of
    users, so clarity wins; switch to a ``@>`` query if the table ever grows.

    This is the single email→user matcher reused by ``resolve_sender_owner``
    (sender attribution) and ``resolve_recipient_from_email`` (recipient
    fallback); it lives here so both ``email_ingest`` and extraction can import
    it without an import cycle (extraction must not import ``email_ingest``).
    """
    normalized = (address or "").strip().lower()
    if not normalized:
        return None
    rows = (await session.execute(select(User.id, User.preferences))).all()
    for user_id, preferences in rows:
        block = (preferences or {}).get("notifications") or {}
        addresses = block.get("email_forward_addresses") or []
        if isinstance(addresses, list) and normalized in {
            str(item).strip().lower() for item in addresses
        }:
            return user_id
    return None


async def resolve_recipient_from_email(session: AsyncSession, addresses: list[str]) -> int | None:
    """Resolve email ``To:`` addresses to a known user's recipient id.

    For the first address that identifies a user (via ``match_user_by_email``),
    returns that user's linked recipient id (created/adopted via
    ``get_or_create_user_recipient``). Returns ``None`` when no address matches a
    user. Used as the "only fill when empty" recipient fallback in
    ``_apply_outcome`` — see there.
    """
    for address in addresses or []:
        user_id = await match_user_by_email(session, address)
        if user_id is None:
            continue
        user = await session.get(User, user_id)
        if user is not None:
            return (await get_or_create_user_recipient(session, user)).id
    return None


async def resolve_recipient_hint(session: AsyncSession, addresses: list[str]) -> str | None:
    """Resolve email ``To:`` addresses to a known user's display name, for the prompt.

    Returns the display name (falling back to the username) of the first address
    that identifies a user, or ``None``. Fed to ``extract`` as ``recipient_hint``
    so the model can reconcile the email envelope against the document body — a
    resolved name, never a raw email address, so no address leaks into the prompt.
    """
    for address in addresses or []:
        user_id = await match_user_by_email(session, address)
        if user_id is None:
            continue
        user = await session.get(User, user_id)
        if user is not None:
            return (user.display_name or "").strip() or user.username
    return None


async def get_or_create_tag(session: AsyncSession, slug: str) -> Tag:
    existing = (await session.execute(select(Tag).where(Tag.slug == slug))).scalar_one_or_none()
    if existing is not None:
        return existing
    tag = Tag(slug=slug, name=slug.replace("-", " ").capitalize())
    session.add(tag)
    await session.flush()
    return tag


async def _record_event(
    session: AsyncSession, document: Document, event: str, detail: dict[str, Any]
) -> None:
    session.add(IngestionEvent(document_id=document.id, event=event, detail=detail))
    await session.commit()


async def _amount_is_allocated(session: AsyncSession, document: Document) -> bool:
    """Does this document's amount carry a manual spend-line allocation?

    Spend lines are the owner's own division of ``amount_total`` and must sum
    to it exactly; migration 0035's mirror trigger on ``documents`` enforces
    that at COMMIT. So a re-extraction that moved the amount would either
    orphan the allocation or — with the trigger in place — abort the commit
    that also writes the ``extraction_completed`` event, failing the whole
    document over a field it had no business changing.

    Treated exactly like ``extra["user_edited_fields"]``: a human statement
    about the amount outranks a re-read of the page. A 400 is meaningless in a
    background job, and letting the job die would discard the rest of a good
    extraction, so the amount is left alone and the skip is *reported* — in
    ``extra["extraction"]["skipped_fields"]`` and in the log — never silently
    passed over. The owner corrects it by clearing the lines, which is the same
    instruction ``PATCH /api/documents/{id}`` gives them.
    """
    return (
        await session.execute(select(SpendLine.id).where(SpendLine.document_id == document.id))
    ).first() is not None


#: Fields re-extraction must leave alone on a document whose amount is split
#: across spend lines. `amount_total` is the one the deferred trigger actually
#: guards; `currency` and `amount_kind` are here because they **denominate and
#: interpret** that number, and writing them while the amount stays pinned to
#: the lines produces a document that disagrees with itself.
#:
#: The currency case is the concrete one, and it is silent: a re-read that finds
#: a different currency leaves `amount_total` matching the lines and `currency`
#: describing something else, and `amount_currency_coupling` cannot fire, because
#: that rule is an XOR on *presence* and both fields are set. Nothing else would
#: report it. Previously only `amount_total` was skipped, which is what
#: docs/charts.md §13 recorded as "re-extraction's skip is partial".
_ALLOCATION_LOCKED_FIELDS: tuple[str, ...] = ("amount_total", "currency", "amount_kind")


async def _apply_outcome(
    session: AsyncSession, document: Document, outcome: ExtractionOutcome
) -> tuple[list[str], list[str]]:
    """Write extracted values onto the document; return (fields set, fields skipped).

    The second half is returned rather than left in ``extra["extraction"]`` alone
    because the caller has to put it in the ``extraction_completed`` **event**:
    ``extra`` is not a surface anyone reads, and a withheld write that is only
    recorded there is indistinguishable from one that never happened.

    Skips any field listed in ``extra["user_edited_fields"]`` (user edits
    win over re-extraction) and never nulls out existing data with a None
    extraction value.

    ``amount_total`` is skipped for a document with spend lines, for the same
    reason and one stronger: an allocation is the owner's own arithmetic over
    the amount, migration 0035's mirror trigger refuses to let a write orphan
    it, and here there is nobody to tell. See :func:`_amount_is_allocated`.
    """
    metadata = outcome.metadata
    user_edited = set(document.extra.get("user_edited_fields", []))
    fields_set: list[str] = []

    def settable(field: str, value: object) -> bool:
        return value is not None and field not in user_edited

    if settable("kind_id", metadata.kind_slug):
        kind = (
            await session.execute(select(Kind).where(Kind.slug == metadata.kind_slug))
        ).scalar_one_or_none()
        if kind is not None:
            document.kind_id = kind.id
            fields_set.append("kind_id")

    if settable("sender_id", metadata.sender_name):
        assert metadata.sender_name is not None
        document.sender_id = (await upsert_sender(session, metadata.sender_name)).id
        fields_set.append("sender_id")

    # Rung 1 — the recipient NAMED IN THE DOCUMENT is the overriding signal.
    # Resolution uses the canonical ``recipient_name`` only: the model is told to
    # leave it null for impersonal material and generic greetings ("Beste klant"),
    # so honouring that null is what stops junk rows. (``addressee_raw`` is the
    # *verbatim* salutation target — it is kept in provenance and used by the
    # deterministic cross-check in ``validation.py``, but it is deliberately NOT a
    # creation source, because it is populated even for a generic greeting the
    # model correctly declined to name.) A name that matches a known recipient/user
    # is always assigned; an UNMATCHED name is created as a plain recipient only
    # when the extraction is high-confidence, so a confident "Dear Mr de Vries"
    # becomes recipient "Mr de Vries" while a low-confidence guess drops through to
    # the context fallbacks below. (Manual edits still use upsert_recipient
    # unconditionally.)
    if settable("recipient_id", metadata.recipient_name):
        assert metadata.recipient_name is not None
        matched = await match_existing_recipient(session, metadata.recipient_name)
        if matched is not None:
            document.recipient_id = matched.id
            fields_set.append("recipient_id")
        elif metadata.confidence == "high":
            document.recipient_id = (await upsert_recipient(session, metadata.recipient_name)).id
            fields_set.append("recipient_id")

    # Rung 2 — context fallback: when the document named no usable recipient (and
    # the user has not locked it), derive it from the email ``To:`` address if
    # that identifies a known user. A document-stated recipient always wins; a
    # user edit always wins. Threaded onto ``extra["email_to"]`` at ingest time.
    if document.recipient_id is None and "recipient_id" not in user_edited:
        email_to = document.extra.get("email_to")
        if isinstance(email_to, list) and email_to:
            recipient_id = await resolve_recipient_from_email(session, email_to)
            if recipient_id is not None:
                document.recipient_id = recipient_id
                fields_set.append("recipient_id")

    # Rung 3 — final fallback: a still-unattributed document belongs to whoever
    # added it. ``uploader_id`` is resolved at ingest from the forwarder's From:
    # address (``resolve_sender_owner``), so for personal mail you forward to the
    # library the owner *is* the recipient — even when the addressee name on the
    # document matched no known user and the To: header is just the library
    # dropbox. This only fires when both stronger signals (the document-stated
    # name and the To: user) came up empty; a manual edit still wins.
    if (
        document.recipient_id is None
        and "recipient_id" not in user_edited
        and document.uploader_id is not None
    ):
        owner = await session.get(User, document.uploader_id)
        if owner is not None:
            document.recipient_id = (await get_or_create_user_recipient(session, owner)).id
            fields_set.append("recipient_id")

    scalar_values: dict[str, object | None] = {
        "title": metadata.title,
        "summary": metadata.summary,
        "document_date": metadata.document_date,
        "due_date": metadata.due_date,
        "expiry_date": metadata.expiry_date,
        "amount_total": Decimal(metadata.amount_total) if metadata.amount_total else None,
        "currency": metadata.currency,
        "amount_kind": AmountKind(metadata.amount_kind) if metadata.amount_kind else None,
        "reference": metadata.reference,
    }
    skipped_fields: list[str] = []
    # Resolved at most once per document, and only if some locked field would
    # really move. `None` means "not asked yet".
    allocated: bool | None = None
    for field, value in scalar_values.items():
        if not settable(field, value):
            continue
        # Ordered so the extra query runs only when a write would really move
        # something: an unchanged (or unextracted) value cannot orphan anything.
        # `is not None` is load-bearing: FILLING a blank contradicts nothing —
        # a document whose currency was never extracted gains one safely, and
        # the trigger only ever guards a *change*. Only an existing value that
        # would move is withheld. (`amount_total` cannot be NULL on an allocated
        # document, so this leaves its behaviour exactly as it was.)
        current = getattr(document, field)
        if field in _ALLOCATION_LOCKED_FIELDS and current is not None and value != current:
            if allocated is None:
                allocated = await _amount_is_allocated(session, document)
            if allocated:
                skipped_fields.append(field)
                logger.warning(
                    "extraction left %s alone on document %s: its amount is allocated "
                    "across spend lines summing to %s",
                    field,
                    document.id,
                    document.amount_total,
                )
                continue
        setattr(document, field, value)
        fields_set.append(field)

    if metadata.language != "unknown" and "language" not in user_edited:
        document.language = DocumentLanguage(metadata.language)
        fields_set.append("language")

    if metadata.topics and "topics" not in user_edited:
        document.topics = metadata.topics
        fields_set.append("topics")

    if metadata.tags and "tags" not in user_edited:
        existing_slugs = {tag.slug for tag in document.tags}
        merged = False
        for slug in metadata.tags:
            if slug not in existing_slugs:
                document.tags.append(await get_or_create_tag(session, slug))
                merged = True
        if merged:
            fields_set.append("tags")

    document.extra = {
        **document.extra,
        "extraction": {
            "prompt_version": outcome.prompt_version,
            "model": outcome.model,
            "confidence": metadata.confidence,
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
            "cost_usd": outcome.cost_usd,
            "escalated": outcome.escalated,
            "input_mode": outcome.input_mode,
            "fields_set": fields_set,
            # Present only when something was withheld, so the ordinary stamp is
            # unchanged and a skip is visible in the document's own record
            # rather than only in a log line nobody reads.
            **({"skipped_fields": skipped_fields} if skipped_fields else {}),
            "reasoning_note": metadata.reasoning_note,
            "addressee_raw": metadata.addressee_raw,
            "signer_raw": metadata.signer_raw,
        },
    }
    return fields_set, skipped_fields


async def revalidate_document(
    session: AsyncSession, document: Document, settings: Settings
) -> list[Finding]:
    """Run deterministic validation and persist ``extra["validation"]``; return
    the findings. Does NOT touch ``review_status`` — the caller applies its own
    status policy (extraction auto-flags via ``derive_review_status``; the edit
    path preserves a user's ``verified`` status — see
    ``documents_service.revalidate_after_edit``).

    Reads whatever the document's current values are, regardless of provenance.
    """
    kind_slug: str | None = None
    if document.kind_id is not None:
        kind = await session.get(Kind, document.kind_id)
        kind_slug = kind.slug if kind is not None else None
    # Resolve the sender name for the generic_sender rule the same way as the
    # kind slug: an explicit session.get, never the lazy relationship (which
    # raises MissingGreenlet on an expired async instance).
    sender_name: str | None = None
    if document.sender_id is not None:
        sender = await session.get(Sender, document.sender_id)
        sender_name = sender.name if sender is not None else None

    findings = validate(
        document,
        kind_slug=kind_slug,
        sender_name=sender_name,
        ocr_floor=settings.extraction_validation_ocr_floor,
        today=datetime.now(UTC).date(),
    )
    document.extra = {
        **document.extra,
        "validation": {
            "prompt_version": PROMPT_VERSION,
            "findings": findings_to_payload(findings),
            "validated_at": datetime.now(UTC).isoformat(),
        },
    }
    return findings


def flag_textless_document(document: Document) -> bool:
    """Seed the ``no_text_extracted`` finding at the OCR stage. Returns whether it did.

    Closes a coverage hole rather than duplicating a rule. ``_apply_validation``
    runs only on ``apply_extraction``'s *success* path, and six branches return
    before it — ``disabled``, ``missing_api_key``, ``already_extracted``,
    ``budget``, ``ExtractionSkipped`` and the generic failure. A textless
    document typically takes the ``ExtractionSkipped("input_unusable")`` branch,
    so the one document that most needs flagging got no validation at all and
    reached ``indexed`` with ``review_status`` untouched.

    Deliberately a floor, not the full rule set: at the OCR stage extraction has
    not run, so amount/date/sender are all None and running ``validate()`` here
    would fire ``empty_extraction`` and friends prematurely. This seeds the one
    finding that is already knowable and lets extraction's own full validation
    supersede it if it gets that far — ``validate()`` re-derives
    ``no_text_extracted`` from the same empty text, so the two agree.

    Skips a document that already carries a ``validation`` payload, so a
    pipeline *resume* re-entering the OCR hook cannot overwrite a richer set of
    findings from an earlier completed extraction with this single one.
    """
    if (document.ocr_text or "").strip():
        return False
    if isinstance(document.extra, dict) and "validation" in document.extra:
        return False
    finding = Finding(
        "no_text_extracted",
        None,
        "warn",
        "no text could be extracted from this document, so it cannot be found by "
        "search — the original is stored and OCR can be re-run",
    )
    document.extra = {
        **document.extra,
        "validation": {
            "prompt_version": PROMPT_VERSION,
            "findings": findings_to_payload([finding]),
            "validated_at": datetime.now(UTC).isoformat(),
        },
    }
    document.review_status = derive_review_status([finding])
    return True


async def _apply_validation(session: AsyncSession, document: Document, settings: Settings) -> None:
    """Run validation and set review_status + extra["validation"] (extraction path).

    Best-effort: the caller (``apply_extraction``) wraps this in a try/except so
    any failure here never propagates and never fails the document.
    """
    findings = await revalidate_document(session, document, settings)
    document.review_status = derive_review_status(findings)


async def apply_extraction(
    session: AsyncSession, document: Document, settings: Settings, *, force: bool = False
) -> None:
    """Extract metadata for one document and persist the result.

    Always commits an audit event (``extraction_completed`` /
    ``extraction_skipped`` / ``extraction_failed``) and never raises for
    extraction-level problems — the document must reach ``indexed`` no
    matter what happens here.

    Gate order (each skip records its reason, and no Anthropic call is made on
    any skip path): ``disabled`` → ``missing_api_key`` → ``already_extracted``
    (idempotency: ``extra["extraction"]`` already stamped with the current
    :data:`PROMPT_VERSION` — a pipeline *resume* must not re-spend on a stage
    that finished, see below) → ``budget`` (checked last so a document that was
    never going to be extracted is not mislabelled a budget skip).

    ``force=True`` bypasses the ``already_extracted`` guard for the deliberate
    re-run paths — the ``extract_document`` task, which backs "re-extract this
    document" (``POST /api/documents/{id}/extract``), the note-edit reprocess,
    the importer, and the backfill CLIs. Those re-runs are the point of the
    call, and their input (an edited note body) or their intent (a corrected
    prompt) is not visible in the stamp. The pipeline path leaves it False:
    ``documents.status`` names the stage a document *entered*, so a worker
    killed after the extract hook committed — but before the loop advanced the
    status — resumes into a re-run of an extraction that already happened, i.e.
    a second billed call for work already paid for. The stamp is the only
    evidence that separates "interrupted" from "finished". Version-scoped like
    ``already_repaired``, so ``library backfill`` (which selects documents whose
    stamp is a *stale* version) keeps working either way.
    """
    if not settings.extraction_enabled:
        await _record_event(session, document, "extraction_skipped", {"reason": "disabled"})
        return
    if settings.anthropic_api_key is None:
        await _record_event(session, document, "extraction_skipped", {"reason": "missing_api_key"})
        return
    extra = document.extra if isinstance(document.extra, dict) else {}
    previous = extra.get("extraction")
    already_extracted = (
        isinstance(previous, dict) and previous.get("prompt_version") == PROMPT_VERSION
    )
    if already_extracted and not force:
        await _record_event(
            session,
            document,
            "extraction_skipped",
            {"reason": "already_extracted", "prompt_version": PROMPT_VERSION},
        )
        return

    spent = await todays_spend_usd(session)
    if spent >= settings.extraction_daily_budget_usd:
        await _record_event(
            session,
            document,
            "extraction_skipped",
            {
                "reason": "budget",
                "spent_usd": spent,
                "budget_usd": settings.extraction_daily_budget_usd,
            },
        )
        return

    # Resolve the email envelope to a known user's name (if any) to hint the
    # model; the document's own recipient still wins (see rung 1 in _apply_outcome).
    recipient_hint: str | None = None
    email_to = document.extra.get("email_to")
    if isinstance(email_to, list) and email_to:
        recipient_hint = await resolve_recipient_hint(session, email_to)

    try:
        async with AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value()) as client:
            outcome = await extract(
                document,
                document.ocr_text or "",
                client=client,
                settings=settings,
                recipient_hint=recipient_hint,
            )
    except ExtractionSkipped as exc:
        await _record_event(
            session, document, "extraction_skipped", {"reason": exc.reason, "detail": str(exc)}
        )
        return
    except Exception as exc:
        logger.exception("extraction failed for document %s", document.id)
        # Drop any partial state, then un-expire the document (rollback expires
        # loaded attributes; a later lazy access would need sync IO and fail).
        await session.rollback()
        await session.refresh(document)
        await _record_event(
            session,
            document,
            "extraction_failed",
            {"error": str(exc), "prompt_version": PROMPT_VERSION},
        )
        return

    fields_set, skipped_fields = await _apply_outcome(session, document, outcome)
    try:
        await _apply_validation(session, document, settings)
    except Exception:  # validation is best-effort; never fail the document
        logger.exception("validation failed for document %s", document.id)

    # Label the document against the controlled facet vocabulary. Best-effort
    # and self-contained, exactly like the validation step above: a missing
    # API key or an unparseable response leaves the document unlabelled and
    # visible in the review queue, never fails the ingest.
    #
    # The SAVEPOINT is what makes "best-effort" true for a *database* failure
    # as well as a Python one. A statement-level Postgres error aborts the
    # whole transaction, so without it the next statement — ``_record_event``,
    # which is also what commits — would raise InFailedSqlTransaction and lose
    # the extracted metadata. Rolling back to the savepoint discards only the
    # labelling's own writes and leaves the outer transaction usable.
    try:
        async with session.begin_nested():
            await label_and_apply(session, settings, document.id)
    except Exception:  # labelling is best-effort; never fail the document
        logger.exception("facet labelling failed for document %s", document.id)

    await _record_event(
        session,
        document,
        "extraction_completed",
        {
            "model": outcome.model,
            "prompt_version": outcome.prompt_version,
            "confidence": outcome.metadata.confidence,
            "input_tokens": outcome.input_tokens,
            "output_tokens": outcome.output_tokens,
            "cost_usd": outcome.cost_usd,
            "escalated": outcome.escalated,
            "input_mode": outcome.input_mode,
            "fields_set": fields_set,
            # Present only when something was withheld, so an ordinary
            # extraction's event shape is unchanged. This is the surface the
            # document timeline renders, and the whole premise of the
            # allocation guard is that nothing is excluded silently — a skip
            # recorded only in `extra` is exactly that.
            **({"skipped_fields": skipped_fields} if skipped_fields else {}),
        },
    )
    logger.info(
        "extraction completed for document %s: model=%s confidence=%s cost=$%.4f fields=%s",
        document.id,
        outcome.model,
        outcome.metadata.confidence,
        outcome.cost_usd,
        fields_set,
    )
