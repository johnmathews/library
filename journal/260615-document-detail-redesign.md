# Document detail view redesign

Reworked `/documents/:id` (`DocumentDetailView.vue`) to make the page feel more
designed and to put the most important metadata front and centre. Also squared
off the sidebar corner.

## What changed

- **Sidebar corner** (`AppSidebar.vue`): right-edge radius `rounded-r-2xl` (16px)
  → `rounded-r-lg` (8px). Softer than a hard square, less of a balloon.

- **Hero header card**: replaced the bare `<h1>` above the grid with a
  full-width Mosaic card containing
  - the title (`h1#document-title`, unchanged id/fallback),
  - a labelled **stat row** — Kind · Sender · Document date · Amount — read-only
    mirrors of the most important fields, em-dash for empties so the 4-up grid
    stays stable (`heroStats` computed), and
  - the document's **tags as colour-varied `AppBadge` pills**. Reused the
    existing `AppBadge` palette; colour is derived from the tag name
    (`tagColour`, a small hash → fixed colour list) so a tag keeps the same
    colour across renders/pages without storing colour on the tag. Full literal
    colour values (no class interpolation) so Tailwind 4 actually emits them.

- **PDF preview**: hid the native viewer toolbar by extending the iframe
  fragment to `#toolbar=0&navpanes=0&view=FitH` (best-effort — Chrome/Edge
  honour it, some Firefox builds ignore it). Added a slim **preview header bar**
  with **Open** (new tab, inline URL) and **Download** (attachment; searchable
  PDF when present, else original) icon+label buttons, and removed the old inline
  "Open the PDF in a new tab" text link (now redundant). The header shows for
  image previews too. Mobile thumbnail / padlock fallbacks and the Actions card
  are untouched.

## Decisions

- **Hero stays read-only; editing stays in the Details list.** Keeps a single
  place to edit (the GOV.UK-style per-row "Change" flow) and lets the hero be a
  clean summary. Avoids two edit affordances for the same field.
- **Reused `AppBadge` rather than a new pill component.** Same component the
  document list uses for tags, so the two pages read as one family.

## Verification

- `vitest run` — 224 passing (34 files). Updated the two iframe-src expectations
  for the new fragment; added tests for the preview header Open/Download URLs and
  the hero stats + tag badges.
- `type-check` and `lint` clean.
- Eyeballed light + dark at desktop width via a Playwright script against the
  Vite dev server with mocked `/api` routes (throwaway, not committed).
