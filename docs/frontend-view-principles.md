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

- **Card / panel:** `bg-white dark:bg-gray-800 shadow-xs rounded-xl border
  border-gray-200 dark:border-gray-700/60 p-5`.
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

## 5. When you add a new view

1. Start from `DocumentDetailView.vue` as the structural template (header,
   no root cap, responsive grid).
2. Drop in `PageHeader` with the title, one-line description, and actions.
3. Lay out content as cards in a responsive grid; default to filling the width.
4. Wire any per-machine preference through `localStorage` (§4).
5. Add/adjust unit tests; if you touch a responsive contract, update the e2e
   spec.
6. Eyeball all four widths and the dark-mode toggle before calling it done.
