# Recipient metadata field + Ask/notes UI improvements

Date: 2026-06-29. Branch: `eng-ui-improvements-and-recipient-field`. Run via the engineering-team
cycle (evaluate → plan → develop → wrap-up); run dir
`.engineering-team/runs/manual-20260629T133534Z/`.

## 1. What shipped

Four user-requested improvements, planned as six work units (W1–W6):

1. **Recipient metadata field (full-stack)** — a new `recipient` on documents to distinguish whose
   document it is (e.g. John's pension vs his wife's). Built as a lookup table mirroring `sender`.
   - **W1 — data layer:** `Recipient` model (`id`, `name` unique, `created_at`) + `documents.recipient_id`
     FK (nullable, indexed, `ondelete="SET NULL"`, `recipient` relationship `lazy="selectin"`). Alembic
     migration `0016_recipient_field.py` creates the table, seeds a "John" recipient, and backfills all
     existing documents to it. Round-trip (`upgrade head → downgrade base → upgrade head`) verified
     against ephemeral Postgres.
   - **W2 — backend behaviour:** LLM extraction of `recipient_name` (`extraction/schema.py`,
     `extractor.py` prompt + `PROMPT_VERSION` → `2026-06-29.1`, `apply.py` `upsert_recipient` respecting
     `user_edited_fields`); `recipient` on document API responses; `recipient` in PATCH (upserted by
     name); `GET /api/recipients` taxonomy endpoint; `recipient_id` list filter; recipient added to Ask
     `semantic_search` results and `structured_query` `DocumentRef`.
   - **W3 — frontend:** recipient display + editable `AppSelect` dropdown with a non-blocking inline
     "Add recipient…" affordance on the document detail view; recipient filter pill on the document list;
     `/api/recipients` wired into the shared taxonomy options cache.
2. **W4 — note editor view-mode toggle:** the in-place note editor (DocumentDetailView) now has the same
   edit/preview/split toggle as the note create view, factored into a shared composable
   `useMarkdownEditorMode` (shared storage key `library:note-editor-mode`), with a live preview bound to
   the draft through the existing DOMPurify+marked pipeline.
3. **W5 — Ask empty-state fix:** the conversation pane no longer says "No questions yet" when threads
   exist but none is selected. `ConversationSidebar` emits a `threads-changed` count; AskView branches
   into a distinct "Select a conversation…" message (`data-testid="ask-select-thread"`).
4. **W6 — Ask citations collapse:** per-turn citations are collapsed by default behind an `AppDetails`
   disclosure labelled `Citations (N)`, preserving the page deep-links.

## 2. Key decisions

1. **Recipient mirrors `sender`, not `kind`.** A flexible lookup table (case-insensitive upsert by name),
   not a rigid DB enum — recipients are a small but growing family set. The "controlled list" UX the user
   asked for comes from the frontend rendering it as a dropdown sourced from `GET /api/recipients`, while
   the backend reuses the proven sender upsert mechanism.
2. **Inline add, no admin panel.** New recipients are created via an "Add recipient…" option in the
   dropdown that reveals an inline text input (no `window.prompt`/blocking dialogs) and PATCHes the name;
   the backend upserts it. Deliberately deferred a recipient-management admin screen.
3. **Conservative auto-extraction.** The LLM proposes `recipient_name`; apply respects `user_edited_fields`
   so manual corrections stick, exactly like sender.
4. **Backfill existing docs to "John".** Done in the migration; reversible via the down-migration
   (column + table drop).

## 3. Issues found during wrap-up (code review) and fixed

1. **MCP parity gap (Important):** the recipient feature mirrored sender across REST/extraction/search/ask
   but the W2 reconnaissance missed `src/library/mcp_server.py`. Fixed: `recipient` added to
   `_document_summary`, a `recipient` substring filter on `search_documents`, and a `list_recipients` MCP
   tool. This required adding `recipient_contains` to `DocumentFilters`/`filter_conditions` in `search.py`
   (mirroring `sender_contains`, including LIKE-wildcard escaping).
2. **Taxonomy cache staleness (Medium):** `useTaxonomyOptions` caches via a module-level `loadPromise`
   that was never invalidated, so a recipient created inline didn't appear in the dashboard filter until a
   reload. Recipient is the first taxonomy item that's both a controlled dropdown and inline-creatable, so
   this was newly observable. Fixed with an exported `refreshTaxonomyOptions()` called after a successful
   inline add.
3. **Stale field-group label (Low):** "Sender & dates" → "Sender, recipient & dates".

Code review otherwise verified the migration, serialization, `user_edited_fields` locking, soft-delete
handling in the taxonomy endpoint, `lazy="selectin"` (no N+1), and all `v-html` sites (DOMPurify-guarded)
as correct.

## 4. Tests & coverage

- Backend: **685 passed** (incl. integration against Docker Postgres), ruff clean. Coverage **88%**.
- Frontend: **463 passed** (vitest), `vue-tsc` type-check clean, eslint clean.
- New tests cover: migration smoke + round-trip, extraction schema/apply for recipient, document API
  PATCH/filter/`GET /api/recipients`, MCP recipient summary/filter/`list_recipients`, the note-editor
  mode toggle + composable, the Ask empty-state branches, collapsed citations, the recipient
  dropdown/inline-add/filter, and the taxonomy refresh.

## 5. Docs updated

`docs/api.md` (recipients endpoint, recipient field/filter/PATCH, user-edited mapping), `docs/ingestion.md`
(recipient extraction field), `docs/ask.md` (recipient in context, collapsed citations, two-way empty
state), `docs/frontend.md` (recipient UI + note-editor toggle), `docs/architecture.md` (data model), and
`README.md` (extraction blurbs). No `CLAUDE.md` in repo; `docs/migration.md` is the paperless import guide,
not a DB-migration list.

## 6. Follow-ups (out of scope this round)

1. No recipient-management admin panel (rename/delete) — inline add only.
2. Recipient stays nullable and single-valued (one per document); no multi-recipient support.
3. No recipient hero-stat on the detail view (sender remains the only hero party).
