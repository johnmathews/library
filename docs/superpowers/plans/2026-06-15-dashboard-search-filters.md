# Dashboard Inline Search & Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the nav-bar-only search with an always-visible search input, dropdown filter pills (Kind, Sender, Date, Tag, and a "More" pill for Language + Status), and removable active-filter chips in the dashboard hero — all driven by the URL query string.

**Architecture:** Frontend-only; the API (`GET /api/documents`) already accepts every filter. URL stays the single source of truth. A new pure utility (`utils/documentQuery.ts`) parses/builds the query so the view, the new filter bar, and tests share one implementation. A new `DocumentFilterBar.vue` renders the hero UI; a new reusable `FilterPill.vue` popover primitive backs each dropdown. The view keeps owning fetch + routing.

**Tech Stack:** Vue 3 (`<script setup lang="ts">`), vue-router, Pinia, Tailwind, Vitest + @vue/test-utils. Run tests from `frontend/`.

**Spec:** `docs/superpowers/specs/2026-06-15-dashboard-search-filters-design.md`

---

## File Structure

- **Create** `frontend/src/utils/documentQuery.ts` — pure parse/build of the documents URL query; the `AppliedFilters` type. One responsibility: query ⇆ applied-state.
- **Create** `frontend/src/utils/__tests__/documentQuery.spec.ts` — unit tests for the above.
- **Create** `frontend/src/components/app/FilterPill.vue` — generic controlled popover (button + panel slot). One responsibility: open/close/keyboard/click-outside.
- **Create** `frontend/src/components/app/__tests__/FilterPill.spec.ts`.
- **Create** `frontend/src/components/DocumentFilterBar.vue` — the hero search + pills + chips. One responsibility: present filter UI, emit the next query.
- **Create** `frontend/src/components/__tests__/DocumentFilterBar.spec.ts`.
- **Modify** `frontend/src/api/documents.ts` — add `DOCUMENT_STATUSES` options array.
- **Modify** `frontend/src/api/__tests__/documents.spec.ts` — assert `DOCUMENT_STATUSES`.
- **Modify** `frontend/src/components/app/index.ts` — export `FilterPill`.
- **Modify** `frontend/src/views/DocumentListView.vue` — use the utility, render `DocumentFilterBar`, drop the old text filter-summary line, pass `tags`/`status` to fetch.
- **Modify** `frontend/src/views/__tests__/DocumentListView.spec.ts` — update for multi-tag/status and the bar.
- **Modify** `docs/frontend.md` — document the filter bar + behaviours.
- **Create** `journal/260615-dashboard-search-filters.md`.

Run all frontend test commands from the `frontend/` directory.

---

## Task 1: `DOCUMENT_STATUSES` options array

**Files:**
- Modify: `frontend/src/api/documents.ts` (after the `DOCUMENT_LANGUAGES` block, ~line 152)
- Test: `frontend/src/api/__tests__/documents.spec.ts`

- [ ] **Step 1: Write the failing test**

Add this `describe` block to `frontend/src/api/__tests__/documents.spec.ts` (and add `DOCUMENT_STATUSES` to the existing top-of-file import from `../documents`):

```ts
import { DOCUMENT_STATUSES } from '../documents'

describe('DOCUMENT_STATUSES', () => {
  it('lists every document status with a human label', () => {
    expect(DOCUMENT_STATUSES.map((s) => s.value)).toEqual([
      'received',
      'ocr',
      'extract',
      'indexed',
      'failed',
    ])
    expect(DOCUMENT_STATUSES.find((s) => s.value === 'indexed')?.text).toBe('Indexed')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/api/__tests__/documents.spec.ts -t DOCUMENT_STATUSES`
Expected: FAIL — `DOCUMENT_STATUSES` is not exported.

- [ ] **Step 3: Add the export**

In `frontend/src/api/documents.ts`, immediately after the `DOCUMENT_LANGUAGES` `as const` array, add:

```ts
export const DOCUMENT_STATUSES: readonly { value: DocumentStatus; text: string }[] = [
  { value: 'received', text: 'Received' },
  { value: 'ocr', text: 'OCR' },
  { value: 'extract', text: 'Extracting' },
  { value: 'indexed', text: 'Indexed' },
  { value: 'failed', text: 'Failed' },
] as const
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/api/__tests__/documents.spec.ts -t DOCUMENT_STATUSES`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/documents.ts frontend/src/api/__tests__/documents.spec.ts
git commit -m "feat(frontend): add DOCUMENT_STATUSES options for status filter"
```

---

## Task 2: `documentQuery` utility (parse / build / active-check)

This extracts the URL ⇆ state logic currently inline in `DocumentListView.vue` into a tested pure module, extended to support **multiple tags** and **status**.

**Files:**
- Create: `frontend/src/utils/documentQuery.ts`
- Test: `frontend/src/utils/__tests__/documentQuery.spec.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/utils/__tests__/documentQuery.spec.ts`:

```ts
import { describe, expect, it } from 'vitest'
import {
  buildDocumentQuery,
  hasActiveFilters,
  parseDocumentQuery,
  type AppliedFilters,
} from '../documentQuery'

const EMPTY: AppliedFilters = {
  q: '',
  kind: '',
  senderId: '',
  tags: [],
  language: '',
  status: '',
  dateFrom: '',
  dateTo: '',
  page: 1,
}

describe('parseDocumentQuery', () => {
  it('defaults to empty applied state for an empty query', () => {
    expect(parseDocumentQuery({})).toEqual(EMPTY)
  })

  it('parses scalar filters, status and page', () => {
    const applied = parseDocumentQuery({
      q: 'rekening',
      kind: 'invoice',
      sender_id: '3',
      language: 'nld',
      status: 'indexed',
      date_from: '2026-05-01',
      date_to: '2026-05-31',
      page: '2',
    })
    expect(applied).toEqual({
      q: 'rekening',
      kind: 'invoice',
      senderId: '3',
      tags: [],
      language: 'nld',
      status: 'indexed',
      dateFrom: '2026-05-01',
      dateTo: '2026-05-31',
      page: 2,
    })
  })

  it('parses a single tag into a one-element array (back-compat)', () => {
    expect(parseDocumentQuery({ tag: 'energie' }).tags).toEqual(['energie'])
  })

  it('parses repeated tags into an array', () => {
    expect(parseDocumentQuery({ tag: ['energie', 'wonen'] }).tags).toEqual(['energie', 'wonen'])
  })

  it('clamps a bad page to 1', () => {
    expect(parseDocumentQuery({ page: 'nonsense' }).page).toBe(1)
    expect(parseDocumentQuery({ page: '0' }).page).toBe(1)
  })
})

describe('buildDocumentQuery', () => {
  it('round-trips a fully-populated applied state, omitting page 1', () => {
    const applied: AppliedFilters = {
      q: 'rekening',
      kind: 'invoice',
      senderId: '3',
      tags: ['energie', 'wonen'],
      language: 'nld',
      status: 'indexed',
      dateFrom: '2026-05-01',
      dateTo: '2026-05-31',
      page: 1,
    }
    expect(buildDocumentQuery(applied)).toEqual({
      q: 'rekening',
      kind: 'invoice',
      sender_id: '3',
      tag: ['energie', 'wonen'],
      language: 'nld',
      status: 'indexed',
      date_from: '2026-05-01',
      date_to: '2026-05-31',
    })
  })

  it('includes page when greater than 1', () => {
    expect(buildDocumentQuery({ ...EMPTY, q: 'x', page: 3 })).toEqual({ q: 'x', page: '3' })
  })

  it('omits empty filters entirely', () => {
    expect(buildDocumentQuery(EMPTY)).toEqual({})
  })

  it('accepts a page override (used when changing a filter resets to page 1)', () => {
    expect(buildDocumentQuery({ ...EMPTY, q: 'x', page: 5 }, 1)).toEqual({ q: 'x' })
  })
})

describe('hasActiveFilters', () => {
  it('is false for empty state and true once any filter is set', () => {
    expect(hasActiveFilters(EMPTY)).toBe(false)
    expect(hasActiveFilters({ ...EMPTY, q: 'x' })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, tags: ['energie'] })).toBe(true)
    expect(hasActiveFilters({ ...EMPTY, status: 'failed' })).toBe(true)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/utils/__tests__/documentQuery.spec.ts`
Expected: FAIL — module `../documentQuery` does not exist.

- [ ] **Step 3: Implement the utility**

Create `frontend/src/utils/documentQuery.ts`:

```ts
/**
 * Pure URL-query ⇆ applied-state for the documents list (route `/`).
 *
 * The dashboard keeps all filter state in the URL so back/forward, refresh
 * and shared links round-trip. The list view, the search modal and the
 * dashboard filter bar all read/write the query through these helpers, so
 * the param names and parsing rules live in exactly one place.
 *
 * `tag` repeats in the URL (?tag=a&tag=b) and ANDs — hence `tags: string[]`.
 */
import type { LocationQuery, LocationQueryRaw } from 'vue-router'

export interface AppliedFilters {
  q: string
  kind: string
  senderId: string
  tags: string[]
  language: string
  status: string
  dateFrom: string
  dateTo: string
  page: number
}

function asString(value: LocationQuery[string]): string {
  return typeof value === 'string' ? value : ''
}

/** A query value may be a string, an array (repeated key) or null. */
function asStringArray(value: LocationQuery[string]): string[] {
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === 'string')
  return typeof value === 'string' ? [value] : []
}

/** Parse the route query into the strongly-typed applied state. */
export function parseDocumentQuery(query: LocationQuery): AppliedFilters {
  return {
    q: asString(query.q),
    kind: asString(query.kind),
    senderId: asString(query.sender_id),
    tags: asStringArray(query.tag),
    language: asString(query.language),
    status: asString(query.status),
    dateFrom: asString(query.date_from),
    dateTo: asString(query.date_to),
    page: Math.max(1, Number.parseInt(asString(query.page), 10) || 1),
  }
}

/**
 * Rebuild the URL query from applied state. Empty filters and page 1 are
 * omitted. Pass `page` to override (e.g. reset to 1 when a filter changes).
 */
export function buildDocumentQuery(
  applied: AppliedFilters,
  page: number = applied.page,
): LocationQueryRaw {
  const query: LocationQueryRaw = {}
  if (applied.q) query.q = applied.q
  if (applied.kind) query.kind = applied.kind
  if (applied.senderId) query.sender_id = applied.senderId
  if (applied.tags.length) query.tag = [...applied.tags]
  if (applied.language) query.language = applied.language
  if (applied.status) query.status = applied.status
  if (applied.dateFrom) query.date_from = applied.dateFrom
  if (applied.dateTo) query.date_to = applied.dateTo
  if (page > 1) query.page = String(page)
  return query
}

/** True when any filter (incl. the search text) is applied. */
export function hasActiveFilters(applied: AppliedFilters): boolean {
  return Boolean(
    applied.q ||
      applied.kind ||
      applied.senderId ||
      applied.tags.length ||
      applied.language ||
      applied.status ||
      applied.dateFrom ||
      applied.dateTo,
  )
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/utils/__tests__/documentQuery.spec.ts`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/utils/documentQuery.ts frontend/src/utils/__tests__/documentQuery.spec.ts
git commit -m "feat(frontend): add documentQuery utility with multi-tag + status support"
```

---

## Task 3: Refactor `DocumentListView` onto the utility

Swap the inline `applied` / `buildQuery` / `isFiltered` for the new utility, and pass `tags` (array) and `status` to the fetch. No new UI yet — the existing text filter-summary line stays for now (removed in Task 6). This keeps the view's existing tests green while the data layer gains multi-tag/status.

**Files:**
- Modify: `frontend/src/views/DocumentListView.vue` (script section, lines ~13–171)
- Test: `frontend/src/views/__tests__/DocumentListView.spec.ts`

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/views/__tests__/DocumentListView.spec.ts` (inside the top-level `describe('DocumentListView', …)`; reuse the file's existing `mountView`/router/fetch helpers — match their names when you open the file). This asserts a two-tag URL drives a two-`tag` request and a `status` param passes through:

```ts
it('sends repeated tag params and status from the URL to the API', async () => {
  await router.push('/?tag=energie&tag=wonen&status=indexed')
  await mountView([])
  await flushPromises()

  const listCall = fetchMock.mock.calls
    .map((c) => String(c[0]))
    .find((url) => url.startsWith('/api/documents'))
  expect(listCall).toBeDefined()
  const params = new URLSearchParams(listCall!.split('?')[1])
  expect(params.getAll('tag')).toEqual(['energie', 'wonen'])
  expect(params.get('status')).toBe('indexed')
})
```

> If the spec file's mount helper has a different name/signature, adapt this call to it — the assertion (two `tag` params + `status` reach `/api/documents`) is what matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts -t "repeated tag"`
Expected: FAIL — current code parses only a single `tag` string, so only one `tag` is sent and `status` is absent.

- [ ] **Step 3: Refactor the view script**

In `frontend/src/views/DocumentListView.vue`:

(a) Replace the `queryString` helper + `applied` computed + `isFiltered` computed + `clearFilters` + `buildQuery` (lines ~51–95) with:

```ts
import {
  parseDocumentQuery,
  buildDocumentQuery,
  hasActiveFilters,
} from '@/utils/documentQuery'

// --- Applied state (the URL is the source of truth) -----------------------

const applied = computed(() => parseDocumentQuery(route.query))
const isFiltered = computed(() => hasActiveFilters(applied.value))

function clearFilters(): void {
  void router.push({ query: {} })
}

function goToPage(page: number): void {
  void router.push({ query: buildDocumentQuery(applied.value, page) })
}
```

Remove the now-unused `LocationQuery` / `LocationQueryRaw` type imports from the `vue-router` import line if nothing else uses them (keep `useRoute`, `useRouter`).

(b) Update the taxonomy-name watch (lines ~104–110) and `filterSummary` (lines ~112–126) to the new field names (`senderId` unchanged; `tag` → `tags`):

```ts
watch(
  () => Boolean(applied.value.kind || applied.value.senderId || applied.value.tags.length),
  (needsNames) => {
    if (needsNames) void ensureLoaded()
  },
  { immediate: true },
)

const filterSummary = computed<string[]>(() => {
  const a = applied.value
  const parts: string[] = []
  if (a.q) parts.push(`search “${a.q}”`)
  if (a.kind) parts.push(`kind ${kinds.value.find((k) => k.slug === a.kind)?.name ?? a.kind}`)
  if (a.senderId) {
    const name = senders.value.find((s) => String(s.id) === a.senderId)?.name
    parts.push(`sender ${name ?? `#${a.senderId}`}`)
  }
  for (const slug of a.tags) {
    parts.push(`tag ${tags.value.find((t) => t.slug === slug)?.name ?? slug}`)
  }
  if (a.language) parts.push(`language ${languageName(a.language as DocumentLanguage)}`)
  if (a.status) parts.push(`status ${a.status}`)
  if (a.dateFrom) parts.push(`dated from ${formatDate(a.dateFrom)}`)
  if (a.dateTo) parts.push(`dated to ${formatDate(a.dateTo)}`)
  return parts
})
```

(c) Update the fetch watcher's `filters` object (lines ~145–156) to use the array `tags` and `status`:

```ts
const senderId = Number.parseInt(state.senderId, 10)
const filters: DocumentFilters = {
  q: state.q || undefined,
  kind: state.kind || undefined,
  sender_id: Number.isInteger(senderId) ? senderId : undefined,
  tag: state.tags.length ? state.tags : undefined,
  language: (state.language || undefined) as DocumentLanguage | undefined,
  status: (state.status || undefined) as DocumentListItem['status'] | undefined,
  date_from: state.dateFrom || undefined,
  date_to: state.dateTo || undefined,
  limit: PAGE_SIZE,
  offset: (state.page - 1) * PAGE_SIZE,
}
```

(d) Find any remaining `buildQuery(` call (e.g. in `goToPage`, already replaced above) and ensure none reference the old name. Search the file for `buildQuery` and `applied.value.tag` (singular) — there should be zero matches after this step.

- [ ] **Step 4: Run the view tests**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts`
Expected: PASS — the new repeated-tag/status test passes and all pre-existing tests still pass. If a pre-existing test asserted the old single-`tag` summary wording, update it to the new per-tag wording.

- [ ] **Step 5: Type-check**

Run: `cd frontend && npx vue-tsc --build`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/DocumentListView.vue frontend/src/views/__tests__/DocumentListView.spec.ts
git commit -m "refactor(frontend): drive DocumentListView via documentQuery util; multi-tag + status"
```

---

## Task 4: `FilterPill` popover primitive

A controlled (open prop + `update:open` emit) pill button with a slotted dropdown panel. Closes on outside click and `Escape` (returning focus to the button). The parent enforces "one open at a time" by binding `:open` to a shared ref.

**Files:**
- Create: `frontend/src/components/app/FilterPill.vue`
- Test: `frontend/src/components/app/__tests__/FilterPill.spec.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/app/__tests__/FilterPill.spec.ts`:

```ts
import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import FilterPill from '../FilterPill.vue'

function mountPill(props: Record<string, unknown> = {}): VueWrapper {
  return mount(FilterPill, {
    attachTo: document.body,
    props: { label: 'Kind', open: false, ...props },
    slots: { default: '<div data-testid="panel-body">panel</div>' },
  })
}

describe('FilterPill', () => {
  let wrapper: VueWrapper | undefined
  afterEach(() => {
    wrapper?.unmount()
    wrapper = undefined
    document.body.replaceChildren()
  })

  it('shows the label, and the value label + active styling when active', () => {
    wrapper = mountPill({ active: true, valueLabel: 'Invoice' })
    const button = wrapper.get('[data-testid="filter-pill-button"]')
    expect(button.text()).toContain('Kind')
    expect(button.text()).toContain('Invoice')
    expect(button.attributes('aria-pressed')).toBe('true')
  })

  it('emits update:open when the button is clicked', async () => {
    wrapper = mountPill({ open: false })
    await wrapper.get('[data-testid="filter-pill-button"]').trigger('click')
    expect(wrapper.emitted('update:open')?.[0]).toEqual([true])
  })

  it('renders the panel only when open', async () => {
    wrapper = mountPill({ open: false })
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(false)
    await wrapper.setProps({ open: true })
    expect(wrapper.find('[data-testid="panel-body"]').exists()).toBe(true)
  })

  it('emits update:open=false on Escape and on outside click', async () => {
    wrapper = mountPill({ open: true })
    await wrapper.get('[data-testid="filter-pill-button"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])

    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/app/__tests__/FilterPill.spec.ts`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/app/FilterPill.vue`:

```vue
<script setup lang="ts">
/**
 * A filter "pill": a rounded button that toggles a dropdown panel beneath it.
 * Controlled — the parent owns the open flag via `v-model:open`, which lets a
 * filter bar keep only one pill open at a time. Closes on Escape (focus
 * returns to the button) and on outside mousedown. The panel content is a slot.
 */
import { onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{
  label: string
  open: boolean
  active?: boolean
  valueLabel?: string
}>()

const emit = defineEmits<{ 'update:open': [boolean] }>()

const root = ref<HTMLElement | null>(null)
const button = ref<HTMLButtonElement | null>(null)

function toggle(): void {
  emit('update:open', !props.open)
}

function close(): void {
  emit('update:open', false)
}

function onEscape(): void {
  close()
  button.value?.focus()
}

function onOutsideMousedown(event: MouseEvent): void {
  if (root.value && event.target instanceof Node && !root.value.contains(event.target)) {
    close()
  }
}

// Listen for outside clicks only while open.
watch(
  () => props.open,
  (open) => {
    if (open) {
      document.addEventListener('mousedown', onOutsideMousedown)
    } else {
      document.removeEventListener('mousedown', onOutsideMousedown)
    }
  },
)

onBeforeUnmount(() => document.removeEventListener('mousedown', onOutsideMousedown))
</script>

<template>
  <div ref="root" class="relative inline-flex" @keydown.escape.stop="onEscape">
    <button
      ref="button"
      type="button"
      data-testid="filter-pill-button"
      class="inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-sm transition-colors"
      :class="
        props.active
          ? 'border-violet-500 bg-violet-50 text-violet-700 dark:border-violet-500 dark:bg-violet-500/15 dark:text-violet-200'
          : 'border-gray-300 bg-white text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700/60'
      "
      :aria-expanded="props.open"
      :aria-pressed="props.active ? 'true' : 'false'"
      aria-haspopup="true"
      @click="toggle"
    >
      <span class="font-medium">{{ props.label }}</span>
      <span v-if="props.active && props.valueLabel" class="text-gray-500 dark:text-gray-400"
        >: {{ props.valueLabel }}</span
      >
      <svg class="h-3 w-3 fill-current opacity-70" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M5.9 11.4L.5 6l1.4-1.4 4 4 4-4L11.3 6z" />
      </svg>
    </button>

    <Transition
      enter-active-class="transition ease-out duration-150"
      enter-from-class="opacity-0 -translate-y-1"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition ease-out duration-150"
      leave-from-class="opacity-100 translate-y-0"
      leave-to-class="opacity-0 -translate-y-1"
    >
      <div
        v-show="props.open"
        v-if="props.open"
        data-testid="filter-pill-panel"
        class="absolute left-0 top-full z-20 mt-1 min-w-56 rounded-lg border border-gray-200 bg-white p-3 shadow-lg dark:border-gray-700/60 dark:bg-gray-800"
      >
        <slot />
      </div>
    </Transition>
  </div>
</template>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/app/__tests__/FilterPill.spec.ts`
Expected: PASS.

- [ ] **Step 5: Export from the app barrel**

In `frontend/src/components/app/index.ts`, add (keep the list alphabetical — after `AppFileUpload`):

```ts
export { default as FilterPill } from './FilterPill.vue'
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/app/FilterPill.vue frontend/src/components/app/__tests__/FilterPill.spec.ts frontend/src/components/app/index.ts
git commit -m "feat(frontend): add FilterPill popover primitive"
```

---

## Task 5: `DocumentFilterBar` component

The hero UI: a debounced search input, a row of `FilterPill`s (Kind, Sender, Date, Tag multi-select, More = Language + Status), and removable active-filter chips with "Clear all". It receives `:applied` and taxonomy, and emits `apply(query, { replace })` and `clear()`. It computes the next query with `buildDocumentQuery` (always resetting to page 1).

**Files:**
- Create: `frontend/src/components/DocumentFilterBar.vue`
- Test: `frontend/src/components/__tests__/DocumentFilterBar.spec.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/__tests__/DocumentFilterBar.spec.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import DocumentFilterBar from '../DocumentFilterBar.vue'
import { resetTaxonomyOptionsForTests } from '@/composables/taxonomyOptions'
import type { AppliedFilters } from '@/utils/documentQuery'

const EMPTY: AppliedFilters = {
  q: '',
  kind: '',
  senderId: '',
  tags: [],
  language: '',
  status: '',
  dateFrom: '',
  dateTo: '',
  page: 1,
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const KINDS = [{ slug: 'invoice', name: 'Invoice', document_count: 3 }]
const SENDERS = [{ id: 3, name: 'Eneco', document_count: 3 }]
const TAGS = [
  { slug: 'energie', name: 'Energie', document_count: 2 },
  { slug: 'wonen', name: 'Wonen', document_count: 1 },
]

function mountBar(applied: AppliedFilters = EMPTY): VueWrapper {
  return mount(DocumentFilterBar, {
    attachTo: document.body,
    props: { applied },
  })
}

describe('DocumentFilterBar', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    resetTaxonomyOptionsForTests()
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockReset()
    fetchMock.mockImplementation((input: unknown) => {
      const url = String(input)
      if (url === '/api/kinds') return Promise.resolve(jsonResponse(KINDS))
      if (url === '/api/senders') return Promise.resolve(jsonResponse(SENDERS))
      if (url === '/api/tags') return Promise.resolve(jsonResponse(TAGS))
      return Promise.resolve(jsonResponse({ detail: `unexpected ${url}` }, 500))
    })
  })

  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('initialises the search input from applied.q', () => {
    const w = mountBar({ ...EMPTY, q: 'rekening' })
    expect((w.get('[data-testid="filter-search"]').element as HTMLInputElement).value).toBe(
      'rekening',
    )
  })

  it('emits a debounced replace apply while typing', async () => {
    vi.useFakeTimers()
    const w = mountBar()
    await w.get('[data-testid="filter-search"]').setValue('reken')
    expect(w.emitted('apply')).toBeUndefined() // not yet — debounced
    vi.advanceTimersByTime(300)
    const [query, opts] = w.emitted('apply')![0] as [Record<string, unknown>, { replace: boolean }]
    expect(query).toEqual({ q: 'reken' })
    expect(opts).toEqual({ replace: true })
  })

  it('emits immediately (push) on Enter', async () => {
    const w = mountBar()
    await w.get('[data-testid="filter-search"]').setValue('reken')
    await w.get('[data-testid="filter-search"]').trigger('keydown.enter')
    const [query, opts] = w.emitted('apply')!.at(-1) as [
      Record<string, unknown>,
      { replace: boolean } | undefined,
    ]
    expect(query).toEqual({ q: 'reken' })
    expect(opts?.replace).toBeFalsy()
  })

  it('selecting a kind emits a push apply with the kind slug', async () => {
    const w = mountBar()
    await flushPromises() // taxonomy load
    await w.get('[data-testid="pill-kind"] [data-testid="filter-pill-button"]').trigger('click')
    await w.get('[data-testid="kind-option-invoice"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ kind: 'invoice' })
  })

  it('selecting multiple tags emits repeated tag', async () => {
    const w = mountBar()
    await flushPromises()
    await w.get('[data-testid="pill-tag"] [data-testid="filter-pill-button"]').trigger('click')
    await w.get('#filter-tags').get('input[value="energie"]').setValue(true)
    await w.get('#filter-tags').get('input[value="wonen"]').setValue(true)
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ tag: ['energie', 'wonen'] })
  })

  it('renders a removable chip per active filter and emits apply without that filter on remove', async () => {
    const w = mountBar({ ...EMPTY, q: 'rekening', kind: 'invoice' })
    await flushPromises()
    const chips = w.findAll('[data-testid^="chip-"]')
    expect(chips.length).toBe(2)
    await w.get('[data-testid="chip-remove-kind"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ q: 'rekening' })
  })

  it('removing one tag keeps the others', async () => {
    const w = mountBar({ ...EMPTY, tags: ['energie', 'wonen'] })
    await flushPromises()
    await w.get('[data-testid="chip-remove-tag-energie"]').trigger('click')
    expect(w.emitted('apply')!.at(-1)![0]).toEqual({ tag: ['wonen'] })
  })

  it('Clear all emits clear', async () => {
    const w = mountBar({ ...EMPTY, q: 'rekening' })
    await w.get('[data-testid="filter-clear-all"]').trigger('click')
    expect(w.emitted('clear')).toHaveLength(1)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentFilterBar.spec.ts`
Expected: FAIL — component does not exist.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/DocumentFilterBar.vue`:

```vue
<script setup lang="ts">
/**
 * Dashboard filter bar (route `/`): a debounced search input, a row of filter
 * pills (Kind, Sender, Date, Tag multi-select, and a "More" pill for Language +
 * Status), and removable active-filter chips.
 *
 * Controlled by the URL: the parent passes the parsed `applied` state in, and
 * this component emits the next query out — `apply(query, { replace })` for the
 * parent to push (discrete filter change) or replace (debounced typing), and
 * `clear()` to drop every filter. Changing any filter resets to page 1, so the
 * emitted query never carries a page. Taxonomy names come from the shared cache.
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { LocationQueryRaw } from 'vue-router'
import { AppCheckboxes, AppDateInput, FilterPill } from '@/components/app'
import type { ChoiceItem } from '@/components/app'
import {
  DOCUMENT_LANGUAGES,
  DOCUMENT_STATUSES,
  type DocumentLanguage,
  type DocumentStatus,
} from '@/api/documents'
import { useTaxonomyOptions } from '@/composables/taxonomyOptions'
import { buildDocumentQuery, type AppliedFilters } from '@/utils/documentQuery'

const SEARCH_DEBOUNCE_MS = 300

const props = defineProps<{ applied: AppliedFilters }>()
const emit = defineEmits<{
  apply: [LocationQueryRaw, { replace?: boolean }?]
  clear: []
}>()

const { kinds, senders, tags, ensureLoaded } = useTaxonomyOptions()
void ensureLoaded()

// Which pill popover is open (only one at a time); null = all closed.
const openPill = ref<string | null>(null)
function pillOpen(name: string): boolean {
  return openPill.value === name
}
function setPillOpen(name: string, open: boolean): void {
  openPill.value = open ? name : openPill.value === name ? null : openPill.value
}

// --- Search input (debounced) ---------------------------------------------

const searchText = ref(props.applied.q)
let debounceTimer: ReturnType<typeof setTimeout> | null = null

// Keep the field in sync if the query changes elsewhere (modal, chip removal).
watch(
  () => props.applied.q,
  (q) => {
    if (q !== searchText.value) searchText.value = q
  },
)

function emitWith(overrides: Partial<AppliedFilters>, replace = false): void {
  const next: AppliedFilters = { ...props.applied, ...overrides }
  emit('apply', buildDocumentQuery(next, 1), { replace })
}

function onSearchInput(): void {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => emitWith({ q: searchText.value.trim() }, true), SEARCH_DEBOUNCE_MS)
}

function onSearchEnter(): void {
  if (debounceTimer) clearTimeout(debounceTimer)
  emitWith({ q: searchText.value.trim() })
}

function clearSearch(): void {
  searchText.value = ''
  if (debounceTimer) clearTimeout(debounceTimer)
  emitWith({ q: '' })
}

onBeforeUnmount(() => {
  if (debounceTimer) clearTimeout(debounceTimer)
})

// --- Discrete filter changes ----------------------------------------------

function selectKind(slug: string): void {
  emitWith({ kind: slug })
  openPill.value = null
}
function selectSender(id: string): void {
  emitWith({ senderId: id })
  openPill.value = null
}
function selectLanguage(value: string): void {
  emitWith({ language: value })
}
function selectStatus(value: string): void {
  emitWith({ status: value })
}

// Tag multi-select via AppCheckboxes (writes straight through to the query).
const tagModel = computed<string[]>({
  get: () => props.applied.tags,
  set: (next) => emitWith({ tags: next }),
})
const tagItems = computed<ChoiceItem[]>(() =>
  tags.value.map((t) => ({ value: t.slug, text: t.name })),
)

// Date range via two AppDateInputs.
const dateFromModel = computed<string | null>({
  get: () => props.applied.dateFrom || null,
  set: (value) => emitWith({ dateFrom: value ?? '' }),
})
const dateToModel = computed<string | null>({
  get: () => props.applied.dateTo || null,
  set: (value) => emitWith({ dateTo: value ?? '' }),
})

// --- Pill value labels -----------------------------------------------------

const kindLabel = computed(
  () => kinds.value.find((k) => k.slug === props.applied.kind)?.name ?? props.applied.kind,
)
const senderLabel = computed(
  () =>
    senders.value.find((s) => String(s.id) === props.applied.senderId)?.name ??
    props.applied.senderId,
)
const tagPillLabel = computed(() => {
  const n = props.applied.tags.length
  if (!n) return ''
  const first = tags.value.find((t) => t.slug === props.applied.tags[0])?.name ?? props.applied.tags[0]
  return n > 1 ? `${first} +${n - 1}` : first
})
const dateActive = computed(() => Boolean(props.applied.dateFrom || props.applied.dateTo))
const moreActive = computed(() => Boolean(props.applied.language || props.applied.status))
const languageName = (value: string): string =>
  DOCUMENT_LANGUAGES.find((l) => l.value === value)?.text ?? value
const statusName = (value: string): string =>
  DOCUMENT_STATUSES.find((s) => s.value === value)?.text ?? value

// --- Active-filter chips ---------------------------------------------------

interface Chip {
  key: string
  label: string
  remove: () => void
}

const chips = computed<Chip[]>(() => {
  const a = props.applied
  const out: Chip[] = []
  if (a.q) out.push({ key: 'q', label: `“${a.q}”`, remove: () => clearSearch() })
  if (a.kind)
    out.push({ key: 'kind', label: `Kind: ${kindLabel.value}`, remove: () => emitWith({ kind: '' }) })
  if (a.senderId)
    out.push({
      key: 'sender',
      label: `Sender: ${senderLabel.value}`,
      remove: () => emitWith({ senderId: '' }),
    })
  for (const slug of a.tags) {
    const name = tags.value.find((t) => t.slug === slug)?.name ?? slug
    out.push({
      key: `tag-${slug}`,
      label: `Tag: ${name}`,
      remove: () => emitWith({ tags: a.tags.filter((s) => s !== slug) }),
    })
  }
  if (a.language)
    out.push({
      key: 'language',
      label: `Language: ${languageName(a.language)}`,
      remove: () => emitWith({ language: '' }),
    })
  if (a.status)
    out.push({
      key: 'status',
      label: `Status: ${statusName(a.status)}`,
      remove: () => emitWith({ status: '' }),
    })
  if (a.dateFrom)
    out.push({
      key: 'date-from',
      label: `From ${a.dateFrom}`,
      remove: () => emitWith({ dateFrom: '' }),
    })
  if (a.dateTo)
    out.push({ key: 'date-to', label: `To ${a.dateTo}`, remove: () => emitWith({ dateTo: '' }) })
  return out
})

const languageOptions = DOCUMENT_LANGUAGES
const statusOptions = DOCUMENT_STATUSES
</script>

<template>
  <div class="mb-5" data-testid="document-filter-bar">
    <!-- Search -->
    <div class="relative mb-3 max-w-xl">
      <svg
        class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400"
        viewBox="0 0 16 16"
        fill="currentColor"
        aria-hidden="true"
      >
        <path
          d="M7 14c-3.86 0-7-3.14-7-7s3.14-7 7-7 7 3.14 7 7-3.14 7-7 7ZM7 2C4.243 2 2 4.243 2 7s2.243 5 5 5 5-2.243 5-5-2.243-5-5-5Z"
        />
        <path
          d="M15.707 14.293 13.314 11.9a8.019 8.019 0 0 1-1.414 1.414l2.393 2.393a.997.997 0 0 0 1.414 0 .999.999 0 0 0 0-1.414Z"
        />
      </svg>
      <input
        v-model="searchText"
        data-testid="filter-search"
        type="search"
        inputmode="search"
        :spellcheck="false"
        placeholder="Search documents…"
        aria-label="Search documents"
        class="form-input w-full rounded-lg pl-9 pr-9"
        @input="onSearchInput"
        @keydown.enter.prevent="onSearchEnter"
      />
      <button
        v-if="searchText"
        type="button"
        data-testid="filter-search-clear"
        class="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
        aria-label="Clear search"
        @click="clearSearch"
      >
        ✕
      </button>
    </div>

    <!-- Pills -->
    <div class="flex flex-wrap items-center gap-2">
      <FilterPill
        data-testid="pill-kind"
        label="Kind"
        :active="Boolean(applied.kind)"
        :value-label="kindLabel"
        :open="pillOpen('kind')"
        @update:open="setPillOpen('kind', $event)"
      >
        <ul class="max-h-64 overflow-auto text-sm">
          <li v-for="k in kinds" :key="k.slug">
            <button
              type="button"
              :data-testid="`kind-option-${k.slug}`"
              class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
              :class="{ 'font-semibold text-violet-600 dark:text-violet-300': applied.kind === k.slug }"
              @click="selectKind(k.slug)"
            >
              {{ k.name }}
            </button>
          </li>
        </ul>
      </FilterPill>

      <FilterPill
        data-testid="pill-sender"
        label="Sender"
        :active="Boolean(applied.senderId)"
        :value-label="senderLabel"
        :open="pillOpen('sender')"
        @update:open="setPillOpen('sender', $event)"
      >
        <ul class="max-h-64 overflow-auto text-sm">
          <li v-for="s in senders" :key="s.id">
            <button
              type="button"
              :data-testid="`sender-option-${s.id}`"
              class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
              :class="{ 'font-semibold text-violet-600 dark:text-violet-300': applied.senderId === String(s.id) }"
              @click="selectSender(String(s.id))"
            >
              {{ s.name }}
            </button>
          </li>
        </ul>
      </FilterPill>

      <FilterPill
        data-testid="pill-date"
        label="Date"
        :active="dateActive"
        :open="pillOpen('date')"
        @update:open="setPillOpen('date', $event)"
      >
        <div class="space-y-3">
          <AppDateInput id="filter-date-from" v-model="dateFromModel" legend="Dated from" />
          <AppDateInput id="filter-date-to" v-model="dateToModel" legend="Dated to" />
        </div>
      </FilterPill>

      <FilterPill
        data-testid="pill-tag"
        label="Tag"
        :active="Boolean(applied.tags.length)"
        :value-label="tagPillLabel"
        :open="pillOpen('tag')"
        @update:open="setPillOpen('tag', $event)"
      >
        <AppCheckboxes
          id="filter-tags"
          legend="Tags"
          legend-size="s"
          :items="tagItems"
          v-model="tagModel"
        />
      </FilterPill>

      <FilterPill
        data-testid="pill-more"
        label="More"
        :active="moreActive"
        :open="pillOpen('more')"
        @update:open="setPillOpen('more', $event)"
      >
        <div class="space-y-3 text-sm">
          <div>
            <p class="mb-1 font-semibold">Language</p>
            <ul>
              <li>
                <button
                  type="button"
                  data-testid="language-option-"
                  class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
                  @click="selectLanguage('')"
                >
                  Any language
                </button>
              </li>
              <li v-for="l in languageOptions" :key="l.value">
                <button
                  type="button"
                  :data-testid="`language-option-${l.value}`"
                  class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
                  :class="{ 'font-semibold text-violet-600 dark:text-violet-300': applied.language === l.value }"
                  @click="selectLanguage(l.value)"
                >
                  {{ l.text }}
                </button>
              </li>
            </ul>
          </div>
          <div>
            <p class="mb-1 font-semibold">Status</p>
            <ul>
              <li>
                <button
                  type="button"
                  data-testid="status-option-"
                  class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
                  @click="selectStatus('')"
                >
                  Any status
                </button>
              </li>
              <li v-for="s in statusOptions" :key="s.value">
                <button
                  type="button"
                  :data-testid="`status-option-${s.value}`"
                  class="block w-full rounded px-2 py-1 text-left hover:bg-gray-100 dark:hover:bg-gray-700/60"
                  :class="{ 'font-semibold text-violet-600 dark:text-violet-300': applied.status === s.value }"
                  @click="selectStatus(s.value)"
                >
                  {{ s.text }}
                </button>
              </li>
            </ul>
          </div>
        </div>
      </FilterPill>
    </div>

    <!-- Active-filter chips -->
    <div v-if="chips.length" class="mt-3 flex flex-wrap items-center gap-2" data-testid="filter-chips">
      <span
        v-for="chip in chips"
        :key="chip.key"
        :data-testid="`chip-${chip.key}`"
        class="inline-flex items-center gap-1 rounded-full bg-violet-100 px-2.5 py-1 text-xs text-violet-700 dark:bg-violet-500/20 dark:text-violet-200"
      >
        {{ chip.label }}
        <button
          type="button"
          :data-testid="`chip-remove-${chip.key}`"
          class="text-violet-500 hover:text-violet-800 dark:hover:text-violet-100"
          :aria-label="`Remove filter ${chip.label}`"
          @click="chip.remove"
        >
          ✕
        </button>
      </span>
      <button
        type="button"
        data-testid="filter-clear-all"
        class="text-xs text-gray-500 underline hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
        @click="emit('clear')"
      >
        Clear all
      </button>
    </div>
  </div>
</template>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/__tests__/DocumentFilterBar.spec.ts`
Expected: PASS. If the tag-checkbox selector differs from `AppCheckboxes`' real markup (it renders `<input type="checkbox" :value=…>` inside `#filter-tags`), adjust the test selectors to match — the assertion (emitted `tag` array) is the contract.

- [ ] **Step 5: Type-check**

Run: `cd frontend && npx vue-tsc --build`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/DocumentFilterBar.vue frontend/src/components/__tests__/DocumentFilterBar.spec.ts
git commit -m "feat(frontend): add DocumentFilterBar (search + filter pills + chips)"
```

---

## Task 6: Wire `DocumentFilterBar` into the dashboard hero

Render the bar under the `<h1>`, route its events, and remove the now-redundant text filter-summary line (chips replace it). Keep the result-count and empty states.

**Files:**
- Modify: `frontend/src/views/DocumentListView.vue` (template ~237–270; script imports + a handler)
- Test: `frontend/src/views/__tests__/DocumentListView.spec.ts`

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/views/__tests__/DocumentListView.spec.ts`:

```ts
it('renders the filter bar and applies its emitted query to the URL', async () => {
  await mountView([])
  await flushPromises()
  const bar = wrapper.findComponent({ name: 'DocumentFilterBar' })
  expect(bar.exists()).toBe(true)

  bar.vm.$emit('apply', { kind: 'invoice' }, {})
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({ kind: 'invoice' })

  bar.vm.$emit('clear')
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({})
})
```

> Use the spec file's existing mount helper / wrapper variable names. `DocumentFilterBar` resolves by name because Vue infers it from the filename; if the suite stubs components globally, register `DocumentFilterBar` as a real component in this test's mount options.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts -t "filter bar"`
Expected: FAIL — the bar is not rendered yet.

- [ ] **Step 3: Add the import + handler (script)**

In `frontend/src/views/DocumentListView.vue` script, add the import near the other component imports:

```ts
import DocumentFilterBar from '@/components/DocumentFilterBar.vue'
import type { LocationQueryRaw } from 'vue-router'
```

(Re-add `LocationQueryRaw` to the `vue-router` import if you removed it in Task 3.) Then add a handler next to `clearFilters`:

```ts
function applyFilterQuery(query: LocationQueryRaw, opts?: { replace?: boolean }): void {
  if (opts?.replace) void router.replace({ query })
  else void router.push({ query })
}
```

- [ ] **Step 4: Update the template**

In `frontend/src/views/DocumentListView.vue`, immediately after the `<h1 id="dashboard-title">…</h1>` line, insert:

```vue
  <DocumentFilterBar :applied="applied" @apply="applyFilterQuery" @clear="clearFilters" />
```

Then delete the old text filter-summary block (the `<p data-testid="filter-summary">… Filtered by … Clear filters …</p>`, lines ~244–258). Leave the `result-count`, `empty-library`, and `empty-results` blocks intact. The `filterSummary` computed and its taxonomy watch are now unused — remove `filterSummary` and, if nothing else references them, the taxonomy `kinds`/`senders`/`tags`/`ensureLoaded` destructure and its watch in the view (the bar now owns name resolution). Verify by searching the file for `filterSummary`, `ensureLoaded`, `kinds.value` — remove whatever is now dead. Keep `languageName`/`formatDate` only if still used elsewhere in the view; otherwise remove them too.

- [ ] **Step 5: Run the view tests**

Run: `cd frontend && npx vitest run src/views/__tests__/DocumentListView.spec.ts`
Expected: PASS. Update or delete any pre-existing test that asserted the removed `filter-summary` / `clear-filters` text element (the chips in the bar replace it). The `empty-results` "clear the filters" link still exists and its test should still pass.

- [ ] **Step 6: Type-check + full frontend test run**

Run: `cd frontend && npx vue-tsc --build && npx vitest run`
Expected: no type errors; all suites pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/DocumentListView.vue frontend/src/views/__tests__/DocumentListView.spec.ts
git commit -m "feat(frontend): surface search + filters in the dashboard hero"
```

---

## Task 7: Documentation & journal

**Files:**
- Modify: `docs/frontend.md`
- Create: `journal/260615-dashboard-search-filters.md`

- [ ] **Step 1: Update `docs/frontend.md`**

Find the section describing the dashboard / `SearchModal` (search `frontend.md` for "SearchModal" or "§1.2.7"). Add a subsection documenting:
- The dashboard filter bar (`components/DocumentFilterBar.vue`): always-visible search input + filter pills (Kind, Sender, Date, Tag [multi-select], More = Language + Status) + removable active-filter chips with "Clear all".
- Live search: typing updates `?q=` after a 300 ms debounce via `router.replace`; Enter applies immediately via `router.push`.
- URL remains the source of truth via `utils/documentQuery.ts` (`parseDocumentQuery` / `buildDocumentQuery` / `hasActiveFilters`); the nav-bar `SearchModal` still works and stays in sync, and is the intended filter surface on small screens.
- Tags are now multi-select (`?tag=a&tag=b`) and `status` is filterable.
- The reusable `FilterPill` popover primitive lives in `components/app/`.

Keep it concise (a short paragraph + a bullet list). Match the document's existing heading style and section-numbering convention.

- [ ] **Step 2: Write the journal entry**

Create `journal/260615-dashboard-search-filters.md` capturing: the goal, the A+B hybrid decision (link the spec at `docs/superpowers/specs/2026-06-15-dashboard-search-filters-design.md`), the choice to keep the URL as source of truth via a shared `documentQuery` util, multi-tag + status additions, keeping the modal as the mobile surface, and the new `FilterPill` primitive. Follow the format of recent entries in `journal/` (dated `yymmdd-…`, decisions + context).

- [ ] **Step 3: Commit**

```bash
git add docs/frontend.md journal/260615-dashboard-search-filters.md
git commit -m "docs: document dashboard search/filter bar and journal entry"
```

---

## Self-Review notes (resolved during planning)

- **Spec coverage:** search input (Task 5) ✓; dropdown pills Kind/Sender/Date/Tag/More (Task 5) ✓; Tag multi-select + status (Tasks 2,3,5) ✓; active chips + Clear all (Task 5) ✓; URL source of truth / modal stays in sync (Tasks 2,3,6) ✓; `FilterPill` primitive (Task 4) ✓; `DOCUMENT_STATUSES` (Task 1) ✓; docs + journal (Task 7) ✓. Mobile "collapse pills behind a Filters button below `sm`" from the spec is **deferred** — the pill row already wraps responsively (acceptable MVP), and the modal remains reachable from the nav for narrow screens. If you want the explicit collapse-to-button, add it as a follow-up; it is not blocking. This is the one spec item intentionally not built in this plan — flagged here rather than silently dropped.
- **Type consistency:** `AppliedFilters` field names (`q`, `kind`, `senderId`, `tags`, `language`, `status`, `dateFrom`, `dateTo`, `page`) are used identically across Tasks 2, 3, 5, 6. `buildDocumentQuery(applied, page?)`, `parseDocumentQuery(query)`, `hasActiveFilters(applied)` signatures are stable across tasks. `emitWith(overrides, replace?)` and the `apply`/`clear` emits match the parent handler `applyFilterQuery(query, opts?)` in Task 6.
- **Placeholder scan:** no TBD/TODO; every code step shows full code.
