<script setup lang="ts">
/**
 * Ask page (routes `/ask` and `/ask/:threadId`).
 *
 * Renders a scrollable multi-turn chat transcript. Each turn shows the
 * user's question, a sanitized markdown answer, a citation card list,
 * and a tools/cost meta line. A follow-up input is pinned below the
 * transcript; it posts with the active thread_id so the backend groups
 * turns into a conversation.
 *
 * Route resume: when mounted on /ask/:threadId (e.g. via the sidebar in
 * Task 7), the component calls getThread() and rehydrates the turn list.
 * A watcher on route.params.threadId handles sidebar navigation without a
 * full remount.
 *
 * The answer is Claude-authored markdown (bold, lists, inline citations
 * like [#42]); it is rendered to sanitized HTML via marked + DOMPurify
 * before being set with v-html.
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { AppButton, AppDetails, AppErrorSummary, AppTextarea, PageHeader } from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import { askQuestion, getThread, type AskCitation, type AskImage } from '@/api/ask'
import { ApiError } from '@/api/client'
import ConversationSidebar from '@/components/ask/ConversationSidebar.vue'

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

const route = useRoute()
const router = useRouter()

const question = ref('')
// True from the moment a request is dispatched until its answer lands or fails.
// Distinct from "sending": the question and the user's optimistic turn appear
// instantly; isAnswering only gates the in-flight LLM call.
const isAnswering = ref(false)
// The controller for the in-flight ask, so the Stop button can cancel it.
let inFlight: AbortController | null = null
const errorMessage = ref<string | null>(null)
const turns = ref<TurnVM[]>([])
const threadId = ref<number | null>(null)
const sidebarRef = ref<InstanceType<typeof ConversationSidebar> | null>(null)
// How many conversation threads exist (surfaced by the sidebar via its
// `threads-changed` event). Lets the empty state tell "no conversations yet"
// apart from "conversations exist but none is selected".
const sidebarThreadCount = ref(0)
const pendingImages = ref<PendingImage[]>([])
const imageInput = ref<HTMLInputElement | null>(null)
const transcriptRef = ref<HTMLElement | null>(null)
const composerRef = ref<HTMLElement | null>(null)
// On small screens the composer used to sit far below the sidebar + transcript,
// so asking a question meant scrolling past everything to find it. It is now
// hidden on mobile (`max-lg:hidden`) until the user opens it — via "New
// conversation" or by opening a thread. At lg+ it is always docked beside the
// thread, so this flag is a no-op there.
const composerOpen = ref(false)

/** Reveal the composer (on mobile), focus the question box, and bring it into
 * view. Wired to "New conversation" so that prominent button does something
 * visible instead of silently clearing state off-screen. */
function openComposer(): void {
  composerOpen.value = true
  void nextTick(() => {
    const field = document.getElementById('ask-question') as HTMLTextAreaElement | null
    field?.focus()
    composerRef.value?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  })
}

// Keep the latest turn in view: the transcript scrolls internally (chat layout),
// so jump it to the bottom after the DOM updates. Called on a new optimistic
// turn, on a resolving answer (length unchanged, so a watcher would miss it),
// and on a rehydrated thread.
function scrollToBottom(): void {
  void nextTick(() => {
    const el = transcriptRef.value
    if (el) el.scrollTop = el.scrollHeight
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
  // composer immediately. Sending is instant and visibly distinct from the LLM
  // thinking that follows (the placeholder turn), per the Claude-app pattern.
  turns.value.push({
    query: trimmed,
    answerHtml: '',
    citations: [],
    usedTools: [],
    costUsd: 0,
    pending: true,
  })
  const pendingIndex = turns.value.length - 1
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
    // Fill the placeholder turn in place (its position in the transcript is
    // already correct) with the rendered answer.
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
    // sidebar). A failure here — e.g. a malformed response missing thread_id,
    // or a Vue Router navigation rejection — must NEVER be surfaced as an error
    // on a valid answer, so it lives outside onSubmit's answer-error catch.
    void syncThread(res.thread_id, wasNewThread)
  } catch (error: unknown) {
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

/** Cmd/Ctrl+Enter sends from the textarea, matching the Claude-app composer. */
function onComposerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    void onSubmit()
  }
}

/**
 * Post-success side effects after a turn has rendered: record the thread id,
 * sync the URL to /ask/:threadId, and refresh the sidebar for a new thread.
 *
 * This is deliberately fire-and-forget and self-contained: it must never throw
 * back into onSubmit, because by the time it runs the answer is already on
 * screen. A missing/non-numeric thread_id (malformed response) is skipped, and
 * a router rejection (e.g. a redundant navigation) is logged rather than shown
 * as a spurious "Something went wrong" error on a successful ask.
 */
async function syncThread(newThreadId: unknown, wasNewThread: boolean): Promise<void> {
  if (typeof newThreadId !== 'number' || !Number.isFinite(newThreadId)) {
    console.warn('Ask response omitted a valid thread_id; skipping URL sync', newThreadId)
    return
  }
  threadId.value = newThreadId
  // If we started on /ask (no threadId param), update the URL so the
  // browser history and sidebar can track this conversation.
  if (!route.params.threadId) {
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
    // Viewing a thread → the composer (for follow-ups) is relevant, so reveal
    // it on mobile. Don't steal focus: the user is reading the answer first.
    composerOpen.value = true
    scrollToBottom()
  } catch (error: unknown) {
    errorMessage.value = friendlyError(error)
  }
}

/** Clear to a fresh conversation and open the composer ready to type. Wired to
 * the sidebar "New conversation" button. */
function resetConversation(): void {
  turns.value = []
  threadId.value = null
  question.value = ''
  errorMessage.value = null
  router.push({ name: 'ask' })
  openComposer()
}

// Resume a thread when the route param is present (including sidebar navigation
// that changes the param without remounting the component).
watch(
  () => route.params.threadId,
  (id) => {
    if (id && Number(id) !== threadId.value) {
      loadThread(Number(id))
    }
  },
)

onMounted(() => {
  const id = route.params.threadId
  if (id) {
    loadThread(Number(id))
  }
})

// Expose resetConversation so the sidebar (Task 7) can call it via template ref.
defineExpose({ resetConversation })
</script>

<template>
  <!-- The whole Ask view is a flex column that fills the shell's content box:
       100dvh − the h-16 shell header − the app-page py-8 = 100dvh − 8rem. The
       title block and error summary take their natural height and the chat panel
       (#ask-page) flexes to fill the rest, so we no longer guess the header's
       height with a brittle calc() magic number. Below lg the column is a plain
       block and the page scrolls normally. -->
  <div class="lg:flex lg:flex-col lg:h-[calc(100dvh-8rem)]">
    <!-- Standard layout: the page title + description sit at the top, full
         width, ABOVE the chat panel (a sibling, never inside it). -->
    <PageHeader
      title="Ask"
      description="Ask a question about your documents in plain language and get an answer with citations."
    />

    <AppErrorSummary
      v-if="errors.length"
      :errors="errors"
      data-testid="error-summary"
      class="mb-6"
    />

    <!-- One cohesive chat panel: the conversation rail, the scrolling message
         thread and the docked composer all share a single Mosaic surface with
         internal dividers — not three separate floating cards. On lg+ it is a
         two-pane row (rail | thread) that fills the column's height; below lg it
         stacks (rail above, then thread, composer last) and the page scrolls. -->
    <div
      id="ask-page"
      class="flex flex-col lg:flex-row overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-800 shadow-xs lg:flex-1 lg:min-h-0"
    >
      <ConversationSidebar
        ref="sidebarRef"
        :active-thread-id="threadId"
        @select="(id: number) => router.push({ name: 'ask-thread', params: { threadId: id } })"
        @new="resetConversation"
        @threads-changed="(count: number) => (sidebarThreadCount = count)"
      />

      <!-- Thread + composer column. On lg+ it is a full-height flex column (the
           thread scrolls internally, the composer stays docked); below lg it is
           a normal block. -->
      <div class="flex flex-col flex-1 min-w-0 lg:min-h-0">
        <!-- Message thread: the user's question as a violet chat bubble, the
             answer as wide rich markdown beneath it. On lg+ it scrolls
             internally (so the docked composer stays put); below lg it flows
             and the page scrolls. -->
        <div
          ref="transcriptRef"
          data-testid="ask-transcript"
          class="min-h-[18rem] lg:min-h-0 lg:flex-1 lg:overflow-y-auto no-scrollbar p-5 sm:p-6"
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

              <!-- While the answer generates, the turn shows a thinking
                   indicator in the answer slot (instead of the answer body). -->
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
                <!-- Assistant answer — full-width sanitized markdown. -->
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
                        class="flex items-center justify-between gap-3 px-4 py-3 border border-gray-200 dark:border-gray-700/60 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/40 transition"
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
            </section>
          </div>

          <!-- Empty states — centred in the thread area so the panel never looks
               broken before the first question. -->
          <div v-else class="h-full flex items-center justify-center text-center">
            <p
              v-if="sidebarThreadCount > 0 && threadId === null"
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

        <!-- Docked composer. A shrink-0 flex sibling below the internally
             scrolling thread, divided from it by a top border (NOT
             position:sticky, which would float over the thread and intercept
             citation clicks). Below lg it is hidden until opened (via "New
             conversation" or opening a thread) so it isn't a far-off box the
             user must scroll to find; at lg+ it is always docked. -->
        <form
          id="ask-form"
          ref="composerRef"
          novalidate
          class="shrink-0 border-t border-gray-200 dark:border-gray-700/60 p-4"
          :class="{ 'max-lg:hidden': !composerOpen }"
          data-testid="ask-form"
          @submit.prevent="onSubmit"
        >
          <AppTextarea
            id="ask-question"
            v-model="question"
            :label="turns.length ? 'Follow-up question' : 'Your question'"
            hint="For example: which invoices are due this month? (⌘/Ctrl + Enter to send)"
            :rows="3"
            data-testid="ask-question"
            @keydown="onComposerKeydown"
          />

          <!-- Pending image attachments (W11): preview thumbnails + remove. -->
          <ul
            v-if="pendingImages.length"
            data-testid="ask-image-previews"
            class="mt-3 flex flex-wrap gap-2"
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

          <div class="mt-3 flex items-center justify-between gap-2">
            <input
              ref="imageInput"
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp"
              multiple
              class="hidden"
              data-testid="ask-image-input"
              @change="onImagesPicked"
            />
            <button
              type="button"
              data-testid="ask-image-attach"
              class="btn-sm bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50"
              :disabled="pendingImages.length >= MAX_IMAGES"
              @click="imageInput?.click()"
            >
              Attach image
            </button>
            <!-- While answering, the primary action is a live Stop control, not
                 a greyed-out button — the request is cancellable, and the UI is
                 never a dead rectangle. Same data-testid on both states. -->
            <AppButton
              v-if="isAnswering"
              id="ask-submit"
              type="button"
              variant="warning"
              data-testid="ask-submit"
              @click="stopAnswering"
            >
              Stop
            </AppButton>
            <AppButton
              v-else
              id="ask-submit"
              type="submit"
              data-testid="ask-submit"
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
