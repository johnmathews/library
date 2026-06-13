# Mosaic Frontend Reskin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the library frontend's GOV.UK (`govuk-frontend`) theme with the Mosaic design language — Tailwind 4, Inter, violet accent, soft cards, dark mode, and a collapsible left-sidebar + top-header shell — by replicating the proven recipe from the sibling `journal-insights` webapp.

**Architecture:** Presentation-layer swap only. The FastAPI backend, REST contracts, `src/api/` client layer, Pinia stores, router *logic*, and auth/session/CSRF behaviour are untouched. We rip out `govuk-frontend`/SCSS, install Tailwind 4 (CSS-first, no config file), port the Mosaic shell from journal, rebuild the 17 `Gov*` form wrappers as Mosaic-styled `App*` components that **preserve the existing props/emits**, then reskin the 6 views. Tests are the regression gate.

**Tech Stack:** Vue 3.5 (`<script setup lang="ts">`), Vite 8, `@tailwindcss/vite` 4, `@tailwindcss/forms`, `@vueuse/core` (dark mode), Pinia 3, vue-router 5, Vitest 4 (jsdom), Playwright.

**Reference sources (read these — they are the source of truth to copy/adapt):**
- Journal repo: `/Users/john/projects/journal/webapp/`
  - `src/assets/main.css`, `src/assets/utility-patterns.css`
  - `src/components/layout/{AppSidebar,AppHeader,ThemeToggle}.vue`
  - `src/layouts/DefaultLayout.vue`, `src/App.vue`, `vite.config.ts`, `index.html`
- The full content of those files is embedded inline in the tasks below.
- Approved spec: `docs/superpowers/specs/2026-06-13-mosaic-reskin-design.md`

**Branch:** Work happens on `mosaic-reskin` (already created and checked out; the spec commit `ff03784` is its first commit). All commits below land on this branch.

**Convention note:** New styles live in `src/assets/` (journal's layout), not the old `src/styles/`. New shell components live in `src/components/layout/`. New form components live in `src/components/app/` with a barrel `src/components/app/index.ts`, mirroring the old `src/components/govuk/index.ts`.

**Working directory for all commands:** `/Users/john/projects/syncthing/agent-lxc/library/frontend`

---

## Phase 0 — Build & styling foundation

Goal of this phase: the app boots on Vite with Tailwind 4 and the Mosaic tokens, with govuk fully removed from the build pipeline. The app will look broken (unstyled `Gov*` components) until Phase 1–3 — that's expected. The gate for this phase is "dev server boots, `vite build` succeeds for the new CSS, no govuk/sass in the dependency graph."

### Task 1: Swap dependencies

**Files:**
- Modify: `package.json`

- [ ] **Step 1: Edit `package.json` dependencies**

Remove from `dependencies`: `@fontsource/inter`, `govuk-frontend`.
Add to `dependencies`: `"@vueuse/core": "^12.5.0"`.
Remove from `devDependencies`: `sass-embedded`.
Add to `devDependencies`: `"@tailwindcss/vite": "^4.0.2"`, `"@tailwindcss/forms": "^0.5.10"`, `"tailwindcss": "^4.0.2"`.

Resulting `dependencies` block:

```json
  "dependencies": {
    "@vueuse/core": "^12.5.0",
    "pinia": "^3.0.4",
    "vue": "^3.5.32",
    "vue-router": "^5.0.4"
  },
```

Add the three Tailwind devDependencies alphabetically into `devDependencies` and delete the `sass-embedded` line.

- [ ] **Step 2: Install**

Run: `npm install`
Expected: completes with no errors; `node_modules/@tailwindcss/vite` and `node_modules/@vueuse/core` exist; `node_modules/govuk-frontend` is gone.

- [ ] **Step 3: Commit**

```bash
git add package.json package-lock.json
git commit -m "build: swap govuk-frontend/sass for tailwind 4 + vueuse"
```

### Task 2: Rewire `vite.config.ts`

**Files:**
- Modify: `vite.config.ts`

- [ ] **Step 1: Replace the file**

The old `css.preprocessorOptions.scss` and `css.lightningcss.errorRecovery` blocks existed only to tolerate govuk's SCSS — remove them. Add the `@tailwindcss/vite` plugin. Keep the existing dev proxy (`/api` → :8000, `/healthz` → :8000) and the `@` alias. Keep `vite-plugin-vue-devtools` (already a devDependency).

```ts
import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/healthz': 'http://localhost:8000',
    },
  },
})
```

- [ ] **Step 2: Commit**

```bash
git add vite.config.ts
git commit -m "build: add @tailwindcss/vite, drop govuk scss config"
```

### Task 3: Add Mosaic design tokens (`main.css` + `utility-patterns.css`)

**Files:**
- Create: `src/assets/main.css`
- Create: `src/assets/utility-patterns.css`
- Modify: `src/main.ts`
- Delete: `src/styles/main.scss` (in Task 27, after views stop importing it — for now just stop importing it)

- [ ] **Step 1: Create `src/assets/utility-patterns.css`** — copy verbatim from journal's `src/assets/utility-patterns.css`:

```css
/* Typography */
.h1 {
  @apply text-4xl font-extrabold tracking-tighter;
}

.h2 {
  @apply text-3xl font-extrabold tracking-tighter;
}

.h3 {
  @apply text-3xl font-extrabold;
}

.h4 {
  @apply text-2xl font-extrabold tracking-tight;
}

@media (width >= theme(--breakpoint-md)) {
  .h1 {
    @apply text-5xl;
  }

  .h2 {
    @apply text-4xl;
  }
}

/* Buttons */
.btn,
.btn-lg,
.btn-sm,
.btn-xs {
  @apply font-medium text-sm inline-flex items-center justify-center border border-transparent rounded-lg leading-5 shadow-xs transition;
}

.btn {
  @apply px-3 py-2;
}

.btn-lg {
  @apply px-4 py-3;
}

.btn-sm {
  @apply px-2 py-1;
}

.btn-xs {
  @apply px-2 py-0.5;
}

/* Forms */
input[type='search']::-webkit-search-decoration,
input[type='search']::-webkit-search-cancel-button,
input[type='search']::-webkit-search-results-button,
input[type='search']::-webkit-search-results-decoration {
  -webkit-appearance: none;
}

.form-input,
.form-textarea,
.form-multiselect,
.form-select,
.form-checkbox,
.form-radio {
  @apply bg-white dark:bg-gray-900/30 border focus:ring-0 focus:ring-offset-0 dark:disabled:bg-gray-700/30 dark:disabled:border-gray-700 dark:disabled:hover:border-gray-700;
}

.form-checkbox {
  @apply rounded-sm;
}

.form-input,
.form-textarea,
.form-multiselect,
.form-select {
  @apply text-base sm:text-sm text-gray-800 dark:text-gray-100 leading-5 py-2 px-3 border-gray-200 hover:border-gray-300 focus:border-gray-300 dark:border-gray-700/60 dark:hover:border-gray-600 dark:focus:border-gray-600 shadow-xs rounded-lg;
}

.form-input,
.form-textarea {
  @apply placeholder-gray-400 dark:placeholder-gray-500;
}

.form-select {
  @apply pr-10;
}

.form-checkbox,
.form-radio {
  @apply text-violet-500 checked:bg-violet-500 checked:border-transparent border border-gray-300 dark:border-gray-700/60 dark:checked:border-transparent focus-visible:not-checked:ring-2 focus-visible:not-checked:ring-violet-500/50;
}

/* Switch element */
.form-switch {
  @apply relative select-none;
  width: 44px;
}

.form-switch label {
  @apply block overflow-hidden cursor-pointer h-6 rounded-full;
}

.form-switch label > span:first-child {
  @apply absolute block rounded-full;
  width: 20px;
  height: 20px;
  top: 2px;
  left: 2px;
  right: 50%;
  transition: all 0.15s ease-out;
}

.form-switch input[type='checkbox'] + label {
  @apply bg-gray-400 dark:bg-gray-700;
}

.form-switch input[type='checkbox']:checked + label {
  @apply bg-violet-500;
}

.form-switch input[type='checkbox']:checked + label > span:first-child {
  left: 22px;
}

.form-switch input[type='checkbox']:disabled + label {
  @apply cursor-not-allowed bg-gray-100 dark:bg-gray-700/20 border border-gray-200 dark:border-gray-700/60;
}

.form-switch input[type='checkbox']:disabled + label > span:first-child {
  @apply bg-gray-400 dark:bg-gray-600;
}

/* Chrome, Safari and Opera */
.no-scrollbar::-webkit-scrollbar {
  display: none;
}

.no-scrollbar {
  -ms-overflow-style: none; /* IE and Edge */
  scrollbar-width: none; /* Firefox */
}
```

- [ ] **Step 2: Create `src/assets/main.css`** — copy from journal's `src/assets/main.css`, but **drop the `fuchsia` palette block** (journal-only, used by its chunk-overlay feature which library doesn't have). Keep everything else identical:

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=fallback');

@import 'tailwindcss';
@import './utility-patterns.css' layer(components);

@plugin "@tailwindcss/forms" {
  strategy: base;
}

@custom-variant dark (&:is(.dark *));
@custom-variant sidebar-expanded (&:is(.sidebar-expanded *));

@theme {
  --shadow-sm: 0 1px 1px 0 rgb(0 0 0 / 0.05), 0 1px 2px 0 rgb(0 0 0 / 0.02);

  --color-gray-50: #f9fafb;
  --color-gray-100: #f3f4f6;
  --color-gray-200: #e5e7eb;
  --color-gray-300: #bfc4cd;
  --color-gray-400: #9ca3af;
  --color-gray-500: #6b7280;
  --color-gray-600: #4b5563;
  --color-gray-700: #374151;
  --color-gray-800: #1f2937;
  --color-gray-900: #111827;
  --color-gray-950: #030712;

  --color-violet-50: #f1eeff;
  --color-violet-100: #e6e1ff;
  --color-violet-200: #d2cbff;
  --color-violet-300: #b7acff;
  --color-violet-400: #9c8cff;
  --color-violet-500: #8470ff;
  --color-violet-600: #755ff8;
  --color-violet-700: #5d47de;
  --color-violet-800: #4634b1;
  --color-violet-900: #2f227c;
  --color-violet-950: #1c1357;

  --color-sky-50: #e3f3ff;
  --color-sky-100: #d1ecff;
  --color-sky-200: #b6e1ff;
  --color-sky-300: #a0d7ff;
  --color-sky-400: #7bc8ff;
  --color-sky-500: #67bfff;
  --color-sky-600: #56b1f3;
  --color-sky-700: #3193da;
  --color-sky-800: #1c71ae;
  --color-sky-900: #124d79;
  --color-sky-950: #0b324f;

  --color-green-50: #d2ffe2;
  --color-green-100: #b1fdcd;
  --color-green-200: #8bf0b0;
  --color-green-300: #67e294;
  --color-green-400: #4bd37d;
  --color-green-500: #3ec972;
  --color-green-600: #34bd68;
  --color-green-700: #239f52;
  --color-green-800: #15773a;
  --color-green-900: #0f5429;
  --color-green-950: #0a3f1e;

  --color-red-50: #ffe8e8;
  --color-red-100: #ffd1d1;
  --color-red-200: #ffb2b2;
  --color-red-300: #ff9494;
  --color-red-400: #ff7474;
  --color-red-500: #ff5656;
  --color-red-600: #fa4949;
  --color-red-700: #e63939;
  --color-red-800: #c52727;
  --color-red-900: #941818;
  --color-red-950: #600f0f;

  --color-yellow-50: #fff2c9;
  --color-yellow-100: #ffe7a0;
  --color-yellow-200: #ffe081;
  --color-yellow-300: #ffd968;
  --color-yellow-400: #f7cd4c;
  --color-yellow-500: #f0bb33;
  --color-yellow-600: #dfad2b;
  --color-yellow-700: #bc9021;
  --color-yellow-800: #816316;
  --color-yellow-900: #4f3d0e;
  --color-yellow-950: #342809;

  --font-inter: 'Inter', 'sans-serif';

  --text-xs: 0.75rem;
  --text-xs--line-height: 1.5;
  --text-sm: 0.875rem;
  --text-sm--line-height: 1.5715;
  --text-base: 1rem;
  --text-base--line-height: 1.5;
  --text-base--letter-spacing: -0.01em;
  --text-lg: 1.125rem;
  --text-lg--line-height: 1.5;
  --text-lg--letter-spacing: -0.01em;
  --text-xl: 1.25rem;
  --text-xl--line-height: 1.5;
  --text-xl--letter-spacing: -0.01em;
  --text-2xl: 1.5rem;
  --text-2xl--line-height: 1.33;
  --text-2xl--letter-spacing: -0.01em;
  --text-3xl: 1.88rem;
  --text-3xl--line-height: 1.33;
  --text-3xl--letter-spacing: -0.01em;
  --text-4xl: 2.25rem;
  --text-4xl--line-height: 1.25;
  --text-4xl--letter-spacing: -0.02em;
  --text-5xl: 3rem;
  --text-5xl--line-height: 1.25;
  --text-5xl--letter-spacing: -0.02em;
  --text-6xl: 3.75rem;
  --text-6xl--line-height: 1.2;
  --text-6xl--letter-spacing: -0.02em;

  --breakpoint-xs: 480px;
}

/*
  The default border color has changed to `currentColor` in Tailwind CSS v4,
  so we add these compatibility styles to match the previous default behavior.
*/
@layer base {
  *,
  ::after,
  ::before,
  ::backdrop,
  ::file-selector-button {
    border-color: var(--color-gray-200, currentColor);
  }

  html {
    @apply font-inter antialiased;
  }

  body {
    @apply bg-gray-100 dark:bg-gray-900 text-gray-600 dark:text-gray-400;
  }
}
```

- [ ] **Step 3: Update `src/main.ts`** — drop the four `@fontsource/inter` imports and the `./styles/main.scss` import; add the new CSS import. Result:

```ts
import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)

app.mount('#app')
```

- [ ] **Step 4: Verify dev server boots**

Run: `npm run dev` (then Ctrl-C after it prints the local URL)
Expected: Vite starts with no CSS/import errors. (Page will look unstyled — fine.)

- [ ] **Step 5: Commit**

```bash
git add src/assets/main.css src/assets/utility-patterns.css src/main.ts
git commit -m "feat: add Mosaic design tokens and utility patterns"
```

### Task 4: Update `index.html`

**Files:**
- Modify: `index.html`

- [ ] **Step 1: Replace the govuk template scaffolding with the Mosaic body seed.**

Remove `class="govuk-template"` from `<html>`, remove `class="govuk-template__body"` and the govuk JS-enabled script from `<body>`, and add the `sidebar-expanded` localStorage seed script (from journal's `index.html`). Update `theme-color` to the Mosaic light background `#f3f4f6` (gray-100). Keep favicon/manifest/apple-touch links and the description meta.

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="description" content="Self-hosted personal document archive: scan, search and file the family paperwork.">
    <meta name="theme-color" content="#f3f4f6">
    <link rel="icon" href="/favicon.ico" sizes="48x48">
    <link rel="icon" href="/favicon.svg" type="image/svg+xml">
    <link rel="apple-touch-icon" href="/apple-touch-icon.png">
    <link rel="manifest" href="/manifest.webmanifest">
    <title>Library</title>
  </head>
  <body>
    <script>
      if (localStorage.getItem('sidebar-expanded') == 'true') {
        document.querySelector('body').classList.add('sidebar-expanded');
      } else {
        document.querySelector('body').classList.remove('sidebar-expanded');
      }
    </script>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 2: Check `src/__tests__/pwa.spec.ts`** for an assertion on `theme-color` `#0b0c0c` or `govuk-template`. If present, update the expected value to `#f3f4f6` (and remove any govuk-template assertion). Also update `public/manifest.webmanifest`'s `theme_color`/`background_color` if they reference `#0b0c0c` → `#f3f4f6`.

Run: `npx vitest run src/__tests__/pwa.spec.ts`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add index.html public/manifest.webmanifest src/__tests__/pwa.spec.ts
git commit -m "feat: Mosaic html shell + sidebar-expanded seed"
```

---

## Phase 1 — The shell (sidebar + header + layout)

Goal: authenticated routes render inside the Mosaic shell; `/login` renders bare. After this phase the chrome is Mosaic even though page bodies still use `Gov*` components.

### Task 5: `ThemeToggle`

**Files:**
- Create: `src/components/layout/ThemeToggle.vue`
- Test: `src/components/layout/__tests__/ThemeToggle.spec.ts`

- [ ] **Step 1: Write the test**

```ts
import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ThemeToggle from '../ThemeToggle.vue'

describe('ThemeToggle', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark')
  })

  it('toggles the dark class on <html> when checked', async () => {
    const wrapper = mount(ThemeToggle)
    const input = wrapper.get('input[type="checkbox"]')
    await input.setValue(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
```

- [ ] **Step 2: Run it — expect FAIL** (`Cannot find module '../ThemeToggle.vue'`)

Run: `npx vitest run src/components/layout/__tests__/ThemeToggle.spec.ts`

- [ ] **Step 3: Create the component** — copy journal's `src/components/layout/ThemeToggle.vue` verbatim (full content):

```vue
<script setup lang="ts">
import { useDark } from '@vueuse/core'

const isDark = useDark({ selector: 'html' })
</script>

<template>
  <div>
    <input
      id="light-switch"
      v-model="isDark"
      type="checkbox"
      name="light-switch"
      class="light-switch sr-only"
    />
    <label
      for="light-switch"
      class="flex items-center justify-center cursor-pointer w-10 h-10 hover:bg-gray-100 lg:hover:bg-gray-200 dark:hover:bg-gray-700/50 dark:lg:hover:bg-gray-800 rounded-full"
    >
      <svg
        class="dark:hidden fill-current text-gray-500/80 dark:text-gray-400/80"
        width="22"
        height="22"
        viewBox="0 0 16 16"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M8 0a1 1 0 0 1 1 1v.5a1 1 0 1 1-2 0V1a1 1 0 0 1 1-1Z" />
        <path d="M12 8a4 4 0 1 1-8 0 4 4 0 0 1 8 0Zm-4 2a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" />
        <path d="M13.657 3.757a1 1 0 0 0-1.414-1.414l-.354.354a1 1 0 0 0 1.414 1.414l.354-.354ZM13.5 8a1 1 0 0 1 1-1h.5a1 1 0 1 1 0 2h-.5a1 1 0 0 1-1-1ZM13.303 11.889a1 1 0 0 0-1.414 1.414l.354.354a1 1 0 0 0 1.414-1.414l-.354-.354ZM8 13.5a1 1 0 0 1 1 1v.5a1 1 0 1 1-2 0v-.5a1 1 0 0 1 1-1ZM4.111 13.303a1 1 0 1 0-1.414-1.414l-.354.354a1 1 0 1 0 1.414 1.414l.354-.354ZM0 8a1 1 0 0 1 1-1h.5a1 1 0 0 0 0 2H1a1 1 0 0 1-1-1ZM3.757 2.343a1 1 0 1 0-1.414 1.414l.354.354A1 1 0 1 0 4.11 2.697l-.354-.354Z" />
      </svg>
      <svg
        class="hidden dark:block fill-current text-gray-500/80 dark:text-gray-400/80"
        width="22"
        height="22"
        viewBox="0 0 16 16"
        xmlns="http://www.w3.org/2000/svg"
      >
        <path d="M11.875 4.375a.625.625 0 1 0 1.25 0c.001-.69.56-1.249 1.25-1.25a.625.625 0 1 0 0-1.25 1.252 1.252 0 0 1-1.25-1.25.625.625 0 1 0-1.25 0 1.252 1.252 0 0 1-1.25 1.25.625.625 0 1 0 0 1.25c.69.001 1.249.56 1.25 1.25Z" />
        <path d="M7.019 1.985a1.55 1.55 0 0 0-.483-1.36 1.44 1.44 0 0 0-1.53-.277C2.056 1.553 0 4.5 0 7.9 0 12.352 3.648 16 8.1 16c3.407 0 6.246-2.058 7.51-4.963a1.446 1.446 0 0 0-.25-1.55 1.554 1.554 0 0 0-1.372-.502c-4.01.552-7.539-2.987-6.97-7ZM2 7.9C2 5.64 3.193 3.664 4.961 2.6 4.82 7.245 8.72 11.158 13.36 11.04 12.265 12.822 10.341 14 8.1 14 4.752 14 2 11.248 2 7.9Z" />
      </svg>
      <span class="sr-only">Switch to light / dark mode</span>
    </label>
  </div>
</template>
```

- [ ] **Step 4: Run the test — expect PASS**

Run: `npx vitest run src/components/layout/__tests__/ThemeToggle.spec.ts`

- [ ] **Step 5: Commit**

```bash
git add src/components/layout/ThemeToggle.vue src/components/layout/__tests__/ThemeToggle.spec.ts
git commit -m "feat: ThemeToggle (dark mode) ported from Mosaic"
```

### Task 6: `AppSidebar`

**Files:**
- Create: `src/components/layout/AppSidebar.vue`
- Test: `src/components/layout/__tests__/AppSidebar.spec.ts`

Base this on journal's `src/components/layout/AppSidebar.vue` (read it in full). Keep ALL of its `<script setup>` logic verbatim (localStorage `sidebar-expanded`, `defaultSidebarExpanded()` matchMedia guard, click-outside/ESC handlers, `close-sidebar` on route change, the expand/collapse button). The ONLY changes are in the template:
1. Replace the logo text `JOURNAL INSIGHTS TOOL` with `LIBRARY`.
2. Remove `import { useAuthStore }` and the Admin link block (library has no admin role).
3. Replace journal's nav `<li>` items with library's four destinations, reusing journal's exact `<RouterLink v-slot custom>` + `<li>` + `<a>` markup pattern (the gradient-active-state classes, the `lg:opacity-0 lg:sidebar-expanded:opacity-100` label classes). Library nav:

| Label | `to` | active match | `data-testid` | icon (16×16 path `d`) |
|---|---|---|---|---|
| Documents | `/` | `isActive` (covers `/documents/:id`) | `sidebar-documents-link` | `M1 3a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H2a1 1 0 0 1-1-1Zm0 5a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H2a1 1 0 0 1-1-1Zm1 4a1 1 0 1 0 0 2h12a1 1 0 1 0 0-2H2Z` |
| Upload | `/upload` | `isActive` | `sidebar-upload-link` | `M8 0a1 1 0 0 1 .7.3l4 4-1.4 1.4L9 3.4V11H7V3.4L4.7 5.7 3.3 4.3l4-4A1 1 0 0 1 8 0ZM1 13h14v2H1z` |
| Settings | `/settings` | `isActive` | `sidebar-settings-link` | (journal's settings gear path — copy verbatim from journal AppSidebar's Settings link) |

> Note: use `isActive` (not `isExactActive`) for Documents so `/documents/:id` highlights it. Drop journal's Search/Entries/Fitness/Storylines/Entities/Jobs/API-keys/Admin links — Search stays in the header (Task 7), not the sidebar.

- [ ] **Step 1: Write the test**

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import AppSidebar from '../AppSidebar.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'documents', component: { template: '<div/>' } },
    { path: '/upload', name: 'upload', component: { template: '<div/>' } },
    { path: '/settings', name: 'settings', component: { template: '<div/>' } },
  ],
})

describe('AppSidebar', () => {
  it('renders the three library nav links', async () => {
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    expect(wrapper.find('[data-testid="sidebar-documents-link"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="sidebar-upload-link"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="sidebar-settings-link"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('LIBRARY')
  })
})
```

- [ ] **Step 2: Run it — expect FAIL** (module not found)

Run: `npx vitest run src/components/layout/__tests__/AppSidebar.spec.ts`

- [ ] **Step 3: Create `AppSidebar.vue`** per the recipe above (journal script verbatim, three nav items, `LIBRARY` logo, no admin/auth import).

- [ ] **Step 4: Run the test — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/components/layout/AppSidebar.vue src/components/layout/__tests__/AppSidebar.spec.ts
git commit -m "feat: AppSidebar with library nav (Documents/Upload/Settings)"
```

### Task 7: `AppHeader`

**Files:**
- Create: `src/components/layout/AppHeader.vue`
- Test: `src/components/layout/__tests__/AppHeader.spec.ts`

Base on journal's `AppHeader.vue`. Adaptations:
1. Remove `AppNotifications` import/usage and the admin `RouterLink` (library has neither).
2. Add a **search trigger button** (the `/`-shortcut entry point) to the left of `ThemeToggle`, emitting an `open-search` event so the parent (`DefaultLayout`) can open the `SearchModal`. Use a magnifier SVG button styled `w-8 h-8 flex items-center justify-center bg-white dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700/50 rounded-full`.
3. The user menu shows `auth.user?.username` (library `User` has `username`, not `displayName`/`email`). Read `src/stores/auth.ts` to confirm the field name; use the real field. Keep the Settings link and a **Sign Out** button that calls `auth.logout()` then `router.push({ name: 'login' })`.

Props: `{ sidebarOpen: boolean }`. Emits: `{ 'toggle-sidebar': []; 'open-search': [] }`.

- [ ] **Step 1: Write the test**

```ts
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import AppHeader from '../AppHeader.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'documents', component: { template: '<div/>' } },
    { path: '/settings', name: 'settings', component: { template: '<div/>' } },
    { path: '/login', name: 'login', component: { template: '<div/>' } },
  ],
})

describe('AppHeader', () => {
  it('emits open-search when the search button is clicked', async () => {
    setActivePinia(createPinia())
    await router.isReady()
    const wrapper = mount(AppHeader, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    await wrapper.get('[data-testid="header-search-button"]').trigger('click')
    expect(wrapper.emitted('open-search')).toBeTruthy()
  })

  it('emits toggle-sidebar from the hamburger', async () => {
    setActivePinia(createPinia())
    await router.isReady()
    const wrapper = mount(AppHeader, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    await wrapper.get('[aria-controls="sidebar"]').trigger('click')
    expect(wrapper.emitted('toggle-sidebar')).toBeTruthy()
  })
})
```

- [ ] **Step 2: Run it — expect FAIL**
- [ ] **Step 3: Create `AppHeader.vue`** per recipe. Give the search button `data-testid="header-search-button"` and `@click="$emit('open-search')"`.
- [ ] **Step 4: Run the test — expect PASS**
- [ ] **Step 5: Commit**

```bash
git add src/components/layout/AppHeader.vue src/components/layout/__tests__/AppHeader.spec.ts
git commit -m "feat: AppHeader with search trigger + user menu"
```

### Task 8: `DefaultLayout`

**Files:**
- Create: `src/layouts/DefaultLayout.vue`

Adapt journal's `DefaultLayout.vue`: drop `AppToast` and `FitnessAuthBanner` (library has neither). Own `sidebarOpen` state. Mount `SearchModal` here and wire `AppHeader`'s `open-search` to it, and keep the global `/`-key shortcut behaviour by letting `SearchModal` manage its own key listener (it already does — see existing `src/components/SearchModal.vue`).

- [ ] **Step 1: Create the file**

```vue
<script setup lang="ts">
import { ref } from 'vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import AppHeader from '@/components/layout/AppHeader.vue'
import SearchModal from '@/components/SearchModal.vue'

const sidebarOpen = ref(false)
const searchModal = ref<InstanceType<typeof SearchModal> | null>(null)
</script>

<template>
  <div class="flex h-[100dvh] overflow-hidden">
    <AppSidebar :sidebar-open="sidebarOpen" @close-sidebar="sidebarOpen = false" />

    <div class="relative flex flex-col flex-1 overflow-y-auto overflow-x-hidden">
      <AppHeader
        :sidebar-open="sidebarOpen"
        @toggle-sidebar="sidebarOpen = !sidebarOpen"
        @open-search="searchModal?.open()"
      />

      <main class="grow">
        <div class="px-4 sm:px-6 lg:px-8 py-8 w-full max-w-[96rem] mx-auto">
          <slot />
        </div>
      </main>
    </div>
  </div>

  <SearchModal ref="searchModal" />
</template>
```

- [ ] **Step 2: Type-check**

Run: `npm run type-check`
Expected: no errors referencing `DefaultLayout`. (Other files may still error until Task 9 — that's fine; just confirm no new error originates in `DefaultLayout.vue`.)

- [ ] **Step 3: Commit**

```bash
git add src/layouts/DefaultLayout.vue
git commit -m "feat: DefaultLayout shell wrapper"
```

### Task 9: Rewrite `App.vue` + add `e2e`-safe route meta

**Files:**
- Modify: `src/App.vue`
- Modify: `src/__tests__/App.spec.ts`

The new `App.vue` follows journal's pattern but library's auth store exposes `ensureLoaded()`/`isAuthenticated` rather than journal's `initialized`. Read `src/stores/auth.ts` to confirm. Use this shape: render the shell for non-public routes, bare `RouterView` for `meta.public`. No masthead/footer/phase-banner/skip-link (those were govuk).

- [ ] **Step 1: Replace `src/App.vue`**

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'
import DefaultLayout from '@/layouts/DefaultLayout.vue'

const route = useRoute()
const isPublicRoute = computed(() => route.meta.public === true)
</script>

<template>
  <RouterView v-if="isPublicRoute" />
  <DefaultLayout v-else>
    <RouterView />
  </DefaultLayout>
</template>
```

> Auth gating already happens in `router.beforeEach(authGuard)` (see `src/router/index.ts`), which redirects unauthenticated users to `/login` before any shell renders. So `App.vue` doesn't need an auth check — the guard guarantees that any non-public route reaching render is authenticated. This removes the old `GovServiceNavigation`/`SearchModal`/masthead wiring (search now lives in the shell).

- [ ] **Step 2: Rewrite `src/__tests__/App.spec.ts`**

The old spec asserts the masthead/nav. Replace its assertions: (a) a public route (`/login`) renders WITHOUT the sidebar; (b) a private route renders the sidebar. Read the old spec for its router/pinia setup harness and keep that harness; swap the assertions to:

```ts
// public route → no sidebar
expect(wrapper.find('#sidebar').exists()).toBe(false)
// private route → sidebar present
expect(wrapper.find('#sidebar').exists()).toBe(true)
```

(Mock auth as authenticated for the private-route case, mirroring the existing harness in `src/router/__tests__/guard.spec.ts`.)

- [ ] **Step 3: Run the spec — expect PASS**

Run: `npx vitest run src/__tests__/App.spec.ts`

- [ ] **Step 4: Manual smoke (shell renders)**

Run: `npm run dev`, open the printed URL, log in. Expected: Mosaic sidebar + header render; nav links work; dark-mode toggle flips the theme; `/` key opens the search modal (unstyled for now). Ctrl-C.

- [ ] **Step 5: Commit**

```bash
git add src/App.vue src/__tests__/App.spec.ts
git commit -m "feat: Mosaic shell in App.vue; drop govuk masthead/nav"
```

**Phase 1 checkpoint:** Shell is Mosaic. Page bodies still render `Gov*` components (unstyled). Proceed to rebuild them.

---

## Phase 2 — `App*` component library

Each task creates a Mosaic-styled replacement for a `Gov*` wrapper that **preserves the existing public API** (props, emits, slots, v-model). For every component:

- **First read the existing `src/components/govuk/<Name>.vue`** to capture its exact props/emits/slots — that file is the API contract. Do not change the contract; only change the rendered markup/classes.
- Where a `__tests__` spec exists for that component, update it: keep behavioural assertions, replace govuk-class/selector assertions with the new markup. Run red → implement → green.
- Add the new component to a new barrel `src/components/app/index.ts`.

Class vocabulary to use (from `utility-patterns.css` / Mosaic):
- Inputs/selects/textareas: `class="form-input w-full"` / `form-select w-full` / `form-textarea w-full`.
- Checkboxes/radios: `class="form-checkbox"` / `form-radio`.
- Labels: `class="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300"`.
- Hints: `class="text-sm text-gray-500 dark:text-gray-400 mb-1"`.
- Field error text: `class="text-sm text-red-500 mt-1"`.
- Cards/panels: `class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60"`.
- Primary button: `class="btn bg-violet-500 hover:bg-violet-600 text-white"`.
- Secondary button: `class="btn border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-800 dark:text-gray-300"`.
- Danger button: `class="btn bg-red-500 hover:bg-red-600 text-white"`.

### Task 10: barrel + `AppButton`

**Files:**
- Create: `src/components/app/index.ts`
- Create: `src/components/app/AppButton.vue`
- Test: `src/components/app/__tests__/AppButton.spec.ts`

- [ ] **Step 1:** Read `src/components/govuk/GovButton.vue`. Note its props (expected: `variant?: 'primary' | 'secondary' | 'warning' | 'inverse'`, `href?`/`to?` for link-buttons, `disabled?`, default slot) and emits (`click`).

- [ ] **Step 2: Write the test**

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppButton from '../AppButton.vue'

describe('AppButton', () => {
  it('renders a <button> with violet primary styling and emits click', async () => {
    const wrapper = mount(AppButton, { slots: { default: 'Save' } })
    const btn = wrapper.get('button')
    expect(btn.classes()).toContain('btn')
    expect(btn.classes().join(' ')).toContain('bg-violet-500')
    await btn.trigger('click')
    expect(wrapper.emitted('click')).toBeTruthy()
  })

  it('renders a RouterLink when "to" is provided', () => {
    const wrapper = mount(AppButton, {
      props: { to: '/upload' },
      slots: { default: 'Go' },
      global: { stubs: { RouterLink: { template: '<a><slot/></a>' } } },
    })
    expect(wrapper.find('a').exists()).toBe(true)
  })
})
```

- [ ] **Step 3: Run — expect FAIL.** Run: `npx vitest run src/components/app/__tests__/AppButton.spec.ts`

- [ ] **Step 4: Implement `AppButton.vue`.** Keep GovButton's prop names. Map `variant` → class: primary→`bg-violet-500 hover:bg-violet-600 text-white`, secondary→`border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-800 dark:text-gray-300`, warning→`bg-red-500 hover:bg-red-600 text-white`, inverse→`bg-white text-violet-600 hover:bg-gray-100`. Base class always `btn`. Render `<RouterLink>` if `to`, `<a>` if `href`, else `<button>`.

- [ ] **Step 5: Create barrel `src/components/app/index.ts`** with `export { default as AppButton } from './AppButton.vue'`.

- [ ] **Step 6: Run — expect PASS. Commit.**

```bash
git add src/components/app/
git commit -m "feat: AppButton (Mosaic) + app barrel"
```

### Task 11: `AppInput`, `AppTextarea`, `AppSelect`

**Files:**
- Create: `src/components/app/AppInput.vue`, `AppTextarea.vue`, `AppSelect.vue`
- Modify: `src/components/app/index.ts`
- Test: `src/components/app/__tests__/AppInput.spec.ts`

- [ ] **Step 1:** Read `GovInput.vue`, `GovTextarea.vue`, `GovSelect.vue` for their props. Expected shared props: `id`, `label`, `hint?`, `error?`, `modelValue`, plus `name?`, `type?` (input), `autocomplete?`, optional `datalist?: string[]` (input), and for select `items: {value, text}[]`. v-model via `modelValue`/`update:modelValue`.

- [ ] **Step 2: Write `AppInput.spec.ts`**

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppInput from '../AppInput.vue'

describe('AppInput', () => {
  it('binds label to input and v-models value', async () => {
    const wrapper = mount(AppInput, {
      props: { id: 'q', label: 'Query', modelValue: '' },
    })
    expect(wrapper.get('label').attributes('for')).toBe('q')
    expect(wrapper.get('input').classes()).toContain('form-input')
    await wrapper.get('input').setValue('hello')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['hello'])
  })

  it('shows the error message and marks the input', () => {
    const wrapper = mount(AppInput, {
      props: { id: 'q', label: 'Query', modelValue: '', error: 'Required' },
    })
    expect(wrapper.text()).toContain('Required')
    expect(wrapper.get('input').classes().join(' ')).toContain('border-red-300')
  })
})
```

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement the three components.** Each: `<label :for=id>` (label classes above), optional hint `<p>`, the control with `form-input`/`form-textarea`/`form-select w-full`, error → append `border-red-300` and render `<p class="text-sm text-red-500 mt-1">`. Wire `aria-describedby` to hint/error ids and `aria-invalid` when error. `AppSelect` renders `<option v-for>` from `items`. `AppInput` renders an optional `<datalist>` when `datalist` provided (preserve GovInput's autocomplete behaviour).

- [ ] **Step 5:** Add all three to the barrel.

- [ ] **Step 6: Run — expect PASS. Commit.**

```bash
git add src/components/app/
git commit -m "feat: AppInput/AppTextarea/AppSelect (Mosaic forms)"
```

### Task 12: `AppCheckboxes`, `AppRadios`

**Files:**
- Create: `src/components/app/AppCheckboxes.vue`, `AppRadios.vue`
- Modify: `src/components/app/index.ts`
- Test: migrate `src/components/govuk/__tests__/GovRadios.spec.ts` → `src/components/app/__tests__/AppRadios.spec.ts`

- [ ] **Step 1:** Read `GovCheckboxes.vue`, `GovRadios.vue` and `GovRadios.spec.ts`. Preserve props: `legend`, `hint?`, `error?`, `items: {value, text, hint?}[]`, `modelValue` (array for checkboxes, scalar for radios), and the conditional-reveal slot pattern (Vue-driven). 

- [ ] **Step 2:** Port `GovRadios.spec.ts`'s behavioural assertions into `AppRadios.spec.ts` (selection emits `update:modelValue`, conditional reveal shows/hides). Replace govuk markup selectors with new ones (`<fieldset>` + `<legend>`, `input.form-radio`).

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement** both as `<fieldset>` + `<legend class="text-sm font-semibold mb-2">` + a list of `<label class="flex items-center gap-2">` rows with `input.form-radio`/`form-checkbox`. Keep the conditional-reveal slot keyed by item value.

- [ ] **Step 5:** Barrel. **Step 6:** Run — expect PASS. Commit.

```bash
git add src/components/app/
git commit -m "feat: AppCheckboxes/AppRadios (Mosaic)"
```

### Task 13: small presentational wrappers — `AppBadge`, `AppPanel`, `AppDetails`, `AppBackLink`, `AppBanner`

**Files:**
- Create: `src/components/app/AppBadge.vue` (replaces GovTag), `AppPanel.vue`, `AppDetails.vue`, `AppBackLink.vue`, `AppBanner.vue` (replaces GovNotificationBanner)
- Modify: `src/components/app/index.ts`
- Test: `src/components/app/__tests__/AppBadge.spec.ts`

- [ ] **Step 1:** Read `GovTag.vue`, `GovPanel.vue`, `GovDetails.vue`, `GovBackLink.vue`, `GovNotificationBanner.vue`.

- [ ] **Step 2: Write `AppBadge.spec.ts`** — GovTag accepts a `colour` prop from a 10-value set; map each to a Mosaic badge palette. Test that the default and one coloured variant render the right classes:

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppBadge from '../AppBadge.vue'

describe('AppBadge', () => {
  it('renders default (gray) badge text', () => {
    const wrapper = mount(AppBadge, { slots: { default: 'Beta' } })
    expect(wrapper.text()).toBe('Beta')
    expect(wrapper.classes().join(' ')).toContain('rounded-full')
  })
  it('maps colour=green to green classes', () => {
    const wrapper = mount(AppBadge, { props: { colour: 'green' }, slots: { default: 'OK' } })
    expect(wrapper.classes().join(' ')).toContain('text-green-700')
  })
})
```

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement.**
  - `AppBadge`: base `inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full`; map GovTag's `colour` values → `{bg,text}` Mosaic pairs (grey→`bg-gray-100 text-gray-600 dark:bg-gray-700/30 dark:text-gray-400`, green→`bg-green-100 text-green-700 dark:bg-green-400/30 dark:text-green-400`, blue→`bg-sky-100 text-sky-700`, red→`bg-red-100 text-red-700`, yellow→`bg-yellow-100 text-yellow-700`, purple→`bg-violet-100 text-violet-700`; reuse nearest Mosaic hue for the remaining govuk colours: turquoise→green, light-blue→sky, pink→`bg-fuchsia-100 text-fuchsia-700`→ instead use `bg-violet-100 text-violet-700`, orange→yellow). Document the mapping in a comment.
  - `AppPanel`: violet confirmation card `bg-violet-500 text-white rounded-xl p-6 text-center` with a title slot + body slot.
  - `AppDetails`: native `<details>` with `<summary class="text-sm font-medium text-violet-500 cursor-pointer">` + content `class="text-sm mt-2"`.
  - `AppBackLink`: `<RouterLink>`/`<a>` with a left-chevron SVG, `class="inline-flex items-center text-sm text-violet-500 hover:text-violet-600"`. Preserve GovBackLink's `to`/`href` props.
  - `AppBanner`: `role="alert"`, `bg-white dark:bg-gray-800 border-l-4 rounded-lg px-4 py-3 shadow-xs`; variant prop (`success`→`border-green-500`, `info`→`border-sky-500`); focus-on-mount (preserve GovNotificationBanner's tabindex/focus behaviour).

- [ ] **Step 5:** Barrel. **Step 6:** Run — expect PASS. Commit.

```bash
git add src/components/app/
git commit -m "feat: AppBadge/AppPanel/AppDetails/AppBackLink/AppBanner"
```

### Task 14: `AppErrorSummary`, `AppErrorMessage`

**Files:**
- Create: `src/components/app/AppErrorSummary.vue`, `AppErrorMessage.vue`
- Modify: `src/components/app/index.ts`
- Test: migrate `src/components/govuk/__tests__/GovErrorSummary.spec.ts` → `src/components/app/__tests__/AppErrorSummary.spec.ts`

The accessibility behaviour is the whole point here — **preserve it**: the summary focuses itself on mount, lists errors as links, and clicking/activating a link moves focus to the offending field.

- [ ] **Step 1:** Read `GovErrorSummary.vue` + its spec and `GovErrorMessage.vue`. Capture props: `errors: { text: string; href: string }[]` (confirm shape) and the focus logic.

- [ ] **Step 2:** Port the spec's behavioural assertions (focuses on mount; renders one link per error; link `href` points at field id) into the new spec, changing only the container classes/selectors.

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement.** Keep GovErrorSummary's `<script>` focus logic verbatim. Template: `<div role="alert" tabindex="-1" class="bg-red-50 dark:bg-red-500/10 border border-red-300 dark:border-red-500/30 rounded-lg p-4 mb-6">` with a `text-red-800 dark:text-red-400 font-semibold` heading "There is a problem" and a `<ul>` of `<a class="text-red-700 dark:text-red-400 underline">`. `AppErrorMessage`: `<p class="text-sm text-red-500 mt-1">` with the govuk visually-hidden "Error:" prefix preserved.

- [ ] **Step 5:** Barrel. **Step 6:** Run — expect PASS. Commit.

```bash
git add src/components/app/
git commit -m "feat: AppErrorSummary/AppErrorMessage (a11y preserved)"
```

### Task 15: `AppSummaryList`

**Files:**
- Create: `src/components/app/AppSummaryList.vue`
- Modify: `src/components/app/index.ts`
- Test: `src/components/app/__tests__/AppSummaryList.spec.ts`

- [ ] **Step 1:** Read `GovSummaryList.vue` — capture props (expected `rows: { key, value, action? }[]` plus per-row "Change" action emit/slot).

- [ ] **Step 2: Write the test** — renders one row per item; emits/links the change action.

```ts
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppSummaryList from '../AppSummaryList.vue'

it('renders key/value rows', () => {
  const wrapper = mount(AppSummaryList, {
    props: { rows: [{ key: 'Title', value: 'Invoice' }, { key: 'Kind', value: 'PDF' }] },
  })
  expect(wrapper.findAll('dt')).toHaveLength(2)
  expect(wrapper.text()).toContain('Invoice')
})
```

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement** as a `<dl class="divide-y divide-gray-200 dark:divide-gray-700/60">` of rows `<div class="flex justify-between gap-4 py-3">` with `<dt class="text-sm font-medium text-gray-500">`, `<dd class="text-sm text-gray-800 dark:text-gray-100">`, and an optional "Change" `RouterLink`/button (`text-violet-500`). Preserve GovSummaryList's action API.

- [ ] **Step 5:** Barrel. **Step 6:** Run — expect PASS. Commit.

```bash
git add src/components/app/
git commit -m "feat: AppSummaryList (Mosaic dl)"
```

### Task 16: `AppPagination`

**Files:**
- Create: `src/components/app/AppPagination.vue`
- Modify: `src/components/app/index.ts`
- Test: `src/components/app/__tests__/AppPagination.spec.ts`

- [ ] **Step 1:** Read `GovPagination.vue` — capture props (expected `page`, `totalPages`/`total`+`pageSize`) and the `@change` emit.

- [ ] **Step 2: Write the test** — clicking "Next" emits `change` with `page+1`; "Previous" disabled on page 1.

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement** Mosaic numeric pagination: a flex row of Prev / page-number buttons / Next, each `btn` with `bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700/60`, the active page `bg-violet-500 text-white`, disabled state `opacity-50 cursor-not-allowed`. Keep the `@change(page)` emit contract.

- [ ] **Step 5:** Barrel. **Step 6:** Run — expect PASS. Commit.

```bash
git add src/components/app/
git commit -m "feat: AppPagination (Mosaic numeric)"
```

### Task 17: `AppDateInput`

**Files:**
- Create: `src/components/app/AppDateInput.vue`
- Modify: `src/components/app/index.ts`
- Test: migrate `src/components/govuk/__tests__/GovDateInput.spec.ts` → `src/components/app/__tests__/AppDateInput.spec.ts`

Decision (locked in spec): keep the **3-field day/month/year** pattern emitting ISO `YYYY-MM-DD`, not Flatpickr.

- [ ] **Step 1:** Read `GovDateInput.vue` + spec. Capture props (`legend`, `hint?`, `error?`, `modelValue: string` ISO) and the parse/format logic that converts 3 fields ↔ ISO.

- [ ] **Step 2:** Port the spec's behavioural assertions verbatim (e.g. typing 5 / 11 / 2026 emits `2026-11-05`; partial input emits empty/invalid as before). Change only field selectors.

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement.** Keep GovDateInput's `<script>` parse/format logic verbatim. Template: `<fieldset>` + `<legend>` + three `<div>` columns, each `<label class="text-xs font-medium">` (Day/Month/Year) over `<input class="form-input w-14" inputmode="numeric">` (year `w-20`). Error → `border-red-300` + `AppErrorMessage`.

- [ ] **Step 5:** Barrel. **Step 6:** Run — expect PASS. Commit.

```bash
git add src/components/app/
git commit -m "feat: AppDateInput (3-field, ISO) Mosaic-styled"
```

### Task 18: `AppFileUpload`

**Files:**
- Create: `src/components/app/AppFileUpload.vue`
- Modify: `src/components/app/index.ts`
- Test: migrate `src/components/govuk/__tests__/GovFileUpload.spec.ts` → `src/components/app/__tests__/AppFileUpload.spec.ts`

- [ ] **Step 1:** Read `GovFileUpload.vue` + spec. Capture props/v-model (`modelValue: File[] | null`), `multiple?`, `accept?`, and the drop-zone behaviour (dragover styling, drop appends files).

- [ ] **Step 2:** Port the spec's behavioural assertions (selecting files emits `update:modelValue` with a `File[]`; drop adds files). Note: this component previously enhanced govuk's `FileUpload` JS class — the new one is pure Vue (no govuk module), so remove any `useGovukComponent` usage.

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement** a Mosaic drop-zone: `<label class="flex flex-col items-center justify-center border-2 border-dashed border-gray-300 dark:border-gray-700/60 rounded-xl p-8 cursor-pointer hover:border-violet-400 transition">` wrapping a hidden `<input type="file" class="sr-only">`, an upload icon, and "Drop files or click to browse". Track `dragover` with a `ring-2 ring-violet-400` class. Emit `update:modelValue` on change/drop. Keep the prop contract.

- [ ] **Step 5:** Barrel. **Step 6:** Run — expect PASS. Commit.

```bash
git add src/components/app/
git commit -m "feat: AppFileUpload (pure-Vue Mosaic drop-zone)"
```

### Task 19: Restyle `AppProgressBar`

**Files:**
- Modify: `src/components/AppProgressBar.vue`
- Test: existing usage covered by `UploadView` spec (Task 24)

- [ ] **Step 1:** Read the current `AppProgressBar.vue` (black border, blue fill). Keep its props (`value`/percent) and tabular-numbers label. Restyle: track `bg-gray-200 dark:bg-gray-700/60 rounded-full h-2 overflow-hidden`, fill `bg-violet-500 h-full rounded-full transition-all`, label `text-xs text-gray-500 tabular-nums`.

- [ ] **Step 2: Type-check + commit**

Run: `npm run type-check` (expect no new errors from this file).

```bash
git add src/components/AppProgressBar.vue
git commit -m "style: AppProgressBar in Mosaic violet"
```

---

## Phase 3 — Reskin the views

Each view keeps its `<script setup>` logic; only the template swaps `Gov*` imports for `App*` and restyles wrapper markup. For each, update the matching `src/views/__tests__/*.spec.ts`: keep behavioural assertions, replace govuk selectors. Read each view fully before editing.

### Task 20: `LoginView`

**Files:**
- Modify: `src/views/LoginView.vue`
- Modify: `src/views/__tests__/LoginView.spec.ts`

- [ ] **Step 1:** Read `LoginView.vue`. Replace its `Gov*` imports with `App*`. Wrap the form in a centered Mosaic card (no shell — `/login` is public): outer `<div class="min-h-[100dvh] flex items-center justify-center bg-gray-100 dark:bg-gray-900 px-4">`, card `class="w-full max-w-md bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-8"`, `<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">Library</h1>`. Use `AppInput` (username/password), `AppButton` (primary, full-width `w-full`), `AppErrorSummary` for failures.

- [ ] **Step 2:** Update the spec: keep "submits credentials → calls `auth.login` → redirects" assertions; swap selectors to the new inputs/button (use `data-testid` or `input#username`/`input#password` ids set in the view).

- [ ] **Step 3: Run — expect PASS.** `npx vitest run src/views/__tests__/LoginView.spec.ts`

- [ ] **Step 4: Commit.**

```bash
git add src/views/LoginView.vue src/views/__tests__/LoginView.spec.ts
git commit -m "feat: Mosaic LoginView (centered card)"
```

### Task 21: `DocumentListView`

**Files:**
- Modify: `src/views/DocumentListView.vue`
- Modify: `src/views/__tests__/DocumentListView.spec.ts`

- [ ] **Step 1:** Read the view. Page header: `<h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">Documents</h1>`. Restyle the search/filter bar with `AppInput`/`AppSelect` in a `flex flex-wrap gap-3` row inside a Mosaic card. Convert each document tile to a Mosaic card: `class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 overflow-hidden hover:shadow-md transition"` with the 4:3 thumbnail on top (`aspect-[4/3] bg-gray-100 dark:bg-gray-900/40 object-contain`), title as `RouterLink` (`text-violet-600 font-medium hover:underline`), metadata as `AppBadge`s. Keep the responsive grid via `grid gap-6 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4`. Use `AppPagination`. Restyle the empty state inside a centered Mosaic card.

- [ ] **Step 2:** Update the spec: keep "renders N tiles for N documents", "search input filters", "pagination emits/refetches"; swap selectors to the card markup (add `data-testid="doc-card"` to each tile).

- [ ] **Step 3: Run — expect PASS.**

- [ ] **Step 4: Commit.**

```bash
git add src/views/DocumentListView.vue src/views/__tests__/DocumentListView.spec.ts
git commit -m "feat: Mosaic DocumentListView (card grid)"
```

### Task 22: `DocumentDetailView`

**Files:**
- Modify: `src/views/DocumentDetailView.vue`
- Modify: `src/views/__tests__/DocumentDetailView.spec.ts`

- [ ] **Step 1:** Read the view. Two-column layout `grid grid-cols-1 lg:grid-cols-2 gap-6`: left = preview pane in a Mosaic card (PDF `<iframe>`/image `object-contain`), right = `AppSummaryList` metadata card with per-row inline edit using `App*` inputs (preserve the existing inline-edit logic). Add `AppBackLink` to the list. Destructive "Delete" via `AppButton variant="warning"` linking to `/documents/:id/delete`.

- [ ] **Step 2:** Update the spec: keep "loads document, shows metadata", "inline edit calls `updateDocument`"; swap selectors.

- [ ] **Step 3: Run — expect PASS. Step 4: Commit.**

```bash
git add src/views/DocumentDetailView.vue src/views/__tests__/DocumentDetailView.spec.ts
git commit -m "feat: Mosaic DocumentDetailView (preview + summary)"
```

### Task 23: `DocumentDeleteView`

**Files:**
- Modify: `src/views/DocumentDeleteView.vue`
- Modify: `src/views/__tests__/DocumentDeleteView.spec.ts`

- [ ] **Step 1:** Read the view. Centered Mosaic danger card: `<h1>` "Are you sure you want to delete '{{ title }}'?", warning text in `text-red-600`, `AppButton variant="warning"` (Delete) + secondary cancel `AppBackLink`/`AppButton` to the detail page. Preserve the delete→redirect+flash logic.

- [ ] **Step 2:** Update spec: keep "confirms delete calls `deleteDocument` and redirects with flash"; swap selectors.

- [ ] **Step 3: Run — expect PASS. Step 4: Commit.**

```bash
git add src/views/DocumentDeleteView.vue src/views/__tests__/DocumentDeleteView.spec.ts
git commit -m "feat: Mosaic DocumentDeleteView (confirmation card)"
```

### Task 24: `UploadView`

**Files:**
- Modify: `src/views/UploadView.vue`
- Modify: `src/views/__tests__/UploadView.spec.ts`

- [ ] **Step 1:** Read the view. `<h1>Upload</h1>`, `AppFileUpload` drop-zone in a Mosaic card, per-file rows showing name + restyled `AppProgressBar` + status `AppBadge` (green=done, yellow=processing, red=error). Preserve all upload/poll logic and duplicate/error summaries (use `AppBanner`).

- [ ] **Step 2:** Update spec: keep "selecting files starts upload", "progress updates", "duplicate/error shown"; swap selectors.

- [ ] **Step 3: Run — expect PASS. Step 4: Commit.**

```bash
git add src/views/UploadView.vue src/views/__tests__/UploadView.spec.ts
git commit -m "feat: Mosaic UploadView (drop-zone + progress)"
```

### Task 25: `SettingsView`

**Files:**
- Modify: `src/views/SettingsView.vue`
- Modify: `src/views/__tests__/SettingsView.spec.ts`

- [ ] **Step 1:** Read the view. `<h1>Settings</h1>` + a Mosaic settings card containing `AppCheckboxes` for the dashboard-field toggles (driven by `DASHBOARD_FIELDS`). Save via `AppButton` (primary). Preserve the get/update settings logic and the field-order semantics noted in `docs/`.

- [ ] **Step 2:** Update spec: keep "loads current prefs", "toggling + save calls `updateSettings` with selected fields"; swap selectors.

- [ ] **Step 3: Run — expect PASS. Step 4: Commit.**

```bash
git add src/views/SettingsView.vue src/views/__tests__/SettingsView.spec.ts
git commit -m "feat: Mosaic SettingsView"
```

### Task 26: `SearchModal`

**Files:**
- Modify: `src/components/SearchModal.vue`
- Modify: `src/components/__tests__/SearchModal.spec.ts`

- [ ] **Step 1:** Read the current `SearchModal.vue` (native `<dialog>`, `/`-key open, query + filter fields). Keep the `<dialog>` element, the `open()`/close methods (called by `DefaultLayout`/`AppHeader`), and the `/`-key listener. Restyle to Mosaic `ModalSearch`: backdrop `bg-gray-900/30`, panel `bg-white dark:bg-gray-800 rounded-xl shadow-lg max-w-2xl`, a top search `AppInput` with a magnifier icon, filter fields as `AppSelect`/`AppDateInput`, results list rows `hover:bg-gray-100 dark:hover:bg-gray-700/30`. Remove any `Gov*`/`useGovukComponent` imports.

- [ ] **Step 2:** Update spec: keep "opens on `/` key", "typing queries the API", "selecting a result navigates"; swap selectors.

- [ ] **Step 3: Run — expect PASS. Step 4: Commit.**

```bash
git add src/components/SearchModal.vue src/components/__tests__/SearchModal.spec.ts
git commit -m "feat: Mosaic SearchModal"
```

**Phase 3 checkpoint:** No view imports anything from `@/components/govuk` anymore. Verify:

Run: `grep -rn "components/govuk" src/ ; grep -rn "govuk-frontend" src/`
Expected: no matches (if any remain, fix before Phase 4).

---

## Phase 4 — Cleanup, tooling, docs

### Task 27: Delete the govuk layer

**Files:**
- Delete: `src/components/govuk/` (entire directory, incl. `__tests__`, `index.ts`, `types.ts`, `useGovukComponent.ts`)
- Delete: `src/styles/main.scss` (and the `src/styles/` dir if now empty)
- Delete: `src/types/govuk-frontend.d.ts`

- [ ] **Step 1:** Confirm nothing imports them.

Run: `grep -rn "govuk" src/ | grep -v "// " || echo "clean"`
Expected: `clean` (or only incidental comment matches). If `types.ts` exported types still used by `App*`/views (e.g. a `{value,text}` item type or `ServiceNavigationItem`), move those into `src/components/app/types.ts` and update imports FIRST, then delete.

- [ ] **Step 2: Delete**

```bash
git rm -r src/components/govuk src/types/govuk-frontend.d.ts
git rm src/styles/main.scss
```

- [ ] **Step 3: Type-check**

Run: `npm run type-check`
Expected: PASS (no dangling references).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove govuk component layer and SCSS"
```

### Task 28: Rewrite `check-assets.mjs`

**Files:**
- Modify: `scripts/check-assets.mjs`

The GDS-Transport/crown licensing rationale is gone. The remaining legitimate guard: the build must not embed a remotely-hosted font as a render-blocking dependency we don't control, and must contain no leftover govuk asset references. Simplest correct replacement: assert no `govuk`/`transport`/`crown`/`crest` strings remain in `dist/`, and that the only web-font files (if any are self-hosted) are expected. Since Mosaic loads Inter from Google Fonts via `@import` (no self-hosted font files in `dist/`), the font-file check is dropped.

- [ ] **Step 1:** Replace the `FORBIDDEN_NAME`, `FONT_EXTENSIONS` font check, and `FORBIDDEN_CONTENT` with a govuk-residue check:

```js
const FORBIDDEN_NAME = /transport|crown|crest|govuk/i
const TEXT_EXTENSIONS = new Set(['.css', '.js', '.mjs', '.html', '.svg', '.json', '.map', '.txt'])
const FORBIDDEN_CONTENT = [/GDS[ -]?Transport/i, /govuk-/i, /crown copyright/i]
```

Remove the `FONT_EXTENSIONS` constant and the font-file `failures.push` block. Update the success message to "no govuk residue". Keep the `dist/` existence guard and the walk.

- [ ] **Step 2: Build + run the check**

Run: `npm run build && npm run check:assets`
Expected: build succeeds; check prints OK. (If it flags a match, trace and remove the residue.)

- [ ] **Step 3: Commit**

```bash
git add scripts/check-assets.mjs
git commit -m "chore: repurpose check-assets to guard govuk residue"
```

### Task 29: e2e selectors + full test sweep

**Files:**
- Modify: `e2e/library.spec.ts`, `e2e/responsive.spec.ts`
- Modify: `src/components/govuk/__tests__/govuk-markup.spec.ts` → already deleted in Task 27; ensure no reference remains.

- [ ] **Step 1:** Read `e2e/library.spec.ts` and `e2e/responsive.spec.ts`. Update selectors that targeted govuk markup (e.g. `.govuk-service-navigation`, masthead, `.govuk-button`) to the Mosaic equivalents (`#sidebar`, `[data-testid="sidebar-*-link"]`, `.btn`, `[data-testid="doc-card"]`, `[data-testid="header-search-button"]`). The responsive spec likely asserts the nav collapses — update to assert the sidebar hamburger behaviour at mobile widths.

- [ ] **Step 2: Run the unit suite**

Run: `npm run test:unit -- --run`
Expected: all green. Fix any stragglers.

- [ ] **Step 3: Run e2e** (requires the app + backend per `e2e/compose.e2e.yml`; if the harness isn't available locally, note it and rely on CI).

Run: `npm run test:e2e`
Expected: green, or documented as CI-only.

- [ ] **Step 4: Commit**

```bash
git add e2e/
git commit -m "test: update e2e selectors for Mosaic shell"
```

### Task 30: Docs + journal

**Files:**
- Modify: `frontend/docs/frontend.md`
- Create: `journal/<yymmdd>-mosaic-reskin.md` (repo-root `journal/`)
- Possibly archive: old govuk-specific docs per the user's docs convention

- [ ] **Step 1:** Rewrite `frontend/docs/frontend.md` to describe the Mosaic architecture: Tailwind 4 CSS-first tokens (`src/assets/main.css`), `utility-patterns.css`, the shell (`AppSidebar`/`AppHeader`/`ThemeToggle`/`DefaultLayout`), the `App*` component library (with the `Gov*`→`App*` mapping table), dark mode, and the public-route-bypasses-shell pattern. Remove the GOV.UK design-system sections. If a section is worth preserving as a decision record, `git mv` the old content into `docs/archive/` with a `**Status:** superseded by frontend.md (2026-06-13).` header (per global docs convention).

- [ ] **Step 2:** Write the journal entry (filename `journal/260613-mosaic-reskin.md`) capturing: why (brutal → pleasant), the journal-recipe approach, key decisions (full sidebar shell, dark mode, thin `App*` wrappers preserving the `Gov*` API, kept 3-field date input, search-as-modal), and what changed structurally.

- [ ] **Step 3: Commit**

```bash
git add frontend/docs/frontend.md journal/260613-mosaic-reskin.md docs/
git commit -m "docs: document Mosaic frontend architecture; journal entry"
```

### Task 31: Final verification gate

- [ ] **Step 1: Full pipeline**

Run, from `frontend/`:
```bash
npm run type-check && npm run lint && npm run test:unit -- --run && npm run build && npm run check:assets
```
Expected: every step exits 0.

- [ ] **Step 2: Manual smoke (real app)** — `npm run dev`, log in, and walk every route: list → detail → delete (cancel) → upload → settings → search modal → sign out → login. Toggle dark mode on each. Resize to mobile and confirm the sidebar collapses to a hamburger overlay. Confirm no console errors.

- [ ] **Step 3: Confirm no govuk residue**

Run: `grep -rn "govuk\|govuk-frontend\|fontsource" src/ package.json vite.config.ts index.html || echo "clean"`
Expected: `clean`.

- [ ] **Step 4: Finish the branch** — invoke `superpowers:finishing-a-development-branch` to choose merge/PR. (CI builds the Docker image and pushes to `ghcr.io/johnmathews/<repo>` on merge to `main`, per the project's CI convention.)

---

## Self-review notes (author)

- **Spec coverage:** Build setup (Tasks 1–4) ✓; shell incl. dark mode (Tasks 5–9) ✓; `Gov*`→`App*` thin wrappers, all 17 mapped (Tasks 10–18) ✓; `AppProgressBar`/`SearchModal` retained+restyled (Tasks 19, 26) ✓; all 6 views (Tasks 20–25) ✓; `check-assets` (Task 28) ✓; tests (Tasks 29, 31) ✓; docs+journal (Task 30) ✓; out-of-scope items (backend/API/auth/charts) never touched ✓.
- **API preservation:** Every `App*` task begins by reading the matching `Gov*` file to lock the props/emits contract before reimplementing markup — this is the mechanism that keeps view diffs to import swaps.
- **Known soft spots the executor must resolve by reading source:** exact prop names on `auth` store (`username` vs other), GovButton/GovInput/GovSelect/GovDateInput/GovFileUpload prop shapes, and GovTag's full colour set. These are all readable in-repo; tasks call them out explicitly rather than guessing.
