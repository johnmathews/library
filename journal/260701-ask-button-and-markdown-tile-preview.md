# Ask-about-this-document button & markdown tile preview

Two small, user-requested frontend improvements (one with a thin backend touch),
shipped together via the engineering-team cycle.

## 1. What shipped

### 1.1 "Ask about this document" button

`DocumentDetailView`'s Actions card gains an **"Ask about this document"** button
(`data-testid="ask-about-document"`). It is a plain `RouterLink` with
`target="_blank"` (AppButton exposes no `target` prop) that opens the Ask view in
a **new tab** at `{ name: 'ask', query: { q: <prompt> } }`. The pre-filled prompt
reads `Tell me about the document "<title>" (<kind> from <sender>, <date>): `,
with each of kind/sender/date gracefully omitted when absent (no empty
parentheses) and the title falling back `title → original_filename → this
document`.

`AskView` now reads a **`?q=` query parameter** on initial mount: on a fresh
`/ask` (not resuming a `/ask/:threadId` thread) it seeds the composer textarea and
opens/focuses it. The existing thread-resume path is guarded by an early `return`,
so seeding never interferes with it.

### 1.2 Markdown/email tile preview

Email-ingested `text/markdown` tiles on the Documents dashboard previously showed
the literal word **"Text"** in the preview box (the generic `fileTypeLabel`
fallback). They now render a **plain-text excerpt of the document body** instead
(`data-testid="markdown-preview"`). `text/plain` tiles are unchanged and still
show "Text".

This is backed by a new **`preview_excerpt`** field on the document **list** item
(`DocumentListItem`), populated in `_list_item_fields` **only for `text/markdown`**
from the already-loaded `ocr_text`, via a new pure helper
`src/library/text_preview.py::markdown_excerpt` (regex-based: strips markdown
syntax, collapses whitespace, truncates to ~240 chars on a word boundary with an
ellipsis; returns `None` for empty/blank).

## 2. Key decisions

- **2.1 Ask is pre-fill only — no backend scoping.** We deliberately did *not*
  add a `document_id` filter to the ask endpoint. The prompt simply names the
  document so the existing RAG retrieval surfaces it. This keeps the whole feature
  a frontend change and avoids coupling the detail view to ask-retrieval internals.
- **2.2 Excerpt from the body, not the summary.** The tile already renders
  `summary` below the title, so reusing it in the preview box would duplicate it.
  We show a real body excerpt instead — hence the new `preview_excerpt` field
  rather than reusing `summary`.
- **2.3 `text/markdown` only.** `text/plain` keeps the "Text" placeholder; the new
  field is `null` for every non-markdown MIME type. This scoped the change and
  kept the list query cheap (`ocr_text` is a non-deferred column already loaded on
  every list row, so no extra query — the excerpt is only computed for markdown
  rows).
- **2.4 No DB migration.** `preview_excerpt` is a computed response field, not a
  stored column.

## 3. Tests & verification

- New `tests/test_text_preview.py` (11 tests): markdown stripping, whitespace
  collapse, length cap + ellipsis, link-text preservation, None/blank handling.
- New backend list assertion in `tests/test_documents_api.py`: `text/markdown`
  → non-null cleaned excerpt; `text/plain` / `application/pdf` / empty-body
  markdown → `None` (scoped by a unique tag per the session-scoped-DB convention).
- New Vitest cases in `AskView.spec.ts` (seed from `?q=`, empty without it,
  thread-resume unaffected), `DocumentDetailView.spec.ts` (button href/target +
  graceful omission), and `DocumentListView.spec.ts` (markdown excerpt renders,
  `text/plain` still shows "Text").
- Full backend suite **792 passed** (coverage 86%; `text_preview.py` 96%).
  Frontend **603 passed**. `ruff check`/`ruff format --check` clean repo-wide;
  ESLint + vue-tsc clean.
- Independent code review: clean, no issues above threshold (verified the regex
  can't raise, the `?q=` array case is filtered, `askPrompt` null-safety, and the
  tile `v-else-if` ordering leaves existing cases untouched).

## 4. Follow-ups / notes

- `text_preview.py` line 44 (word-boundary fallback branch) is the one uncovered
  line — cosmetic; the truncation is exercised via the hard-cut path.
- If a document-scoped Ask is wanted later, the natural extension is a
  `document_id` param on the ask endpoint + retrieval filter; explicitly out of
  scope here.
