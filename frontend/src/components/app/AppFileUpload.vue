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

function setFiles(list: FileList | File[] | null): void {
  let files = list && list.length > 0 ? Array.from(list) : null
  if (files && !props.multiple && files.length > 1) files = files.slice(0, 1)
  model.value = files
}

function onChange(event: Event): void {
  setFiles((event.target as HTMLInputElement).files)
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
  </div>
</template>
