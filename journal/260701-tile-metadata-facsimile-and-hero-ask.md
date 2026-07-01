# Tile metadata facsimile & hero Ask button (design revision)

A same-day revision of the two improvements shipped earlier in
[260701-ask-button-and-markdown-tile-preview](260701-ask-button-and-markdown-tile-preview.md),
based on user feedback after seeing them live.

## 1. Why

1. The markdown tile "body excerpt" preview looked bad in practice: ingested
   emails begin with **forwarded-header boilerplate** ("John +31… Forwarded
   message… From:… Date:… Subject:…"), so the excerpt showed noise, not content.
2. The "Ask about this document" button sat at the **bottom** of the detail page
   in the Actions card — we want to encourage its use, so it needed prominence.

## 2. What changed

### 2.1 Tile: metadata "facsimile" instead of a body excerpt

Replaced the body-excerpt preview with a compact **metadata facsimile** rendered
in the tile's preview box for `text/markdown` docs: the **title** as a heading
line, then one line each for **Kind / From (sender) / To (recipient) / Date**,
with empty fields omitted (`previewMetadata` / `hasPreviewMetadata` in
`DocumentListView.vue`, `data-testid="markdown-preview"`). `text/plain` still
shows the "Text" placeholder.

The framing that makes this work (and not read as duplication of the metadata row
below the title) is treating the preview box as a **stand-in for a missing
thumbnail** — a synthesized "document header" that echoes what a PDF thumbnail
would show. Verified visually with a static Tailwind mock before shipping.

**Backend simplification:** because every field is already on the list item, the
`preview_excerpt` field, the `markdown_excerpt` helper (`src/library/text_preview.py`),
and their tests — all added earlier the same day — were **removed entirely**. No
backend field or query change; the tile builds the facsimile from existing data.

### 2.2 Detail: Ask button moved to the hero

Moved "Ask about this document" from the Actions card to the **hero header**,
top-right of the title (stacks under the title on mobile), as a **primary violet
`AppButton`** with a chat icon. Removed it from the Actions card (moved, not
duplicated — one CTA per action). Same `?q=` pre-fill and new-tab behavior.

### 2.3 `AppButton` gained a `target` prop

The first version had to hand-roll a raw `RouterLink` because `AppButton` couldn't
open a new tab. Added a `target` prop (and widened `to` from `string` to
`RouteLocationRaw` so callers can pass `{ name, query }`); `_blank` automatically
gets `rel="noopener"`. The hero button now uses a proper `<AppButton>`, and the
prop is reusable for any future new-tab button.

## 3. Decisions

- **3.1** Metadata facsimile over an excerpt: the body's leading content (email
  headers) is unreliable; structured metadata is always meaningful.
- **3.2** Keep `title` in the facsimile (per the user's explicit field list) even
  though it repeats the violet title link directly below — it anchors the
  "document header" framing. Flagged as trivially droppable if it reads redundant.
- **3.3** Move (not duplicate) the Ask button; hero placement makes it the page's
  primary call to action.

## 4. Verification

- Full backend suite **780 passed** (12 fewer than before — exactly the removed
  `text_preview` + `preview_excerpt` tests); coverage 86%.
- Frontend **607 passed** incl. new tests: `AppButton` `target`/`rel` behavior,
  the tile facsimile (fields rendered, empties omitted, `text/plain` unchanged),
  and the hero button (single instance, in `#document-hero`, `rel="noopener"`).
- `ruff check`/`format --check` clean repo-wide; ESLint + vue-tsc clean.
- Independent code review: no critical/important bugs; its one Medium note (an
  untested `rel` on the RouterLink branch) was addressed by adding the assertion.
- Docs updated: removed the `preview_excerpt` docs from `api.md`, updated the
  `DocumentListView`/`DocumentDetailView` descriptions in `frontend.md`.
