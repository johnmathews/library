"""Tests for the Ask archive-context block.

The block tells the answering model who the user is and what vocabulary the
archive uses (kinds, tags, projects, matters, facets, frequent senders) so it can name
the right slugs in tool calls and treat "my" as this user. Two halves:
``load_archive_context`` reads it from the database, ``render_archive_context``
turns it into a deterministic prompt block.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from library.ask.context import (
    ArchiveContext,
    load_archive_context,
    render_archive_context,
)
from library.auth.passwords import hash_password
from library.models import Matter, Project, Recipient, User
from tests.test_documents_api import _seed_document

# --- render ------------------------------------------------------------------


def _context(**overrides: object) -> ArchiveContext:
    base: dict[str, object] = {
        "user_name": "Ada Example",
        "own_recipients": ("Ada Example", "A. Example"),
        "kinds": (("invoice", "Invoice"), ("utility-bill", "Utility bill")),
        "tags": (("tax-2025", "tax-2025"),),
        "projects": (("kitchen-renovation", "Kitchen renovation", "Rebuilding the kitchen"),),
        "matters": (("car-insurance", "Car insurance", "Policies for the family car"),),
        "facets": (),
        "senders": ("Example Energy", "Example Telecom"),
        "profile": "",
    }
    base.update(overrides)
    return ArchiveContext(**base)  # type: ignore[arg-type]


def test_render_names_the_user_and_their_recipients() -> None:
    text = render_archive_context(_context())
    assert 'The user is "Ada Example"' in text
    assert '"A. Example", "Ada Example"' in text
    # The point of naming them: "my"/"me" resolves to this user.
    assert '"my"' in text


def test_render_lists_taxonomy_with_exact_slugs_and_hints() -> None:
    text = render_archive_context(_context())
    assert "utility-bill: Utility bill" in text
    assert "car-insurance: Car insurance — Policies for the family car" in text
    assert "kitchen-renovation: Kitchen renovation — Rebuilding the kitchen" in text
    assert "tax-2025" in text
    assert "Example Energy; Example Telecom" in text


def test_render_omits_empty_sections() -> None:
    text = render_archive_context(_context(matters=(), projects=(), tags=(), senders=()))
    assert "Matters" not in text
    assert "Projects" not in text
    assert "Tags" not in text
    assert "senders" not in text
    # Identity and kinds are always present.
    assert "Ada Example" in text
    assert "invoice" in text


def test_render_is_byte_stable_regardless_of_input_order() -> None:
    """The block sits inside the cached prompt prefix; any reordering between
    requests would silently invalidate the cache."""
    forward = _context()
    reversed_ = _context(
        own_recipients=("A. Example", "Ada Example"),
        kinds=(("utility-bill", "Utility bill"), ("invoice", "Invoice")),
        senders=("Example Telecom", "Example Energy"),
    )
    assert render_archive_context(forward) == render_archive_context(reversed_)


def test_render_includes_the_profile_as_authoritative_notes() -> None:
    text = render_archive_context(
        _context(profile="We live at Example Street 1.\nThe Volvo is the family car.")
    )
    assert "About the user" in text
    assert "authoritative" in text
    assert "We live at Example Street 1." in text
    # Continuation lines are indented so the block stays one bullet.
    assert "\n  The Volvo is the family car." in text


def test_render_omits_the_profile_when_blank() -> None:
    assert "About the user" not in render_archive_context(_context(profile="  \n "))


# --- load --------------------------------------------------------------------

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _open_session(database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session
    finally:
        await engine.dispose()


async def _user(
    session: AsyncSession,
    username: str,
    display_name: str = "",
    preferences: dict[str, object] | None = None,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password("pw"),
        display_name=display_name,
        preferences=preferences or {},
    )
    session.add(user)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_load_reads_identity_and_taxonomy(api_database_url: str) -> None:
    await _seed_document(
        api_database_url,
        "ctx-doc-1",
        kind_slug="utility-bill",
        sender_name="Example Energy",
        recipient_name="Ada Example",
        tag_slugs=["tax-2025"],
        project_slugs=["kitchen-renovation"],
        matter_slugs=["car-insurance"],
    )
    await _seed_document(api_database_url, "ctx-doc-2", sender_name="Example Telecom")

    async with _open_session(api_database_url) as session:
        user = await _user(session, "ada", display_name="Ada Example")
        recipient = (
            await session.execute(select(Recipient).where(Recipient.name == "Ada Example"))
        ).scalar_one()
        recipient.user_id = user.id
        matter = (
            await session.execute(select(Matter).where(Matter.slug == "car-insurance"))
        ).scalar_one()
        matter.hint = "Policies for the family car"
        project = (
            await session.execute(select(Project).where(Project.slug == "kitchen-renovation"))
        ).scalar_one()
        project.description = "Rebuilding the kitchen"
        await session.commit()

        context = await load_archive_context(session, user)

    assert context.user_name == "Ada Example"
    assert context.own_recipients == ("Ada Example",)
    assert ("utility-bill", "Utility bill") in context.kinds
    assert context.tags == (("tax-2025", "tax-2025"),)
    assert context.projects == (
        ("kitchen-renovation", "kitchen-renovation", "Rebuilding the kitchen"),
    )
    assert context.matters == (("car-insurance", "car-insurance", "Policies for the family car"),)
    assert context.senders == ("Example Energy", "Example Telecom")


@pytest.mark.asyncio
async def test_load_falls_back_to_username_without_display_name(api_database_url: str) -> None:
    async with _open_session(api_database_url) as session:
        user = await _user(session, "plain-user")
        context = await load_archive_context(session, user)
    assert context.user_name == "plain-user"
    assert context.own_recipients == ()
    assert context.profile == ""


@pytest.mark.asyncio
async def test_load_reads_the_ask_profile_preference(api_database_url: str) -> None:
    async with _open_session(api_database_url) as session:
        user = await _user(
            session, "profiled", preferences={"ask_profile": "The Volvo is the family car."}
        )
        context = await load_archive_context(session, user)
    assert context.profile == "The Volvo is the family car."


@pytest.mark.asyncio
async def test_load_treats_a_garbage_profile_as_absent(api_database_url: str) -> None:
    async with _open_session(api_database_url) as session:
        user = await _user(session, "garbled", preferences={"ask_profile": ["not", "text"]})
        context = await load_archive_context(session, user)
    assert context.profile == ""


@pytest.mark.asyncio
async def test_load_excludes_archived_projects_and_matters(api_database_url: str) -> None:
    await _seed_document(
        api_database_url,
        "ctx-archived",
        project_slugs=["old-project"],
        matter_slugs=["old-matter"],
    )
    async with _open_session(api_database_url) as session:
        for model, slug in ((Project, "old-project"), (Matter, "old-matter")):
            row = (await session.execute(select(model).where(model.slug == slug))).scalar_one()
            row.archived_at = func.now()
        await session.commit()
        user = await _user(session, "archiver")
        context = await load_archive_context(session, user)
    assert context.projects == ()
    assert context.matters == ()


@pytest.mark.asyncio
async def test_load_caps_projects_and_matters_alphabetically(api_database_url: str) -> None:
    """Ask's own write tool can create projects and matters, so the block must
    stay bounded even if the taxonomy grows without limit."""
    await _seed_document(
        api_database_url,
        "ctx-cap",
        project_slugs=["p-charlie", "p-alpha", "p-bravo"],
        matter_slugs=["m-charlie", "m-alpha", "m-bravo"],
    )
    async with _open_session(api_database_url) as session:
        user = await _user(session, "capper")
        context = await load_archive_context(session, user, max_projects=2, max_matters=2)
    assert [slug for slug, _, _ in context.projects] == ["p-alpha", "p-bravo"]
    assert [slug for slug, _, _ in context.matters] == ["m-alpha", "m-bravo"]


@pytest.mark.asyncio
async def test_load_keeps_only_the_most_frequent_senders(api_database_url: str) -> None:
    for i in range(3):
        await _seed_document(api_database_url, f"ctx-busy-{i}", sender_name="Busy Sender")
    for i in range(2):
        await _seed_document(api_database_url, f"ctx-mid-{i}", sender_name="Mid Sender")
    await _seed_document(api_database_url, "ctx-rare", sender_name="Rare Sender")

    async with _open_session(api_database_url) as session:
        user = await _user(session, "counter")
        context = await load_archive_context(session, user, max_senders=2)
    assert context.senders == ("Busy Sender", "Mid Sender")


# --- facets (#136) -----------------------------------------------------------


def test_render_lists_the_facet_vocabulary_with_its_values() -> None:
    """A facet filter the model cannot spell is a filter it cannot use.

    Unlike kinds or tags, a facet needs BOTH halves — the key and the allowed
    value keys — because the filter is a `{key: value}` pair. Rendering the keys
    alone would leave the model guessing at values, and a guessed value matches
    no document while looking like a real answer of nothing.
    """
    text = render_archive_context(
        _context(facets=(("category", (("software", "Software"), ("supplies", "Supplies"))),))
    )
    assert "category" in text
    assert "software" in text
    assert "supplies" in text


def test_render_omits_the_facets_line_when_there_is_no_vocabulary() -> None:
    """An archive with no facets must not carry an empty heading into the
    cached prompt prefix, the way every other optional line is omitted."""
    assert "Facets" not in render_archive_context(_context(facets=()))


@pytest.mark.asyncio
async def test_load_reads_the_facet_vocabulary(api_database_url: str) -> None:
    from library.facets.vocabulary import create_facet, create_value

    engine = create_async_engine(api_database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await create_facet(session, "ctx_category", "Ctx Category", 0)
            await create_value(session, "ctx_category", "ctx_software", "Ctx Software")
            await create_value(session, "ctx_category", "ctx_supplies", "Ctx Supplies")
            await session.commit()

            user = User(
                username="facet-context-user",
                password_hash=hash_password("x"),
                display_name="Facet Context User",
            )
            session.add(user)
            await session.commit()

            context = await load_archive_context(session, user)
    finally:
        await engine.dispose()

    by_key = dict(context.facets)
    assert "ctx_category" in by_key
    assert [value_key for value_key, _ in by_key["ctx_category"]] == [
        "ctx_software",
        "ctx_supplies",
    ]


@pytest.mark.asyncio
async def test_loading_twice_renders_byte_identical_blocks(api_database_url: str) -> None:
    """The property the cached prompt prefix actually depends on.

    The other lists are sorted at render time because their queries carry no
    ORDER BY; the facets line is ordered at the database instead, so the
    render-level "sorted regardless of input order" test cannot cover it. What
    matters either way is that two loads of the same archive produce the same
    bytes — a block that reordered itself between turns would invalidate the
    cache on every request while looking identical to a reader.
    """
    engine = create_async_engine(api_database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user = User(
                username="cache-stability-user",
                password_hash=hash_password("x"),
                display_name="Cache Stability User",
            )
            session.add(user)
            await session.commit()

            first = render_archive_context(await load_archive_context(session, user))
            second = render_archive_context(await load_archive_context(session, user))
    finally:
        await engine.dispose()

    assert first == second
