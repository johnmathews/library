<script setup lang="ts">
/**
 * Ask — question answering over the archive, laid out like a chat app.
 *
 * One column fills the area under the app header, edge to edge, with no card
 * around it: a slim thread bar (AskThreadBar), the transcript, and the
 * composer docked at the bottom (AskComposer). The transcript and the composer
 * share one centred reading column, so the text you type lines up with the
 * answers above it. Conversations are switched from the title's ▾ menu rather
 * than a list beside the transcript, so the reading column keeps the width.
 *
 * Routes:
 *   - `/ask` and `/ask/new` (names `ask`, `ask-new`) → a fresh conversation.
 *   - `/ask/:threadId`      (name `ask-thread`)      → that conversation.
 *   - `/ask?q=…` (the document detail "Ask about this document" button) moves
 *     to `/ask/new?q=…` and seeds the composer.
 *
 * Retry and edit re-ask in place: the request names the turn to replace
 * (`replace_from_turn_id`), and the server drops it and every later turn only
 * once the new answer exists. Until then the transcript shows the replacement
 * as pending; if it fails, the previous turns come back unchanged.
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useMediaQuery } from '@vueuse/core'
import { useRoute, useRouter } from 'vue-router'
import { AppErrorSummary } from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import { askQuestion, deleteThread, getThread, renameThread, type AskImage } from '@/api/ask'
import { getLLMBackends } from '@/api/settings'
import { ApiError } from '@/api/client'
import AskThreadBar from '@/components/ask/AskThreadBar.vue'
import AskTurn, { type AskTurnVM } from '@/components/ask/AskTurn.vue'
import AskComposer, { type PendingImage } from '@/components/ask/AskComposer.vue'
import { backendLabel, modelLabel } from '@/components/ask/modelLabel'
import { useAskViewMode } from '@/composables/useAskViewMode'
import { usePageTitle } from '@/composables/usePageTitle'

// A few starter questions shown in a new conversation; tapping one fills the
// composer, ready to edit or send.
const EXAMPLE_PROMPTS = [
  'Which invoices are due this month?',
  'Summarise my latest energy contract',
  'When does my passport expire?',
]

const route = useRoute()
const router = useRouter()

// The app bar shows the section title; this view has no PageHeader of its own.
const titleToken = Symbol('ask')
const { claimPageTitle, releasePageTitle } = usePageTitle()
claimPageTitle(titleToken, 'Ask')
onUnmounted(() => releasePageTitle(titleToken))

const question = ref('')
const pendingImages = ref<PendingImage[]>([])
// True from the moment a request is dispatched until its answer lands or fails.
const isAnswering = ref(false)
// The controller for the in-flight ask, so Stop can cancel it.
let inFlight: AbortController | null = null
const errorMessage = ref<string | null>(null)
const turns = ref<AskTurnVM[]>([])
const threadId = ref<number | null>(null)
const threadTitle = ref('')
const transcriptRef = ref<HTMLElement | null>(null)
const composerRef = ref<InstanceType<typeof AskComposer> | null>(null)
const modelChip = ref<string | null>(null)

// Desktop is lg+; below that plain Enter inserts a newline (see AskComposer).
const isLargeScreen = useMediaQuery('(min-width: 1024px)')
const { viewMode, isDocumentMode, modes } = useAskViewMode(isLargeScreen)

const isThreadRoute = computed<boolean>(() => route.name === 'ask-thread')
// Starting a new conversation from an empty new one does nothing, so the
// button is disabled there rather than a dead affordance.
const canStartNew = computed<boolean>(() => threadId.value !== null || turns.value.length > 0)
const errors = computed<ErrorSummaryItem[]>(() =>
  errorMessage.value ? [{ text: errorMessage.value }] : [],
)

function focusComposer(): void {
  void nextTick(() => composerRef.value?.focus())
}

// Keep the latest turn in view after a send or an answer.
function scrollToBottom(): void {
  void nextTick(() => {
    const el = transcriptRef.value
    if (!el) return
    if (el.scrollHeight > el.clientHeight + 1) {
      el.scrollTop = el.scrollHeight
      return
    }
    const turnEls = el.querySelectorAll('[data-testid="ask-turn"]')
    const last = turnEls[turnEls.length - 1] as HTMLElement | undefined
    last?.scrollIntoView?.({ behavior: 'smooth', block: 'end' })
  })
}

// Opening an existing conversation starts at its BEGINNING: the first
// question is what makes the rest readable. Scroll-to-bottom is for new turns.
function scrollToTop(): void {
  void nextTick(() => {
    if (transcriptRef.value) transcriptRef.value.scrollTop = 0
  })
}

function usePrompt(prompt: string): void {
  question.value = prompt
  focusComposer()
}

function friendlyError(error: unknown): string {
  if (error instanceof ApiError) {
    // Several faults answer 503 — no API key, an expired subscription login —
    // and the server's `detail` names which one, so show it. A 503 with no JSON
    // body came from a proxy or a restart, not the app.
    if (error.status === 503 && error.body === null) {
      return 'Answering is unavailable right now — try again shortly.'
    }
    if (error.status === 0) return 'Network problem — check your connection and try again.'
    return error.detail
  }
  return 'Something went wrong — try again.'
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

/**
 * Ask `text`. With `replaceFrom` (a transcript index) this is a retry or an
 * edit: the turn at that index and every later one are replaced by the new
 * answer — on screen at once, on the server only once the answer exists.
 */
async function send(text: string, replaceFrom?: number): Promise<void> {
  // One question at a time: while an answer generates the composer's primary
  // action is Stop, not a second Send.
  if (isAnswering.value) return
  const trimmed = text.trim()
  if (!trimmed) {
    errorMessage.value = 'Enter a question to ask'
    return
  }
  let replaceFromTurnId: number | undefined
  if (replaceFrom !== undefined) {
    // A turn still generating has no id yet, so there is nothing to replace.
    const id = turns.value[replaceFrom]?.id
    if (typeof id !== 'number') return
    replaceFromTurnId = id
  }

  errorMessage.value = null
  isAnswering.value = true
  const wasNewThread = threadId.value === null
  const previousTurns = turns.value
  const sentImages = replaceFrom === undefined ? pendingImages.value : []
  const apiImages: AskImage[] = sentImages.map((image) => ({
    media_type: image.media_type,
    data: image.data,
  }))

  // Optimistically show the question with a thinking placeholder.
  const pendingTurn: AskTurnVM = {
    id: null,
    query: trimmed,
    answer: '',
    citations: [],
    usedTools: [],
    costUsd: 0,
    pending: true,
  }
  turns.value =
    replaceFrom === undefined
      ? [...previousTurns, pendingTurn]
      : [...previousTurns.slice(0, replaceFrom), pendingTurn]
  // The array the placeholder lives in. Navigating to another conversation
  // replaces `turns.value` wholesale; a result that lands after that belongs
  // to a screen no longer shown and must not touch the new one.
  const activeTurns = turns.value
  const pendingIndex = activeTurns.length - 1
  if (replaceFrom === undefined) {
    question.value = ''
    pendingImages.value = []
  }
  scrollToBottom()

  const controller = new AbortController()
  inFlight = controller
  try {
    const res = await askQuestion(
      trimmed,
      threadId.value ?? undefined,
      controller.signal,
      apiImages.length ? apiImages : undefined,
      replaceFromTurnId,
    )
    if (turns.value !== activeTurns) return
    turns.value[pendingIndex] = {
      id: res.turn_id,
      query: trimmed,
      answer: res.answer,
      citations: res.citations,
      usedTools: res.used_tools,
      costUsd: res.cost_usd,
    }
    scrollToBottom()
    // Post-success side effects live outside this try: a failure there must
    // never surface as an error on an answer that rendered fine.
    void syncThread(res.thread_id, wasNewThread)
  } catch (error: unknown) {
    if (turns.value !== activeTurns) return
    // Put back exactly what was there: for a plain send, the question and its
    // images return to the composer; for a retry or edit, the replaced turns
    // return to the transcript, because the server kept them.
    turns.value = previousTurns
    if (replaceFrom === undefined) {
      question.value = trimmed
      pendingImages.value = sentImages
    }
    if (!isAbortError(error)) errorMessage.value = friendlyError(error)
  } finally {
    isAnswering.value = false
    inFlight = null
  }
}

function onSubmit(): void {
  void send(question.value)
}
function stopAnswering(): void {
  inFlight?.abort()
}
function retry(index: number): void {
  const turn = turns.value[index]
  if (turn) void send(turn.query, index)
}
function editQuestion(index: number, text: string): void {
  void send(text, index)
}

/**
 * After a turn has rendered: record the thread, move the URL to
 * /ask/:threadId, and pick up the generated title of a new conversation.
 * Fire-and-forget; it must never throw back into `send`.
 */
async function syncThread(newThreadId: unknown, wasNewThread: boolean): Promise<void> {
  if (typeof newThreadId !== 'number' || !Number.isFinite(newThreadId)) {
    console.warn('Ask response omitted a valid thread_id; skipping URL sync', newThreadId)
    return
  }
  threadId.value = newThreadId
  if (route.name !== 'ask-thread' || Number(route.params.threadId) !== newThreadId) {
    try {
      await router.replace({ name: 'ask-thread', params: { threadId: newThreadId } })
    } catch (navError: unknown) {
      console.error('Failed to sync the Ask thread URL', navError)
    }
  }
  if (wasNewThread) {
    try {
      const detail = await getThread(newThreadId)
      if (threadId.value === newThreadId) threadTitle.value = detail.title
    } catch {
      // The title bar keeps "New conversation"; the switcher has the real one.
    }
  }
}

async function loadThread(id: number): Promise<void> {
  errorMessage.value = null
  try {
    const detail = await getThread(id)
    turns.value = detail.turns.map((t) => ({
      id: t.id,
      query: t.query,
      answer: t.answer,
      citations: t.citations,
      usedTools: t.used_tools,
      costUsd: t.cost_usd,
    }))
    threadId.value = id
    threadTitle.value = detail.title
    scrollToTop()
  } catch (error: unknown) {
    errorMessage.value = friendlyError(error)
  }
}

function resetConversation(): void {
  turns.value = []
  threadId.value = null
  threadTitle.value = ''
  errorMessage.value = null
}

function startNewChat(): void {
  if (route.name === 'ask-new') {
    // Already on a fresh chat: router.push would be a no-op, so clear in place.
    resetConversation()
    question.value = ''
    focusComposer()
    return
  }
  void router.push({ name: 'ask-new' })
}

function openThread(id: number): void {
  void router.push({ name: 'ask-thread', params: { threadId: id } })
}

async function onRename(title: string): Promise<void> {
  if (threadId.value === null) return
  try {
    await renameThread(threadId.value, title)
    threadTitle.value = title
  } catch (error: unknown) {
    errorMessage.value = friendlyError(error)
  }
}

async function onDelete(): Promise<void> {
  if (threadId.value === null) return
  try {
    await deleteThread(threadId.value)
  } catch (error: unknown) {
    errorMessage.value = friendlyError(error)
    return
  }
  void router.push({ name: 'ask-new' })
}

async function loadModelChip(): Promise<void> {
  try {
    const backends = await getLLMBackends()
    const ask = backends.surfaces.find((s) => s.surface === 'ask')
    if (ask) modelChip.value = `${modelLabel(ask.model)} · ${backendLabel(ask.backend)}`
  } catch {
    // The chip is informational; without it the composer still works.
  }
}

/** One place decides what each route shows (mount and every navigation). */
function applyRoute(): void {
  // An answer still generating belongs to the screen being left.
  if (isAnswering.value) inFlight?.abort()
  const name = route.name
  if (name === 'ask-thread') {
    const id = Number(route.params.threadId)
    if (Number.isFinite(id) && id !== threadId.value) void loadThread(id)
    return
  }
  if (name === 'ask' && typeof route.query.q === 'string' && route.query.q.length > 0) {
    void router.replace({ name: 'ask-new', query: { q: route.query.q } })
    return
  }
  resetConversation()
  const seed = route.query.q
  question.value = typeof seed === 'string' && seed.length > 0 ? seed : ''
  focusComposer()
}

watch(() => route.fullPath, applyRoute)
onMounted(() => {
  applyRoute()
  void loadModelChip()
})
</script>

<template>
  <!-- Fills the area under the 4rem app header, edge to edge: the negative
       margins cancel #app-page's padding on every breakpoint, so there is no
       card, border or gutter between the app shell and the conversation.
       `100dvh` tracks a phone's on-screen keyboard, so the composer stays
       docked above it. -->
  <div
    id="ask-page"
    data-testid="ask-page"
    class="-mx-4 -my-8 flex h-[calc(100dvh_-_4rem)] min-h-0 flex-col overflow-hidden sm:-mx-6 lg:-mx-8"
  >
    <AskThreadBar
      v-model:view-mode="viewMode"
      :title="threadTitle"
      :thread-id="threadId"
      :can-start-new="canStartNew"
      :is-large-screen="isLargeScreen"
      :modes="modes"
      @select="openThread"
      @new="startNewChat"
      @rename="onRename"
      @delete="onDelete"
    />

    <div
      id="ask-transcript"
      ref="transcriptRef"
      data-testid="ask-transcript"
      class="thin-scrollbar min-h-0 flex-1 overflow-y-auto"
    >
      <!-- Padding OUTSIDE the column, as the composer below has it, so the
           answers and the composer box share exactly the same left and right
           edges (e2e: ask-chat-layout.spec.ts). -->
      <div class="px-4 pt-4 pb-10 sm:px-6">
        <div class="mx-auto w-full max-w-3xl">
          <AppErrorSummary
            v-if="errors.length"
            :errors="errors"
            data-testid="error-summary"
            class="mb-6"
          />

          <div v-if="turns.length" class="space-y-10">
            <AskTurn
              v-for="(turn, index) in turns"
              :key="turn.id ?? `pending-${index}`"
              :turn="turn"
              :is-document-mode="isDocumentMode"
              :is-last="index === turns.length - 1"
              :later-count="turns.length - 1 - index"
              :busy="isAnswering"
              @retry="retry(index)"
              @edit="editQuestion(index, $event)"
            />
          </div>

          <div
            v-else-if="!isThreadRoute"
            data-testid="ask-greeting"
            class="mx-auto flex min-h-[50dvh] max-w-md flex-col items-center justify-center gap-5 text-center"
          >
            <div>
              <h2 class="text-2xl font-semibold text-gray-800 dark:text-gray-100">
                Ask your documents
              </h2>
              <p class="mt-2 text-sm text-gray-500 dark:text-gray-400">
                Ask a question in plain language and get an answer with citations.
              </p>
            </div>
            <div class="flex w-full flex-col gap-2">
              <button
                v-for="prompt in EXAMPLE_PROMPTS"
                :key="prompt"
                type="button"
                data-testid="ask-example-prompt"
                class="rounded-xl border border-gray-200 px-4 py-2.5 text-left text-sm text-gray-700 transition hover:border-violet-400 hover:bg-violet-50 dark:border-gray-700/60 dark:text-gray-200 dark:hover:bg-violet-500/10"
                @click="usePrompt(prompt)"
              >
                {{ prompt }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- The composer docks at the bottom on every breakpoint. The bottom padding
         includes the safe-area inset so it clears a phone's home indicator. -->
    <div
      class="relative shrink-0 px-4 pt-1 sm:px-6"
      style="padding-bottom: calc(env(safe-area-inset-bottom, 0px) + 0.75rem)"
    >
      <!-- Fades the transcript out above the composer instead of cutting text
           off at the box's top edge. Page-canvas coloured, so it reads as the
           page, not as another surface. -->
      <div
        aria-hidden="true"
        class="pointer-events-none absolute inset-x-0 -top-8 h-8 bg-linear-to-t from-(--app-canvas) to-transparent dark:from-gray-900"
      ></div>
      <div class="mx-auto w-full max-w-3xl">
        <AskComposer
          ref="composerRef"
          v-model="question"
          v-model:images="pendingImages"
          :is-answering="isAnswering"
          :is-large-screen="isLargeScreen"
          :placeholder="turns.length ? 'Ask a follow-up…' : 'Ask a question…'"
          :field-label="turns.length ? 'Follow-up question' : 'Your question'"
          :model-chip="modelChip"
          @submit="onSubmit"
          @stop="stopAnswering"
        />
      </div>
    </div>
  </div>
</template>
