# Document comments, detail-view island, and Ask Enter-to-send

Date: 2026-07-06

Three related UX features shipped on branch `worktree-feat-doc-comments-ask`,
built via the full brainstorm → spec → plan → subagent-driven execution cycle
(9 planned tasks, per-task review, and a whole-branch review before merge).

## 1. What shipped

### 1.1 `/ask` Enter-to-send
The composer now sends on plain **Enter**; **Shift+Enter** and **Ctrl+J** insert
a newline; **Cmd/Ctrl+Enter** still sends; Enter is ignored during IME
composition (`isComposing` / `keyCode 229`). One handler in `AskView.vue`.

### 1.2 Floating detail-view island
A `position: fixed` bottom-right control on `/documents/:id` that appears once
the hero scrolls off screen (IntersectionObserver on `#document-hero`). It holds
an "Ask about this document" anchor and an Edit/Done metadata toggle. To let the
island drive the same edit mode as the Details card's own button, the metadata
edit-mode flag was lifted from a component-local ref into a module-singleton
composable `useMetadataEditMode` — mirroring the existing `useDocumentLayout`
pattern. This is distinct from the "Edit layout" (rearrange) mode.

### 1.3 Document comments + `/ask`
A new first-class concept: user-authored, dated free text attached to an
existing document (distinct from the standalone "notes", which are
`source='note'` Documents).
- `document_comments` table (migration `0022`) + a nullable
  `document_chunks.comment_id` provenance column.
- CRUD API `/api/documents/{id}/comments` (blank bodies rejected 422; each
  mutation writes an `IngestionEvent` and re-embeds the document).
- A "Comments" card on the detail page, registered as card id `comments` in the
  layout system so it participates in "Edit layout" reordering.
- **Indexing:** `run_embed` now emits one embedded chunk per comment (framed
  `User comment (YYYY-MM-DD): <body>`), so `/ask` semantic search finds a
  document through its comments. Deleting a comment drops its chunk two ways (FK
  `ON DELETE CASCADE` + the re-embed rebuild).
- **`get_document` ask tool:** returns a located document's fields + comments +
  bounded full text (`ask_get_document_max_chars`, default 8000). This closes
  the gap where the agent could *find* a document via a comment but couldn't read
  its details — so "what's the surface area of my current house" now works:
  locate doc via the "current house" comment, then read its area.

## 2. Key decisions

1. Comments are a **separate concept** from notes (attached annotation vs
   standalone note-document), named "Comments" to avoid confusion.
2. Comment date = auto `created_at` (no backdating in v1).
3. Comments made searchable by reusing the existing chunk/embed path (one chunk
   per comment) rather than a separate vector store or polluting `ocr_text`.
4. Added the `get_document` read tool — the enabler that makes the example
   queries actually work; not just find-the-doc but read-the-doc.
5. Island Edit is a plain Edit/Done toggle over the existing autosave editor (no
   batched save/cancel), per the user's choice.

## 3. Issues caught in review (and fixed)

The review layers earned their keep — several defects passed a green test suite
and were caught by reviewers:

1. **CRITICAL (whole-branch review): edit-mode singleton not reset on in-view
   document navigation.** Lifting the metadata edit flag to a module singleton
   (for the island) created a lifecycle obligation the review-queue Prev/Next
   flow didn't meet: the unkeyed `<RouterView>` + `route.params.id` watcher
   reloads a new document without unmounting, so `editMode` stayed `true`, the
   editor's non-immediate `watch(editMode)` never re-hydrated, and doc B's fields
   rendered **blank** — with a narrow Enter-to-wipe path. Fixed by resetting both
   edit-mode singletons in the navigation watcher; regression test confirmed
   RED-before/GREEN-after.
2. **CRITICAL (Task 8 review): island Edit opened editors un-hydrated.** The
   island flipped the shared ref but `hydrateDrafts()` only ran in the card's own
   toggle → blank fields + empty autosave. Fixed by driving hydrate/reset off a
   single `watch(editMode)` so both entry paths hydrate.
3. **Important (Task 8 review): hero "Ask" downgraded from anchor to button.**
   The share-with-island refactor turned the hero link into a `<button>` +
   `window.open`, losing middle/cmd-click-new-tab and link semantics. Fixed by
   sharing a computed `askHref` and keeping both hero and island as real anchors.
4. **Task 3 async conversion:** serializing comments into the detail payload
   forced `_detail()` sync→async across 8 call sites; reviewer verified all are
   awaited and the `lazy="raise"` comments relationship is queried explicitly.
5. **Blank-comment rejection, `passive_deletes` consistency, `reloadDocument`
   unmounted/extracting guards** — smaller review fixes.

## 4. Security note (deliberate decision)

Automated review flagged the comment edit/delete endpoints as IDOR (no
author-ownership check). **Decision: not enforced.** Auth + CSRF are applied
globally (`api_router` dependencies); this app has **no per-user document
ownership anywhere** — `notes.update_note`, `update_document`, and
`delete_document` all let any authenticated user mutate any content (a shared
household library). `author_id` is provenance, not an authz boundary. Comments
follow the same trust model; author-only editing would be inconsistent. Easy to
add later if per-user attribution is ever wanted (`author_id` is stored).

## 5. Follow-ups (non-blocking)

1. Comment delete is one-click (no confirm), unlike notes' two-step — consider a
   confirm for consistency.
2. The Comments card also renders on `source='note'` documents (harmless, likely
   unintended) — could gate it in `cardPresent`.
3. No end-to-end test of the `changed → reloadDocument → re-render` chain.

## 6. Verification

- Backend: `uv run pytest` → **952 passed**. Frontend: `npm run test:unit` →
  **810 passed** (coverage 90.5% stmts / 92.7% lines). `ruff format --check` +
  `ruff check` clean repo-wide; eslint clean; `vite build` succeeds;
  `vue-tsc --build` type-check clean. Migration `0022` round-trips
  (upgrade/downgrade/upgrade) via the generic migration-cycle test.
- Not done: a live click-through against the running stack — the features are
  covered by component/API/embedding tests that exercise real DB queries and
  rendered DOM, but the end-to-end "add a comment, then ask about it" flow will
  be confirmed live after deploy.
