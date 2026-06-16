<script setup lang="ts">
/**
 * Ask page (route `/ask`).
 *
 * The user types a natural-language question about their archive and gets a
 * cited answer from POST /api/ask. While the request is in flight the submit
 * button is disabled and shows a progress label. Failures — most notably the
 * 503 "no API key configured" case — land in a GOV.UK error summary with a
 * friendly message. The answer renders as plain text above a list of
 * citation cards, each linking to the cited document's detail page.
 */
import { computed, ref } from 'vue'
import { AppButton, AppErrorSummary, AppTextarea } from '@/components/app'
import type { ErrorSummaryItem } from '@/components/app'
import { askQuestion, type AskResponse } from '@/api/ask'
import { ApiError } from '@/api/client'

const question = ref('')
const loading = ref(false)
const result = ref<AskResponse | null>(null)
const errorMessage = ref<string | null>(null)

const errors = computed<ErrorSummaryItem[]>(() =>
  errorMessage.value ? [{ text: errorMessage.value }] : [],
)

const costLabel = computed(() => {
  const cost = result.value?.cost_usd
  if (cost === undefined) return null
  return `$${cost.toFixed(4)}`
})

async function onSubmit(): Promise<void> {
  const trimmed = question.value.trim()
  if (!trimmed) {
    errorMessage.value = 'Enter a question to ask'
    return
  }
  loading.value = true
  errorMessage.value = null
  result.value = null
  try {
    result.value = await askQuestion(trimmed)
  } catch (error: unknown) {
    errorMessage.value = friendlyError(error)
  } finally {
    loading.value = false
  }
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
</script>

<template>
  <div id="ask-page" class="max-w-2xl">
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

    <form
      id="ask-form"
      novalidate
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5 mb-6"
      @submit.prevent="onSubmit"
    >
      <AppTextarea
        id="ask-question"
        v-model="question"
        label="Your question"
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

    <section
      v-if="result"
      id="ask-result"
      class="bg-white dark:bg-gray-800 shadow-xs rounded-xl border border-gray-200 dark:border-gray-700/60 p-5"
      data-testid="ask-result"
    >
      <h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-2">Answer</h2>
      <p
        id="ask-answer"
        class="text-gray-800 dark:text-gray-100 whitespace-pre-wrap"
        data-testid="ask-answer"
      >
        {{ result.answer }}
      </p>

      <div v-if="result.citations.length" class="mt-5">
        <h3 class="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-2">Citations</h3>
        <ul
          id="ask-citations"
          class="divide-y divide-gray-200 dark:divide-gray-700/60 border border-gray-200 dark:border-gray-700/60 rounded-lg"
          data-testid="ask-citations"
        >
          <li v-for="citation in result.citations" :key="citation.document_id">
            <RouterLink
              class="flex items-center justify-between gap-3 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700/40 transition"
              :to="{ name: 'document-detail', params: { id: citation.document_id } }"
              data-testid="ask-citation"
            >
              <span class="min-w-0 truncate text-sm text-violet-600 dark:text-violet-400 underline">
                {{ citation.title ?? 'Untitled' }}
              </span>
              <span class="shrink-0 text-xs text-gray-500 dark:text-gray-400"
                >#{{ citation.document_id }}</span
              >
            </RouterLink>
          </li>
        </ul>
      </div>

      <p
        v-if="result.used_tools.length || costLabel"
        class="mt-4 text-xs text-gray-500 dark:text-gray-400"
        data-testid="ask-meta"
      >
        <span v-if="result.used_tools.length">Tools: {{ result.used_tools.join(', ') }}</span>
        <span v-if="result.used_tools.length && costLabel"> · </span>
        <span v-if="costLabel">Estimated cost: {{ costLabel }}</span>
      </p>
    </section>
  </div>
</template>
