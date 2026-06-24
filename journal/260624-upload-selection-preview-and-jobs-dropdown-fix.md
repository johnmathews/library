# Upload selection preview + jobs-dropdown overflow fix

**Date:** 2026-06-24

Two webapp UX fixes, both surfaced while actually using the app to file a
real paper document (scanned → uploaded via the web view).

## 1. Selected-files preview on the upload page

**Problem.** On `/upload`, after picking files but before clicking **Upload**,
nothing reflected the selection. The `AppFileUpload` drop-zone always showed the
static "Drop files or click to browse"; the selected `File[]` lived in the
parent's `selected` ref but was never displayed. You couldn't confirm *which*
documents would be uploaded, or that anything was selected at all.

**Fix.** Self-contained in `AppFileUpload.vue` (the component already owns the
`v-model` selection and the drop-zone, so the preview belongs beside them;
`UploadView.vue` is untouched). Below the zone it now renders a count line and
one row per file — name, human-readable size, and a `✕` remove button
(`aria-label="Remove {name}"`, `[data-testid="selected-file"]`). Behavior:

- **Additive in multiple mode:** new picks/drops merge into the existing
  selection rather than clobbering it, de-duped by `name + size + lastModified`.
- **Single-file mode** still replaces.
- **Remove** drops a row; removing the last file resets the model to `null` so
  the parent's "select at least one file" validation keeps working.
- **Input value reset** after each `change` so re-picking a just-removed file
  fires `change` again.

Six new component tests (count, remove, clear-to-null, additive merge, dedupe,
single-file replace). Followed brainstorming → spec → TDD; spec at
`docs/superpowers/specs/2026-06-24-upload-selected-files-preview-design.md`.

## 2. Jobs dropdown clipped off-screen on phones

**Problem.** The background-jobs dropdown overflowed the **left** edge of the
screen on a phone (reported with a screenshot — "PROCESSING / Document #110 /
View all jobs" cut off). Cause: it was `absolute right-0`, anchored to the jobs
button, which sits **mid-cluster** (search/theme/user-menu are to its right). At
`min-w-64` (256px) it extended leftward from there and ran off-screen. The user
menu avoids this only because its button is at the far right.

**Fix.** In `AppHeader.vue`, below `sm` the dropdown now pins to the viewport's
right edge (`fixed top-16 right-2 w-72 max-w-[calc(100vw-1rem)]`) so it can't
overflow; at `sm`+ the original under-button `absolute` anchor is restored.
CSS-only; no behavioral change.

**Verification.** Built a throwaway Vite harness mounting the real `AppHeader`
with the jobs store seeded to "Document #110 / OCR", drove it with Playwright,
and screenshotted at two widths: at **390px** the dropdown is fully on-screen,
right-aligned; at **1024px** it still anchors under the button. Harness removed
after.

## Docs

Updated `docs/frontend.md`: expanded the `AppFileUpload` row to describe the
selection preview + additive/dedup behavior, and filled a pre-existing gap in
the `AppHeader` section — it never mentioned the background-jobs indicator /
dropdown at all (now documented, including the viewport-pinning behavior).

## Shipping

Branch `feat-upload-selected-files-preview`: spec commit, the upload feature,
and the header fix. Both features ride the same branch — noted to John in case
he wants them split.
