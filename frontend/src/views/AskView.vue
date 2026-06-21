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
import { AppButton, AppErrorSummary, AppTextarea } from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import { askQuestion, getThread, type AskCitation } from '@/api/ask'
import { ApiError } from '@/api/client'

interface TurnVM {
  query: string
  answerHtml: string
  citations: AskCitation[]
  usedTools: string[]
  costUsd: number
}

const route = useRoute()
const router = useRouter()

const question = ref('')
const loading = ref(false)
const errorMessage = ref<string | null>(null)
const turns = ref<TurnVM[]>([])
const threadId = ref<number | null>(null)

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
  try {
    const res = await askQuestion(trimmed, threadId.value ?? undefined, controller.signal)
    turns.value.push({
      query: trimmed,
      answerHtml: renderAnswer(res.answer),
      citations: res.citations,
      usedTools: res.used_tools,
      costUsd: res.cost_usd,
    })
    threadId.value = res.thread_id
    question.value = ''
    // If we started on /ask (no threadId param), update the URL so the
    // browser history and sidebar can track this conversation.
    if (!route.params.threadId) {
      await router.replace({ name: 'ask-thread', params: { threadId: res.thread_id } })
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
    if (id) {
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
  <div id="ask-page" class="max-w-3xl mx-auto">
    <h1 class="text-2xl md:text-3xl text-gray-800 dark:text-gray-100 font-bold mb-2">Ask</h1>
    <p class="text-gray-500 dark:text-gray-400 mb-6">
      Ask a question about your documents in plain language and get an answer with citations.
    </p>

    <AppErrorSummary
      v-if="errors.length"
      :errors="errors"
      data-testid="error-summary"
      class="mb-6"
    />

    <!-- Transcript of Q&A turns -->
    <div v-if="turns.length" class="space-y-6 mb-6">
      <section
        v-for="(turn, i) in turns"
        :key="i"
        data-testid="ask-turn"
        class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
      >
        <p class="text-sm font-medium text-gray-500 dark:text-gray-400 mb-3">
          {{ turn.query }}
        </p>

        <!-- eslint-disable-next-line vue/no-v-html -- sanitized via DOMPurify in renderAnswer -->
        <div
          class="ask-answer text-gray-800 dark:text-gray-100"
          data-testid="ask-answer"
          v-html="turn.answerHtml"
        />

        <div v-if="turn.citations.length" class="mt-5">
          <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Citations</h3>
          <ul
            class="divide-y divide-gray-200 dark:divide-gray-700/60 border border-gray-200 dark:border-gray-700/60 rounded-lg"
            data-testid="ask-citations"
          >
            <li v-for="citation in turn.citations" :key="citation.document_id">
              <RouterLink
                class="flex items-center justify-between gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/40 transition"
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

    <!-- Follow-up input form -->
    <form
      id="ask-form"
      novalidate
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
      data-testid="ask-form"
      @submit.prevent="onSubmit"
    >
      <AppTextarea
        id="ask-question"
        v-model="question"
        :label="turns.length ? 'Follow-up question' : 'Your question'"
        hint="For example: which invoices are due this month?"
        :rows="4"
        data-testid="ask-question"
      />
      <AppButton
        id="ask-submit"
        type="submit"
        class="mt-4"
        data-testid="ask-submit"
        :disabled="loading"
      >
        {{ loading ? 'Asking…' : 'Ask' }}
      </AppButton>
    </form>
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
</style>
