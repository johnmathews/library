# Document Comments, Detail Island, and Ask Enter-to-send Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/ask` Enter-to-send, a floating detail-view island, and user-authored dated document comments that `/ask` can retrieve (plus a `get_document` read tool).

**Architecture:** Comments are a new `document_comments` table; each comment is embedded as one extra `document_chunks` row (tagged with `comment_id`) via the existing re-embed job, so the existing hybrid semantic search finds a document through its comments. A new `get_document` agent tool lets the ask engine read a located document's fields/comments/text. The two UI features (island, Enter-to-send) are self-contained frontend changes.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + pgvector (backend, Python 3.13, `uv`, pytest); Vue 3 + TypeScript + Pinia + Tailwind v4 + Chart.js (frontend, Vitest + Playwright).

## Global Constraints

- Python 3.13; dependency management via `uv`; type annotations on all signatures.
- Backend tests: pytest. Run the FULL backend suite + `ruff format --check` + `ruff check` over the whole repo (migrations included) before merge.
- Frontend: `vue-tsc --build` type-check (stricter than `--noEmit`; `noUncheckedIndexedAccess` is on — guard array indexing), `eslint`, `vite build`, Vitest. e2e (Playwright) runs on mobile/tablet-below-`lg`; gate edit-only/floating UI with `v-if` not `v-show`.
- New Alembic revision: `0022`, `down_revision = "0021"`. Format new migration files with `ruff format` before committing (CI runs ruff over migrations/).
- Embedding dim is bge-m3 1024 (`EMBEDDING_DIM` in `models.py`); comment chunks reuse the same embedder.
- API list endpoints cap `limit` at 100.
- Commit after each task's tests pass.

---

## File Structure

**Backend**
- Create `src/library/api/comments.py` — comment CRUD router.
- Modify `src/library/models.py` — `DocumentComment` model, `Document.comments` rel, `DocumentChunk.comment_id` column.
- Create `migrations/versions/0022_document_comments.py` — table + `comment_id` column.
- Modify `src/library/jobs.py` — `run_embed` emits comment chunks.
- Modify `src/library/ask/engine.py` — `get_document` tool + system prompt.
- Modify `src/library/api/documents.py` — serialize comments into detail payload.
- Modify `src/library/app.py` — mount comments router.

**Frontend**
- Modify `frontend/src/views/AskView.vue` — Enter-to-send handler.
- Create `frontend/src/composables/useMetadataEditMode.ts` — shared metadata edit-mode flag.
- Modify `frontend/src/components/DocumentMetadataEditor.vue` — consume the composable.
- Modify `frontend/src/views/DocumentDetailView.vue` — island + Comments card slot.
- Create `frontend/src/components/DocumentComments.vue` — comments card.
- Modify `frontend/src/composables/useDocumentLayout.ts` — add `comments` card id.
- Modify `frontend/src/api/documents.ts` — comment API client + types.

---

## Task 1: `/ask` Enter-to-send

**Files:**
- Modify: `frontend/src/views/AskView.vue:264-270` (handler) and `:555` (hint text)
- Test: `frontend/src/views/__tests__/AskView.spec.ts` (extend existing; if absent, create colocated matching a sibling view spec)

**Interfaces:**
- Produces: `onComposerKeydown(event: KeyboardEvent): void` — Enter (no Shift) submits; Shift+Enter and Ctrl+J newline; Cmd/Ctrl+Enter submits; IME-composing Enter ignored.

- [ ] **Step 1: Write failing tests**

Add to the AskView spec (mirror the existing mount/mocks in that file; `onSubmit` posts to the ask API — reuse the file's existing mock). Test at the component level by dispatching keydown on the textarea (`[data-testid]` or `wrapper.find('textarea')`) and asserting the submit spy:

```ts
it('sends on plain Enter, not on Shift+Enter / Ctrl+J / while composing', async () => {
  const wrapper = mountAskView() // use this spec's existing mount helper
  const ta = wrapper.find('textarea')
  await ta.setValue('hello')

  await ta.trigger('keydown', { key: 'Enter' })
  expect(submitSpy).toHaveBeenCalledTimes(1) // plain Enter sends

  await ta.trigger('keydown', { key: 'Enter', shiftKey: true })
  await ta.trigger('keydown', { key: 'j', ctrlKey: true })
  expect(submitSpy).toHaveBeenCalledTimes(1) // neither sends

  await ta.trigger('keydown', { key: 'Enter', isComposing: true })
  expect(submitSpy).toHaveBeenCalledTimes(1) // IME compose does not send

  await ta.trigger('keydown', { key: 'Enter', metaKey: true })
  expect(submitSpy).toHaveBeenCalledTimes(2) // cmd/ctrl+enter still sends
})
```

`submitSpy` = spy on the network/submit path this spec already stubs. If the spec asserts via the mocked ask API call, count those instead.

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/views/__tests__/AskView.spec.ts`
Expected: FAIL (plain Enter currently does not submit).

- [ ] **Step 3: Implement the handler**

Replace `onComposerKeydown` (`AskView.vue:264-270`) with:

```ts
function onComposerKeydown(event: KeyboardEvent): void {
  // IME composition: Enter commits the candidate, never sends.
  if (event.isComposing || event.keyCode === 229) return

  // Ctrl+J inserts a newline at the caret (not a default insertion key).
  if (event.key === 'j' && event.ctrlKey && !event.metaKey) {
    event.preventDefault()
    const el = event.target as HTMLTextAreaElement
    const start = el.selectionStart ?? question.value.length
    const end = el.selectionEnd ?? start
    question.value = question.value.slice(0, start) + '\n' + question.value.slice(end)
    void nextTick(() => {
      el.selectionStart = el.selectionEnd = start + 1
    })
    return
  }

  if (event.key === 'Enter') {
    // Shift+Enter: let the browser insert a newline.
    if (event.shiftKey) return
    // Plain Enter, or Cmd/Ctrl+Enter: send.
    event.preventDefault()
    void onSubmit()
  }
}
```

Ensure `nextTick` is imported from `vue` in this file (add to the existing import if missing).

- [ ] **Step 4: Update the hint text**

Change the hint at `AskView.vue:555` from the "⌘/Ctrl + Enter to send" copy to: `Enter to send · Shift+Enter for new line`.

- [ ] **Step 5: Run tests + type-check + lint**

Run: `cd frontend && npx vitest run src/views/__tests__/AskView.spec.ts && npm run type-check && npx eslint src/views/AskView.vue src/views/__tests__/AskView.spec.ts`
Expected: PASS, exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/AskView.vue frontend/src/views/__tests__/AskView.spec.ts
git commit -m "feat(ask): Enter sends, Shift+Enter/Ctrl+J newline"
```

---

## Task 2: `DocumentComment` model + migration `0022`

**Files:**
- Modify: `src/library/models.py` (add `DocumentComment`, `Document.comments`, `DocumentChunk.comment_id`)
- Create: `migrations/versions/0022_document_comments.py`
- Test: `tests/test_models_comments.py` (new) and an alembic upgrade/downgrade test (mirror any existing migration test; else assert `alembic upgrade head` then table exists)

**Interfaces:**
- Produces: `DocumentComment(id, document_id, author_id, body, created_at, updated_at)`; `Document.comments -> list[DocumentComment]`; `DocumentChunk.comment_id: int | None`.

- [ ] **Step 1: Write the model**

In `src/library/models.py`, add after the `NoteVersion` class (near `:505`), matching the file's existing style (Mapped columns, `mapped_column`):

```python
class DocumentComment(Base):
    """User-authored, dated free-text attached to an existing document.

    Distinct from a note (a source='note' Document): a comment annotates
    another document and is embedded as an extra chunk so /ask can find the
    document through it. `created_at` is the recorded date shown in the UI.
    """

    __tablename__ = "document_comments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    author_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="comments")
```

Add to `Document` (near the `chunks`/`events` relationships, ~`:383-396`):

```python
    comments: Mapped[list["DocumentComment"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentComment.created_at",
        lazy="raise",
    )
```

Add to `DocumentChunk` (near its columns, ~`:410-443`):

```python
    comment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("document_comments.id", ondelete="CASCADE"), nullable=True, index=True
    )
```

Confirm `datetime`, `func`, `ForeignKey`, `Text`, `BigInteger`, `DateTime` are already imported at the top of `models.py` (they are used by neighbours); add any missing import.

- [ ] **Step 2: Write the migration**

Create `migrations/versions/0022_document_comments.py` (mirror `0021_series_suggestions.py` structure):

```python
"""document comments + chunk provenance

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_comments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_document_comments_document_id", "document_comments", ["document_id"])
    op.add_column(
        "document_chunks",
        sa.Column(
            "comment_id",
            sa.BigInteger(),
            sa.ForeignKey("document_comments.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_document_chunks_comment_id", "document_chunks", ["comment_id"])


def downgrade() -> None:
    op.drop_index("ix_document_chunks_comment_id", "document_chunks")
    op.drop_column("document_chunks", "comment_id")
    op.drop_index("ix_document_comments_document_id", "document_comments")
    op.drop_table("document_comments")
```

- [ ] **Step 3: Format the migration**

Run: `uv run ruff format migrations/versions/0022_document_comments.py && uv run ruff check migrations/versions/0022_document_comments.py`
Expected: reformatted, no lint errors.

- [ ] **Step 4: Write + run a model test (fails → passes)**

`tests/test_models_comments.py` (use the repo's existing DB test fixture/session — mirror another `tests/test_*` that creates a Document):

```python
def test_comment_attaches_and_cascades(db_session, make_document):
    doc = make_document()
    c = DocumentComment(document_id=doc.id, body="this is my current house")
    db_session.add(c)
    db_session.commit()
    assert c.created_at is not None
    db_session.delete(doc)
    db_session.commit()
    assert db_session.get(DocumentComment, c.id) is None  # cascade
```

Run: `uv run pytest tests/test_models_comments.py -v` (after `alembic upgrade head` in the test DB setup, which the fixture already does). Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/library/models.py migrations/versions/0022_document_comments.py tests/test_models_comments.py
git commit -m "feat(comments): document_comments model + migration 0022"
```

---

## Task 3: Comment CRUD API + detail serialization

**Files:**
- Create: `src/library/api/comments.py`
- Modify: `src/library/app.py:196-205` (mount router)
- Modify: `src/library/api/documents.py` (serialize `comments` into `DocumentDetailOut`, near the `events` serialization `:605-607`)
- Test: `tests/test_api_comments.py`

**Interfaces:**
- Produces: routes `GET/POST /api/documents/{id}/comments`, `PATCH/DELETE /api/documents/{id}/comments/{cid}`; `CommentOut{id, body, author_id, created_at}`; each mutation writes an `IngestionEvent` and calls `embed_document.defer_async(document_id)` (Task 4 consumes the re-embed).
- Consumes: `DocumentComment` (Task 2).

- [ ] **Step 1: Write failing API tests**

`tests/test_api_comments.py` — mirror `tests/` patterns for authed client + a seeded document (reuse existing fixtures; the notes API tests are the closest template):

```python
def test_comment_crud_and_events(client, seed_document):
    doc_id = seed_document.id
    # create
    r = client.post(f"/api/documents/{doc_id}/comments", json={"body": "this is my current house"})
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["body"] == "this is my current house"
    # list newest-first
    r = client.get(f"/api/documents/{doc_id}/comments")
    assert [c["id"] for c in r.json()] == [cid]
    # edit
    r = client.patch(f"/api/documents/{doc_id}/comments/{cid}", json={"body": "current house (edited)"})
    assert r.json()["body"] == "current house (edited)"
    # detail payload includes comments
    r = client.get(f"/api/documents/{doc_id}")
    assert any(c["id"] == cid for c in r.json()["comments"])
    # delete
    assert client.delete(f"/api/documents/{doc_id}/comments/{cid}").status_code == 204
    assert client.get(f"/api/documents/{doc_id}/comments").json() == []
```

Add an assertion that a re-embed is deferred: patch/spy `embed_document.defer_async` and assert it was called on create/edit/delete (mirror how notes tests assert `_reprocess_note`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api_comments.py -v`
Expected: FAIL (router not mounted).

- [ ] **Step 3: Implement the router**

Create `src/library/api/comments.py` mirroring `src/library/api/notes.py` (auth dep, `_get_document_or_404`, session dep, `IngestionEvent` writes). Key shape:

```python
router = APIRouter(prefix="/api/documents/{document_id}/comments", tags=["comments"])


class CommentIn(BaseModel):
    body: str = Field(min_length=1)


class CommentOut(BaseModel):
    id: int
    document_id: int
    author_id: int | None
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=list[CommentOut])
def list_comments(document_id: int, session: Session = Depends(get_session), user=Depends(current_user)):
    _get_document_or_404(session, document_id)
    rows = session.execute(
        select(DocumentComment).where(DocumentComment.document_id == document_id)
        .order_by(DocumentComment.created_at.desc())
    ).scalars().all()
    return rows


@router.post("", response_model=CommentOut, status_code=201)
def create_comment(document_id, payload: CommentIn, session=..., user=...):
    _get_document_or_404(session, document_id)
    c = DocumentComment(document_id=document_id, author_id=user.id, body=payload.body.strip())
    session.add(c)
    session.add(IngestionEvent(document_id=document_id, event="comment_added", detail={"comment_id": None}))
    session.commit()
    session.refresh(c)
    embed_document.defer_async(document_id=document_id)
    return c
```

`PATCH` updates `body` (+ `comment_edited` event + re-embed), `DELETE` removes and writes `comment_deleted` + re-embed. Use the same auth/ownership conventions as `notes.py`. Import `embed_document` from `src/library/jobs.py`.

- [ ] **Step 4: Mount the router**

In `src/library/app.py` after `:197` (`notes.router`):

```python
    api_router.include_router(comments.router)
```

Add `from library.api import comments` to the imports alongside the other `api` imports.

- [ ] **Step 5: Serialize comments into the detail payload**

In `src/library/api/documents.py`, add a `comments: list[CommentOut]` field to the detail response model and populate it where `events` is populated (`~:605-607`). Load via `select(DocumentComment)... order_by(created_at.desc())` (the relationship is `lazy="raise"`, so query explicitly or `selectinload`). Reuse `CommentOut` from `api/comments.py` (import it) to avoid duplication.

- [ ] **Step 6: Run tests + lint**

Run: `uv run pytest tests/test_api_comments.py -v && uv run ruff format --check src/library/api/comments.py && uv run ruff check src/library/api/comments.py src/library/app.py src/library/api/documents.py`
Expected: PASS, clean.

- [ ] **Step 7: Commit**

```bash
git add src/library/api/comments.py src/library/app.py src/library/api/documents.py tests/test_api_comments.py
git commit -m "feat(comments): CRUD API + detail serialization + history events"
```

---

## Task 4: Embed comment chunks in `run_embed`

**Files:**
- Modify: `src/library/jobs.py:193-266` (`run_embed`)
- Test: `tests/test_embed_comments.py`

**Interfaces:**
- Consumes: `DocumentComment` (Task 2), `DocumentChunk.comment_id` (Task 2).
- Produces: after `run_embed`, `document_chunks` contains content chunks (`comment_id IS NULL`) plus one chunk per comment (`comment_id` set), text framed `"User comment (YYYY-MM-DD): {body}"`.

- [ ] **Step 1: Write failing test**

`tests/test_embed_comments.py` (use the embed test fixtures; stub `embed_texts` to return deterministic vectors as existing embed tests do):

```python
def test_run_embed_emits_one_chunk_per_comment(db_session, make_document_with_text, fake_embedder):
    doc = make_document_with_text("area is 120 sqm")
    db_session.add(DocumentComment(document_id=doc.id, body="this is my current house"))
    db_session.commit()

    run_embed(db_session, doc.id)  # match the real signature

    chunks = db_session.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
    ).scalars().all()
    comment_chunks = [c for c in chunks if c.comment_id is not None]
    assert len(comment_chunks) == 1
    assert "this is my current house" in comment_chunks[0].text
    assert any(c.comment_id is None for c in chunks)  # content chunks still present
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_embed_comments.py -v`
Expected: FAIL (no comment chunks emitted).

- [ ] **Step 3: Implement**

In `run_embed` (`jobs.py:193-266`), after the content `chunk_records` are built (around `:234`) and before the `embed_texts` call (`:242`), append comment records. Load comments explicitly (relationship is `lazy="raise"`):

```python
    comments = session.execute(
        select(DocumentComment).where(DocumentComment.document_id == document.id)
        .order_by(DocumentComment.created_at)
    ).scalars().all()
    comment_records: list[tuple[str, int]] = [
        (f"User comment ({c.created_at.date().isoformat()}): {c.body}", c.id) for c in comments
    ]
```

Extend the embed call to cover both sets, and when inserting `DocumentChunk` rows set `comment_id` for the comment ones (`None` for content). Keep `chunk_index` monotonic across both. The existing delete-then-insert stays (idempotent). Concretely: build `texts = [t for t, _ in chunk_records] + [t for t, _ in comment_records]`, embed all, then insert content chunks with `page_number` + `comment_id=None`, then comment chunks with `page_number=None` + `comment_id=<id>`.

Guard `settings.embedding_enabled` exactly as the existing code does.

- [ ] **Step 4: Run test**

Run: `uv run pytest tests/test_embed_comments.py -v`
Expected: PASS.

- [ ] **Step 5: Add a retrieval test**

In the semantic-search test module (mirror existing `tests/test_search*.py`; use `graded_vector`-style deterministic embeddings per the repo's flaky-vector convention), assert a document with a comment "this is my current house" is returned for query "my current house" when the fake embedder maps those texts to near vectors. If the search tests rely on the real embedder, place this as an integration test guarded like the others.

- [ ] **Step 6: Commit**

```bash
git add src/library/jobs.py tests/test_embed_comments.py tests/test_search*.py
git commit -m "feat(comments): embed one chunk per comment so /ask can find the document"
```

---

## Task 5: `get_document` ask tool + system prompt

**Files:**
- Modify: `src/library/ask/engine.py` (`TOOLS` `:94-223`, dispatch, system prompt `:40-76`, add setting)
- Modify: `src/library/config.py` (or wherever `Settings` lives) — `ask_get_document_max_chars: int = 8000`
- Test: `tests/test_ask_get_document.py`

**Interfaces:**
- Consumes: `DocumentComment`, document text (`DocumentPage.markdown` / `ocr_text`).
- Produces: tool `get_document(document_id: int)` returning `{title, sender, recipient, kind, document_date, due_date, expiry_date, amount_total, currency, language, summary, topics, comments: [{body, date}], text: str, text_truncated: bool}`.

- [ ] **Step 1: Write failing test**

`tests/test_ask_get_document.py` — call the dispatch function directly (mirror how existing engine tests call `_run_semantic_search`):

```python
def test_get_document_returns_fields_comments_and_text(db_session, make_document_with_text):
    doc = make_document_with_text("The internal floor area is 120 square metres.")
    db_session.add(DocumentComment(document_id=doc.id, body="this is my current house"))
    db_session.commit()
    out = _run_get_document(db_session, settings, {"document_id": doc.id})
    assert out["title"] == doc.title
    assert "120 square metres" in out["text"]
    assert any("current house" in c["body"] for c in out["comments"])
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ask_get_document.py -v`
Expected: FAIL (`_run_get_document` undefined).

- [ ] **Step 3: Implement the tool**

Add the tool schema to `TOOLS` (`engine.py:94-223`):

```python
    {
        "name": "get_document",
        "description": (
            "Read one document in full by its id: structured fields, the user's "
            "comments (authoritative personal context), and its text. Use after "
            "locating a document via semantic_search to answer a specific detail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "integer"}},
            "required": ["document_id"],
        },
    },
```

Implement `_run_get_document(session, settings, tool_input) -> dict` near the other dispatchers (`:276-365`): load the `Document`, its comments (explicit query), join `DocumentPage.markdown` (or `ocr_text` fallback) into `text`, truncate to `settings.ask_get_document_max_chars` (set `text_truncated`), and return the metadata dict. Wire it into the tool-dispatch switch in the loop (`:647-713`) alongside `semantic_search`/`query_documents`.

- [ ] **Step 4: Update system prompt**

In the system prompt (`engine.py:40-76`) add a sentence: user comments are authoritative personal context, and the agent may call `get_document` to read a specific document in full after locating it.

- [ ] **Step 5: Add the setting**

Add `ask_get_document_max_chars: int = 8000` to `Settings`. No `MODEL_PRICING` change (no new model).

- [ ] **Step 6: Run test + lint**

Run: `uv run pytest tests/test_ask_get_document.py -v && uv run ruff check src/library/ask/engine.py`
Expected: PASS, clean.

- [ ] **Step 7: Commit**

```bash
git add src/library/ask/engine.py src/library/config.py tests/test_ask_get_document.py
git commit -m "feat(ask): get_document read tool + comments as authoritative context"
```

---

## Task 6: Comment API client + `comments` card id

**Files:**
- Modify: `frontend/src/api/documents.ts` (types + client fns)
- Modify: `frontend/src/composables/useDocumentLayout.ts` (`DEFAULT_CARD_ORDER`)
- Test: `frontend/src/composables/__tests__/useDocumentLayout.spec.ts` (reconcile appends `comments`)

**Interfaces:**
- Produces: `DocumentComment` TS type `{id, document_id, author_id, body, created_at}`; `listComments(id)`, `createComment(id, body)`, `updateComment(id, cid, body)`, `deleteComment(id, cid)`; `DocumentDetail.comments: DocumentComment[]`; card id `'comments'` in `DEFAULT_CARD_ORDER`.

- [ ] **Step 1: Add the card id + test its reconcile**

Add `'comments'` to `DEFAULT_CARD_ORDER` in `useDocumentLayout.ts` (place after `'history'` or wherever the metadata column groups — Task 7 renders it in the metadata column). Add a test that an old stored order without `comments` gets it appended by `reconcileCardOrder`:

```ts
it('appends the comments card to an older stored order', () => {
  const merged = reconcileCardOrder(['preview', 'metadata'], DEFAULT_CARD_ORDER)
  expect(merged).toContain('comments')
})
```

Run: `cd frontend && npx vitest run src/composables/__tests__/useDocumentLayout.spec.ts` → PASS.

- [ ] **Step 2: Add API client + types**

In `frontend/src/api/documents.ts` add the `DocumentComment` type, add `comments` to the `DocumentDetail` type, and add the four client fns (mirror existing `updateNote`/`listNoteVersions` fetch helpers). Type everything.

- [ ] **Step 3: Type-check + lint + commit**

Run: `cd frontend && npm run type-check && npx eslint src/api/documents.ts src/composables/useDocumentLayout.ts`
Expected: exit 0.

```bash
git add frontend/src/api/documents.ts frontend/src/composables/useDocumentLayout.ts frontend/src/composables/__tests__/useDocumentLayout.spec.ts
git commit -m "feat(comments): API client, DocumentComment type, comments card id"
```

---

## Task 7: Comments card component + mount on detail view

**Files:**
- Create: `frontend/src/components/DocumentComments.vue`
- Modify: `frontend/src/views/DocumentDetailView.vue` (render `DocumentComments` in the metadata column, keyed to card id `comments`)
- Test: `frontend/src/components/__tests__/DocumentComments.spec.ts`

**Interfaces:**
- Consumes: comment API client (Task 6), `DocumentDetail.comments` (Task 6).
- Produces: `<DocumentComments :document-id :comments @changed>` — lists comments with dates, adds/edits/deletes via the API, emits `changed` so the parent refetches the doc.

- [ ] **Step 1: Write failing component test**

`DocumentComments.spec.ts` (mock the API client fns): renders each comment's body + formatted date (`data-testid="comment-item-{id}"`); typing in `comment-add-body` and clicking `comment-add-submit` calls `createComment` and emits `changed`; delete calls `deleteComment`. Assert on testids.

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentComments.spec.ts` → FAIL (component absent).

- [ ] **Step 3: Implement the component**

Create `DocumentComments.vue` following the app's card conventions (shared `.form-*`/`.btn` classes, uppercase-xs labels, `data-testid`s per §4.6). Props `documentId: number`, `comments: DocumentComment[]`; emit `changed`. Add box (textarea + Add), list newest-first with author + `formatDateTime(created_at)`, per-item edit/delete. Stable ids: `document-comments`, `comment-add-body`, `comment-add-submit`, `comment-item-{id}`, `comment-edit-{id}`, `comment-delete-{id}`.

- [ ] **Step 4: Mount it in the detail view**

In `DocumentDetailView.vue`, render `<DocumentComments :document-id="doc.id" :comments="doc.comments" @changed="reload" />` inside the metadata-column card loop, wrapped with the `section-card-comments` wrapper so it participates in card reordering (mirror the existing card wrappers). `reload` = the existing doc refetch.

- [ ] **Step 5: Run tests + type-check + lint**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentComments.spec.ts src/views/__tests__/DocumentDetailView.spec.ts && npm run type-check && npx eslint src/components/DocumentComments.vue src/views/DocumentDetailView.vue`
Expected: PASS, exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/DocumentComments.vue frontend/src/components/__tests__/DocumentComments.spec.ts frontend/src/views/DocumentDetailView.vue
git commit -m "feat(comments): Comments card on the document detail view"
```

---

## Task 8: Floating island + lifted metadata edit-mode

**Files:**
- Create: `frontend/src/composables/useMetadataEditMode.ts`
- Modify: `frontend/src/components/DocumentMetadataEditor.vue` (consume the composable instead of a local ref)
- Modify: `frontend/src/views/DocumentDetailView.vue` (island + IntersectionObserver; extract the Ask-prompt fn)
- Test: `frontend/src/composables/__tests__/useMetadataEditMode.spec.ts`, extend `DocumentDetailView.spec.ts`

**Interfaces:**
- Produces: `useMetadataEditMode()` singleton → `{ editMode: Ref<boolean>, toggle(), setEditMode(v) }` (ephemeral, resets on unmount like `useDocumentLayout`); island testids `detail-island`, `island-ask`, `island-edit-toggle`.

- [ ] **Step 1: Composable + test (fails → passes)**

Create `useMetadataEditMode.ts` mirroring the singleton pattern in `useDocumentLayout.ts` (module-level `ref`, ephemeral). Test: `toggle()` flips it; two callers share state. Run its spec → PASS.

- [ ] **Step 2: Consume it in the editor**

In `DocumentMetadataEditor.vue`, replace the local `editMode` ref (`:287`) and `toggleEditMode` (`:370`) with the composable's `editMode`/`toggle`. Keep all existing autosave behaviour and testids (`edit-toggle`) unchanged — only the state source changes. Run the editor's existing spec → PASS.

- [ ] **Step 3: Failing island test**

Extend `DocumentDetailView.spec.ts` (mock `IntersectionObserver`): island absent while hero intersects; after the observer reports the hero not intersecting, `[data-testid="detail-island"]` appears; clicking `island-edit-toggle` flips `useMetadataEditMode().editMode`; `island-ask` calls the shared prompt/navigation fn (spy). Run → FAIL.

- [ ] **Step 4: Extract the Ask-prompt fn**

In `DocumentDetailView.vue`, extract the hero "Ask about this document" prompt-building + `window.open`/router navigation into one function (e.g. `askAboutDocument()`), and call it from both the hero button and the island so there is no duplication.

- [ ] **Step 5: Implement the island**

Add a `v-if`-gated fixed element (`data-testid="detail-island"`, `class="fixed bottom-4 right-4 z-40 ..."`) shown when `heroVisible` is false. Wire `heroVisible` with an `IntersectionObserver` on the `#document-hero` element registered in `onMounted`, disconnected in `onBeforeUnmount`. Buttons: `island-ask` → `askAboutDocument()`; `island-edit-toggle` → `useMetadataEditMode().toggle()` (label reflects `editMode`). Ensure it does not overlap the chart tooltip or break mobile e2e (use `v-if`).

- [ ] **Step 6: Run tests + type-check + lint + build**

Run: `cd frontend && npm run test:unit -- run && npm run type-check && npx eslint src/components/DocumentMetadataEditor.vue src/views/DocumentDetailView.vue src/composables/useMetadataEditMode.ts && npm run build-only`
Expected: full suite PASS, type-check/eslint/build exit 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/composables/useMetadataEditMode.ts frontend/src/components/DocumentMetadataEditor.vue frontend/src/views/DocumentDetailView.vue frontend/src/composables/__tests__/useMetadataEditMode.spec.ts frontend/src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat(detail): floating island (Ask + Edit/Done) with lifted metadata edit-mode"
```

---

## Task 9: Documentation

**Files:**
- Modify: `docs/frontend.md` (Comments card, island, Ask composer keys)
- Modify: the ask/api docs (`get_document` tool, comments endpoints) — find the doc that documents ask tools / API and update it

- [ ] **Step 1: Update `docs/frontend.md`** — AskView Enter-to-send keys; the detail-view island; the Comments card (new card id in the layout system).
- [ ] **Step 2: Update ask/API docs** — `get_document` tool and the `/api/documents/{id}/comments` endpoints; note that comments are embedded and distinct from notes.
- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs: comments, get_document tool, ask Enter-to-send, detail island"
```

---

## Final verification (before merge)

- [ ] Backend: `uv run pytest` (full suite green) + `uv run ruff format --check .` + `uv run ruff check .`
- [ ] Frontend: `cd frontend && npm run test:unit -- run` (green) + `npm run type-check` (exit 0) + `npm run lint` + `npm run build-only`
- [ ] `alembic upgrade head` then `alembic downgrade -1` then `upgrade head` cleanly (migration round-trips).
- [ ] Manual smoke on a real document: add a comment, confirm it shows with a date, then ask `/ask` "which is my current house" and a follow-up detail question.

## Self-review against the spec

- Spec §2 (Enter-to-send) → Task 1. §3 (island, lifted edit-mode) → Task 8. §4.2 (model/migration) → Task 2. §4.3 (API + detail serialization) → Task 3. §4.4 (comment chunks) → Task 4. §4.5 (`get_document` + prompt) → Task 5. §4.6 (Comments card UI + card id) → Tasks 6-7. §4.7 (testing) → distributed across each task. Docs (§6) → Task 9. No spec section is unmapped.
- Types consistent across tasks: `DocumentComment` (Task 2) used by 3/4/5/6/7; `comment_id` (Task 2) used by 4; `CommentOut` (Task 3) reused by documents detail; `useMetadataEditMode` (Task 8) consumed by the editor; card id `'comments'` (Task 6) consumed by Task 7.
