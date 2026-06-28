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
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { AppButton, AppErrorSummary, AppTextarea, PageHeader } from '@/components/app'
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
const loading = ref(false)
const errorMessage = ref<string | null>(null)
const turns = ref<TurnVM[]>([])
const threadId = ref<number | null>(null)
const sidebarRef = ref<InstanceType<typeof ConversationSidebar> | null>(null)
const pendingImages = ref<PendingImage[]>([])
const imageInput = ref<HTMLInputElement | null>(null)

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

async function onSubmit(): Promise<void> {
  const trimmed = question.value.trim()
  if (!trimmed) {
    errorMessage.value = 'Enter a question to ask'
    return
  }
  loading.value = true
  errorMessage.value = null
  const controller = new AbortController()
  const wasNewThread = threadId.value === null
  const images: AskImage[] = pendingImages.value.map((image) => ({
    media_type: image.media_type,
    data: image.data,
  }))
  try {
    const res = images.length
      ? await askQuestion(trimmed, threadId.value ?? undefined, controller.signal, images)
      : await askQuestion(trimmed, threadId.value ?? undefined, controller.signal)
    turns.value.push({
      query: trimmed,
      answerHtml: renderAnswer(res.answer),
      citations: res.citations,
      usedTools: res.used_tools,
      costUsd: res.cost_usd,
    })
    threadId.value = res.thread_id
    question.value = ''
    pendingImages.value = []
    // If we started on /ask (no threadId param), update the URL so the
    // browser history and sidebar can track this conversation.
    if (!route.params.threadId) {
      await router.replace({ name: 'ask-thread', params: { threadId: res.thread_id } })
    }
    // Refresh the sidebar when a new thread was just created.
    if (wasNewThread) {
      sidebarRef.value?.refresh()
    }
  } catch (error: unknown) {
    errorMessage.value = friendlyError(error)
  } finally {
    loading.value = false
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
  } catch (error: unknown) {
    errorMessage.value = friendlyError(error)
  }
}

/** Clear state and navigate to /ask — wired to the sidebar "New conversation" in Task 7. */
function resetConversation(): void {
  turns.value = []
  threadId.value = null
  question.value = ''
  errorMessage.value = null
  router.push({ name: 'ask' })
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
  <div id="ask-page" class="flex gap-6 items-start">
    <ConversationSidebar
      ref="sidebarRef"
      :active-thread-id="threadId"
      @select="(id: number) => router.push({ name: 'ask-thread', params: { threadId: id } })"
      @new="resetConversation"
    />

    <!-- Answer column fills the width; the composer sticks to the bottom while
         scrolling. The column takes its natural height (no forced min-height) so
         the sticky composer never floats over short content and intercept its
         clicks (e.g. a citation link) on small viewports. -->
    <div class="flex-1 min-w-0">
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

      <!-- Transcript: wide rich-markdown answers, not chat bubbles. -->
      <div>
        <div v-if="turns.length" class="space-y-6 mb-6">
          <section
            v-for="(turn, i) in turns"
            :key="i"
            data-testid="ask-turn"
            class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-6"
          >
            <h2 class="text-base font-semibold text-gray-800 dark:text-gray-100 mb-4">
              {{ turn.query }}
            </h2>

            <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in renderAnswer -->
            <div
              class="ask-answer text-gray-800 dark:text-gray-100"
              data-testid="ask-answer"
              v-html="turn.answerHtml"
            />

            <div v-if="turn.citations.length" class="mt-5">
              <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">
                Citations
              </h3>
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
            </div>

            <p
              v-if="turn.usedTools.length || turn.costUsd"
              class="mt-4 text-xs text-gray-500 dark:text-gray-400"
              data-testid="ask-meta"
            >
              <span v-if="turn.usedTools.length">Tools: {{ turn.usedTools.join(', ') }}</span>
              <span v-if="turn.usedTools.length && turn.costUsd"> · </span>
              <span v-if="turn.costUsd">Estimated cost: ${{ turn.costUsd.toFixed(4) }}</span>
            </p>
          </section>
        </div>

        <p
          v-else
          data-testid="ask-empty"
          class="text-gray-500 dark:text-gray-400 mb-6"
        >
          No questions yet. Ask one below — for example, “which invoices are due this month?”
        </p>
      </div>

      <!-- Sticky multi-line composer pinned to the bottom of the column. -->
      <form
        id="ask-form"
        novalidate
        class="sticky bottom-0 z-10 mt-2 bg-gray-50 dark:bg-gray-900 pt-2 pb-3"
        data-testid="ask-form"
        @submit.prevent="onSubmit"
      >
        <div
          class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-4"
        >
          <AppTextarea
            id="ask-question"
            v-model="question"
            :label="turns.length ? 'Follow-up question' : 'Your question'"
            hint="For example: which invoices are due this month?"
            :rows="3"
            data-testid="ask-question"
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
            <AppButton
              id="ask-submit"
              type="submit"
              data-testid="ask-submit"
              :disabled="loading"
            >
              {{ loading ? 'Sending…' : 'Send' }}
            </AppButton>
          </div>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
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
