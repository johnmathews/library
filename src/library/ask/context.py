"""The archive-context block for the Ask system prompt.

Ask's tools take exact slugs (``kind``, ``projects``, ``matters``, ``tags``)
and substrings of names (``sender_contains``, ``recipient_contains``). Without
being told the archive's vocabulary the model has to guess them, and without
being told who the user is it cannot tell "my energy bill" from a bill addressed
to someone else in the household. This module reads that context once per turn
and renders it as a block appended to the system prompt.

Rendering is **deterministic** — every list is sorted and no volatile value
(counts, timestamps) is included — because the block sits inside the prompt
prefix that Anthropic caches. A block that reordered itself between requests
would invalidate that cache on every turn while looking identical to a reader.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from library.models import Document, Kind, Matter, Project, Recipient, Sender, Tag, User
from library.schemas import resolve_preferences

# Frequent-sender cap. Membership of the top-N changes rarely; the rendered
# order is alphabetical, so a shuffle in document counts within the set does
# not change the block.
DEFAULT_MAX_SENDERS: int = 40
# Tag cap, alphabetical by slug; a personal archive is well below this.
DEFAULT_MAX_TAGS: int = 100
# Project/matter caps, alphabetical by slug. Bounded like tags because Ask's
# own write tool can create both (update_document_metadata → get-or-create),
# so nothing else guarantees the taxonomy stays small.
DEFAULT_MAX_PROJECTS: int = 50
DEFAULT_MAX_MATTERS: int = 50


@dataclass(frozen=True, slots=True)
class ArchiveContext:
    """What the model is told about the user and the archive's vocabulary."""

    user_name: str
    # Recipient names linked to this user (``Recipient.user_id``): documents
    # addressed to any of these are the user's own.
    own_recipients: tuple[str, ...]
    kinds: tuple[tuple[str, str], ...]  # (slug, name)
    tags: tuple[tuple[str, str], ...]  # (slug, name)
    projects: tuple[tuple[str, str, str | None], ...]  # (slug, name, description); active only
    matters: tuple[tuple[str, str, str | None], ...]  # (slug, name, hint); active only
    senders: tuple[str, ...]  # the most frequent senders, by name
    # The user's own "About you" notes (Settings → Ask); "" when unset.
    profile: str = ""


async def load_archive_context(
    session: AsyncSession,
    user: User,
    *,
    max_senders: int = DEFAULT_MAX_SENDERS,
    max_tags: int = DEFAULT_MAX_TAGS,
    max_projects: int = DEFAULT_MAX_PROJECTS,
    max_matters: int = DEFAULT_MAX_MATTERS,
) -> ArchiveContext:
    """Read the context for ``user`` from the database.

    Archived projects and matters are omitted: they are not vocabulary the
    user is still filing under; the rest are capped alphabetically by slug.
    Senders are the ``max_senders`` with the most non-deleted documents.
    """
    user_name = (user.display_name or "").strip() or user.username
    profile = resolve_preferences(user.preferences).ask_profile

    recipients = (
        await session.execute(select(Recipient.name).where(Recipient.user_id == user.id))
    ).scalars()
    kinds = (await session.execute(select(Kind.slug, Kind.name))).all()
    tags = (
        await session.execute(select(Tag.slug, Tag.name).order_by(Tag.slug).limit(max_tags))
    ).all()
    projects = (
        await session.execute(
            select(Project.slug, Project.name, Project.description)
            .where(Project.archived_at.is_(None))
            .order_by(Project.slug)
            .limit(max_projects)
        )
    ).all()
    matters = (
        await session.execute(
            select(Matter.slug, Matter.name, Matter.hint)
            .where(Matter.archived_at.is_(None))
            .order_by(Matter.slug)
            .limit(max_matters)
        )
    ).all()
    sender_rows = (
        await session.execute(
            select(Sender.name, func.count(Document.id).label("n"))
            .join(Document, (Document.sender_id == Sender.id) & Document.deleted_at.is_(None))
            .group_by(Sender.id)
            .order_by(func.count(Document.id).desc(), Sender.name)
            .limit(max_senders)
        )
    ).all()

    return ArchiveContext(
        user_name=user_name,
        own_recipients=tuple(sorted(recipients)),
        kinds=tuple(sorted((slug, name) for slug, name in kinds)),
        tags=tuple(sorted((slug, name) for slug, name in tags)),
        projects=tuple(sorted((slug, name, desc) for slug, name, desc in projects)),
        matters=tuple(sorted((slug, name, hint) for slug, name, hint in matters)),
        senders=tuple(sorted(name for name, _ in sender_rows)),
        profile=profile,
    )


def _quoted(names: tuple[str, ...]) -> str:
    return ", ".join(f'"{name}"' for name in sorted(names))


def _described(rows: tuple[tuple[str, str, str | None], ...]) -> str:
    parts: list[str] = []
    for slug, name, description in sorted(rows):
        detail = f" — {description.strip()}" if description and description.strip() else ""
        parts.append(f"{slug}: {name}{detail}")
    return "; ".join(parts)


def render_archive_context(context: ArchiveContext) -> str:
    """Render ``context`` as the prompt block. Sorted everywhere — see module doc."""
    lines: list[str] = ["Archive context (authoritative; use these exact slugs in tool calls):"]

    identity = f'- The user is "{context.user_name}".'
    if context.own_recipients:
        identity += (
            f" Documents addressed to {_quoted(context.own_recipients)} are the user's own;"
            ' "my"/"me"/"I" in a question refers to this user.'
        )
    else:
        identity += ' "my"/"me"/"I" in a question refers to this user.'
    lines.append(identity)

    profile = context.profile.strip()
    if profile:
        # One bullet: continuation lines are indented so the note reads as a
        # block, and the model is told how much weight it carries.
        body = "\n  ".join(line.rstrip() for line in profile.splitlines())
        lines.append(
            "- About the user (their own notes; authoritative personal context — "
            "trust these over inference from documents):\n  " + body
        )

    lines.append(
        "- Kinds (slug: name): "
        + "; ".join(f"{slug}: {name}" for slug, name in sorted(context.kinds))
    )
    if context.matters:
        lines.append(f"- Matters (slug: name — hint): {_described(context.matters)}")
    if context.projects:
        lines.append(f"- Projects (slug: name — description): {_described(context.projects)}")
    if context.tags:
        tag_parts = [
            slug if name == slug else f"{slug}: {name}" for slug, name in sorted(context.tags)
        ]
        lines.append("- Tags (slug): " + "; ".join(tag_parts))
    if context.senders:
        lines.append("- Frequent senders: " + "; ".join(sorted(context.senders)))
    return "\n".join(lines)
