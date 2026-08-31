# Frontend view design principles

**Status:** active. **Last updated:** 2026-08-31 (the legacy series stack was deleted, taking `components/charts/ChartControls.vue` — this document's reference implementation — with it. The recipe is unchanged; §3, §5 and §5.1 now name `views/JobsView.vue`'s `jobs-filter-bar` as the reference, because it fills both `PageHeader` slots and is the route `e2e/header-toolbar.spec.ts` now drives, with `SpendingWorkspaceView.vue`'s `workspace-toolbar` as the native-date exemplar. §5.1's geometry table and its code example move to `/jobs` with it.) Earlier: 2026-08-30 (§4: registered `library:charts-board-currency` — `SpendingBoardView.vue`'s display-currency preference — alongside `library:currency-options`, so the two don't drift). Earlier (2026-08-22): §1.2 and new §5.1: a view's filter bar moves into `PageHeader`'s `#controls` slot, merging into one toolbar via a **container** query; `/jobs`' bar rebuilt to the §5 label recipe. Earlier (2026-08-22): §1.2 and §7: the page title moves to the app bar; the description stays as a lede at the top of `#app-page`. Earlier (2026-08-21): §4: relocate what a hidden container held, and test the capability rather than the mechanism. Earlier (2026-08-21): registered `library:ask-view-mode` and the wide-only mode pattern — store the preference, clamp the render, hide the control with `v-if`. Earlier (2026-08-12, documentation verification sweep): corrected the `AppButton` variant/size vocabulary, the sidebar storage key and the unsupported 44px claim.
**Last verified:** 2026-08-31 — method: partial, scoped to the four sites that named `ChartControls.vue` plus §5.1's geometry table. Read `views/JobsView.vue`'s template in full before naming it: its `#controls` slot holds `<div class="flex flex-wrap items-end gap-3" data-testid="jobs-filter-bar">` with `.filter-label` + raw `.form-select`/`.form-input` and no `App*` form component, and its `#actions` slot holds `jobs-show-system` and `jobs-columns-button` — so it exhibits both the §5 label recipe and the §5.1 two-group merge. Read `views/SpendingWorkspaceView.vue`'s `workspace-toolbar` likewise: same row class, same label recipe, two native `<input type="date">` fields, but `#controls` only, which is why it is the secondary exemplar and not the reference. The geometry numbers were not re-measured in this pass — they carry forward from the re-measurement recorded in `e2e/header-toolbar.spec.ts`'s own header, which was taken against the real stack on 2026-08-31 when that spec was repointed off the deleted `/charts/legacy`. Earlier the same day — method: re-stamp only. The #130 squash-merge landed on 2026-08-31, dating this file's last edit a day after its stamp, which `check_docs` reports as `stale-doc-edit` (the known date jump, issue #126 — the same re-stamp #122 made after #121). No prose changed; the verification below stands as performed. Previously verified 2026-08-30 — method: partial re-verification, scoped to the new §4 bullet: read `useCurrencyOptions.ts` for `CURRENCY_OPTIONS_STORAGE_KEY = 'library:currency-options'` and `SpendingBoardView.vue` for `useStorage<string>('library:charts-board-currency', ...)`, confirming both key literals against the prose. The rest of the document carries forward its 2026-08-22 verification, scoped to the new §5.1 and the §1.2 sentence pointing at it. These are **geometry** claims and were measured in the real stack (docker compose + the Vite dev server + Playwright) rather than read off class lists: the 1280px sidebar-expanded/collapsed table is the measured `#app-page` width and the observed merge outcome at each, and the container-vs-viewport claim was confirmed by swapping `@5xl:` for `lg:` and watching `e2e/header-toolbar.spec.ts` go red. Backed by 1102 frontend unit tests and the header-toolbar spec (5 tests). The rest of the document carries forward its 2026-08-22 verification, whose method was: the relocated title checked as a visual claim in the real stack via Playwright screenshots at 1440px and 375px.

How to build a Library view that looks **right the first time** — using the
Mosaic design language already in the app. This is a checklist plus the reasoning
behind it. If you follow §1 you will avoid every layout problem found in the
2026-06-28 UX pass. Companion docs: [frontend.md](frontend.md) (architecture,
shell, `App*` components), and the Mosaic reskin design record
([design record](superpowers/specs/2026-06-13-mosaic-reskin-design.md)).

## 1. The checklist

Before a view is "done", every box is ticked:

1. **No `max-w-*` on the view root.** The shell
   (`layouts/DefaultLayout.vue`) already caps content at `max-w-[96rem]` with
   responsive padding. A second cap inside it just wastes screen. Width is
   controlled by *content* (cards, grids, prose), never by an arbitrary outer
   wrapper. The model view is `DocumentDetailView.vue` (no root cap, internal
   two-column grid).
2. **Use a `PageHeader`.** Declare the title, the optional one-line description
   and any right-aligned primary/secondary actions there. Never hand-roll
   `<h1>`+`<p>`+buttons.

   A view's filter/control bar goes in the header's **`#controls` slot** rather
   than in a band of its own below it (§5.1).

   Note where each part lands. The **title goes to the app bar**, not to the top
   of the page body — `PageHeader` claims it through `usePageTitle` and
   `AppHeader` renders the page's one `<h1>` beside the hamburger (the standard
   contextual top-app-bar pattern). The **description stays at the top of
   `#app-page`** as a muted, measure-capped *lede*, since with no title above it
   a full-width paragraph would read as body copy. A `PageHeader` given only a
   title renders **nothing at all** — don't add an empty description to "keep
   the spacing".
3. **Primary action is reachable without scrolling.** Save / Edit / Delete /
   Cancel live in the page header (or a sticky bar), not at the bottom of a long
   form. The user should never scroll down to commit.
4. **Fill width with a responsive grid, not blank space.** Single-column forms on
   a 1500px screen are a smell. Use `grid grid-cols-1 lg:grid-cols-2` (or 3) for
   settings, metadata, and side-by-side editor/preview. Reserve narrow columns
   for genuine reading-line-length cases (`max-w-prose` on long body copy only).
5. **Let inputs breathe.** Don't pin controls to fixed pixel widths (`w-44`,
   `w-72`) in a flex bar — they wrap awkwardly and their hint text crushes. Use
   `flex-1` / `min-w-0` / responsive widths so a control's description stays on
   one line when there is space beside it.
6. **Verify at four widths.** Phone (~375px), tablet (~768px), laptop (~1280px),
   wide desktop (~1920px). The same view must look intentional at all four — not
   just "not broken". This is the consistency the design language promises.
7. **Use the Mosaic primitives, not raw markup.** Cards, buttons, forms, badges
   all have canonical classes/components (§3). Reach for those before writing new
   Tailwind.
8. **Per-machine view preferences go in `localStorage`.** Density/columns/column-
   visibility are about *this screen*, so persist them client-side (see §4), not
   in the server-side user profile.
9. **Keep dark mode working.** Every colour gets a `dark:` variant. Test the
   toggle.
10. **Keep tests green and update contracts.** Some layout facts are acceptance
    contracts (e.g. the dashboard column counts in `e2e/responsive.spec.ts`). If
    you change one, change the contract and its test deliberately.
11. **One field pattern per bar; prefer native controls.** In any filter/control
    row, every control shares the same label recipe and `.form-*` class, laid out
    with `flex flex-wrap items-end gap-3` (§5). Prefer native inputs
    (`<input type="date">`, `<select>`) over hand-rolled multi-field widgets
    where they suffice.

## 2. Why width is a per-view discipline problem

The single most common defect in the 2026-06-28 pass: views re-imposed a narrow
cap inside the already-wide shell.

As found in the 2026-06-28 pass — **all three have since been fixed**; none of
these views carries a root cap today. Kept as the worked example of the defect:

```
DefaultLayout  → max-w-[96rem]   (correct, one source of truth)
  NewNoteView  → max-w-4xl       ❌ ~57% of width used
  UploadView   → max-w-2xl       ❌ ~43%
  SettingsView → max-w-2xl       ❌ ~43%
  DocumentDetailView → (none)    ✅ 100%, content-driven two-column grid
```

The rule is simple: **the shell owns max width; the view owns content density.**
If a view feels too wide with one column, that is a signal to add a *second
column* (grid), not to clamp the whole page narrower.

## 3. The Mosaic vocabulary (use these, don't reinvent)

Defined in `assets/main.css` (`@theme` tokens) and
`assets/utility-patterns.css` (component classes):

- **Card / panel:** the `.card` class (surface + `shadow-xs` + `rounded-xl` +
  hairline border, defined once in `utility-patterns.css`); it carries **no
  padding**, so add your own `p-5` (`class="card p-5"`). Apply the class; don't
  re-spec the surface recipe per view.
- **Buttons:** `AppButton` — `variant` is `primary` (violet, the default) /
  `secondary` (gray) / `warning` (red, the destructive one) / `inverse`, and
  `size` is `sm` / `lg` or omitted, mapping to `.btn-sm` / `.btn-lg` / `.btn`.
  (`.btn-xs` exists in the CSS but `AppButton` cannot emit it and nothing in
  `frontend/src/` uses it.)
- **Forms:** `AppInput` / `AppTextarea` / `AppSelect` / `AppCheckboxes` /
  `AppRadios` (`.form-input` etc.) — label + hint + error baked in, a11y
  preserved (the error summary takes focus on mount).
- **Accent:** violet (`--color-violet-500` family). Status colours: green / red /
  yellow / sky scales.
- **Type:** Inter; headings `text-2xl md:text-3xl font-bold` for page titles.
- **Badges/pills:** `AppBadge`, `FilterPill`. **Dropdowns/menus/popovers:**
  `AppPopover` (controlled `v-model:open`, Escape/outside-click close, focus
  return, `--z-popover` token) — don't hand-roll open-state + outside-click; a
  true modal uses the native `<dialog>` (`SearchModal`) instead.
  **Empty/loading/error states:** reuse existing view patterns (e.g.
  `DocumentListView`).
- **Field rows / filter bars:** the `.filter-label` recipe + `.form-*` controls
  in a `flex flex-wrap items-end gap-3` row; prefer native `<input type="date">` /
  `<select>` over hand-rolled widgets. See §5 and the reference implementation
  `views/JobsView.vue` (`data-testid="jobs-filter-bar"`).

Full `App*` inventory: `components/app/index.ts`.

## 4. Persisting per-machine preferences

Established pattern (mirror it — don't invent a new one):

- `AppSidebar.vue` persists `library:sidebar-expanded` (the bare
  `sidebar-expanded` key is a legacy read-once fallback, not the pattern).
- `JobsView.vue` persists table column visibility under `library:jobs-columns`.
- `useAskViewMode.ts` persists the Ask transcript layout under
  `library:ask-view-mode` (`conversation` | `document`).
- `useCurrencyOptions.ts` persists the custom-currency-code list under
  `library:currency-options`. `SpendingBoardView.vue` persists a DIFFERENT
  thing — the board's own selected display currency — under
  `library:charts-board-currency`; the two are deliberately separate keys
  (one is the shared list of codes to offer, the other is which one is
  currently picked) and both are catalogued here together so a future rename
  of either doesn't drift out of step with the other.
- `@vueuse/core` `useStorage` is already a dependency — prefer it over raw
  `localStorage.getItem/setItem` for new keys.
- Naming: `library:<feature>-<thing>` (e.g. `library:doc-grid-cols`).

**Modes that only make sense on a wide screen** (the note editor's Split, the
Ask transcript's Document) store the *preference* and clamp the *render*
separately: the stored value survives a visit on a phone, while a derived
`effective*` computed falls back to the narrow layout. Do not clamp by writing
the fallback back into storage — that silently discards the user's choice the
first time they open the app on a small screen. And hide a wide-only control
with `v-if`, not a `hidden`/`lg:` utility: a CSS-hidden button stays in the tab
order and the accessibility tree.

**If a wide-only mode hides a container, relocate what the container held.**
Hiding a rail to buy back its width also removes every control inside it. The
Ask transcript's document mode hid the conversation rail — and with it the only
`lg+` "New conversation" button — and the tests still passed, because they
asserted the rail *disappeared* and never asked whether its capabilities were
still reachable. **Test the capability, not the mechanism:** assert the action
can still be performed, by a selector that does not care which container
currently hosts it. Keeping the moved control's original `data-testid` is the
cheap way to get that.

Per-machine (display-size) preferences = `localStorage`. Account-level
preferences (what *fields* show on a tile, notification settings) = server-side
user profile via the settings API. Choose by asking "is this about this screen,
or about this user everywhere?"

## 5. Field rows, filter bars, and native inputs

Filter/control bars (search toolbars, the charts control bar, list filters) are
where inconsistency shows most: several labelled controls sit side by side, so the
eye compares them directly. Use one pattern for all of them.

- **The row:** `flex flex-wrap items-end gap-3` — controls bottom-aligned so
  labels and inputs line up; wraps cleanly on narrow screens.
- **The label (identical on every control):** the `.filter-label` class
  (`block text-xs uppercase text-gray-600 dark:text-gray-300 font-semibold mb-1`,
  defined once in `utility-patterns.css`). Apply the class; don't re-spec the
  recipe per bar. Mixing label styles within one bar (one control
  `text-sm font-medium`, the next a `<legend>`) is the single thing that made the
  pre-2026-07-01 `/charts` bar look "weird" even though each control worked in
  isolation.
  - **Two scoped label recipes — don't cross them.** This uppercase-xs
    `.filter-label` is the recipe for **filter/control bars only**. Stacked
    **forms** use the *different* label baked into the `App*` input components
    (`text-sm font-medium text-gray-700`, §3) — do not hand-roll or override it.
    A filter bar is therefore built from raw `.form-input`/`.form-select` +
    `.filter-label` (as `jobs-filter-bar` and `workspace-toolbar` both do),
    **not** from `App*` form components, because those carry the stacked-form
    label. The two recipes are
    intentional: uppercase-xs reads as a compact control legend; sentence-case
    reads better down a long form.
- **The controls:** `.form-input` / `.form-select` already carry border, bg,
  `rounded-lg`, `text-base sm:text-sm`, and dark mode — add the class, don't
  re-spec padding/border per control.
- **Prefer native inputs where they suffice.** A native `<input type="date">`
  styled with `.form-input` gives a calendar popup, correct locale display, and
  accessibility for free — and is *less* code than a hand-rolled widget. The
  old `/charts` From/To fields were three cramped Day/Month/Year boxes
  (`AppDateInput`); replacing them with native date inputs matched the look and
  deleted logic. That bar is gone, but the shape it settled on survives verbatim
  in `SpendingWorkspaceView.vue`'s `workspace-toolbar` — two `<input type="date">`
  fields carrying `.form-input`, each with a `.filter-label` above it. Reach
  for a bespoke multi-field control only when the native one genuinely can't do the
  job (`AppDateInput` remains for partial-date entry, e.g. `DocumentFilterBar`).

### 5.1 The bar belongs in the header, not in a band below it

Pass the bar to `PageHeader`'s **`#controls`** slot. The header then renders one
toolbar — **view-state controls left, page commands right** — instead of a
mostly-empty actions row above a mostly-empty filter row:

```vue
<PageHeader title="Jobs">
  <template #controls><div class="flex flex-wrap items-end gap-3">…</div></template>
  <template #actions><AppButton>Columns</AppButton></template>
</PageHeader>
```

Three rules, each of which cost something to learn:

1. **The merge is a container query (`@5xl`), never a viewport one.** The
   content column is the viewport minus a sidebar the user collapses
   independently, so the same viewport width offers different amounts of room.
   Measured on `/charts/legacy` on 2026-08-22 and re-measured on `/jobs` on
   2026-08-31, with identical numbers — the `@5xl` threshold lives on
   `PageHeader`'s own container, not on the view filling its slots:

   | viewport | sidebar | `#app-page` | merged? |
   |---|---|---|---|
   | 1280 | expanded | 1024 | no |
   | 1280 | collapsed | 1200 | **yes** |

   No `lg:` rule can produce that row. `e2e/header-toolbar.spec.ts` asserts both
   halves, and goes red if the container query is swapped for a viewport one. It
   drives `/jobs`: it used to drive `/charts/legacy`, and that route went with
   the series stack on 2026-08-31.
2. **Below the threshold the two groups stack**, reproducing the pre-slot
   layout. The merge is a wide-screen gain and a phone no-op. DOM order is
   visual order at both widths — controls, then actions — so focus order never
   disagrees with the screen.
3. **The row is `items-end`.** A lede-and-buttons row aligns on centres, but a
   row of labelled fields aligns on the *inputs'* bottom edge, and the buttons
   join that edge. `PageHeader` switches between the two on the slot's presence.

This is also why §5's one-recipe-per-bar rule got sharper: the bar now shares a
row with the header's own controls, so a second label recipe is visible side by
side rather than a band apart. `/jobs` was rebuilt from `AppSelect`/`AppInput`
to raw `.form-*` + `.filter-label` for exactly that reason, and its document
field's hint became a placeholder — a hint line under one field breaks the row's
shared bottom edge.

**Reference implementation:** `views/JobsView.vue`'s `jobs-filter-bar`. It is the
bar to copy because it is the one this section's geometry is asserted against —
it fills **both** slots (`jobs-filter-bar` in `#controls`, `jobs-show-system` /
`jobs-columns-button` in `#actions`), which is what makes the merge observable at
all. For the native-date half of §5, see `SpendingWorkspaceView.vue`'s
`workspace-toolbar`; it is `#controls`-only, so it cannot demonstrate the merge,
but it is the closest surviving descendant of the bar that established the
recipe. The original reference, `components/charts/ChartControls.vue`
(2026-07-01), was deleted with the legacy series stack on 2026-08-31 — the recipe
is unchanged, only its exemplar moved. The sister project `journal/webapp` (same
Mosaic stack) uses the identical pattern in its Search view; when a Library bar
looks off, compare against it.

**Why this holds.** The design language's quality is *systemic*, not per-view
inspiration: tokens defined once (`@theme` in `main.css`), a small shared CSS
component layer (`utility-patterns.css`), one naming vocabulary (`data-testid`),
all enforced by lint/format/coverage gates. You get "right the first time" by
*applying* the system, not re-deciding padding, colour, and radius each view.
Because the filter-bar specs assert on `data-testid` (not classes), a bar can be
fully restyled without breaking contracts — so consistency is cheap to maintain.
(This is the rule for *filter bars*, not the whole suite: plenty of `App*`
component specs do assert on classes, deliberately, because the class **is** the
contract there.)

## 6. Which error surface to use

Errors are the other place inconsistency creeps in — the app has four ways to
show one, and using the wrong one (or hand-rolling a fifth) is what made error
states feel arbitrary view-to-view. Pick by *where the error belongs*, don't
hand-roll a red `border-l-4` box:

- **Form-submit validation** (a `POST`/`PUT` the user just triggered) →
  `AppErrorSummary`. It focuses itself on mount and links to the offending
  field, so keyboard/screen-reader users land on the problem.
- **A page/section failed to load, or a section-level status** →
  `AppBanner` (`variant="error"`). One banner at the top of the section, e.g. a
  charts grid or an admin panel that couldn't fetch.
- **Background/async outcome** (something that finished while the user was
  elsewhere — an upload, a job) → the `notifications` toast store.
- **A single field is invalid** → the `errorMessage` prop already baked into the
  `App*` inputs — not a separate element.

Row-scoped inline errors inside a dense CRUD table (e.g. the admin taxonomy
panels) are the one deliberate exception — they stay next to their row.

## 7. When you add a new view

1. Start from `ProjectsListView.vue` (index) or `SettingsView.vue` (tabbed
   detail) as the structural template: `PageHeader`, no root cap, content in a
   responsive grid. **Not `DocumentDetailView.vue`** — it predates `PageHeader`
   and hand-rolls its own `<h1>`s, so copying it reproduces exactly the defect
   §1 tells you to avoid. Its *layout* (no root cap, `grid-cols-1
   lg:grid-cols-2`) is still the reference; its header is not.
2. Drop in `PageHeader` with the title, one-line description, and actions —
   remembering the title surfaces in the app bar (§1.2). A view with its own
   hero title (document detail) deliberately claims none, so the bar stays empty
   rather than naming a section you are not on.
3. Lay out content as cards in a responsive grid; default to filling the width.
4. Wire any per-machine preference through `localStorage` (§4).
5. Add/adjust unit tests; if you touch a responsive contract, update the e2e
   spec.
6. Eyeball all four widths and the dark-mode toggle before calling it done.
