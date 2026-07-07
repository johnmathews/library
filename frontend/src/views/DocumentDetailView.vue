<script setup lang="ts">
/**
 * Document detail page (route `/documents/:id`).
 *
 * Two-column on desktop (metadata left, preview right), stacked on
 * mobile/iPad-portrait. Metadata follows the GOV.UK
 * summary-list "Change" pattern with an inline reveal per row (a full
 * one-thing-per-page flow would be heavy for single-field edits): the
 * row's Change button swaps the value cell for the right input with
 * Save/Cancel. Each save PATCHes only that row's field(s) and replaces
 * local state with the server response — no optimistic updates.
 *
 * PDF preview uses DocumentPdfPreview (pdf.js canvas renderer) for
 * consistent cross-browser rendering on every viewport.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue'
import Sortable from 'sortablejs'
import { useRoute, useRouter } from 'vue-router'
import {
  AppBackLink,
  AppBadge,
  AppBanner,
  AppButton,
  AppErrorSummary,
} from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import {
  fetchDocumentMarkdown,
  getDocument,
  originalUrl,
  requestExtraction,
  searchablePdfUrl,
  thumbnailUrl,
  verifyDocument,
  type DocumentDetail,
  type DocumentMarkdownResponse,
} from '@/api/documents'
import { ApiError } from '@/api/client'
import { useJobsStore } from '@/stores/jobs'
import { useAuthStore } from '@/stores/auth'
import { useReviewQueueStore } from '@/stores/reviewQueue'
import { resolveReviewReasons, type ReviewReason } from '@/utils/validationReason'
import { formatDate, formatDateTime, markdownPageHtml, tagColour } from '@/utils/documentFormat'
import DocumentSeriesTrend from '@/components/DocumentSeriesTrend.vue'
import DocumentPdfPreview from '@/components/DocumentPdfPreview.vue'
import DocumentHistoryTimeline from '@/components/DocumentHistoryTimeline.vue'
import NoteEditorPanel from '@/components/NoteEditorPanel.vue'
import DocumentMetadataEditor from '@/components/DocumentMetadataEditor.vue'
import DocumentComments from '@/components/DocumentComments.vue'
import ActionDock from '@/components/ActionDock.vue'
import { useDocumentLayout, HERO_FIELD_LABELS } from '@/composables/useDocumentLayout'
import { useMetadataEditMode } from '@/composables/useMetadataEditMode'

const props = withDefaults(
  defineProps<{
    /** Delay between re-extraction status polls; tests pass 0. */
    pollIntervalMs?: number
    /** Stop polling for re-extraction results after this long. */
    extractTimeoutMs?: number
  }>(),
  { pollIntervalMs: 2000, extractTimeoutMs: 60_000 },
)

const route = useRoute()
const router = useRouter()
const reviewQueue = useReviewQueueStore()

// --- Document loading --------------------------------------------------------

const doc = ref<DocumentDetail | null>(null)
const notFound = ref(false)
const loadError = ref(false)

let unmounted = false
onBeforeUnmount(() => {
  unmounted = true
})

// --- Hero header (title + key stats + tags) -----------------------------------
//
// The hero is customisable (W5): a single page-wide "Edit layout" mode (shared
// with the section-card reorder, W6) lets the user show/hide and drag-reorder
// the labelled stat fields. Order + visibility persist per-machine via
// `useDocumentLayout`; the mode itself is ephemeral (resets on reload). Values
// remain read-only here — editing metadata stays in the Details card below.

const {
  heroFields,
  cardColumns,
  editMode: layoutEditMode,
  toggleEditMode: toggleLayoutEditMode,
  setEditMode: setLayoutEditMode,
  setHeroFieldVisible,
  moveHeroField,
  moveCard,
  resetLayout,
} = useDocumentLayout()

// The ActionDock's Edit/Done button drives the SAME metadata edit mode as
// the Details card's own "Edit" toggle (not this view's "Edit layout" mode
// above) — both read/flip the one `useMetadataEditMode` singleton so opening
// the editors from the dock shows exactly what the card's toggle would. This
// view only needs to reset the flag on unmount/navigation; ActionDock owns
// reading and flipping it.
const { setEditMode: setMetadataEditMode } = useMetadataEditMode()

/** Display string for every known hero field, resolved from the current doc.
 * An empty string means "no value" — such a field is dropped from the read-mode
 * hero (preserving today's "only show when present" behaviour) but is still
 * listed (with an em-dash placeholder) inside edit mode so it can be toggled. */
const heroFieldValues = computed<Record<string, string>>(() => {
  const d = doc.value
  const values: Record<string, string> = {}
  if (!d) return values
  values.kind = d.kind?.name ?? ''
  values.sender = d.sender?.name ?? ''
  values.recipient = d.recipient?.name ?? ''
  values.document_date = formatDate(d.document_date) ?? ''
  values.created_at = formatDateTime(d.created_at)
  values.updated_at = formatDateTime(d.updated_at)
  values.amount =
    d.amount_total !== null ? [d.amount_total, d.currency].filter(Boolean).join(' ') : ''
  values.language = d.language ?? ''
  values.due_date = formatDate(d.due_date) ?? ''
  values.expiry_date = formatDate(d.expiry_date) ?? ''
  return values
})

/** Resolved display value for a hero field key ('' when absent). */
function heroValue(key: string): string {
  return heroFieldValues.value[key] ?? ''
}

/** Human label for a hero field key. */
function heroLabel(key: string): string {
  return HERO_FIELD_LABELS[key] ?? key
}

/** Read-mode hero fields: visible AND with a value, in the saved order. */
const readHeroFields = computed(() =>
  heroFields.value.filter((f) => f.visible && heroValue(f.key) !== ''),
)

/** Pre-filled text for the "Ask about this document" button. It just names the
 * current document so the existing Ask RAG retrieval surfaces it — there is no
 * backend change. Kind/sender/date are folded into a parenthetical, and any
 * missing part is omitted gracefully (no empty `()`). */
const askPrompt = computed<string>(() => {
  const d = doc.value
  if (!d) return ''
  const title = d.title?.trim() || d.original_filename?.trim() || ''
  // kind + sender read as one phrase ("Invoice from Eneco"); the date is a
  // separate comma-delimited part ("…, 15 May 2026").
  const descriptor: string[] = []
  if (d.kind?.name) descriptor.push(d.kind.name)
  if (d.sender?.name) descriptor.push(`from ${d.sender.name}`)
  const parenParts = [descriptor.join(' '), formatDate(d.document_date)].filter(
    (part): part is string => Boolean(part),
  )
  const parenthetical = parenParts.length ? ` (${parenParts.join(', ')})` : ''
  return title
    ? `Tell me about the document "${title}"${parenthetical}: `
    : `Tell me about this document${parenthetical}: `
})

/**
 * Where the "Ask about this document" action points: the Ask view,
 * pre-filled with `askPrompt`. Shared so both the hero button and
 * `ActionDock` (which appears once the hero has scrolled off screen, see
 * below) render as anchors pointing at the exact same URL rather than each
 * duplicating the resolve logic. Both render a real `<a href target
 * rel="noopener">` (via `AppButton`), so native new-tab affordances —
 * middle-click, cmd/ctrl-click, right-click "open in new tab" — keep
 * working, unlike a `window.open` call from a click handler.
 */
const askHref = computed(() => router.resolve({ name: 'ask', query: { q: askPrompt.value } }).href)

// --- ActionDock (Ask + metadata Edit/Done) ------------------------------------
//
// The hero's primary actions (Ask, and — via the dock only — metadata Edit)
// stay reachable once the hero itself has scrolled out of view: an
// IntersectionObserver on #document-hero flips `heroVisible`, which mounts
// `ActionDock`. `v-if` (not `v-show`) keeps it fully out of the DOM while the
// hero is visible, so it never interferes with mobile e2e specs that assert
// on visibility lower on the page.
//
// `ActionDock` is `position: sticky`, which only pins in a direction that has
// scrollable content beyond it. So a top-anchored dock must be mounted *early*
// in the flow (content below to pin `top-16` against) and a bottom-anchored
// one *late* (content above to pin `bottom-0` against) — hence the two mount
// slots below, selected by `dockAtTop`. A single slot can't serve both; a dock
// rendered only at the bottom leaves the three `top-*` positions stuck at the
// very bottom of the page (see the note in ActionDock.vue).
const dockAtTop = computed(() => useAuthStore().dockPosition.startsWith('top'))
const heroEl = ref<HTMLElement | null>(null)
const heroVisible = ref(true)
let heroObserver: IntersectionObserver | null = null

/** Function ref for `#document-hero`: (re)points the observer at the element
 * whenever it mounts (the hero only exists once `doc` has loaded, so this can
 * fire after `onMounted` as well as — in principle — before it). */
function setHeroEl(el: Element | ComponentPublicInstance | null): void {
  heroEl.value = (el as HTMLElement | null) ?? null
  if (!heroObserver) return
  heroObserver.disconnect()
  if (heroEl.value) heroObserver.observe(heroEl.value)
}

onMounted(() => {
  // jsdom under Node <22 and some older browsers lack IntersectionObserver;
  // degrade to "always visible" (ActionDock simply never appears) rather than
  // throwing.
  if (typeof IntersectionObserver === 'undefined') return
  heroObserver = new IntersectionObserver(([entry]) => {
    heroVisible.value = entry?.isIntersecting ?? true
  })
  if (heroEl.value) heroObserver.observe(heroEl.value)
})

// --- Page-level notifications -------------------------------------------------

/** The success / progress notification shown at the top of the page. */
const notice = ref<{ variant?: 'success'; text: string } | null>(null)
/** Failure of a page-level action (re-extraction / verify). */
const actionError = ref<string | null>(null)

const errorItems = computed<ErrorSummaryItem[]>(() => {
  const items: ErrorSummaryItem[] = []
  if (actionError.value) items.push({ text: actionError.value })
  return items
})

// --- Re-extraction ------------------------------------------------------------

const extracting = ref(false)

/**
 * Change marker for "extraction ran again": the provenance block plus the
 * number of extraction audit events (a skipped/failed run leaves the
 * provenance untouched but still appends an event).
 */
function extractionFingerprint(d: DocumentDetail): string {
  const eventCount = d.events.filter((event) => event.event.startsWith('extraction')).length
  return `${JSON.stringify(d.extraction)}|${eventCount}`
}

async function rerunExtraction(): Promise<void> {
  if (!doc.value || extracting.value) return
  const id = doc.value.id
  const before = extractionFingerprint(doc.value)
  actionError.value = null
  try {
    await requestExtraction(id)
  } catch {
    actionError.value = 'Could not queue the extraction — try again later'
    return
  }
  notice.value = { text: 'Extraction queued — this page will refresh when it finishes.' }
  extracting.value = true
  const deadline = Date.now() + props.extractTimeoutMs
  while (!unmounted && Date.now() <= deadline) {
    await sleep(props.pollIntervalMs)
    if (unmounted) return
    try {
      const fresh = await getDocument(id)
      if (extractionFingerprint(fresh) !== before) {
        doc.value = fresh
        notice.value = { variant: 'success', text: 'Extraction finished — metadata refreshed.' }
        break
      }
    } catch {
      // transient poll error: keep trying until the deadline
    }
  }
  extracting.value = false
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// --- Live status (SSE) --------------------------------------------------------
//
// While this document is open and processing in the background, refetch it on
// each of its own pipeline events so the status badge (and any metadata the
// pipeline fills in) updates without a manual reload. Skip while a re-extraction
// poll is running — that loop already owns refreshes and a double-fetch would
// race it.
const jobsStore = useJobsStore()
watch(
  () => jobsStore.lastEvent,
  async (event) => {
    if (!event || extracting.value) return
    const current = doc.value
    if (!current || event.document_id !== current.id) return
    try {
      const fresh = await getDocument(current.id)
      if (!unmounted) doc.value = fresh
    } catch {
      // Transient — the next event or a reload recovers the latest state.
    }
  },
)

// --- Validation findings ------------------------------------------------------

/**
 * Every reason this document needs review, in plain language — the source for
 * the prominent top-of-page "Why this needs review" panel. Unlike the old
 * document-level-only banner, this includes field-mapped findings (e.g. an
 * implausible `document_date`) so the reason is never hidden behind a small
 * per-field ⚠ badge. Shown only while the document actually needs review.
 */
const reviewReasons = computed<ReviewReason[]>(() => {
  if (doc.value?.review_status !== 'needs_review') return []
  const findings = doc.value?.validation?.findings
  if (!findings?.length) return []
  return resolveReviewReasons(findings)
})

// --- Mark verified ------------------------------------------------------------

const verifying = ref(false)

async function markVerified(): Promise<void> {
  if (!doc.value || verifying.value) return
  verifying.value = true
  actionError.value = null
  try {
    doc.value = await verifyDocument(doc.value.id)
    notice.value = { variant: 'success', text: 'Document marked as verified.' }
  } catch (error: unknown) {
    actionError.value =
      error instanceof ApiError && error.status !== 0
        ? error.detail
        : 'Could not mark verified — check your connection and try again'
  } finally {
    verifying.value = false
  }
}

// --- Review queue (step-through mode) -----------------------------------------
//
// Entered from the dashboard's "Review these one by one" button, which loads the
// needs-review set into the reviewQueue store and opens the first doc with
// `?queue=1`. Editing is the page's normal per-field autosave (revalidated
// server-side), so a fixed document simply drops off `needs_review`; the queue
// controls then advance to the next id, exiting to the dashboard when done.

const inQueue = computed(() => route.query.queue === '1' && reviewQueue.isActive)

/** Navigate to the next queued document, or exit to the dashboard when the
 *  queue is empty. */
function goToQueueTarget(targetId: number | null): void {
  if (targetId === null) {
    reviewQueue.reset()
    void router.push('/')
    return
  }
  void router.push({ name: 'document-detail', params: { id: targetId }, query: { queue: '1' } })
}

/** "Next": advance past the current document. If its findings were resolved
 *  (autosave dropped it off needs_review) it leaves the queue; otherwise it
 *  stays for a later pass and we just step the cursor. */
function queueNext(): void {
  const resolved = doc.value?.review_status !== 'needs_review'
  goToQueueTarget(resolved ? reviewQueue.resolveCurrent() : reviewQueue.next())
}

/** "Verify & next": accept the document as-is, then advance. */
async function verifyAndNext(): Promise<void> {
  await markVerified()
  if (doc.value?.review_status === 'verified') {
    goToQueueTarget(reviewQueue.resolveCurrent())
  }
}

/** "Prev": step back to the previous queued document. */
function queuePrev(): void {
  if (reviewQueue.hasPrev) goToQueueTarget(reviewQueue.prev())
}

/** Leave the queue without finishing it. */
function exitQueue(): void {
  reviewQueue.reset()
  void router.push('/')
}

// --- Preview and OCR text -----------------------------------------------------

const preview = computed<'image' | 'pdf' | 'none'>(() => {
  if (!doc.value) return 'none'
  if (doc.value.mime_type.startsWith('image/')) return 'image'
  if (doc.value.mime_type === 'application/pdf' || doc.value.has_searchable_pdf) return 'pdf'
  return 'none'
})

/** Preview the searchable PDF when the pipeline produced one (it has a
 * text layer, so in-viewer text selection/search works), else the original.
 * Inline disposition so the browser renders rather than downloads. */
const pdfPreviewUrl = computed(() =>
  doc.value
    ? doc.value.has_searchable_pdf
      ? searchablePdfUrl(doc.value.id, { inline: true })
      : originalUrl(doc.value.id, { inline: true })
    : '',
)

/** Positive integer page number from `?page=N` in the route query, or null. */
const pageParam = computed<number | null>(() => {
  const value = route.query.page
  const n = Array.isArray(value) ? Number(value[0]) : Number(value)
  return Number.isInteger(n) && n > 0 ? n : null
})

/** Where the preview header's "Open" button points (open the inline preview in
 * a new tab): the PDF for PDFs, the original image for images. */
const previewOpenUrl = computed(() => {
  if (!doc.value) return ''
  if (preview.value === 'pdf') return pdfPreviewUrl.value
  if (preview.value === 'image') return originalUrl(doc.value.id, { inline: true })
  return ''
})

/** Where the preview header's "Download" button points (attachment download):
 * the searchable PDF when present, otherwise the original file. */
const previewDownloadUrl = computed(() => {
  if (!doc.value) return ''
  return doc.value.has_searchable_pdf ? searchablePdfUrl(doc.value.id) : originalUrl(doc.value.id)
})

// --- Document text reader (markdown, fetched eagerly on load) -----------------

const markdownData = ref<DocumentMarkdownResponse | null>(null)
const markdownLoading = ref(false)
const markdownError = ref(false)

// On small screens the document text sits above the metadata column (summary,
// amount, …), so a long document forces the reader to scroll past all of it to
// reach the metadata. Collapse the text by default below lg and offer a toggle;
// at lg+ the text and metadata are side by side, so it stays expanded (the
// toggle is hidden and the body is forced visible via `lg:!block`). Initialised
// at setup so the first render is already correct on mobile (no expand→collapse
// flash). matchMedia is absent in jsdom → defaults to expanded under test.
const textExpanded = ref(
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(min-width: 1024px)').matches
    : true,
)

/** Whether the document has readable extracted text to show in the reader. */
const hasReadableText = computed(() => (markdownData.value?.pages.length ?? 0) > 0)

/** Fetch the rendered markdown for a document. Called from the load watcher so
 * the reader is the primary content even for files with no PDF/image preview. */
async function loadMarkdown(id: number): Promise<void> {
  markdownLoading.value = true
  markdownError.value = false
  try {
    markdownData.value = await fetchDocumentMarkdown(id)
  } catch {
    markdownError.value = true
  } finally {
    markdownLoading.value = false
  }
}

// --- Notes: in-place editing + version history --------------------------------
//
// Note documents (source === 'note') are authored in-app and carry their body
// in the markdown reader. They get their own edit affordance (separate from the
// generic per-field metadata editor) plus a version-history panel with restore.

const isNote = computed(() => doc.value?.source === 'note')

/** The note's current markdown body, assembled from the reader's pages. */
const noteBody = computed(() =>
  (markdownData.value?.pages ?? []).map((page) => page.markdown).join('\n\n'),
)

// --- Comments ------------------------------------------------------------------
//
// Unlike the note/metadata editors, comment mutations don't return the full
// DocumentDetail (createComment/updateComment/deleteComment return just the
// comment or void), so there's no v-model:doc round-trip to piggyback on —
// the card instead emits `changed` and this re-fetches the document.

async function reloadDocument(): Promise<void> {
  if (!doc.value || extracting.value) return
  const id = doc.value.id
  try {
    const fresh = await getDocument(id)
    if (!unmounted) doc.value = fresh
  } catch {
    // Transient — the stale comments list is still correct apart from the one
    // in-flight change; the next reload or SSE-triggered refresh recovers it.
  }
}

// --- Section-card reorder (W6 / Task 1: free-form cross-column drag) --------
//
// The two-column grid renders its cards from the persisted `cardColumns`
// (`{ left, right }`). Both columns share one SortableJS `group`, so a card
// can be dragged into either column, not just reordered within its own
// (see `buildSortables` below). A column's rendered list filters its full,
// persisted id list down to cards that actually have content for the current
// document (notes exist only for note docs; the series chart hides itself
// when there is no qualifying series — in read mode the wrapper's
// `empty:hidden` drops it visually, but in edit mode the drag handle keeps
// the wrapper non-empty, so `seriesChartPresent` gates `cardPresent` instead).

// Whether the series-chart card has anything to show for the current
// document. Defaults true so the card renders on first mount, mounting
// `DocumentSeriesTrend`, which then reports the real answer via `presence` —
// starting false would never mount the child, so it could never re-emit and
// correct itself. Reset to true on doc→doc navigation (see the route watcher
// below) so a new document's card isn't left hidden by the previous one's result.
const seriesChartPresent = ref(true)

/** Whether a card id has content to render for the current document. */
function cardPresent(id: string): boolean {
  if (id === 'notes') return isNote.value
  if (id === 'series-chart') return seriesChartPresent.value
  return true
}

const previewCards = computed(() => cardColumns.value.right.filter(cardPresent))
const metadataCards = computed(() => cardColumns.value.left.filter(cardPresent))

/**
 * Map a rendered/present-list index (SortableJS only sees rendered DOM nodes,
 * so `evt.newIndex` is relative to `fullList` filtered to `cardPresent` and
 * with `excludeId` removed) to the corresponding index in `fullList`. The
 * moved card lands immediately before whichever id currently sits at
 * `presentIndex` among those present, excluded-adjusted cards; an index at or
 * past the end of that list lands at the very end of `fullList`. Excluding
 * `excludeId` mirrors what `moveCard` itself does (it always removes the
 * dragged card from both columns before splicing it back in), so the index
 * this returns is valid input to `moveCard`'s `toIndex` even for a
 * same-column reorder.
 */
function presentIndexToFullIndex(
  fullList: readonly string[],
  presentIndex: number,
  excludeId: string,
): number {
  const withoutDragged = fullList.filter((id) => id !== excludeId)
  const present = withoutDragged.filter(cardPresent)
  if (presentIndex >= present.length) return withoutDragged.length
  const landingId = present[presentIndex]
  const fullIndex = landingId === undefined ? -1 : withoutDragged.indexOf(landingId)
  return fullIndex === -1 ? withoutDragged.length : fullIndex
}

/**
 * Shared `onEnd` for both section-card column Sortables (`group: 'doc-cards'`
 * lets a card cross from one to the other). SortableJS has already physically
 * moved the dragged DOM node into `evt.to` by the time this fires; that move
 * is reverted first so Vue's own re-render (from the mutated `cardColumns`
 * below) is the only thing that ever places the node — otherwise the node
 * would exist twice for one render (the DOM-moved copy plus Vue's newly
 * rendered one) until the next tick sorted it out.
 */
function onCardDragEnd(evt: Sortable.SortableEvent): void {
  const fromCol = (evt.from as HTMLElement).dataset.col as 'left' | 'right' | undefined
  const toCol = (evt.to as HTMLElement).dataset.col as 'left' | 'right' | undefined
  if (!fromCol || !toCol || evt.oldIndex == null || evt.newIndex == null) return
  // The rendered lists are filtered by cardPresent; map DOM index -> card id
  // via the present list for the source column so the id is correct even
  // with hidden cards (e.g. 'notes' on a non-note document).
  const sourceList = fromCol === 'left' ? metadataCards.value : previewCards.value
  const cardId = sourceList[evt.oldIndex]
  if (!cardId) return
  // Revert SortableJS's DOM mutation so Vue re-renders from the reactive
  // arrays (prevents a duplicate node when the card crosses into the other
  // column's DOM subtree).
  const from = evt.from as HTMLElement
  const ref = from.children[evt.oldIndex] ?? null
  from.insertBefore(evt.item, ref)
  // moveCard's toIndex is an index into the destination column's FULL list
  // (present + hidden); evt.newIndex is an index into that column's rendered
  // (present) list, so it needs converting.
  const toIndex = presentIndexToFullIndex(cardColumns.value[toCol], evt.newIndex, cardId)
  moveCard(cardId, toCol, toIndex)
}

// --- Drag wiring: sortablejs, live only while editing the layout --------------
//
// Sortable instances attach when edit mode turns on and are destroyed when it
// turns off (or on unmount). Each `onEnd` translates the DOM move into a call to
// the composable's reorder helper — the reactive array is the source of truth,
// and Vue re-renders from it (we never treat the DOM move as authoritative).

const heroEditListEl = ref<HTMLElement | null>(null)
const previewColumnEl = ref<HTMLElement | null>(null)
const metadataColumnEl = ref<HTMLElement | null>(null)
let sortables: Sortable[] = []

function destroySortables(): void {
  for (const instance of sortables) instance.destroy()
  sortables = []
}

function buildSortables(): void {
  destroySortables()
  if (heroEditListEl.value) {
    sortables.push(
      Sortable.create(heroEditListEl.value, {
        handle: '[data-hero-drag-handle]',
        animation: 150,
        onEnd: (evt: Sortable.SortableEvent) => {
          if (evt.oldIndex == null || evt.newIndex == null) return
          moveHeroField(evt.oldIndex, evt.newIndex)
        },
      }),
    )
  }
  if (previewColumnEl.value) {
    sortables.push(
      Sortable.create(previewColumnEl.value, {
        group: 'doc-cards',
        handle: '[data-card-drag-handle]',
        animation: 150,
        onEnd: onCardDragEnd,
      }),
    )
  }
  if (metadataColumnEl.value) {
    sortables.push(
      Sortable.create(metadataColumnEl.value, {
        group: 'doc-cards',
        handle: '[data-card-drag-handle]',
        animation: 150,
        onEnd: onCardDragEnd,
      }),
    )
  }
}

watch(layoutEditMode, async (on) => {
  if (on) {
    await nextTick()
    buildSortables()
  } else {
    destroySortables()
  }
})

// Reset the (module-singleton) edit-mode flag on unmount so it never persists
// across SPA navigation. Without this, leaving the view in edit mode and
// returning would render the edit affordances with `editMode` still true but no
// Sortable instances attached (the watcher above only fires on a *change*), so
// dragging would be silently dead until the user toggled Done→Edit again.
onBeforeUnmount(() => {
  destroySortables()
  setLayoutEditMode(false)
  // Same rationale for the metadata edit-mode singleton: never leave the
  // Details card (or the ActionDock) rendering its editors after navigating away.
  setMetadataEditMode(false)
  heroObserver?.disconnect()
  heroObserver = null
})

// --- Load on navigation (registered last: the handler runs immediately and
// --- touches the edit/notice state declared above) ----------------------------

watch(
  () => route.params.id,
  async (id) => {
    if (route.name !== 'document-detail') return
    doc.value = null
    notFound.value = false
    loadError.value = false
    notice.value = null
    actionError.value = null
    markdownData.value = null
    markdownLoading.value = false
    markdownError.value = false
    // The edit-mode flags are module singletons (shared with ActionDock),
    // and this view is reused across in-queue Prev/Next navigation
    // (the RouterView in App.vue is unkeyed, so no unmount happens). Reset
    // both here too, or a still-true editMode survives into the new document
    // while the editor's non-immediate hydration watcher never re-fires,
    // leaving blank edit inputs that can PATCH an empty value on Enter.
    setLayoutEditMode(false)
    setMetadataEditMode(false)
    // Re-show the series-chart card so the new document's DocumentSeriesTrend
    // remounts and re-reports its own presence; otherwise a no-series doc's
    // `false` would survive into a doc that does have a qualifying series,
    // leaving the card hidden forever.
    seriesChartPresent.value = true
    const numericId = Number(id)
    if (!Number.isInteger(numericId) || numericId < 1) {
      notFound.value = true
      return
    }
    try {
      doc.value = await getDocument(numericId)
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 404) notFound.value = true
      else loadError.value = true
      return
    }
    // Fetch the rendered text eagerly so the reader is ready without a reveal.
    await loadMarkdown(numericId)
  },
  { immediate: true },
)
</script>

<template>
  <AppBackLink to="/" text="Back to documents" class="mb-4" />

  <template v-if="doc">
    <!-- Top-anchored dock: mounted early in the flow so its `sticky top-16`
         has content below to pin against (see the ActionDock note above). The
         zero-height rail reserves no space, so mounting it here shifts nothing.
         Bottom-anchored positions render from the late slot near the end. -->
    <ActionDock v-if="!heroVisible && dockAtTop" :ask-href="askHref" />
    <div
      v-if="inQueue"
      class="mb-6 flex flex-col gap-3 rounded-lg border border-violet-300 bg-violet-50 p-3 sm:flex-row sm:items-center dark:border-violet-500/40 dark:bg-violet-500/10"
      data-testid="review-queue-bar"
    >
      <p class="text-sm font-medium text-violet-800 dark:text-violet-200" data-testid="review-queue-position">
        Reviewing {{ reviewQueue.position }} of {{ reviewQueue.total }}
      </p>
      <div class="flex flex-1 flex-wrap items-center gap-2 sm:justify-end">
        <AppButton
          type="button"
          variant="secondary"
          :disabled="!reviewQueue.hasPrev"
          data-testid="queue-prev"
          @click="queuePrev"
        >
          ← Prev
        </AppButton>
        <AppButton
          v-if="doc.review_status !== 'verified'"
          type="button"
          variant="secondary"
          :disabled="verifying"
          data-testid="queue-verify-next"
          @click="verifyAndNext"
        >
          Verify &amp; next
        </AppButton>
        <AppButton type="button" data-testid="queue-next" @click="queueNext"> Next → </AppButton>
        <button
          type="button"
          class="text-sm text-violet-700 hover:underline dark:text-violet-300"
          data-testid="queue-exit"
          @click="exitQueue"
        >
          Exit
        </button>
      </div>
    </div>
    <AppBanner v-if="notice" :variant="notice.variant" data-testid="detail-banner" class="mb-6">
      {{ notice.text }}
    </AppBanner>
    <AppErrorSummary v-if="errorItems.length" :errors="errorItems" data-testid="error-summary" />
    <AppBanner
      v-if="reviewReasons.length"
      data-testid="validation-findings"
      class="mb-6"
    >
      <p class="font-semibold">Why this needs review</p>
      <ul class="mt-1 list-disc list-inside space-y-1">
        <li v-for="reason in reviewReasons" :key="`${reason.rule}:${reason.field ?? 'doc'}`">
          <span class="font-medium">{{ reason.title }}</span
          ><template v-if="reason.detail"> — {{ reason.detail }}</template>
        </li>
      </ul>
    </AppBanner>

    <div
      id="document-hero"
      :ref="setHeroEl"
      class="card p-5 sm:p-6 mb-6"
    >
      <!-- Title row. `break-words` lets a long title wrap rather than overflow;
           the layout controls sit top-right, stacking under the title on narrow
           screens (flex keeps them reachable when the grid stacks below lg). -->
      <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <h1
          id="document-title"
          class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold break-words app-detail-title"
        >
          {{ doc.title ?? 'Untitled document' }}
        </h1>
        <div class="flex shrink-0 items-center gap-2">
          <button
            v-if="layoutEditMode"
            type="button"
            class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
            data-testid="reset-layout"
            @click="resetLayout"
          >
            Reset layout
          </button>
          <button
            type="button"
            class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
            data-testid="edit-layout-toggle"
            :aria-pressed="layoutEditMode"
            @click="toggleLayoutEditMode"
          >
            {{ layoutEditMode ? 'Done' : 'Edit layout' }}
          </button>
        </div>
      </div>

      <!-- Read mode: labelled stat row — only visible fields that have a value,
           in the saved order (unchanged from before apart from customisation). -->
      <dl
        v-if="!layoutEditMode && readHeroFields.length"
        class="mt-12 grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3"
        data-testid="hero-stats"
      >
        <div v-for="field in readHeroFields" :key="field.key" :data-testid="`hero-field-${field.key}`">
          <dt class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
            {{ heroLabel(field.key) }}
          </dt>
          <dd class="mt-0.5 text-sm font-medium text-gray-800 dark:text-gray-100 break-words">
            {{ heroValue(field.key) }}
          </dd>
        </div>
      </dl>

      <!-- Edit mode: every known field as a reorderable row with a show/hide
           toggle + drag handle. Empty fields show a muted em-dash so they can
           still be toggled/reordered. -->
      <div v-else-if="layoutEditMode" class="mt-6" data-testid="hero-fields-editor">
        <p class="mb-2 text-xs text-gray-500 dark:text-gray-400">
          Show, hide and drag to reorder the fields shown here. Cards below can be dragged to reorder them or move them between columns.
        </p>
        <ul ref="heroEditListEl" role="list" class="flex flex-col gap-1">
          <li
            v-for="field in heroFields"
            :key="field.key"
            class="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700/40"
            :data-testid="`hero-field-${field.key}`"
          >
            <button
              type="button"
              data-hero-drag-handle
              class="cursor-grab text-gray-400 hover:text-violet-500 active:cursor-grabbing"
              :aria-label="`Drag to reorder ${heroLabel(field.key)}`"
              tabindex="-1"
            >
              ⠿
            </button>
            <input
              type="checkbox"
              class="form-checkbox text-violet-600"
              :checked="field.visible"
              :aria-label="`Show ${heroLabel(field.key)} in the hero`"
              :data-testid="`hero-field-toggle-${field.key}`"
              @change="setHeroFieldVisible(field.key, ($event.target as HTMLInputElement).checked)"
            />
            <span class="w-32 shrink-0 text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500">
              {{ heroLabel(field.key) }}
            </span>
            <span class="min-w-0 truncate text-sm text-gray-800 dark:text-gray-100">
              {{ heroValue(field.key) || '—' }}
            </span>
          </li>
        </ul>
      </div>
      <!-- Bottom row: tag pills on the left, the primary "Ask" action pinned
           bottom-right and baseline-aligned with the pills (sm:items-end). On a
           narrow screen the row stacks (flex-col) so the button drops neatly
           below the pills, left-aligned to match them. `sm:ml-auto` keeps the
           button hard-right even when a document has no tags. -->
      <div
        id="document-hero-bottom-row"
        class="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end"
      >
        <div
          v-if="doc.tags.length"
          class="flex flex-wrap gap-2"
          data-testid="hero-tags"
        >
          <AppBadge v-for="tag in doc.tags" :key="tag.slug" :colour="tagColour(tag.name)">
            {{ tag.name }}
          </AppBadge>
        </div>

        <AppButton
          :href="askHref"
          target="_blank"
          variant="primary"
          class="shrink-0 gap-1.5 self-start sm:self-end sm:ml-auto"
          data-testid="ask-about-document"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            stroke-width="1.5"
            stroke="currentColor"
            class="w-4 h-4"
            aria-hidden="true"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z"
            />
          </svg>
          Ask about this document
        </AppButton>
      </div>
    </div>

    <div id="document-detail-grid" class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <!-- Preview: right column on desktop (lg:order-2), first on mobile.
           min-w-0 lets this grid column shrink below its content's intrinsic
           width so long tokens wrap instead of widening the page (iOS zoom). -->
      <div
        id="document-preview-column"
        ref="previewColumnEl"
        data-col="right"
        class="min-w-0 space-y-4 lg:order-2"
      >
        <!-- Each card is a reorderable wrapper (drag handle shown in edit mode).
             `empty:hidden` drops a card that rendered nothing (e.g. the series
             chart with no qualifying series) so a seriesless doc reads as before. -->
        <div
          v-for="cardId in previewCards"
          :key="cardId"
          :data-testid="`section-card-${cardId}`"
          class="relative empty:hidden"
        >
          <button
            v-if="layoutEditMode"
            type="button"
            data-card-drag-handle
            :data-testid="`card-drag-handle-${cardId}`"
            class="absolute right-2 top-2 z-10 cursor-grab rounded bg-white/90 px-2 py-1 text-gray-400 shadow-sm hover:text-violet-500 active:cursor-grabbing dark:bg-gray-800/90"
            aria-label="Drag to reorder this section"
          >
            ⠿
          </button>
        <div
          v-if="cardId === 'preview'"
          id="document-preview-card"
          class="card overflow-hidden"
        >
          <!-- Preview header: keeps the document window itself clean (the native
               PDF toolbar is hidden) while giving an unambiguous way to open the
               file full-size or download it. -->
          <div
            v-if="preview !== 'none'"
            class="flex items-center justify-between gap-3 border-b border-gray-200 dark:border-gray-700/60 px-4 py-2.5"
          >
            <span class="text-sm font-medium text-gray-500 dark:text-gray-400">Document</span>
            <div class="flex items-center gap-2">
              <a
                :href="previewOpenUrl"
                target="_blank"
                rel="noopener"
                class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 gap-1.5"
                data-testid="preview-open"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke-width="1.5"
                  stroke="currentColor"
                  class="w-4 h-4"
                  aria-hidden="true"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"
                  />
                </svg>
                Open
              </a>
              <a
                :href="previewDownloadUrl"
                class="btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300 gap-1.5"
                data-testid="preview-download"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke-width="1.5"
                  stroke="currentColor"
                  class="w-4 h-4"
                  aria-hidden="true"
                >
                  <path
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
                  />
                </svg>
                Download
              </a>
            </div>
          </div>
          <!-- Inline disposition: Firefox refuses to render <img> responses
               served as attachment, and other browsers would download them. -->
          <img
            v-if="preview === 'image'"
            class="w-full object-contain bg-gray-100 dark:bg-gray-900/40"
            :src="originalUrl(doc.id, { inline: true })"
            :alt="`Preview of ${doc.title ?? 'this document'}`"
            data-testid="preview-image"
          />
          <DocumentPdfPreview
            v-else-if="preview === 'pdf'"
            :src="pdfPreviewUrl"
            :poster="doc.has_thumbnail ? thumbnailUrl(doc.id) : undefined"
            :open-href="previewOpenUrl"
            :download-href="previewDownloadUrl"
            :initial-page="pageParam"
            data-testid="preview-pdf"
          />
          <div
            v-else-if="!hasReadableText"
            class="p-4 text-sm text-gray-500 dark:text-gray-400"
            data-testid="preview-fallback"
          >
            No preview is available for this file type.
            <a class="text-violet-600 hover:underline" :href="originalUrl(doc.id)"
              >Download the original file</a
            >
            to view it.
          </div>
        </div>

        <!-- Document text: a first-class long-form reader. The extracted text is
             the primary content for files with no PDF/image preview, so at lg+
             it is rendered directly (beside the metadata column). On small
             screens it stacks above the metadata, so it collapses by default
             behind a Show/Hide toggle to keep the metadata reachable. -->
        <div
          v-else-if="cardId === 'markdown'"
          id="document-markdown-card"
          class="card p-5"
        >
          <div class="flex items-center justify-between gap-3 mb-3">
            <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">Document text</h2>
            <!-- Mobile-only collapse toggle: at lg+ the text sits beside the
                 metadata (no scroll problem) so the body is always shown. -->
            <button
              type="button"
              class="lg:hidden btn-sm border-gray-200 dark:border-gray-700/60 hover:border-gray-300 text-gray-700 dark:text-gray-300"
              data-testid="markdown-toggle"
              :aria-expanded="textExpanded"
              aria-controls="document-markdown-body"
              @click="textExpanded = !textExpanded"
            >
              {{ textExpanded ? 'Hide' : 'Show' }}
            </button>
          </div>
          <!-- v-show keeps the body in the DOM (so deep-links/anchors resolve)
               but hides it on mobile when collapsed; `lg:!block` overrides the
               inline display:none at lg+ so it is always visible there. -->
          <div id="document-markdown-body" v-show="textExpanded" class="lg:!block">
            <div v-if="markdownLoading" class="text-sm text-gray-500 dark:text-gray-400" data-testid="markdown-loading">
              Loading…
            </div>
            <div v-else-if="markdownError" class="text-sm text-red-600 dark:text-red-400" data-testid="markdown-error">
              Could not load markdown — try again later.
            </div>
            <div v-else-if="markdownData && markdownData.page_count === 0" class="text-sm text-gray-500 dark:text-gray-400" data-testid="markdown-empty">
              No markdown content is available for this document yet.
            </div>
            <template v-else-if="markdownData">
              <div
                v-for="page in markdownData.pages"
                :key="page.page_number"
                class="mt-3 first:mt-0"
                data-testid="markdown-page"
              >
                <p
                  v-if="markdownData.page_count > 1"
                  class="text-xs font-medium uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1"
                >
                  Page {{ page.page_number }}
                </p>
                <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in markdownPageHtml -->
                <div
                  class="doc-markdown text-gray-800 dark:text-gray-100"
                  data-testid="markdown-content"
                  v-html="markdownPageHtml(page.markdown)"
                />
                <!-- eslint-enable vue/no-v-html -->
              </div>
            </template>
          </div>
        </div>

        <DocumentSeriesTrend
          v-else-if="cardId === 'series-chart'"
          :document-id="doc.id"
          @presence="seriesChartPresent = $event"
        />
        </div>
      </div>

      <!-- Metadata: left column on desktop (lg:order-1). min-w-0 (as above)
           lets long metadata values wrap rather than widen the page. -->
      <div
        id="document-metadata-column"
        ref="metadataColumnEl"
        data-col="left"
        class="min-w-0 space-y-6 lg:order-1"
      >
        <div
          v-for="cardId in metadataCards"
          :key="cardId"
          :data-testid="`section-card-${cardId}`"
          class="relative empty:hidden"
        >
          <button
            v-if="layoutEditMode"
            type="button"
            data-card-drag-handle
            :data-testid="`card-drag-handle-${cardId}`"
            class="absolute right-2 top-2 z-10 cursor-grab rounded bg-white/90 px-2 py-1 text-gray-400 shadow-sm hover:text-violet-500 active:cursor-grabbing dark:bg-gray-800/90"
            aria-label="Drag to reorder this section"
          >
            ⠿
          </button>
        <!-- Note-only controls: in-place note editing + version history. Shown
             only for notes (source === 'note'); the generic metadata editor
             below stays available for notes too. -->
        <NoteEditorPanel
          v-if="cardId === 'notes'"
          v-model:doc="doc"
          :note-body="noteBody"
          @reload-markdown="loadMarkdown(doc.id)"
        />

        <DocumentMetadataEditor v-else-if="cardId === 'metadata'" v-model:doc="doc" />

        <DocumentComments
          v-else-if="cardId === 'comments'"
          :document-id="doc.id"
          :comments="doc.comments"
          @changed="reloadDocument"
        />

        <div
          v-else-if="cardId === 'actions'"
          id="document-actions-card"
          class="card p-5"
        >
          <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Actions</h2>
          <p class="text-sm mb-2">
            <a
              class="text-violet-600 hover:underline"
              :href="originalUrl(doc.id)"
              data-testid="download-original"
            >
              Download the original file
            </a>
          </p>
          <p v-if="doc.has_searchable_pdf" class="text-sm mb-2">
            <a
              class="text-violet-600 hover:underline"
              :href="searchablePdfUrl(doc.id)"
              data-testid="download-searchable"
            >
              Download the searchable PDF
            </a>
          </p>
          <p class="text-sm mb-4">
            <RouterLink
              class="text-violet-600 hover:underline"
              :to="`/jobs?document_id=${doc.id}`"
              data-testid="view-job-history"
            >
              View job history
            </RouterLink>
          </p>
          <div class="flex flex-wrap gap-3">
            <AppButton
              v-if="doc.review_status !== 'verified'"
              type="button"
              :disabled="verifying"
              data-testid="mark-verified"
              @click="markVerified"
            >
              {{ verifying ? 'Saving…' : 'Mark verified' }}
            </AppButton>
            <AppButton
              type="button"
              variant="secondary"
              :disabled="extracting"
              data-testid="rerun-extraction"
              @click="rerunExtraction"
            >
              {{ extracting ? 'Extraction running…' : 'Re-run extraction' }}
            </AppButton>
            <AppButton
              variant="warning"
              :to="`/documents/${doc.id}/delete`"
              data-testid="delete-link"
            >
              Delete this document
            </AppButton>
          </div>
        </div>

        <DocumentHistoryTimeline
          v-else-if="cardId === 'history'"
          :events="doc.events"
        />
        </div>
      </div>
    </div>

    <!-- The hero's primary actions, reachable once the hero itself has
         scrolled off screen (see the IntersectionObserver above). `v-if` (not
         `v-show`) keeps it out of the DOM entirely while the hero is visible,
         so it can't interfere with narrow-viewport e2e specs. This is the
         *late* mount slot: bottom-anchored positions pin `bottom-0` from here;
         top-anchored positions use the early slot near the top instead. -->
    <ActionDock v-if="!heroVisible && !dockAtTop" :ask-href="askHref" />
  </template>

  <template v-else-if="notFound">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">
      Document not found
    </h1>
    <p class="text-gray-600 dark:text-gray-300">It may have been deleted, or the link is wrong.</p>
  </template>
  <div
    v-else-if="loadError"
    class="card p-4 text-gray-600 dark:text-gray-300"
  >
    Sorry, the document could not be loaded. Try again later.
  </div>
</template>

<style scoped>
/* Markdown rendered via v-html; restore readable prose spacing stripped by
   Tailwind preflight (mirrors .ask-answer in AskView.vue). */
.doc-markdown :deep(p) {
  margin-bottom: 0.75rem;
}
.doc-markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.doc-markdown :deep(strong) {
  font-weight: 600;
}
.doc-markdown :deep(em) {
  font-style: italic;
}
.doc-markdown :deep(ul),
.doc-markdown :deep(ol) {
  margin: 0.5rem 0 0.75rem;
  padding-left: 1.5rem;
}
.doc-markdown :deep(ul) {
  list-style: disc;
}
.doc-markdown :deep(ol) {
  list-style: decimal;
}
.doc-markdown :deep(li) {
  margin-bottom: 0.25rem;
}
.doc-markdown :deep(h1),
.doc-markdown :deep(h2),
.doc-markdown :deep(h3) {
  font-weight: 600;
  margin: 0.75rem 0 0.5rem;
}
.doc-markdown :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.875em;
  padding: 0.1em 0.3em;
  border-radius: 0.25rem;
  background: rgb(0 0 0 / 0.06);
}
.dark .doc-markdown :deep(code) {
  background: rgb(255 255 255 / 0.08);
}
/* Fenced code blocks scroll horizontally inside the block rather than
   overflowing the card and the viewport. */
.doc-markdown :deep(pre) {
  margin: 0.75rem 0;
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  background: rgb(0 0 0 / 0.06);
  overflow-x: auto;
}
.dark .doc-markdown :deep(pre) {
  background: rgb(255 255 255 / 0.08);
}
.doc-markdown :deep(pre code) {
  padding: 0;
  background: none;
  white-space: pre;
}
</style>
