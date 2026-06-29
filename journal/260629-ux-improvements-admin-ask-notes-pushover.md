# UX improvements: Pushover links, admin panels, note titles, Ask layout

Date: 2026-06-29. Branch: `feat/ux-improvements-admin-ask-notes`. Run via the
engineering-team skill (`.engineering-team/runs/manual-20260629T063554Z/`).

Four user-requested improvements, scoped tightly (no broad audit). Phone
screenshots drove the visual work.

## 1. Pushover document deep-links — config, not code

The deep-link feature was **already fully implemented**: `notifications.py`
builds `{LIBRARY_PUBLIC_BASE_URL}/documents/{id}` and sends it as Pushover's
`url`/`url_title` for all four events. It silently no-ops when the env var is
unset — which is why live notifications had no link (`.env.example` left it
commented). Changes:

- Uncommented `LIBRARY_PUBLIC_BASE_URL` in `.env.example` with a real example.
- Added a one-line **startup `WARNING`** (`warn_if_no_public_base_url` in
  `app.py`, called from the lifespan) when it is unset, so the misconfig is
  loud instead of invisible. Unit-tested in `test_spa.py`.
- Documented the behaviour in `docs/jobs-and-notifications.md` §1.5.4.

The actual fix in production is to set the var on the host; no app logic changed.

## 2. Note title from the first line of the body

Removed the separate Title field from note **create** (`NewNoteView.vue`) and the
in-place **edit** editor (`DocumentDetailView.vue`). The title is now the body's
first non-empty line, leading markdown heading marker stripped, capped at 200
chars — shared helper `deriveNoteTitle` in `src/utils/noteTitle.ts` (unit-tested).
Backend is unchanged: the views still send a non-empty `title`, so FTS, list
display and `note_versions` keep working. Save is gated on a non-empty first
line; a hint under the textarea explains the behaviour.

## 3. Ask view layout — standard "header on top, work area below"

The portrait-phone screenshot showed the bug: the sidebar took the left half and
the "Ask" title was crushed into a one-word-per-line right column. Fix in
`AskView.vue` + `ConversationSidebar.vue`:

- Promoted `PageHeader` (title + description) to a **full-width** sibling above
  `#ask-page` (Vue 3 multi-root).
- `#ask-page` now **stacks on mobile** (`flex-col`) and is a row only on `lg+`
  (`lg:flex-row`); the sidebar is full-width when stacked (`w-full lg:w-64`,
  dropped the mobile `sticky`), its list capped `max-lg:max-h-72`.
- Desktop sticky-chat preserved; height offset bumped `8rem → 14rem` to account
  for the header now above the working area.

## 4. Admin panels — Coverage detail + formatting fixes

**Coverage tab (more detail).** `scripts/coverage_summary.py` now keeps per-file
data — `files_total`, `files_below_gate`, and `worst_files` (the
`MAX_WORST_FILES=10` lowest-covered, ascending). `CoverageSide` (admin API) and
the TS types gained these fields (additive, backward-compatible). The tab renders
one card per side: headline %, a gate **Pass / Below gate** badge, file counts,
and the lowest-covered files, with a when/which-build footer.

**Formatting bugs (from the screenshots).**

- Architecture tab: fenced code blocks / ASCII diagrams overflowed off the right
  edge. Added a `.doc-markdown pre` rule (`overflow-x: auto`, padded, tinted) so
  they scroll inside the block. Applied to all three `.doc-markdown` users
  (Admin, NewNote, DocumentDetail) since they share the style.
- Git SHA ran off-screen → `break-all` on the System and Coverage SHA values.
- System **Configuration** went from a cramped 2-col `whitespace-nowrap` /
  `break-all` table to a stacked key-above-value `<dl>` that wraps cleanly on a
  phone (`system-config-row` testid preserved).

## 5. Verification

- Backend: `pytest` **676 passed**; `ruff check`/`format` clean over the whole repo.
- Frontend: `vitest` **444 passed**; `vue-tsc` and `eslint` clean; production
  build compiles.
- Not yet done: live visual check against the real docker stack / e2e (offered to
  the user as a follow-up). The structural changes are covered by the existing
  unit + e2e specs, which were updated alongside the code.
