# Cross-browser PDF preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the native-`<iframe>` PDF preview on the document detail page with a self-rendered pdf.js viewer that scrolls through all pages and behaves identically in Chrome, Firefox, and Safari.

**Architecture:** A single self-contained `DocumentPdfPreview.vue` loads the PDF with `pdfjs-dist` and renders every page to its own `<canvas>`, stacked vertically and fit-to-width in a scroll container. Pages render lazily via `IntersectionObserver` so large PDFs don't paint every canvas at once. The component owns its loading / rendered / error / password states; `DocumentDetailView.vue` drops the iframe and all per-browser workarounds and mounts the component on every viewport.

**Tech Stack:** Vue 3.5 (`<script setup lang="ts">`), Vite 8, Vitest 4 (jsdom), Playwright, `pdfjs-dist`.

## Global Constraints

- Type annotations on all function signatures and non-obvious variables (project + global rule).
- Package manager: **npm** (`package-lock.json`); never yarn/pnpm.
- `pdfjs-dist` v4+ (module worker `build/pdf.worker.min.mjs`). Worker is lazy-loaded; keep it off the initial bundle.
- One unified preview path for all viewports — no `lg:` desktop/mobile fork for PDFs.
- Component is decoupled from the documents API: it receives URLs as props (`src`, `poster`, `openHref`, `downloadHref`), never imports `@/api/documents`.
- Commit after every task. Work stays on branch `cross-browser-pdf-preview`.

---

### Task 1: Dependency, worker config, and the load state machine

Build `DocumentPdfPreview.vue` to the point where it loads a PDF and reports `loading → rendered | error | password`. No page rendering yet.

**Files:**
- Modify: `frontend/package.json` (+ `package-lock.json` via npm)
- Create: `frontend/src/components/DocumentPdfPreview.vue`
- Test: `frontend/src/components/__tests__/DocumentPdfPreview.spec.ts`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - Component props: `src: string`, `poster?: string`, `openHref: string`, `downloadHref: string`, `initialPage?: number | null`.
  - Reactive status of type `'loading' | 'rendered' | 'error' | 'password'`, exposed in the DOM via `data-testid` on the root: `pdf-preview-loading`, `pdf-preview-pages`, `pdf-preview-error`, `pdf-preview-password`.
  - `pageCount` (number) used by Task 2.

- [ ] **Step 1: Install the dependency**

```bash
cd frontend && npm install pdfjs-dist
```

Expected: `pdfjs-dist` appears under `dependencies` in `package.json` and `package-lock.json` updates.

- [ ] **Step 2: Write the failing test**

Create `frontend/src/components/__tests__/DocumentPdfPreview.spec.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// pdfjs-dist can't run its worker/canvas in jsdom — mock the whole module.
const getDocument = vi.fn()
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument,
}))

import DocumentPdfPreview from '../DocumentPdfPreview.vue'

/** A fake PDFDocumentProxy whose pages resolve immediately. */
function fakePdf(numPages: number) {
  return {
    numPages,
    getPage: vi.fn(async (n: number) => ({
      getViewport: ({ scale }: { scale: number }) => ({ width: 100 * scale, height: 140 * scale }),
      render: () => ({ promise: Promise.resolve() }),
    })),
    destroy: vi.fn(),
  }
}

const props = {
  src: '/api/documents/1/searchable.pdf?disposition=inline',
  poster: '/api/documents/1/thumbnail',
  openHref: '/api/documents/1/searchable.pdf?disposition=inline',
  downloadHref: '/api/documents/1/searchable.pdf',
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('DocumentPdfPreview state machine', () => {
  it('shows the loading poster before the document resolves', () => {
    getDocument.mockReturnValue({ promise: new Promise(() => {}) })
    const wrapper = mount(DocumentPdfPreview, { props })
    expect(wrapper.find('[data-testid="pdf-preview-loading"]').exists()).toBe(true)
  })

  it('renders the page container once the document resolves', async () => {
    getDocument.mockReturnValue({ promise: Promise.resolve(fakePdf(2)) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-pages"]').exists()).toBe(true)
  })

  it('falls back to the error state when loading rejects', async () => {
    getDocument.mockReturnValue({ promise: Promise.reject(new Error('network')) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-error"]').exists()).toBe(true)
  })

  it('falls back to the password state on a PasswordException', async () => {
    const err = Object.assign(new Error('locked'), { name: 'PasswordException' })
    getDocument.mockReturnValue({ promise: Promise.reject(err) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-password"]').exists()).toBe(true)
  })
})
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentPdfPreview.spec.ts`
Expected: FAIL — cannot resolve `../DocumentPdfPreview.vue`.

- [ ] **Step 4: Write the minimal component**

Create `frontend/src/components/DocumentPdfPreview.vue`:

```vue
<script setup lang="ts">
import { ref, shallowRef, onMounted, onBeforeUnmount, watch } from 'vue'
import * as pdfjsLib from 'pdfjs-dist'
import type { PDFDocumentProxy } from 'pdfjs-dist'

// Module worker, bundled by Vite and loaded on demand (off the initial bundle).
pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

const props = defineProps<{
  src: string
  poster?: string
  openHref: string
  downloadHref: string
  initialPage?: number | null
}>()

type Status = 'loading' | 'rendered' | 'error' | 'password'
const status = ref<Status>('loading')
const pageCount = ref(0)
const pdf = shallowRef<PDFDocumentProxy | null>(null)

async function load(): Promise<void> {
  status.value = 'loading'
  try {
    const doc = await pdfjsLib.getDocument({ url: props.src }).promise
    pdf.value = doc
    pageCount.value = doc.numPages
    status.value = 'rendered'
  } catch (err: unknown) {
    status.value =
      (err as { name?: string } | null)?.name === 'PasswordException' ? 'password' : 'error'
  }
}

onMounted(load)
watch(() => props.src, load)
onBeforeUnmount(() => {
  void pdf.value?.destroy()
})

defineExpose({ status, pageCount })
</script>

<template>
  <div data-testid="pdf-preview">
    <div v-if="status === 'loading'" data-testid="pdf-preview-loading" class="h-[70vh] bg-gray-100 dark:bg-gray-900/40" />
    <div v-else-if="status === 'rendered'" data-testid="pdf-preview-pages" class="h-[70vh] overflow-y-auto bg-gray-100 dark:bg-gray-900/40" />
    <div v-else-if="status === 'password'" data-testid="pdf-preview-password" class="h-[70vh]" />
    <div v-else data-testid="pdf-preview-error" class="h-[70vh]" />
  </div>
</template>
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentPdfPreview.spec.ts`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json \
  frontend/src/components/DocumentPdfPreview.vue \
  frontend/src/components/__tests__/DocumentPdfPreview.spec.ts
git commit -m "feat(frontend): pdf.js preview component — load state machine"
```

---

### Task 2: Lazy per-page canvas rendering + initial-page scroll

Render one `<canvas>` per page inside the scroll container, painting each only when it nears the viewport, and scroll to `initialPage` on open.

**Files:**
- Modify: `frontend/src/components/DocumentPdfPreview.vue`
- Test: `frontend/src/components/__tests__/DocumentPdfPreview.spec.ts`

**Interfaces:**
- Consumes: the Task 1 component (`status`, `pageCount`, `pdf`, `load`).
- Produces: one `[data-page="N"]` element per page inside `[data-testid="pdf-preview-pages"]`, each containing a `<canvas>`; `renderPage(n)` paints lazily; `scrollToPage(n)` scrolls a page into view.

- [ ] **Step 1: Write the failing test**

Append to `DocumentPdfPreview.spec.ts`:

```ts
describe('DocumentPdfPreview page rendering', () => {
  // jsdom has no IntersectionObserver — capture the callback so the test can fire it.
  let ioCallback: (entries: Array<{ isIntersecting: boolean; target: Element }>) => void
  beforeEach(() => {
    vi.stubGlobal(
      'IntersectionObserver',
      class {
        constructor(cb: typeof ioCallback) {
          ioCallback = cb
        }
        observe() {}
        disconnect() {}
      },
    )
    // jsdom canvases return null for getContext — hand back a stub so render() runs.
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({})) as never
  })

  it('creates one page slot per page', async () => {
    getDocument.mockReturnValue({ promise: Promise.resolve(fakePdf(3)) })
    const wrapper = mount(DocumentPdfPreview, { props, attachTo: document.body })
    await flushPromises()
    expect(wrapper.findAll('[data-page]')).toHaveLength(3)
  })

  it('renders a page only after it intersects', async () => {
    const pdf = fakePdf(3)
    getDocument.mockReturnValue({ promise: Promise.resolve(pdf) })
    const wrapper = mount(DocumentPdfPreview, { props, attachTo: document.body })
    await flushPromises()
    expect(pdf.getPage).not.toHaveBeenCalled()

    const slot = wrapper.find('[data-page="2"]').element
    ioCallback([{ isIntersecting: true, target: slot }])
    await flushPromises()
    expect(pdf.getPage).toHaveBeenCalledWith(2)
  })

  it('scrolls to initialPage when provided', async () => {
    const scrollIntoView = vi.fn()
    Element.prototype.scrollIntoView = scrollIntoView
    getDocument.mockReturnValue({ promise: Promise.resolve(fakePdf(5)) })
    mount(DocumentPdfPreview, { props: { ...props, initialPage: 4 }, attachTo: document.body })
    await flushPromises()
    expect(scrollIntoView).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentPdfPreview.spec.ts`
Expected: FAIL — no `[data-page]` elements / `getPage` never called.

- [ ] **Step 3: Implement lazy rendering**

Replace the `<script setup>` body and the `pdf-preview-pages` block in `DocumentPdfPreview.vue`.

Add to imports and after `pageCount`:

```ts
import { ref, shallowRef, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
```

```ts
const container = ref<HTMLElement | null>(null)
const canvasRefs = ref<Array<HTMLCanvasElement | null>>([])
const renderedPages = new Set<number>()
let observer: IntersectionObserver | null = null

function setCanvasRef(el: Element | null, index: number): void {
  canvasRefs.value[index] = el as HTMLCanvasElement | null
}

async function renderPage(n: number): Promise<void> {
  if (renderedPages.has(n) || !pdf.value) return
  renderedPages.add(n)
  const canvas = canvasRefs.value[n - 1]
  const ctx = canvas?.getContext('2d')
  if (!canvas || !ctx) return
  const page = await pdf.value.getPage(n)
  const width = container.value?.clientWidth || canvas.clientWidth || 800
  const unscaled = page.getViewport({ scale: 1 })
  const scale = (width / unscaled.width) * (window.devicePixelRatio || 1)
  const viewport = page.getViewport({ scale })
  canvas.width = viewport.width
  canvas.height = viewport.height
  await page.render({ canvasContext: ctx, viewport }).promise
}

function observePages(): void {
  observer?.disconnect()
  observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          const n = Number((entry.target as HTMLElement).dataset.page)
          void renderPage(n)
        }
      }
    },
    { root: container.value, rootMargin: '300px' },
  )
  container.value?.querySelectorAll('[data-page]').forEach((el) => observer?.observe(el))
}

function scrollToPage(n: number): void {
  container.value?.querySelector(`[data-page="${n}"]`)?.scrollIntoView()
}
```

Update `load()`'s success path (replace the `status.value = 'rendered'` line):

```ts
    pageCount.value = doc.numPages
    renderedPages.clear()
    status.value = 'rendered'
    await nextTick()
    observePages()
    if (props.initialPage) scrollToPage(props.initialPage)
```

Update `onBeforeUnmount`:

```ts
onBeforeUnmount(() => {
  observer?.disconnect()
  void pdf.value?.destroy()
})
```

Replace the `pdf-preview-pages` template block:

```vue
    <div
      v-else-if="status === 'rendered'"
      ref="container"
      data-testid="pdf-preview-pages"
      class="h-[70vh] overflow-y-auto bg-gray-100 dark:bg-gray-900/40"
    >
      <canvas
        v-for="n in pageCount"
        :key="n"
        :ref="(el) => setCanvasRef(el as Element | null, n - 1)"
        :data-page="n"
        class="mx-auto mb-2 block w-full max-w-3xl shadow-sm"
      />
    </div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentPdfPreview.spec.ts`
Expected: PASS (all Task 1 + Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DocumentPdfPreview.vue \
  frontend/src/components/__tests__/DocumentPdfPreview.spec.ts
git commit -m "feat(frontend): lazy per-page canvas rendering + initial-page scroll"
```

---

### Task 3: Loading poster, error and password fallbacks

Fill the non-rendered states with real UI: a thumbnail poster while loading, and Open/Download fallback cards for error and password.

**Files:**
- Modify: `frontend/src/components/DocumentPdfPreview.vue`
- Test: `frontend/src/components/__tests__/DocumentPdfPreview.spec.ts`

**Interfaces:**
- Consumes: `status`, `props.poster`, `props.openHref`, `props.downloadHref`.
- Produces: links with `data-testid="pdf-preview-open"` and `data-testid="pdf-preview-download"` inside the error and password states; an `<img>` poster in the loading state when `poster` is set.

- [ ] **Step 1: Write the failing test**

Append to `DocumentPdfPreview.spec.ts`:

```ts
describe('DocumentPdfPreview fallbacks', () => {
  it('shows the thumbnail poster while loading', () => {
    getDocument.mockReturnValue({ promise: new Promise(() => {}) })
    const wrapper = mount(DocumentPdfPreview, { props })
    const img = wrapper.find('[data-testid="pdf-preview-loading"] img')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe(props.poster)
  })

  it('error state links to open and download', async () => {
    getDocument.mockReturnValue({ promise: Promise.reject(new Error('x')) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-open"]').attributes('href')).toBe(props.openHref)
    expect(wrapper.find('[data-testid="pdf-preview-download"]').attributes('href')).toBe(props.downloadHref)
  })

  it('password state links to open', async () => {
    const err = Object.assign(new Error('locked'), { name: 'PasswordException' })
    getDocument.mockReturnValue({ promise: Promise.reject(err) })
    const wrapper = mount(DocumentPdfPreview, { props })
    await flushPromises()
    expect(wrapper.find('[data-testid="pdf-preview-password"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="pdf-preview-open"]').attributes('href')).toBe(props.openHref)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentPdfPreview.spec.ts`
Expected: FAIL — poster `img` / fallback links not found.

- [ ] **Step 3: Implement the fallback UI**

Replace the loading, password, and error template blocks in `DocumentPdfPreview.vue`:

```vue
    <div
      v-if="status === 'loading'"
      data-testid="pdf-preview-loading"
      class="relative flex h-[70vh] items-center justify-center bg-gray-100 dark:bg-gray-900/40"
    >
      <img
        v-if="poster"
        :src="poster"
        alt=""
        class="absolute inset-0 h-full w-full object-contain opacity-40"
      />
      <span class="relative text-sm text-gray-500 dark:text-gray-400">Loading preview…</span>
    </div>
```

Add a shared fallback snippet for password + error (place both blocks after the pages block):

```vue
    <div
      v-else-if="status === 'password'"
      data-testid="pdf-preview-password"
      class="flex h-[70vh] flex-col items-center justify-center gap-3 bg-gray-100 p-6 text-center text-gray-500 dark:bg-gray-900/40 dark:text-gray-400"
    >
      <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" class="h-12 w-12" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
      </svg>
      <span class="text-sm font-medium">Protected PDF — open to unlock</span>
      <a :href="openHref" target="_blank" rel="noopener" data-testid="pdf-preview-open" class="text-violet-600 hover:underline">Open</a>
    </div>
    <div
      v-else
      data-testid="pdf-preview-error"
      class="flex h-[70vh] flex-col items-center justify-center gap-2 bg-gray-100 p-6 text-center text-sm text-gray-500 dark:bg-gray-900/40 dark:text-gray-400"
    >
      <span>This preview couldn’t be displayed.</span>
      <span class="flex gap-3">
        <a :href="openHref" target="_blank" rel="noopener" data-testid="pdf-preview-open" class="text-violet-600 hover:underline">Open</a>
        <a :href="downloadHref" data-testid="pdf-preview-download" class="text-violet-600 hover:underline">Download</a>
      </span>
    </div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentPdfPreview.spec.ts`
Expected: PASS (all DocumentPdfPreview tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DocumentPdfPreview.vue \
  frontend/src/components/__tests__/DocumentPdfPreview.spec.ts
git commit -m "feat(frontend): loading poster + open/download fallbacks for pdf preview"
```

---

### Task 4: Wire into DocumentDetailView and delete the iframe workarounds

Mount `DocumentPdfPreview` on every viewport and remove the iframe, the `#toolbar=0&navpanes=0` fragment builder, the Firefox toolbar clip, and the mobile thumbnail/padlock special-cases.

**Files:**
- Modify: `frontend/src/views/DocumentDetailView.vue`
- Test: `frontend/src/views/__tests__/DocumentDetailView.spec.ts`

**Interfaces:**
- Consumes: `DocumentPdfPreview` (props `src`, `poster`, `openHref`, `downloadHref`, `initialPage`); existing computeds `pdfPreviewUrl`, `previewOpenUrl`, `previewDownloadUrl`, `pageParam`, and `thumbnailUrl(doc.id)`.
- Produces: a single `[data-testid="preview-pdf"]` mount for the PDF case.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/views/__tests__/DocumentDetailView.spec.ts` (inside the existing top-level `describe`; reuse the file's existing mount/`getDocument` mock helpers — match how other tests in this file build a `doc`):

```ts
it('renders the pdf.js preview component for a PDF document', async () => {
  // Arrange a PDF doc via the file's existing helper, then:
  const wrapper = /* mount the detail view for a doc with mime_type 'application/pdf' */ undefined as never
  await flushPromises()
  const preview = wrapper.findComponent({ name: 'DocumentPdfPreview' })
  expect(preview.exists()).toBe(true)
  expect(preview.props('src')).toContain('disposition=inline')
  // The legacy native iframe must be gone.
  expect(wrapper.find('iframe').exists()).toBe(false)
})
```

> Implementer note: replace the `undefined as never` placeholder with this file's established mount pattern (it already stubs `@/api/documents` and builds `DocumentDetail` fixtures). Set `mime_type: 'application/pdf'` and `has_thumbnail: true` on the fixture. Name the component import so `findComponent({ name: 'DocumentPdfPreview' })` resolves — add `defineOptions({ name: 'DocumentPdfPreview' })` to the component if the test can't resolve it by file name.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentDetailView.spec.ts`
Expected: FAIL — `DocumentPdfPreview` not found / `iframe` still present.

- [ ] **Step 3: Edit DocumentDetailView.vue**

1. Add the import near the other component imports:

```ts
import DocumentPdfPreview from '@/components/DocumentPdfPreview.vue'
```

2. Delete the `pdfPreviewIframeUrl` computed (the `#toolbar=0&navpanes=0&view=FitH` builder) and the `hidePdfToolbar` computed (and its `navigator`/Firefox docblock). Keep `pdfPreviewUrl`, `previewOpenUrl`, `previewDownloadUrl`, `pageParam`.

3. Replace the **entire** `<template v-else-if="preview === 'pdf'"> … </template>` block (the mobile thumbnail link, the locked padlock, and the desktop `<div class="hidden lg:block overflow-hidden"><iframe …></div>`) with:

```vue
          <DocumentPdfPreview
            v-else-if="preview === 'pdf'"
            :src="pdfPreviewUrl"
            :poster="doc.has_thumbnail ? thumbnailUrl(doc.id) : undefined"
            :open-href="previewOpenUrl"
            :download-href="previewDownloadUrl"
            :initial-page="pageParam"
            data-testid="preview-pdf"
          />
```

4. If `thumbnailUrl` isn't already imported from `@/api/documents`, add it to that import.

- [ ] **Step 4: Run the focused tests**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentDetailView.spec.ts src/components/__tests__/DocumentPdfPreview.spec.ts`
Expected: PASS.

- [ ] **Step 5: Full unit suite + type check**

Run: `cd frontend && npx vitest run && npx vue-tsc --build`
Expected: all tests PASS; no type errors. (Fix any now-unused imports flagged by `vue-tsc`, e.g. a no-longer-referenced helper.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/DocumentDetailView.vue \
  frontend/src/views/__tests__/DocumentDetailView.spec.ts \
  frontend/src/components/DocumentPdfPreview.vue
git commit -m "refactor(frontend): self-rendered pdf preview replaces native iframe"
```

---

### Task 5: Cross-browser e2e — desktop Firefox + WebKit + a scroll-through spec

Add the missing desktop engines to the Playwright matrix and a spec that proves the preview renders and scrolls in all three.

**Files:**
- Modify: `frontend/playwright.config.ts`
- Create: `frontend/e2e/pdf-preview.spec.ts`

**Interfaces:**
- Consumes: the rendered detail page with `[data-testid="preview-pdf"]` and `[data-testid="pdf-preview-pages"]`, and `canvas[data-page="N"]` per page.
- Produces: two new projects `firefox` and `webkit` (desktop); one preview spec.

- [ ] **Step 1: Add the desktop projects**

In `frontend/playwright.config.ts`, append to the `projects` array (after `tablet-webkit`):

```ts
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
```

Update the docblock comment count ("Three projects" → "Five projects … plus desktop Firefox and desktop WebKit, which render the self-drawn PDF preview").

- [ ] **Step 2: Write the e2e spec**

Create `frontend/e2e/pdf-preview.spec.ts`. Reuse the sign-in + upload helpers' pattern from `e2e/library.spec.ts` (import `signIn`/upload if exported, otherwise replicate the documented `signIn` from that file). The spec must self-skip when `E2E_BASE_URL` is unset.

```ts
/**
 * Cross-browser proof for the self-rendered (pdf.js) document preview.
 * Runs in every matrix project — crucially desktop Chromium, Firefox, and
 * WebKit — and asserts the preview paints canvases and scrolls through pages,
 * the behavior the old native <iframe> got wrong differently in each engine.
 * Skips when E2E_BASE_URL is unset (see docs/frontend.md §1.5).
 */
import { expect, test, type Page } from '@playwright/test'

const BASE_URL = process.env.E2E_BASE_URL
test.skip(!BASE_URL, 'E2E_BASE_URL is not set — start the compose stack and vite preview first')

async function openFirstPdfDocument(page: Page): Promise<void> {
  // Sign in and open the seeded/uploaded fixture document's detail page.
  // Replicate the signIn + first-tile-open steps from library.spec.ts.
}

test('renders pdf pages on a canvas and scrolls through them', async ({ page }) => {
  await openFirstPdfDocument(page)
  const preview = page.getByTestId('preview-pdf')
  await expect(preview).toBeVisible()

  // First page paints to a canvas (not a native viewer, not a black box).
  const firstCanvas = preview.locator('canvas[data-page="1"]')
  await expect(firstCanvas).toBeVisible()
  await expect.poll(async () => firstCanvas.evaluate((c: HTMLCanvasElement) => c.width)).toBeGreaterThan(0)

  // The fixture has >1 page; scrolling reveals and paints page 2.
  const pages = page.getByTestId('pdf-preview-pages')
  const secondCanvas = preview.locator('canvas[data-page="2"]')
  await secondCanvas.scrollIntoViewIfNeeded()
  await expect.poll(async () => secondCanvas.evaluate((c: HTMLCanvasElement) => c.width)).toBeGreaterThan(0)
  await expect(pages).toBeVisible()
})
```

> Implementer note: confirm `frontend/e2e/fixtures/library-fixture.pdf` has ≥2 pages; if it's single-page, drop the page-2 assertion or swap in a 2-page fixture. Fill `openFirstPdfDocument` using the exact selectors from `library.spec.ts` (sign-in form ids, the tile/heading the detail page exposes).

- [ ] **Step 3: Verify config parses and lists the new projects**

Run: `cd frontend && npx playwright test --list 2>/dev/null | head -20` (or `npx playwright test pdf-preview --list`)
Expected: the command resolves; `firefox` and `webkit` projects appear. With `E2E_BASE_URL` unset the spec self-skips — that's correct.

- [ ] **Step 4: Run against the real stack (when available)**

Per `docs/frontend.md §1.5`, bring up the compose stack + `vite preview`, export `E2E_BASE_URL`, then:

Run: `cd frontend && npx playwright test pdf-preview.spec.ts`
Expected: PASS in `chromium`, `firefox`, and `webkit` (and the mobile/tablet WebKit passes). This is the cross-browser proof.

- [ ] **Step 5: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e/pdf-preview.spec.ts
git commit -m "test(frontend): cross-browser e2e for self-rendered pdf preview"
```

---

### Task 6: Documentation

Document the self-rendered preview and the expanded e2e matrix.

**Files:**
- Modify: `docs/frontend.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update docs/frontend.md**

Find the section describing the detail-page PDF preview (it currently describes the native `<iframe>`). Replace it with a short description of `DocumentPdfPreview.vue`: pdf.js renders every page to `<canvas>`, fit-to-width, lazily via `IntersectionObserver`, identical across Chrome/Firefox/Safari; loading poster, and Open/Download fallbacks for render-failure and password-protected PDFs. Note that the Playwright matrix now includes desktop Firefox and desktop WebKit projects that exercise it. If `docs/frontend.md` enumerates dependencies, add `pdfjs-dist`.

- [ ] **Step 2: Verify no stale iframe references remain**

Run: `grep -rn -iE "iframe|navpanes|hidePdfToolbar" docs/frontend.md`
Expected: no matches (or only historical/archive notes). Fix any stale active-doc references.

- [ ] **Step 3: Commit**

```bash
git add docs/frontend.md
git commit -m "docs(frontend): document self-rendered pdf.js preview"
```

---

## Self-Review

**Spec coverage:**
- pdf.js self-render to canvas → Tasks 1-2. ✓
- All pages, scrollable, lazy IntersectionObserver → Task 2. ✓
- Loading poster / error / password states → Tasks 1, 3. ✓
- Unified path, delete iframe + `pdfPreviewIframeUrl` + `hidePdfToolbar` + mobile special-cases → Task 4. ✓
- Worker via Vite `new URL(...)` → Task 1. ✓
- `pdfjs-dist` dependency → Task 1. ✓
- Unit tests (state machine, lazy render, fallbacks) → Tasks 1-3. ✓
- E2e desktop Firefox + WebKit projects + scroll-through spec → Task 5. ✓
- docs/frontend.md → Task 6. ✓
- Out-of-scope items (inline password entry, zoom, text search) correctly omitted. ✓

**Placeholder scan:** The two `> Implementer note` blocks (Tasks 4 & 5) intentionally defer to existing in-file test/e2e patterns rather than duplicating their fixtures verbatim; each names the exact selectors/props to use. No "TBD"/"add error handling"-style gaps.

**Type consistency:** `status` values, `pageCount`, prop names (`src`/`poster`/`openHref`/`downloadHref`/`initialPage`), `renderPage(n)`, `scrollToPage(n)`, and the `data-testid` values (`pdf-preview-loading|pages|password|error|open|download`, `preview-pdf`, `data-page`) are used identically across Tasks 1-5. ✓
