<script setup lang="ts">
import { computed, ref } from 'vue'
import AppErrorMessage from './AppErrorMessage.vue'

// Replaces GovFileUpload. Pure Vue drop-zone (NO govuk-frontend module): a
// dashed label wrapping a hidden file input, with drag-and-drop. v-model
// carries the selected files; null when nothing is selected.
const props = defineProps<{
  id: string
  label: string
  name?: string
  hint?: string
  errorMessage?: string
  multiple?: boolean
  accept?: string
}>()

const model = defineModel<File[] | null>({ default: null })

const dragging = ref(false)

const describedBy = computed(() => {
  const ids: string[] = []
  if (props.hint) ids.push(`${props.id}-hint`)
  if (props.errorMessage) ids.push(`${props.id}-error`)
  return ids.length ? ids.join(' ') : undefined
})

// Identity for dedup: the same file picked twice (same name/size/mtime)
// must not be added twice when selections accumulate.
function fileKey(file: File): string {
  return `${file.name} ${file.size} ${file.lastModified}`
}

// Merge new picks into the current selection. In multiple mode selections
// accumulate (de-duplicated) so re-opening the picker adds rather than
// clobbers; in single mode a new pick replaces. An empty pick (the user
// cancelled) leaves the selection untouched.
function setFiles(list: FileList | File[] | null): void {
  const incoming = list && list.length > 0 ? Array.from(list) : null
  if (!incoming) return
  if (!props.multiple) {
    model.value = [incoming[0]!]
    return
  }
  const merged = model.value ? [...model.value] : []
  const seen = new Set(merged.map(fileKey))
  for (const file of incoming) {
    const key = fileKey(file)
    if (!seen.has(key)) {
      seen.add(key)
      merged.push(file)
    }
  }
  model.value = merged
}

function removeFile(index: number): void {
  const files = model.value ? model.value.slice() : []
  files.splice(index, 1)
  model.value = files.length > 0 ? files : null
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function onChange(event: Event): void {
  const input = event.target as HTMLInputElement
  setFiles(input.files)
  // Reset so re-picking a just-removed file fires `change` again (the
  // browser suppresses the event when the input value is unchanged).
  input.value = ''
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  setFiles(event.dataTransfer?.files ?? null)
}
</script>

<template>
  <div>
    <p class="block text-sm font-medium mb-1 text-gray-700 dark:text-gray-300">{{ props.label }}</p>
    <p
      v-if="props.hint"
      :id="`${props.id}-hint`"
      class="text-sm text-gray-500 dark:text-gray-400 mb-1"
    >{{ props.hint }}</p>
    <AppErrorMessage v-if="props.errorMessage" :id="`${props.id}-error`">
      {{ props.errorMessage }}
    </AppErrorMessage>
    <label
      class="flex flex-col items-center justify-center border-2 border-dashed border-gray-300 dark:border-gray-700/60 rounded-xl p-8 cursor-pointer hover:border-violet-400 transition"
      :class="[
        { 'ring-2 ring-violet-400': dragging },
        { 'border-red-300': props.errorMessage },
      ]"
      :for="props.id"
      @dragover.prevent="dragging = true"
      @dragenter.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <input
        class="sr-only"
        :id="props.id"
        :name="props.name ?? props.id"
        type="file"
        :multiple="props.multiple"
        :accept="props.accept"
        :aria-describedby="describedBy"
        @change="onChange"
      />
      <svg
        class="w-8 h-8 mb-2 text-gray-400"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        stroke-width="1.5"
        stroke="currentColor"
        aria-hidden="true"
      >
        <path
          stroke-linecap="round"
          stroke-linejoin="round"
          d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5"
        />
      </svg>
      <span class="text-sm text-gray-500 dark:text-gray-400">Drop files or click to browse</span>
    </label>

    <div v-if="model && model.length" data-testid="selected-files" class="mt-3">
      <p class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
        {{ model.length === 1 ? '1 file selected' : `${model.length} files selected` }}
      </p>
      <ul class="rounded-lg border border-gray-200 dark:border-gray-700/60 divide-y divide-gray-200 dark:divide-gray-700/60">
        <li
          v-for="(file, index) in model"
          :key="`${file.name} ${file.size} ${file.lastModified}`"
          data-testid="selected-file"
          class="flex items-center gap-3 px-3 py-2"
        >
          <span class="flex-1 min-w-0 truncate text-sm text-gray-800 dark:text-gray-100">{{
            file.name
          }}</span>
          <span class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap">{{
            formatSize(file.size)
          }}</span>
          <button
            type="button"
            class="text-gray-400 hover:text-red-500 transition shrink-0"
            :aria-label="`Remove ${file.name}`"
            @click="removeFile(index)"
          >
            <svg class="w-4 h-4" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
              <path
                d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z"
              />
            </svg>
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>
