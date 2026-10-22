<script setup lang="ts">
/**
 * Ask composer — one rounded box, the way a chat app draws it: attached images
 * above the text, an auto-growing borderless textarea, and a control row with
 * attach (＋) on the left and send (↑) or stop (■) on the right. Under the box
 * a quiet line says which model and backend will answer.
 *
 * The box is the only surface. It sits on the page background, centred on the
 * same column as the transcript, so the text you type lines up with the
 * answers above it.
 *
 * Keys (unchanged from the previous composer): at lg+ plain Enter sends,
 * Shift+Enter and Ctrl+J insert a newline; below lg plain Enter inserts a
 * newline like every phone chat app and sending is the button's job;
 * Cmd/Ctrl+Enter always sends; Enter mid-IME-composition never sends.
 */
import { nextTick, ref, watch } from 'vue'
import type { AskImage } from '@/api/ask'

export interface PendingImage extends AskImage {
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

const props = defineProps<{
  isAnswering: boolean
  isLargeScreen: boolean
  placeholder: string
  /** Accessible name of the question field. */
  fieldLabel: string
  /** "Opus 4.8 · Subscription", or null until the backends have loaded. */
  modelChip: string | null
}>()
const emit = defineEmits<{ submit: []; stop: [] }>()

const question = defineModel<string>({ required: true })
const images = defineModel<PendingImage[]>('images', { required: true })

const questionEl = ref<HTMLTextAreaElement | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)

/** Grow the textarea to fit its content, up to a cap. */
function autoGrow(): void {
  const el = questionEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
}
watch(question, () => void nextTick(autoGrow))

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
  const next = [...images.value]
  for (const file of Array.from(input.files ?? [])) {
    if (next.length >= MAX_IMAGES) break
    const mediaType = file.type as AskImage['media_type']
    if (!SUPPORTED_IMAGE_TYPES.includes(mediaType)) continue
    const dataUrl = await readAsDataUrl(file)
    next.push({
      media_type: mediaType,
      data: dataUrl.slice(dataUrl.indexOf(',') + 1),
      url: dataUrl,
      name: file.name,
    })
  }
  images.value = next
  // Reset so the same file can be re-picked after removal.
  input.value = ''
}

function removeImage(index: number): void {
  images.value = images.value.filter((_, i) => i !== index)
}

function onKeydown(event: KeyboardEvent): void {
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
    if (event.metaKey || event.ctrlKey) {
      event.preventDefault()
      emit('submit')
      return
    }
    if (!props.isLargeScreen) return
    event.preventDefault()
    emit('submit')
  }
}

function focus(): void {
  void nextTick(() => questionEl.value?.focus())
}

defineExpose({ focus })
</script>

<template>
  <div>
    <form
      id="ask-form"
      novalidate
      data-testid="ask-form"
      class="rounded-2xl border border-gray-200 bg-white px-3 pt-3 pb-2 shadow-xs transition-colors focus-within:border-violet-400 dark:border-gray-700/60 dark:bg-gray-800 dark:focus-within:border-violet-500"
      @submit.prevent="emit('submit')"
    >
      <ul v-if="images.length" data-testid="ask-image-previews" class="mb-2 flex flex-wrap gap-2 px-1">
        <li v-for="(image, i) in images" :key="i" class="relative">
          <img
            :src="image.url"
            :alt="image.name"
            data-testid="ask-image-preview"
            class="h-16 w-16 rounded-lg border border-gray-200 object-cover dark:border-gray-700"
          />
          <button
            type="button"
            data-testid="ask-image-remove"
            :aria-label="`Remove ${image.name}`"
            class="absolute -top-2 -right-2 flex h-5 w-5 items-center justify-center rounded-full bg-gray-700 text-xs text-white hover:bg-red-600"
            @click="removeImage(i)"
          >
            ×
          </button>
        </li>
      </ul>

      <textarea
        id="ask-question"
        ref="questionEl"
        v-model="question"
        rows="1"
        data-testid="ask-question"
        :aria-label="fieldLabel"
        :placeholder="placeholder"
        class="block w-full resize-none border-0 bg-transparent px-1 py-1 text-base leading-6 text-gray-800 placeholder-gray-400 focus:ring-0 focus:outline-none sm:text-sm dark:text-gray-100 dark:placeholder-gray-500"
        @keydown="onKeydown"
      ></textarea>

      <div class="mt-1 flex items-center justify-between gap-2">
        <input
          ref="imageInput"
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          multiple
          class="hidden"
          aria-label="Attach image"
          data-testid="ask-image-input"
          @change="onImagesPicked"
        />
        <button
          type="button"
          data-testid="ask-image-attach"
          aria-label="Attach image"
          title="Attach image"
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-gray-500 transition hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40 dark:text-gray-400 dark:hover:bg-gray-700/60 dark:hover:text-gray-200"
          :disabled="images.length >= MAX_IMAGES"
          @click="imageInput?.click()"
        >
          <svg class="h-5 w-5 fill-none stroke-current" stroke-width="2" stroke-linecap="round" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>

        <!-- While answering, the primary action is a live Stop: the request is
             cancellable, so it is never a greyed-out Send. -->
        <button
          v-if="isAnswering"
          id="ask-submit"
          type="button"
          data-testid="ask-submit"
          aria-label="Stop answering"
          title="Stop"
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-800 text-white transition hover:bg-gray-700 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
          @click="emit('stop')"
        >
          <svg class="h-3 w-3 fill-current" viewBox="0 0 12 12" aria-hidden="true">
            <rect width="12" height="12" rx="2" />
          </svg>
        </button>
        <button
          v-else
          id="ask-submit"
          type="submit"
          data-testid="ask-submit"
          aria-label="Send"
          title="Send"
          class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-violet-500 text-white transition hover:bg-violet-600 disabled:bg-gray-200 disabled:text-gray-400 dark:disabled:bg-gray-700 dark:disabled:text-gray-500"
          :disabled="!question.trim()"
        >
          <svg class="h-4 w-4 fill-none stroke-current" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
        </button>
      </div>
    </form>

    <p
      v-if="modelChip"
      data-testid="ask-model-chip"
      class="mt-1.5 px-3 text-right text-xs text-gray-400 dark:text-gray-500"
    >
      {{ modelChip }}
    </p>
  </div>
</template>
