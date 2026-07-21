<script setup lang="ts">
/**
 * Ask page — a two-screen conversational UI (Option B).
 *
 * The visible screen is driven by the ROUTE, so the phone's back gesture and
 * browser history behave like a native chat app:
 *
 *   - `/ask`            (name `ask`)        → the conversation LIST.
 *   - `/ask/new`        (name `ask-new`)    → a fresh CHAT (empty state).
 *   - `/ask/:threadId`  (name `ask-thread`) → the CHAT for that thread.
 *
 * On mobile each is a full screen: the list fills the viewport; opening a thread
 * (or ＋ New) swaps to the chat screen, which carries a back arrow and a composer
 * pinned to the bottom. At lg+ both panes are shown side by side (rail | thread)
 * and the route only decides which thread is active.
 *
 * The answer is Claude-authored markdown (bold, lists, inline citations like
 * [#42]); it is rendered to sanitized HTML via marked + DOMPurify before being
 * set with v-html.
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { AppButton, AppDetails, AppErrorSummary, AppTextarea, PageHeader } from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import {
  askQuestion,
  getThread,
  renameThread,
  deleteThread,
  type AskCitation,
  type AskImage,
} from '@/api/ask'
import { ApiError } from '@/api/client'
import ConversationSidebar from '@/components/ask/ConversationSidebar.vue'
import ThreadActionsMenu from '@/components/ask/ThreadActionsMenu.vue'

interface TurnVM {
  query: string
  answerHtml: string
  citations: AskCitation[]
  usedTools: string[]
  costUsd: number
  // True while the question is on screen but its answer is still generating.
  // The turn renders a thinking indicator instead of an answer body.
  pending?: boolean
}

// A picked-but-not-yet-sent image: the base64 payload plus a data URL for the
// preview thumbnail and the original filename.
interface PendingImage extends AskImage {
  url: string
  name: string
}

const MAX_IMAGES = 5
const SUPPORTED_IMAGE_TYPES: AskImage['media_type'][] = [
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
]

// A few starter questions shown in the new-chat empty state; tapping one fills
// the composer, ready to edit or send.
const EXAMPLE_PROMPTS = [
  'Which invoices are due this month?',
  'Summarise my latest energy contract',
  'When does my passport expire?',
]

const route = useRoute()
const router = useRouter()

const question = ref('')
// True from the moment a request is dispatched until its answer lands or fails.
const isAnswering = ref(false)
// The controller for the in-flight ask, so the Stop button can cancel it.
let inFlight: AbortController | null = null
const errorMessage = ref<string | null>(null)
const turns = ref<TurnVM[]>([])
const threadId = ref<number | null>(null)
// The active thread's title, shown in the chat title bar and edited inline
// there. Empty for a not-yet-saved new chat.
const threadTitle = ref('')
const sidebarRef = ref<InstanceType<typeof ConversationSidebar> | null>(null)
// How many conversation threads exist (surfaced by the sidebar via its
// `threads-changed` event). Lets the empty state tell "no conversations yet"
// apart from "conversations exist but none is selected".
const sidebarThreadCount = ref(0)
const pendingImages = ref<PendingImage[]>([])
const imageInput = ref<HTMLInputElement | null>(null)
const transcriptRef = ref<HTMLElement | null>(null)
const composerRef = ref<HTMLElement | null>(null)

// Inline rename / delete of the ACTIVE thread from the chat title bar (the same
// actions the list rows offer, for the thread you're currently reading).
const titleEditing = ref(false)
const titleDraft = ref('')
const titleConfirmingDelete = ref(false)

// The route name decides the mobile screen and the empty-state copy.
const mobileScreen = computed<'list' | 'chat'>(() =>
  route.name === 'ask' ? 'list' : 'chat',
)
const isNewChat = computed<boolean>(() => route.name === 'ask-new')
// Whether the thread pane has a conversation to show (an active thread, a fresh
// new chat, or turns on screen). Drives the title bar; on desktop `/ask` with
// nothing selected this is false and the pane shows the "select a conversation"
// empty state instead of a title bar.
const hasChatContext = computed<boolean>(
  () => threadId.value !== null || isNewChat.value || turns.value.length > 0,
)

/** Focus the composer's question box (used when starting or opening a chat). */
function focusComposer(): void {
  void nextTick(() => {
    const field = document.getElementById('ask-question') as HTMLTextAreaElement | null
    field?.focus()
  })
}

// Keep the latest turn in view after the DOM updates. The transcript grows with
// the page rather than scrolling internally, so bring the newest turn to the
// bottom of the viewport via the page scroll. (If the transcript is ever
// internally scrollable — a narrow edge case — jump its own scrollTop instead.)
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

function readAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function onImagesPicked(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  for (const file of Array.from(input.files ?? [])) {
    if (pendingImages.value.length >= MAX_IMAGES) break
    const mediaType = file.type as AskImage['media_type']
    if (!SUPPORTED_IMAGE_TYPES.includes(mediaType)) continue
    const dataUrl = await readAsDataUrl(file)
    pendingImages.value.push({
      media_type: mediaType,
      data: dataUrl.slice(dataUrl.indexOf(',') + 1),
      url: dataUrl,
      name: file.name,
    })
  }
  // Reset so the same file can be re-picked after removal.
  input.value = ''
}

function removeImage(index: number): void {
  pendingImages.value.splice(index, 1)
}

function usePrompt(prompt: string): void {
  question.value = prompt
  focusComposer()
}

// "New conversation" is redundant only when the view is already an empty new
// conversation (no thread, no turns) — starting another does nothing. The
// sidebar greys its button out then; the mobile ＋ always navigates.
const newConversationRedundant = computed<boolean>(
  () => threadId.value === null && turns.value.length === 0,
)

const errors = computed<ErrorSummaryItem[]>(() =>
  errorMessage.value ? [{ text: errorMessage.value }] : [],
)

// The answer is markdown from Claude; render it to HTML and sanitize before
// v-html (it can echo document text, so we never trust it raw).
function renderAnswer(answer: string): string {
  return DOMPurify.sanitize(marked.parse(answer, { async: false }) as string)
}

function friendlyError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 503) {
      return 'Answering is unavailable — no AI API key is configured. Add one in settings to use Ask.'
    }
    if (error.status === 0) return 'Network problem — check your connection and try again.'
    return error.detail
  }
  return 'Something went wrong — try again.'
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

async function onSubmit(): Promise<void> {
  // One question at a time: ignore a submit while an answer is generating (the
  // primary action is a Stop button in that state, not a second Send).
  if (isAnswering.value) return
  const trimmed = question.value.trim()
  if (!trimmed) {
    errorMessage.value = 'Enter a question to ask'
    return
  }
  errorMessage.value = null
  isAnswering.value = true

  const wasNewThread = threadId.value === null
  const sentImages = pendingImages.value
  const apiImages: AskImage[] = sentImages.map((image) => ({
    media_type: image.media_type,
    data: image.data,
  }))

  // Optimistically render the question with a thinking placeholder and clear the
  // composer immediately.
  turns.value.push({
    query: trimmed,
    answerHtml: '',
    citations: [],
    usedTools: [],
    costUsd: 0,
    pending: true,
  })
  // The array we appended the optimistic turn to. If the user navigates to a
  // different thread (or a new/empty chat) before this request resolves,
  // `turns.value` is replaced wholesale (loadThread / applyRoute). Applying a
  // stale result to the *new* array would corrupt the now-active transcript and
  // could force-navigate back, so both branches below bail when the reference
  // has changed. (applyRoute also aborts an in-flight ask on navigation.)
  const activeTurns = turns.value
  const pendingIndex = activeTurns.length - 1
  question.value = ''
  pendingImages.value = []
  scrollToBottom()

  const controller = new AbortController()
  inFlight = controller
  try {
    const res = await askQuestion(
      trimmed,
      threadId.value ?? undefined,
      controller.signal,
      apiImages.length ? apiImages : undefined,
    )
    // The user navigated to another thread/chat while this was in flight — the
    // answer belongs to a screen that is no longer shown. Drop it silently.
    if (turns.value !== activeTurns) return
    // Fill the placeholder turn in place with the rendered answer.
    turns.value[pendingIndex] = {
      query: trimmed,
      answerHtml: renderAnswer(res.answer),
      citations: res.citations,
      usedTools: res.used_tools,
      costUsd: res.cost_usd,
    }
    scrollToBottom()
    // The answer has rendered successfully. Everything below is a post-success
    // side effect (track the thread in state, sync the URL, refresh the
    // sidebar). A failure here must NEVER be surfaced as an error on a valid
    // answer, so it lives outside onSubmit's answer-error catch.
    void syncThread(res.thread_id, wasNewThread)
  } catch (error: unknown) {
    // Navigated away mid-request: the optimistic turn is gone with the old
    // transcript, and there is nothing on this screen to restore or report.
    if (turns.value !== activeTurns) return
    // Drop the optimistic turn and restore the question + images so the user can
    // edit and resend. A user-initiated Stop (AbortError) is silent; any other
    // failure surfaces the friendly error.
    turns.value.splice(pendingIndex, 1)
    question.value = trimmed
    pendingImages.value = sentImages
    if (!isAbortError(error)) {
      errorMessage.value = friendlyError(error)
    }
  } finally {
    isAnswering.value = false
    inFlight = null
  }
}

/** Cancel the in-flight ask (wired to the Stop button while answering). */
function stopAnswering(): void {
  inFlight?.abort()
}

/**
 * Enter sends; Shift+Enter and Ctrl+J insert a newline; Cmd/Ctrl+Enter still
 * sends. Enter while an IME composition is in progress never sends.
 */
function onComposerKeydown(event: KeyboardEvent): void {
  if (event.isComposing || event.keyCode === 229) return

  if (event.key === 'j' && event.ctrlKey && !event.metaKey) {
    event.preventDefault()
    const el = event.target as HTMLTextAreaElement
    const start = el.selectionStart ?? question.value.length
    const end = el.selectionEnd ?? start
    question.value = question.value.slice(0, start) + '\n' + question.value.slice(end)
    void nextTick(() => {
      el.selectionStart = el.selectionEnd = start + 1
    })
    return
  }

  if (event.key === 'Enter') {
    if (event.shiftKey) return
    event.preventDefault()
    void onSubmit()
  }
}

/**
 * Post-success side effects after a turn has rendered: record the thread id,
 * sync the URL to /ask/:threadId, and refresh the sidebar for a new thread.
 *
 * Fire-and-forget and self-contained: it must never throw back into onSubmit,
 * because by the time it runs the answer is already on screen.
 */
async function syncThread(newThreadId: unknown, wasNewThread: boolean): Promise<void> {
  if (typeof newThreadId !== 'number' || !Number.isFinite(newThreadId)) {
    console.warn('Ask response omitted a valid thread_id; skipping URL sync', newThreadId)
    return
  }
  threadId.value = newThreadId
  // Move to /ask/:threadId unless we're already there (following a thread).
  if (route.name !== 'ask-thread' || Number(route.params.threadId) !== newThreadId) {
    try {
      await router.replace({ name: 'ask-thread', params: { threadId: newThreadId } })
    } catch (navError: unknown) {
      console.error('Failed to sync the Ask thread URL', navError)
    }
  }
  // Refresh the sidebar when a new thread was just created.
  if (wasNewThread) {
    sidebarRef.value?.refresh()
  }
}

async function loadThread(id: number): Promise<void> {
  errorMessage.value = null
  titleEditing.value = false
  titleConfirmingDelete.value = false
  try {
    const detail = await getThread(id)
    turns.value = detail.turns.map((t) => ({
      query: t.query,
      answerHtml: renderAnswer(t.answer),
      citations: t.citations,
      usedTools: t.used_tools,
      costUsd: t.cost_usd,
    }))
    threadId.value = id
    threadTitle.value = detail.title
    scrollToBottom()
  } catch (error: unknown) {
    errorMessage.value = friendlyError(error)
  }
}

/** Navigate to a fresh chat screen and focus the composer. */
function startNewChat(): void {
  if (route.name === 'ask-new') {
    // Already on a fresh chat: just clear and focus (router.push would reject).
    turns.value = []
    threadId.value = null
    threadTitle.value = ''
    question.value = ''
    errorMessage.value = null
    focusComposer()
    return
  }
  void router.push({ name: 'ask-new' })
}

/** Back arrow on the chat screen → the conversation list. */
function backToList(): void {
  void router.push({ name: 'ask' })
}

/** Exposed for the sidebar's "new" event (and delete-of-active fallback). */
function resetConversation(): void {
  startNewChat()
}

// ── Chat title-bar actions (rename / delete the active thread) ───────────────
function startTitleRename(): void {
  titleConfirmingDelete.value = false
  titleDraft.value = threadTitle.value
  titleEditing.value = true
  void nextTick(() => {
    document.getElementById('ask-title-rename-input')?.focus()
  })
}

function cancelTitleRename(): void {
  titleEditing.value = false
  titleDraft.value = ''
}

async function saveTitleRename(): Promise<void> {
  const title = titleDraft.value.trim()
  if (threadId.value === null || !title || title === threadTitle.value) {
    cancelTitleRename()
    return
  }
  await renameThread(threadId.value, title)
  threadTitle.value = title
  cancelTitleRename()
  sidebarRef.value?.refresh()
}

async function confirmTitleDelete(): Promise<void> {
  if (threadId.value === null) return
  const id = threadId.value
  titleConfirmingDelete.value = false
  await deleteThread(id)
  sidebarRef.value?.refresh()
  backToList()
}

/**
 * React to route changes (initial mount and subsequent navigation, including
 * the sidebar changing the thread id without a remount). One place decides what
 * each screen shows.
 */
function applyRoute(): void {
  // If an answer is still generating for the screen we're leaving, cancel it —
  // its result belongs to the old thread. onSubmit's navigation guard makes this
  // safe even if the abort races the transcript swap.
  if (isAnswering.value) inFlight?.abort()
  const name = route.name
  if (name === 'ask-thread') {
    const id = Number(route.params.threadId)
    if (Number.isFinite(id) && id !== threadId.value) loadThread(id)
    return
  }
  // A `/ask?q=…` deep link (e.g. the document detail "Ask about this document"
  // button) seeds a NEW question — send it to the chat screen where the composer
  // lives (on mobile the list screen has no composer). Preserve the query.
  if (name === 'ask' && typeof route.query.q === 'string' && route.query.q.length > 0) {
    void router.replace({ name: 'ask-new', query: { q: route.query.q } })
    return
  }
  // `ask` (empty list / nothing selected) or `ask-new` (fresh chat): no active
  // thread. Clear the transcript so a stale thread doesn't linger.
  turns.value = []
  threadId.value = null
  threadTitle.value = ''
  titleEditing.value = false
  titleConfirmingDelete.value = false
  errorMessage.value = null
  if (name === 'ask-new') {
    const seed = route.query.q
    question.value = typeof seed === 'string' && seed.length > 0 ? seed : ''
    focusComposer()
  }
}

watch(() => route.fullPath, applyRoute)

onMounted(applyRoute)

// Expose resetConversation so the sidebar can call it via template ref.
defineExpose({ resetConversation })
</script>

<template>
  <!-- The Ask view grows with its content and the whole page scrolls. On mobile
       it is two full screens (list ⇄ chat); at lg+ it is one two-pane panel. -->
  <div>
    <!-- Desktop page header. Hidden on mobile, where the list screen has its own
         compact title bar and the chat screen has none (F6: no big blurb). -->
    <PageHeader
      title="Ask"
      description="Ask a question about your documents in plain language and get an answer with citations."
      class="max-lg:hidden"
    />

    <!-- Mobile list-screen title bar: a compact "Ask" heading + a ＋ that starts
         a new chat. Only on mobile, only on the list screen. -->
    <div
      v-if="mobileScreen === 'list'"
      class="lg:hidden flex items-center justify-between mb-4"
    >
      <h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100">Ask</h1>
      <button
        type="button"
        data-testid="ask-new-mobile"
        aria-label="New conversation"
        class="flex h-10 w-10 items-center justify-center rounded-xl bg-violet-500 text-white shadow-xs hover:bg-violet-600 transition"
        @click="startNewChat"
      >
        <svg class="h-5 w-5 fill-current" viewBox="0 0 16 16" aria-hidden="true">
          <path d="M15 7H9V1c0-.6-.4-1-1-1S7 .4 7 1v6H1c-.6 0-1 .4-1 1s.4 1 1 1h6v6c0 .6.4 1 1 1s1-.4 1-1V9h6c.6 0 1-.4 1-1s-.4-1-1-1z" />
        </svg>
      </button>
    </div>

    <AppErrorSummary
      v-if="errors.length"
      :errors="errors"
      data-testid="error-summary"
      class="mb-6"
    />

    <!-- One cohesive chat panel. On lg+ a two-pane row (rail | thread); below lg
         the rail (list screen) and the thread column (chat screen) are shown one
         at a time, gated by the route-derived mobileScreen. -->
    <div
      id="ask-page"
      class="flex flex-col lg:flex-row rounded-xl border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-800 shadow-xs"
    >
      <ConversationSidebar
        ref="sidebarRef"
        :active-thread-id="threadId"
        :new-disabled="newConversationRedundant"
        :class="{ 'max-lg:hidden': mobileScreen !== 'list' }"
        @select="(id: number) => router.push({ name: 'ask-thread', params: { threadId: id } })"
        @new="resetConversation"
        @threads-changed="(count: number) => (sidebarThreadCount = count)"
      />

      <!-- Thread + composer column (the chat screen on mobile). -->
      <div
        data-testid="ask-thread-pane"
        class="flex flex-col flex-1 min-w-0"
        :class="{ 'max-lg:hidden': mobileScreen !== 'chat' }"
      >
        <!-- Chat title bar: back arrow (mobile), the thread title (or inline
             rename), and a ⋯ menu for the active thread. Shown whenever there's
             a conversation in the pane. -->
        <div
          v-if="hasChatContext"
          data-testid="ask-thread-bar"
          class="shrink-0 flex items-center gap-2 border-b border-gray-200 dark:border-gray-700/60 px-3 py-2.5 min-h-[3.25rem]"
        >
          <button
            type="button"
            data-testid="ask-back"
            aria-label="Back to conversations"
            class="lg:hidden flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700/60 transition"
            @click="backToList"
          >
            <svg class="h-5 w-5 fill-none stroke-current" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M15 5l-7 7 7 7" />
            </svg>
          </button>

          <template v-if="titleEditing">
            <input
              id="ask-title-rename-input"
              v-model="titleDraft"
              data-testid="ask-title-rename-input"
              type="text"
              maxlength="120"
              aria-label="Conversation title"
              class="form-input flex-1 text-sm"
              @keydown.enter.prevent="saveTitleRename"
              @keydown.esc.prevent="cancelTitleRename"
            />
            <button
              type="button"
              data-testid="ask-title-rename-save"
              class="text-xs font-medium text-violet-600 hover:text-violet-700 dark:text-violet-400 transition"
              @click="saveTitleRename"
            >
              Save
            </button>
            <button
              type="button"
              class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
              @click="cancelTitleRename"
            >
              Cancel
            </button>
          </template>

          <template v-else>
            <span class="min-w-0 flex-1 truncate text-sm font-medium text-gray-800 dark:text-gray-100">
              {{ threadTitle || 'New conversation' }}
            </span>
            <template v-if="threadId !== null">
              <template v-if="titleConfirmingDelete">
                <button
                  type="button"
                  data-testid="ask-title-delete-confirm"
                  class="text-xs font-medium text-red-500 hover:text-red-600 dark:hover:text-red-400 transition"
                  @click="confirmTitleDelete"
                >
                  Delete
                </button>
                <button
                  type="button"
                  class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
                  @click="titleConfirmingDelete = false"
                >
                  Cancel
                </button>
              </template>
              <ThreadActionsMenu
                v-else
                testid="ask-title-actions"
                label="Conversation actions"
                @rename="startTitleRename"
                @delete="titleConfirmingDelete = true"
              />
            </template>
          </template>
        </div>

        <!-- Message thread: the user's question as a right-aligned violet bubble,
             the answer beneath it on a subtle surface card. It grows with the
             conversation; the page (not this box) scrolls. Bottom padding leaves
             room for the pinned composer so the last turn's citations are never
             hidden behind it. -->
        <div
          ref="transcriptRef"
          data-testid="ask-transcript"
          class="p-5 sm:p-6 pb-28"
        >
          <div v-if="turns.length" class="space-y-8">
            <section
              v-for="(turn, i) in turns"
              :key="i"
              data-testid="ask-turn"
              class="space-y-3"
            >
              <!-- User question — right-aligned chat bubble in the violet accent. -->
              <div class="flex justify-end">
                <p
                  class="max-w-[85%] rounded-2xl rounded-tr-sm bg-violet-600 text-white px-4 py-2 text-sm whitespace-pre-wrap break-words"
                >
                  {{ turn.query }}
                </p>
              </div>

              <!-- Assistant answer on a subtle surface card. -->
              <div
                data-testid="ask-answer-surface"
                class="space-y-3 rounded-2xl rounded-tl-sm border border-gray-200 dark:border-gray-700/60 bg-gray-50 dark:bg-gray-900/40 px-4 py-3"
              >
                <div
                  v-if="turn.pending"
                  data-testid="ask-thinking"
                  class="flex items-center gap-1.5 text-gray-400 dark:text-gray-500"
                  aria-live="polite"
                  aria-busy="true"
                >
                  <span class="sr-only">Generating answer…</span>
                  <span class="ask-dot"></span>
                  <span class="ask-dot" style="animation-delay: 0.15s"></span>
                  <span class="ask-dot" style="animation-delay: 0.3s"></span>
                </div>

                <template v-else>
                  <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in renderAnswer -->
                  <div
                    class="ask-answer text-gray-800 dark:text-gray-100"
                    data-testid="ask-answer"
                    v-html="turn.answerHtml"
                  />

                  <div v-if="turn.citations.length" data-testid="ask-citations-disclosure">
                    <AppDetails :summary="`Citations (${turn.citations.length})`" :open="false">
                      <ul
                        class="grid grid-cols-1 md:grid-cols-2 gap-2"
                        data-testid="ask-citations"
                      >
                        <li v-for="citation in turn.citations" :key="citation.document_id">
                          <RouterLink
                            class="flex items-center justify-between gap-3 px-4 py-3 border border-gray-200 dark:border-gray-700/60 rounded-lg hover:bg-white dark:hover:bg-gray-700/40 transition"
                            :to="{
                              name: 'document-detail',
                              params: { id: citation.document_id },
                              query: citation.page_number ? { page: citation.page_number } : {},
                            }"
                            data-testid="ask-citation"
                          >
                            <span
                              class="min-w-0 truncate text-sm text-violet-600 dark:text-violet-400 underline"
                            >
                              {{ citation.title ?? 'Untitled'
                              }}<span v-if="citation.page_number">, p. {{ citation.page_number }}</span>
                            </span>
                            <span class="shrink-0 text-xs text-gray-500 dark:text-gray-400"
                              >#{{ citation.document_id }}</span
                            >
                          </RouterLink>
                        </li>
                      </ul>
                    </AppDetails>
                  </div>

                  <p
                    v-if="turn.usedTools.length || turn.costUsd"
                    class="text-xs text-gray-500 dark:text-gray-400"
                    data-testid="ask-meta"
                  >
                    <span v-if="turn.usedTools.length">Tools: {{ turn.usedTools.join(', ') }}</span>
                    <span v-if="turn.usedTools.length && turn.costUsd"> · </span>
                    <span v-if="turn.costUsd">Estimated cost: ${{ turn.costUsd.toFixed(4) }}</span>
                  </p>
                </template>
              </div>
            </section>
          </div>

          <!-- Empty states, centred in a minimum-height area so the pane never
               looks broken before the first question. -->
          <div v-else class="min-h-[18rem] flex flex-col items-center justify-center text-center">
            <!-- New chat: a friendly greeting + a few example prompts. -->
            <template v-if="isNewChat">
              <div
                data-testid="ask-greeting"
                class="flex flex-col items-center gap-4 max-w-sm"
              >
                <span class="flex h-14 w-14 items-center justify-center rounded-2xl bg-violet-50 dark:bg-violet-500/15 text-violet-600 dark:text-violet-400">
                  <svg class="h-7 w-7 fill-none stroke-current" stroke-width="1.8" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12z" />
                  </svg>
                </span>
                <div>
                  <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100">Ask your documents</h2>
                  <p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
                    Ask a question in plain language and get an answer with citations.
                  </p>
                </div>
                <div class="flex w-full flex-col gap-2">
                  <button
                    v-for="prompt in EXAMPLE_PROMPTS"
                    :key="prompt"
                    type="button"
                    data-testid="ask-example-prompt"
                    class="rounded-xl border border-gray-200 dark:border-gray-700/60 px-4 py-2.5 text-left text-sm text-gray-700 dark:text-gray-200 hover:border-violet-400 hover:bg-violet-50 dark:hover:bg-violet-500/10 transition"
                    @click="usePrompt(prompt)"
                  >
                    {{ prompt }}
                  </button>
                </div>
              </div>
            </template>

            <!-- Nothing selected (chiefly the desktop rail with no active thread). -->
            <p
              v-else-if="sidebarThreadCount > 0"
              data-testid="ask-select-thread"
              class="max-w-sm text-gray-500 dark:text-gray-400"
            >
              Select a conversation from the sidebar, or tap “New conversation” to ask one.
            </p>

            <p
              v-else
              data-testid="ask-empty"
              class="max-w-sm text-gray-500 dark:text-gray-400"
            >
              No questions yet. Tap “New conversation” to ask one — for example, “which invoices
              are due this month?”
            </p>
          </div>
        </div>

        <!-- Composer. Pinned to the bottom of the chat area (sticky), always
             present on the chat screen — no reveal step. A solid background masks
             the transcript scrolling beneath it; the transcript's bottom padding
             keeps the last turn's citations clear of it (the historical
             citation-overlap bug). Hidden on the mobile list screen because the
             whole thread column is. -->
        <form
          id="ask-form"
          ref="composerRef"
          novalidate
          class="sticky bottom-0 z-10 shrink-0 border-t border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-800 p-3 sm:p-4 rounded-b-xl"
          data-testid="ask-form"
          @submit.prevent="onSubmit"
        >
          <!-- Pending image attachments: preview thumbnails + remove. -->
          <ul
            v-if="pendingImages.length"
            data-testid="ask-image-previews"
            class="mb-3 flex flex-wrap gap-2"
          >
            <li v-for="(image, i) in pendingImages" :key="i" class="relative">
              <img
                :src="image.url"
                :alt="image.name"
                data-testid="ask-image-preview"
                class="h-16 w-16 object-cover rounded-lg border border-gray-200 dark:border-gray-700"
              />
              <button
                type="button"
                data-testid="ask-image-remove"
                :aria-label="`Remove ${image.name}`"
                class="absolute -top-2 -right-2 h-5 w-5 flex items-center justify-center rounded-full bg-gray-700 text-white text-xs hover:bg-red-600"
                @click="removeImage(i)"
              >
                ×
              </button>
            </li>
          </ul>

          <div class="flex items-end gap-2">
            <input
              ref="imageInput"
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              multiple
              class="hidden"
              data-testid="ask-image-input"
              @change="onImagesPicked"
            />
            <!-- Inline paperclip attach: a compact icon-button inside the
                 composer, replacing the old labelled "Attach image" button. -->
            <button
              type="button"
              data-testid="ask-image-attach"
              aria-label="Attach image"
              class="mb-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700/60 disabled:opacity-40 transition"
              :disabled="pendingImages.length >= MAX_IMAGES"
              @click="imageInput?.click()"
            >
              <svg class="h-5 w-5 fill-none stroke-current" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M21 12.5 12 21a5 5 0 0 1-7-7l8.5-8.5a3.5 3.5 0 0 1 5 5L10 18a1.5 1.5 0 0 1-2-2l7.5-7.5" />
              </svg>
            </button>

            <div class="min-w-0 flex-1">
              <AppTextarea
                id="ask-question"
                v-model="question"
                :label="turns.length ? 'Follow-up question' : 'Your question'"
                hide-label
                hint="Enter to send · Shift+Enter for new line"
                :rows="2"
                data-testid="ask-question"
                @keydown="onComposerKeydown"
              />
            </div>

            <!-- While answering, the primary action is a live Stop control, not a
                 greyed-out button — the request is cancellable. -->
            <AppButton
              v-if="isAnswering"
              id="ask-submit"
              type="button"
              variant="warning"
              data-testid="ask-submit"
              class="mb-1"
              @click="stopAnswering"
            >
              Stop
            </AppButton>
            <AppButton
              v-else
              id="ask-submit"
              type="submit"
              data-testid="ask-submit"
              class="mb-1"
            >
              Send
            </AppButton>
          </div>
        </form>
      </div>
    </div>
  </div>
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
.ask-answer :deep(p) {
  margin-bottom: 0.75rem;
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
  margin: 0.5rem 0 0.75rem;
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
  margin: 0.75rem 0 0.5rem;
}
/* Wide answers can include GFM tables; give them readable borders/spacing. */
.ask-answer :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75rem 0;
  font-size: 0.875rem;
}
.ask-answer :deep(th),
.ask-answer :deep(td) {
  border: 1px solid rgb(0 0 0 / 0.12);
  padding: 0.4rem 0.6rem;
  text-align: left;
}
.dark .ask-answer :deep(th),
.dark .ask-answer :deep(td) {
  border-color: rgb(255 255 255 / 0.14);
}
.ask-answer :deep(th) {
  font-weight: 600;
  background: rgb(0 0 0 / 0.04);
}
.dark .ask-answer :deep(th) {
  background: rgb(255 255 255 / 0.06);
}
</style>
