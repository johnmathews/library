# Document detail: hide PDF toolbar (Firefox), panel the OCR text, group the metadata

Follow-up polish on `/documents/:id` (`DocumentDetailView.vue`) after yesterday's
redesign ([260615-document-detail-redesign](260615-document-detail-redesign.md)).
Three issues from review: the PDF viewer toolbar was still visible, the extracted
text floated outside any panel, and the Details list was a flat, undifferentiated
block with a lot of blank space.

## What changed

- **PDF toolbar actually hidden on Firefox.** Yesterday's `#toolbar=0` fragment
  only works on Chrome/Edge; Firefox's built-in viewer ignores it and there is no
  URL fragment that hides it. So the `lg+` iframe now sits in an `overflow-hidden`
  wrapper and, **on Firefox only** (`hidePdfToolbar`, UA-gated), is nudged up by
  `2.6rem` (`h-[calc(70vh+2.6rem)] -mt-[2.6rem]`) to clip the toolbar off the top
  edge. Chrome/Edge keep the plain `h-[70vh]` (clipping there would just eat
  document content). **The iframe `src` is unchanged** — the test asserts it
  verbatim, and the fragment still does the work on the browsers that honour it.

- **Extracted OCR text now lives in its own card.** Was a bare `AppDetails`
  disclosure floating below the preview. Wrapped it in a Mosaic card matching the
  others; the text sits in a scrollable monospace inset (rounded, bordered,
  tinted, `max-h-[28rem] overflow-auto`) instead of raw `<pre>`. The `ocr-details`
  / `ocr-text` hooks and the `?highlight=` reveal behaviour are unchanged.

- **Details card → themed metadata groups.** Replaced the flat uniform `dl` with
  grouped sub-panels driven by `fieldGroups` + an `ACCENT` map: **Content**
  (violet), **Classification** (yellow), **Sender & dates** (sky), **Financial**
  (green), and a read-only **System** group (neutral). Each panel has an accent
  left rail, faint accent tint and uppercase accent heading, so metadata *types*
  are distinguishable at a glance. Fields lay out in a **two-column grid**
  (`sm:grid-cols-2`; wide fields and any open editor span both columns), which
  killed most of the blank space. **Values are larger** (`text-base`) under
  **smaller uppercase labels** (`text-xs`); **Amount** is an emphasised figure
  (`text-2xl`) and **Status** a coloured pill (`statusAccent`). Editing is
  unchanged — same `App*` inputs, same per-row PATCH, same
  `row-<field>`/`row-value`/`.app-link-button` hooks (the v-for now keys on
  `field` via a `rowByField` lookup so the grouped layout can place each row).

## Decisions

- **Clip, don't depend on a flag, for Firefox.** There is no cross-browser URL
  parameter to hide the native PDF toolbar; the overflow-clip is the pragmatic
  fix. Gated on the Firefox UA so it never costs content on browsers that already
  hide the toolbar properly. `2.6rem` is an estimate of Firefox's toolbar height —
  flagged to the user to eyeball; it's a one-number tweak if off.
- **Accent classes kept as literal strings in the `<script>`.** Tailwind 4 emits
  utilities by scanning source; the per-accent class strings are full literals (no
  interpolation) so they survive the build. Confirmed the compiled CSS contains
  them, not just that the build passed.
- **Group panels use a left rail + tint, not card-in-card borders.** Avoids a
  shorthand-vs-longhand border-colour fight (`border-l-<accent>` only) and reads
  as sections rather than nested cards.

## Verification

- `vitest run` — **224 passing (34 files)**; the detail-view spec's 18 cases still
  green (iframe src, edit/PATCH flows, OCR highlight, read-only rows all intact).
- `type-check` and `lint` clean.
- `vite build` succeeds; grepped `dist` CSS to confirm the accent + clip arbitrary
  classes (`border-l-violet-400`, `calc(70vh + 2.6rem)`, `margin-top:-2.6rem`,
  `28rem`) are present, not purged.
- Shipped: merged to `main` (`ba0c94a`), CI green, image promoted to
  `ghcr.io/johnmathews/library:latest`, deployed to the `paperless` LXC. Verified
  live — `/api/settings` → 401 (new build), `/healthz` → 200, served bundle hash
  matches the local build.
- **Open item:** the Firefox toolbar clip needs a real-browser eyeball (Firefox
  only; not reproducible in headless Chromium) to confirm `2.6rem` is right and
  doesn't shave the page top.
