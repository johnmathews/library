# Document comments, detail-view island, and Ask Enter-to-send

**Status:** active. **Last updated:** 2026-07-06. **Supersedes:** none.

Design for three related UX improvements. Two are small and self-contained; the
third (document comments feeding `/ask`) is the substantial one and drives the
data-model and retrieval work.

## 1. Scope and goals

Three features, buildable independently but shipped together:

1. **`/ask` Enter-to-send** — plain Enter sends the message; Shift+Enter and
   Ctrl+J insert a newline.
2. **Floating "island" on `/documents/:id`** — a fixed bottom-right control that
   appears once the hero scrolls off screen, holding "Ask about this document"
   and an Edit/Done toggle.
3. **Document comments + `/ask` integration** — user-authored, dated comments
   attached to an existing document, indexed so `/ask` semantic search can find
   the document through them, plus a new `get_document` read tool so the agent
   can then read that document's details.

Non-goals: no change to the existing standalone **notes** feature (a note is a
`source='note'` Document); comments are a *separate* concept. No batched
save/cancel for metadata editing (autosave-on-blur stays).

## 2. Feature A — `/ask` Enter-to-send

### 2.1 Current behaviour

The composer is a real `<textarea>` (`AppTextarea`) in
`frontend/src/views/AskView.vue:551-559`, inside `<form @submit.prevent>`
(`:549`). The only keyboard path is `onComposerKeydown`
(`AskView.vue:264-270`): today **only Cmd/Ctrl+Enter** submits; plain Enter
falls through to the browser default (newline). Hint text at `:555` reads
"(⌘/Ctrl + Enter to send)". `onSubmit` already guards empty input and the
in-flight (`isAnswering`) state (`:186-191`).

### 2.2 Change

Rewrite `onComposerKeydown` so that, for `event.key === 'Enter'`:
- **plain Enter** → `preventDefault()` + submit;
- **Shift+Enter** → do nothing (browser inserts the newline);
- **Cmd/Ctrl+Enter** → still submit (backward-compatible);
- guard `event.isComposing` (and legacy `keyCode === 229`): while an IME is
  composing, Enter must **not** send — it commits the composition.

Add a separate branch for **Ctrl+J** (`event.key === 'j' && event.ctrlKey`):
`preventDefault()` and insert a newline at the caret. Because Ctrl+J is not a
default text-insertion key, do it explicitly: splice `\n` into the bound
`question` string at the textarea's `selectionStart`/`selectionEnd`, then restore
the caret after the inserted newline on `nextTick`.

Update the hint text to "Enter to send · Shift+Enter for new line".

### 2.3 Testing

Unit-test the handler behaviour: plain Enter triggers submit; Shift+Enter does
not; Ctrl/Cmd+Enter submits; Ctrl+J inserts a newline and does not submit;
Enter while `isComposing` does not submit; empty/whitespace and `isAnswering`
remain guarded.

## 3. Feature B — Floating island on the detail view

### 3.1 Behaviour

A `position: fixed` element pinned bottom-right of the viewport, rendered by
`DocumentDetailView.vue`. It is **hidden while the hero is on screen** and
appears once the hero (`#document-hero`) has scrolled out of view. Contents:

1. **Ask about this document** — identical action to the existing hero button
   (`[data-testid="ask-about-document"]`): opens the Ask view in a new tab with
   the prefilled prompt. Extract the prompt-building + navigation so both the
   hero button and the island call one function (no duplication).
2. **Edit / Done** — toggles the Details editor's edit mode (autosave semantics
   unchanged; no Save/Cancel).

### 3.2 Visibility mechanism

Use an `IntersectionObserver` on the `#document-hero` element: when it is not
intersecting the viewport, show the island; otherwise hide it. Register the
observer on mount, disconnect on unmount. `v-if` (not `v-show`) gates the island
so it does not affect the mobile/tablet e2e visibility assertions when hidden.

### 3.3 Lifting the metadata edit-mode

The Details editor's edit-mode boolean currently lives inside
`DocumentMetadataEditor.vue` (`editMode` ref `:287`, toggled at `:370`). To let
the island toggle the same state the card shows, lift this to shared state:
either a `v-model:edit-mode` prop owned by `DocumentDetailView`, or a small
composable `useMetadataEditMode()` (module singleton, mirroring
`useDocumentLayout`). Prefer the composable for symmetry with the existing
layout-edit state and to avoid threading props. The card's own Edit/Done button
and the island both read/write this one flag.

This is **distinct** from the "Edit layout" mode (`useDocumentLayout.editMode`) —
two different edit modes; keep their labels unambiguous ("Edit" for metadata
values vs "Edit layout" for rearranging).

### 3.4 Styling / responsiveness

Bottom-right, above page content (`z-index` above cards, below any modal),
comfortable tap targets, stacks/shrinks gracefully on mobile. Stable ids:
`data-testid="detail-island"`, `island-ask`, `island-edit-toggle`.

### 3.5 Testing

Island absent while hero intersects, present once it does not (mock
`IntersectionObserver`); the island Edit button and the card Edit button reflect
and toggle the same edit-mode; the island Ask button calls the shared
prompt/navigation function.

## 4. Feature C — Document comments and `/ask`

### 4.1 Concept

A **comment** is user-authored free text attached to an existing document, with
an automatically recorded date. It is a new, first-class concept — *not* a
standalone note (`source='note'` Document) and *not* an `IngestionEvent`. Its
purpose: capture personal context ("this is my current house") that (a) shows on
the document and (b) is retrievable by `/ask` so questions phrased around that
context resolve to the document.

### 4.2 Data model

New table `document_comments` (Alembic revision `0022`, `down_revision='0021'`):

| column | type | notes |
| --- | --- | --- |
| `id` | BigInteger PK | |
| `document_id` | FK → `documents.id`, `ON DELETE CASCADE` | |
| `author_id` | FK → `users.id`, nullable | who wrote it |
| `body` | Text, not null | the comment |
| `created_at` | timestamptz, default now | the recorded date (shown in UI) |
| `updated_at` | timestamptz, onupdate now | |

SQLAlchemy model `DocumentComment` in `src/library/models.py`, with a
`Document.comments` relationship (`lazy="raise"`, ordered by `created_at`). The
recorded "date of the note" is `created_at`; comments are auto-dated (no
user-set date in v1 — a future `noted_on` column can add backdating if wanted).

### 4.3 API

Nested REST under a document (new router `src/library/api/comments.py`, mounted
alongside `documents`/`notes`):

- `GET  /api/documents/{id}/comments` → list, newest-first.
- `POST /api/documents/{id}/comments` `{body}` → create (author = current user).
- `PATCH /api/documents/{id}/comments/{cid}` `{body}` → edit.
- `DELETE /api/documents/{id}/comments/{cid}` → delete.

Each mutation records an `IngestionEvent` (`comment_added` / `comment_edited` /
`comment_deleted`) on the document so the history timeline reflects it, and
triggers a re-embed (§4.4). Serialize comments into the document-detail payload
(`DocumentDetailOut`) so the UI has them on load.

### 4.4 Indexing (making comments answerable)

Semantic search only sees `document_chunks`. So comment text must become chunks.

**Chosen approach — comment chunks inside the existing embed job.** Extend
`run_embed` (`src/library/jobs.py:193-266`) so a document's chunk set =
content chunks (from `DocumentPage.markdown` / `ocr_text`, unchanged) **plus one
chunk per comment**. Each comment chunk's text is framed to carry its date, e.g.
`"User comment (2026-07-06): this is my current house"`, so the date is part of
the embedding and the retrieved excerpt. Because `run_embed` already
delete-and-reinserts all chunks idempotently, comment chunks are rebuilt on every
re-embed; comment create/edit/delete calls `embed_document.defer_async` (as notes
already do, `api/notes.py:168-178`).

Provenance: add a nullable `comment_id` FK column to `document_chunks` (migration
`0022`). `NULL` = content chunk; set = comment chunk. This lets citations label
"your comment" vs document text and lets the UI/agent distinguish provenance.

*Alternatives considered:* (1) append comment text into `ocr_text` — rejected,
pollutes the document's own text and FTS; (2) a separate comment-vector table +
extend `semantic_search` to union it — more code and a second index for no real
gain, since chunks already carry `document_id`. The chosen approach reuses the
whole existing retrieval path.

### 4.5 `/ask` retrieval and the new read tool

With comment chunks in `document_chunks`, `semantic_search`
(`src/library/search.py:419`) already retrieves them and returns `document_id`
(`ask/engine.py:298-317`). So *"my current house"* surfaces doc 103 via its
comment.

The gap: the agent can *find* a document but has **no tool to read its full text
or fields** — the search excerpt is only the matched chunk. So *"surface area of
my current house"* needs a second step. Add a read tool:

- **`get_document(document_id)`** in `ask/engine.py` `TOOLS` (`:94-223`), dispatch
  alongside the others. Returns the document's structured fields (title, sender,
  recipient, kind, dates, amount, currency, language, summary, topics), its
  **comments** (body + date), and its **full text** (per-page markdown joined, or
  `ocr_text`), bounded to a sane char cap (e.g. `settings.ask_get_document_max_chars`,
  default ~8000) with a truncation note. Read-only; no confirmation gate.
- Update the system prompt (`ask/engine.py:40-76`) to tell the agent that user
  comments are authoritative personal context, and that it can call
  `get_document` to read a specific document in full after locating it.

This makes the two-step reasoning work: locate the document via a comment (or any
search), then read it for the specific fact.

### 4.6 UI — Comments card on `/documents/:id`

A new **"Comments"** card: a list of comments (each with body, author, and
formatted date), an add box (textarea + Add), and per-comment edit/delete for the
author. It integrates with the existing card-layout system as a new card id
`comments` in `useDocumentLayout` `DEFAULT_CARD_ORDER` — the merge-safe reconcile
appends it for existing users automatically. Place it in the metadata column by
default. Stable ids: `data-testid="document-comments"`, `comment-add-body`,
`comment-add-submit`, `comment-item-{id}`, `comment-edit-{id}`,
`comment-delete-{id}`. New API client fns in `frontend/src/api/documents.ts`.

Naming: the card is labelled **"Comments"** to avoid confusion with the standalone
Notes feature.

### 4.7 Testing

- **Model/migration:** `document_comments` table + `document_chunks.comment_id`
  column; upgrade/downgrade; cascade on document delete.
- **API:** comment CRUD (create/list newest-first/edit/delete), author recorded,
  `IngestionEvent` written, re-embed deferred on each mutation.
- **Embedding:** `run_embed` emits one chunk per comment (framed with date) in
  addition to content chunks; editing a comment changes the comment chunk;
  deleting removes it; a document with no comments is unchanged.
- **Retrieval:** a semantic-search test — a comment "this is my current house" on
  a document makes that document retrievable for the query "my current house".
- **Ask tool:** `get_document` returns fields + comments + (bounded) text; an
  ask-engine test that a comment resolves a "which is my …" question and
  `get_document` supplies a detail from the located document.
- **UI:** Comments card renders comments with dates, add/edit/delete call the API
  and update the list; card appears via layout reconcile.

## 5. Work breakdown (for the plan)

Independent-first ordering:

1. **A** — Ask Enter-to-send (frontend only).
2. **C1** — `document_comments` model + migration `0022` (incl.
   `document_chunks.comment_id`) + comment CRUD API + detail serialization.
3. **C2** — extend `run_embed` to embed comment chunks + re-embed on mutation.
4. **C3** — `get_document` ask tool + system-prompt update.
5. **C4** — Comments card UI + card-layout registration + API client.
6. **B** — floating island + lift metadata edit-mode composable.

## 6. Cross-cutting notes

- Backend: Python 3.13, `uv`, pytest; run the full backend suite + `ruff
  format --check`/`ruff check` over the whole repo (migrations included) before
  merge. Frontend: Vitest + Playwright; `vue-tsc --build` type-check (stricter
  than `--noEmit`), eslint, `vite build`.
- Model-pricing note: `get_document` adds no new model setting, but any new
  `*_model` setting elsewhere would need a `MODEL_PRICING_USD_PER_MTOK` row —
  not applicable here.
- Docs: update `docs/frontend.md` (Comments card, island, Ask composer keys) and
  the API/ask docs (`get_document` tool, comments endpoints) as part of the work.
