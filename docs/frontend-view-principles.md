# Frontend view design principles

How to build a Library view that looks **right the first time** — using the
Mosaic design language already in the app. This is a checklist plus the reasoning
behind it. If you follow §1 you will avoid every layout problem found in the
2026-06-28 UX pass. Companion docs: [frontend.md](frontend.md) (architecture,
shell, `App*` components), and the Mosaic reskin design record
([archive](superpowers/specs/2026-06-13-mosaic-reskin-design.md)).

## 1. The checklist

Before a view is "done", every box is ticked:

1. **No `max-w-*` on the view root.** The shell
   (`layouts/DefaultLayout.vue`) already caps content at `max-w-[96rem]` with
   responsive padding. A second cap inside it just wastes screen. Width is
   controlled by *content* (cards, grids, prose), never by an arbitrary outer
   wrapper. The model view is `DocumentDetailView.vue` (no root cap, internal
   two-column grid).
2. **Use a `PageHeader`.** Title + description + right-aligned primary/secondary
   actions, full width, at the top. Never hand-roll `<h1>`+`<p>`+buttons.
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
- **Buttons:** `AppButton` (`.btn` / `.btn-sm` / `.btn-lg` / `.btn-xs`), violet
  primary, gray secondary, red destructive.
- **Forms:** `AppInput` / `AppTextarea` / `AppSelect` / `AppCheckboxes` /
  `AppRadios` (`.form-input` etc.) — label + hint + error baked in, a11y
  preserved (error-summary focus, 44px targets).
- **Accent:** violet (`--color-violet-500` family). Status colours: green / red /
  yellow / sky scales.
- **Type:** Inter; headings `text-2xl md:text-3xl font-bold` for page titles.
- **Badges/pills:** `AppBadge`, `FilterPill`. **Empty/loading/error states:**
  reuse existing view patterns (e.g. `DocumentListView`).
- **Field rows / filter bars:** the `.filter-label` recipe + `.form-*` controls
  in a `flex flex-wrap items-end gap-3` row; prefer native `<input type="date">` /
  `<select>` over hand-rolled widgets. See §5 and the reference implementation
  `components/charts/ChartControls.vue`.

Full `App*` inventory: `components/app/index.ts`.

## 4. Persisting per-machine preferences

Established pattern (mirror it — don't invent a new one):

- `AppSidebar.vue` persists `sidebar-expanded`.
- `JobsView.vue` persists table column visibility under `library:jobs-columns`.
- `@vueuse/core` `useStorage` is already a dependency — prefer it over raw
  `localStorage.getItem/setItem` for new keys.
- Naming: `library:<feature>-<thing>` (e.g. `library:doc-grid-cols`).

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
    `.filter-label` (as `ChartControls` does), **not** from `App*` form
    components, because those carry the stacked-form label. The two recipes are
    intentional: uppercase-xs reads as a compact control legend; sentence-case
    reads better down a long form.
- **The controls:** `.form-input` / `.form-select` already carry border, bg,
  `rounded-lg`, `text-base sm:text-sm`, and dark mode — add the class, don't
  re-spec padding/border per control.
- **Prefer native inputs where they suffice.** A native `<input type="date">`
  styled with `.form-input` gives a calendar popup, correct locale display, and
  accessibility for free — and is *less* code than a hand-rolled widget. The
  `/charts` From/To fields were three cramped Day/Month/Year boxes (`AppDateInput`);
  replacing them with native date inputs matched the look and deleted logic. Reach
  for a bespoke multi-field control only when the native one genuinely can't do the
  job (`AppDateInput` remains for partial-date entry, e.g. `DocumentFilterBar`).

**Reference implementation:** `components/charts/ChartControls.vue` (2026-07-01).
The sister project `journal/webapp` (same Mosaic stack) uses the identical pattern
in its Search view; when a Library bar looks off, compare against it.

**Why this holds.** The design language's quality is *systemic*, not per-view
inspiration: tokens defined once (`@theme` in `main.css`), a small shared CSS
component layer (`utility-patterns.css`), one naming vocabulary (`data-testid`),
all enforced by lint/format/coverage gates. You get "right the first time" by
*applying* the system, not re-deciding padding, colour, and radius each view.
Because tests assert on `data-testid` (not classes), a bar can be fully restyled
without breaking contracts — so consistency is cheap to maintain.

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

1. Start from `DocumentDetailView.vue` as the structural template (header,
   no root cap, responsive grid).
2. Drop in `PageHeader` with the title, one-line description, and actions.
3. Lay out content as cards in a responsive grid; default to filling the width.
4. Wire any per-machine preference through `localStorage` (§4).
5. Add/adjust unit tests; if you touch a responsive contract, update the e2e
   spec.
6. Eyeball all four widths and the dark-mode toggle before calling it done.
