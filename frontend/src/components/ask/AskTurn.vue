<script setup lang="ts">
/**
 * One question/answer turn in the Ask transcript.
 *
 * Laid out like a chat app's transcript, with no card around anything: the
 * question is a right-aligned bubble (or, in document view, a role-labelled
 * block), the answer is plain text in the reading column, and what the answer
 * rests on — the tools it used, the documents it cites — folds away into
 * one-line disclosures under it. A quiet action row carries the one-click
 * actions: copy, retry (on the latest answer) and the estimated cost.
 *
 * Editing a question re-asks from that point. Every later turn is dropped, and
 * the note under the editor says how many, because the server deletes them
 * once the new answer lands (POST /api/ask `replace_from_turn_id`).
 */
import { computed, nextTick, ref } from 'vue'
import type { AskCitation } from '@/api/ask'
import { renderAnswer } from './renderAnswer'

export interface AskTurnVM {
  /** The stored turn's id; null while the answer is still generating. */
  id: number | null
  query: string
  /** The raw markdown answer — what Copy puts on the clipboard. */
  answer: string
  citations: AskCitation[]
  usedTools: string[]
  costUsd: number
  pending?: boolean
}

const props = defineProps<{
  turn: AskTurnVM
  isDocumentMode: boolean
  /** Only the latest answer offers Retry: an earlier one is re-asked by editing its question. */
  isLast: boolean
  /** How many turns follow this one — what an edit would drop. */
  laterCount: number
  /** An answer is generating somewhere in the thread; actions wait for it. */
  busy: boolean
}>()
const emit = defineEmits<{ retry: []; edit: [question: string] }>()

const answerHtml = computed<string>(() => (props.turn.pending ? '' : renderAnswer(props.turn.answer)))
const canAct = computed<boolean>(() => props.turn.id !== null && !props.turn.pending && !props.busy)

// ── Copy ────────────────────────────────────────────────────────────────────
const copied = ref(false)
let copiedTimer: ReturnType<typeof setTimeout> | undefined
async function copyAnswer(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.turn.answer)
  } catch {
    return
  }
  copied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => (copied.value = false), 2000)
}

// ── Edit ────────────────────────────────────────────────────────────────────
const editing = ref(false)
const draft = ref('')
const editEl = ref<HTMLTextAreaElement | null>(null)

function startEdit(): void {
  draft.value = props.turn.query
  editing.value = true
  void nextTick(() => {
    editEl.value?.focus()
    editEl.value?.select()
  })
}
function cancelEdit(): void {
  editing.value = false
}
function submitEdit(): void {
  const question = draft.value.trim()
  if (!question) return
  editing.value = false
  emit('edit', question)
}
function onEditKeydown(event: KeyboardEvent): void {
  if (event.isComposing) return
  if (event.key === 'Escape') {
    event.preventDefault()
    cancelEdit()
  } else if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    submitEdit()
  }
}

const iconButton =
  'flex h-7 w-7 items-center justify-center rounded-md text-gray-400 transition hover:bg-gray-100 hover:text-gray-700 disabled:pointer-events-none disabled:opacity-40 dark:text-gray-500 dark:hover:bg-gray-700/60 dark:hover:text-gray-200'
</script>

<template>
  <section data-testid="ask-turn" class="space-y-3">
    <!-- The question -->
    <div v-if="editing" data-testid="ask-edit" class="ml-auto w-full max-w-[85%] space-y-2">
      <textarea
        ref="editEl"
        v-model="draft"
        rows="3"
        data-testid="ask-edit-input"
        aria-label="Edit your question"
        class="form-textarea w-full rounded-2xl text-sm"
        @keydown="onEditKeydown"
      ></textarea>
      <div class="flex items-center justify-end gap-3">
        <p
          v-if="laterCount > 0"
          data-testid="ask-edit-note"
          class="mr-auto text-xs text-gray-500 dark:text-gray-400"
        >
          Sending replaces this answer and the {{ laterCount === 1 ? 'one' : laterCount }} after
          it.
        </p>
        <button
          type="button"
          data-testid="ask-edit-cancel"
          class="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          @click="cancelEdit"
        >
          Cancel
        </button>
        <button
          type="button"
          data-testid="ask-edit-submit"
          class="btn-sm bg-violet-500 text-white hover:bg-violet-600 disabled:opacity-50"
          :disabled="!draft.trim()"
          @click="submitEdit"
        >
          Send
        </button>
      </div>
    </div>

    <div v-else-if="isDocumentMode" data-testid="ask-doc-block" class="group flex w-full flex-col">
      <span
        data-testid="ask-doc-role"
        class="mb-1 text-xs font-semibold tracking-wide text-violet-600 uppercase dark:text-violet-400"
        >You</span
      >
      <div class="flex items-start gap-2">
        <p
          class="min-w-0 flex-1 rounded-xl bg-violet-50 px-4 py-3 text-sm break-words whitespace-pre-wrap text-gray-800 dark:bg-violet-500/10 dark:text-gray-100"
        >
          {{ turn.query }}
        </p>
        <button
          v-if="canAct"
          type="button"
          data-testid="ask-edit-start"
          aria-label="Edit question"
          title="Edit question"
          :class="iconButton"
          @click="startEdit"
        >
          <svg class="h-4 w-4 fill-none stroke-current" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
          </svg>
        </button>
      </div>
    </div>

    <div v-else class="group flex items-start justify-end gap-1">
      <button
        v-if="canAct"
        type="button"
        data-testid="ask-edit-start"
        aria-label="Edit question"
        title="Edit question"
        class="mt-1.5 lg:opacity-0 lg:group-hover:opacity-100 lg:focus-visible:opacity-100"
        :class="iconButton"
        @click="startEdit"
      >
        <svg class="h-4 w-4 fill-none stroke-current" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
        </svg>
      </button>
      <p
        data-testid="ask-question-bubble"
        class="max-w-[85%] rounded-2xl bg-gray-100 px-4 py-2.5 text-sm break-words whitespace-pre-wrap text-gray-800 dark:bg-gray-700/60 dark:text-gray-100"
      >
        {{ turn.query }}
      </p>
    </div>

    <!-- The answer -->
    <span
      v-if="isDocumentMode"
      data-testid="ask-doc-role"
      class="block text-xs font-semibold tracking-wide text-gray-500 uppercase dark:text-gray-400"
      >Library</span
    >
    <div data-testid="ask-answer-surface" class="@container space-y-3">
      <div
        v-if="turn.pending"
        data-testid="ask-thinking"
        class="flex items-center gap-1.5 py-1 text-gray-400 dark:text-gray-500"
        role="status"
      >
        <span class="sr-only">Generating answer…</span>
        <span class="ask-dot"></span>
        <span class="ask-dot" style="animation-delay: 0.15s"></span>
        <span class="ask-dot" style="animation-delay: 0.3s"></span>
      </div>

      <template v-else>
        <div
          class="ask-answer text-[0.9375rem] leading-7 text-gray-800 dark:text-gray-100"
          data-testid="ask-answer"
          v-html="answerHtml"
        />

        <details v-if="turn.usedTools.length" data-testid="ask-tools" class="group/tools">
          <summary
            class="flex w-fit cursor-pointer list-none items-center gap-1 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 [&::-webkit-details-marker]:hidden"
          >
            <span>Used {{ turn.usedTools.length }} {{ turn.usedTools.length === 1 ? 'tool' : 'tools' }}</span>
            <svg class="h-3.5 w-3.5 fill-none stroke-current transition-transform group-open/tools:rotate-90" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path d="m9 18 6-6-6-6" />
            </svg>
          </summary>
          <ul class="mt-2 space-y-1 border-l-2 border-gray-200 pl-3 text-sm text-gray-600 dark:border-gray-700 dark:text-gray-300">
            <li v-for="(tool, i) in turn.usedTools" :key="i" class="font-mono text-xs">{{ tool }}</li>
          </ul>
        </details>

        <details
          v-if="turn.citations.length"
          data-testid="ask-citations-disclosure"
          class="group/cites"
        >
          <summary
            class="flex w-fit cursor-pointer list-none items-center gap-1 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 [&::-webkit-details-marker]:hidden"
          >
            <!-- Its own span, no surrounding whitespace: e2e finds this label by
                 an anchored /^Citations \(\d+\)$/ text match. -->
            <span>Citations ({{ turn.citations.length }})</span>
            <svg class="h-3.5 w-3.5 fill-none stroke-current transition-transform group-open/cites:rotate-90" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path d="m9 18 6-6-6-6" />
            </svg>
          </summary>
          <ul class="mt-2 grid grid-cols-1 gap-2 @lg:grid-cols-2" data-testid="ask-citations">
            <li v-for="citation in turn.citations" :key="citation.document_id">
              <RouterLink
                class="flex items-center justify-between gap-3 rounded-lg border border-gray-200 px-4 py-3 transition hover:bg-gray-50 dark:border-gray-700/60 dark:hover:bg-gray-700/40"
                :to="{
                  name: 'document-detail',
                  params: { id: citation.document_id },
                  query: citation.page_number ? { page: citation.page_number } : {},
                }"
                data-testid="ask-citation"
              >
                <span class="min-w-0 truncate text-sm text-violet-600 underline dark:text-violet-400">
                  {{ citation.title ?? 'Untitled'
                  }}<span v-if="citation.page_number">, p. {{ citation.page_number }}</span>
                </span>
                <span class="shrink-0 text-xs text-gray-500 dark:text-gray-400"
                  >#{{ citation.document_id }}</span
                >
              </RouterLink>
            </li>
          </ul>
        </details>

        <div data-testid="ask-actions" class="-ml-1.5 flex items-center gap-1">
          <button
            type="button"
            data-testid="ask-copy"
            :aria-label="copied ? 'Copied' : 'Copy answer'"
            :title="copied ? 'Copied' : 'Copy answer'"
            :class="iconButton"
            @click="copyAnswer"
          >
            <svg v-if="copied" class="h-4 w-4 fill-none stroke-current" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M20 6 9 17l-5-5" />
            </svg>
            <svg v-else class="h-4 w-4 fill-none stroke-current" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <rect x="9" y="9" width="12" height="12" rx="2" />
              <path d="M5 15V5a2 2 0 0 1 2-2h10" />
            </svg>
          </button>
          <button
            v-if="isLast"
            type="button"
            data-testid="ask-retry"
            aria-label="Retry this answer"
            title="Retry"
            :class="iconButton"
            :disabled="!canAct"
            @click="emit('retry')"
          >
            <svg class="h-4 w-4 fill-none stroke-current" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 12a9 9 0 0 1 15.5-6.2L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.2L3 16M3 21v-5h5" />
            </svg>
          </button>
          <span
            v-if="turn.costUsd"
            data-testid="ask-meta"
            class="ml-1 text-xs text-gray-400 dark:text-gray-500"
            >Estimated cost: ${{ turn.costUsd.toFixed(4) }}</span
          >
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
/* Thinking indicator: three dots that pulse in sequence while the answer
   generates. */
.ask-dot {
  display: inline-block;
  width: 0.4rem;
  height: 0.4rem;
  border-radius: 9999px;
  background: currentColor;
  animation: ask-bounce 1.2s infinite ease-in-out both;
}
@keyframes ask-bounce {
  0%,
  80%,
  100% {
    transform: scale(0.6);
    opacity: 0.4;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .ask-dot {
    animation: none;
    opacity: 0.7;
  }
}

/* The answer is rendered markdown (v-html). Tailwind's preflight strips
   default styling from these elements, so restore readable prose spacing,
   emphasis and lists for the answer body only. */

/* min-width: 0 is load-bearing, not tidiness. `.ask-answer` sits in a flex
   chain, and a flex item defaults to `min-width: auto` — its intrinsic content
   width. A wide table or a long unbroken code line then pushes the whole
   column past its basis. With min-width: 0 the overflow stays inside this
   container, where the scroll wrappers below can handle it. */
.ask-answer {
  min-width: 0;
}
.ask-answer :deep(p) {
  margin-bottom: 0.875rem;
}
.ask-answer :deep(p:last-child) {
  margin-bottom: 0;
}
.ask-answer :deep(strong) {
  font-weight: 600;
}
.ask-answer :deep(em) {
  font-style: italic;
}
.ask-answer :deep(ul),
.ask-answer :deep(ol) {
  margin: 0.5rem 0 0.875rem;
  padding-left: 1.5rem;
}
.ask-answer :deep(ul) {
  list-style: disc;
}
.ask-answer :deep(ol) {
  list-style: decimal;
}
.ask-answer :deep(li) {
  margin-bottom: 0.25rem;
}
.ask-answer :deep(a) {
  color: var(--color-violet-600, #7c3aed);
  text-decoration: underline;
}
.dark .ask-answer :deep(a) {
  color: var(--color-violet-400, #a78bfa);
}
.ask-answer :deep(code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.875em;
  padding: 0.1em 0.3em;
  border-radius: 0.25rem;
  background: rgb(0 0 0 / 0.06);
}
.dark .ask-answer :deep(code) {
  background: rgb(255 255 255 / 0.08);
}
.ask-answer :deep(h1),
.ask-answer :deep(h2),
.ask-answer :deep(h3) {
  font-weight: 600;
  margin: 1rem 0 0.5rem;
}
/* Fenced code blocks scroll horizontally inside the answer rather than
   overflowing it. */
.ask-answer :deep(pre) {
  margin: 0.75rem 0;
  padding: 0.75rem;
  border-radius: 0.375rem;
  overflow-x: auto;
  background: rgb(0 0 0 / 0.06);
}
.dark .ask-answer :deep(pre) {
  background: rgb(255 255 255 / 0.08);
}
.ask-answer :deep(pre code) {
  padding: 0;
  background: transparent;
}

/* The per-table scroll container emitted by renderAnswer's wrapTables(). That
   wrapper, `table { width: max-content }` below, and `.ask-answer
   { min-width: 0 }` above are one mechanism in three parts. */
.ask-answer :deep(.ask-table-wrap) {
  max-width: 100%;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  margin: 0.75rem 0;
  border-radius: 0.5rem;
  border: 1px solid rgb(0 0 0 / 0.1);
}
.dark .ask-answer :deep(.ask-table-wrap) {
  border-color: rgb(255 255 255 / 0.12);
}
.ask-answer :deep(.ask-table-wrap:focus-visible) {
  outline: 2px solid var(--color-violet-500);
  outline-offset: 2px;
}

/* `max-content` lets each column take its natural width and lets the wrapper
   above scroll; `width: 100%` squeezed a seven-column table into slivers and
   then burst the column anyway. The wrapper draws the outer border, so cells
   draw only the inner rules. */
.ask-answer :deep(table) {
  width: max-content;
  min-width: 100%;
  max-width: none;
  border-collapse: collapse;
  margin: 0;
  font-size: 0.875rem;
}
.ask-answer :deep(th),
.ask-answer :deep(td) {
  border-bottom: 1px solid rgb(0 0 0 / 0.08);
  padding: 0.5rem 0.75rem;
  text-align: left;
  vertical-align: top;
}
.ask-answer :deep(tr:last-child td) {
  border-bottom: 0;
}
.dark .ask-answer :deep(th),
.dark .ask-answer :deep(td) {
  border-color: rgb(255 255 255 / 0.1);
}
.ask-answer :deep(th) {
  font-weight: 600;
  background: rgb(0 0 0 / 0.04);
}
.dark .ask-answer :deep(th) {
  background: rgb(255 255 255 / 0.05);
}
</style>
