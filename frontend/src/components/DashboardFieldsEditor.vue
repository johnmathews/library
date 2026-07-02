<script setup lang="ts">
/**
 * Reusable editor for the per-user dashboard card fields: toggle each field on
 * or off and reorder them. The stored value (`dashboard_fields`) is an ordered
 * list of the ENABLED fields — that order drives the card meta row in
 * DocumentListView. Disabled fields are kept in the list (so they can be
 * re-enabled in place) but are not part of the emitted value.
 *
 * Reordering is available three ways for accessibility + touch: drag (SortableJS
 * via the handle), and per-row Up/Down buttons (keyboard + screen-reader). The
 * component is uncontrolled after mount: it seeds its rows from `modelValue`
 * once and emits changes; parents re-seed by remounting (the popover does) or
 * by leaving it mounted (Settings binds it with v-model).
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import Sortable from 'sortablejs'
import { DASHBOARD_FIELDS, DEFAULT_DASHBOARD_FIELDS, type DashboardField } from '@/api/settings'

const props = defineProps<{ modelValue: DashboardField[] }>()
const emit = defineEmits<{ 'update:modelValue': [DashboardField[]] }>()

interface Row {
  field: DashboardField
  enabled: boolean
}

const LABELS: Record<DashboardField, string> = Object.fromEntries(
  DASHBOARD_FIELDS.map((f) => [f.value, f.text]),
) as Record<DashboardField, string>

const CATALOG: DashboardField[] = DASHBOARD_FIELDS.map((f) => f.value)

/** Enabled fields first (in the given order), then the remaining fields. */
function toRows(enabled: DashboardField[]): Row[] {
  const enabledSet = new Set(enabled)
  const ordered = enabled.filter((f) => CATALOG.includes(f))
  const rest = CATALOG.filter((f) => !enabledSet.has(f))
  return [...ordered.map((field) => ({ field, enabled: true })), ...rest.map((field) => ({ field, enabled: false }))]
}

const rows = ref<Row[]>(toRows(props.modelValue))
const listEl = ref<HTMLUListElement | null>(null)
let sortable: Sortable | null = null

function emitChange(): void {
  emit(
    'update:modelValue',
    rows.value.filter((r) => r.enabled).map((r) => r.field),
  )
}

function toggle(index: number): void {
  const row = rows.value[index]
  if (!row) return
  row.enabled = !row.enabled
  emitChange()
}

function reorder(from: number, to: number): void {
  if (to < 0 || to >= rows.value.length || from === to) return
  const next = [...rows.value]
  const [item] = next.splice(from, 1)
  if (!item) return
  next.splice(to, 0, item)
  rows.value = next
  emitChange()
}

function move(index: number, delta: number): void {
  reorder(index, index + delta)
}

function reset(): void {
  rows.value = toRows(DEFAULT_DASHBOARD_FIELDS)
  emitChange()
}

onMounted(() => {
  if (!listEl.value) return
  sortable = Sortable.create(listEl.value, {
    handle: '[data-drag-handle]',
    animation: 150,
    onEnd: (evt: Sortable.SortableEvent) => {
      if (evt.oldIndex == null || evt.newIndex == null) return
      reorder(evt.oldIndex, evt.newIndex)
    },
  })
})

onBeforeUnmount(() => {
  sortable?.destroy()
  sortable = null
})

defineExpose({ reset })
</script>

<template>
  <div>
    <ul ref="listEl" role="list" class="flex flex-col gap-1" data-testid="dashboard-fields-list">
      <li
        v-for="(row, index) in rows"
        :key="row.field"
        class="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700/40"
        :data-testid="`dashboard-field-row-${row.field}`"
      >
        <button
          type="button"
          data-drag-handle
          class="cursor-grab text-gray-400 hover:text-violet-500 active:cursor-grabbing"
          :aria-label="`Drag to reorder ${LABELS[row.field]}`"
          tabindex="-1"
        >
          ⠿
        </button>
        <label class="flex flex-1 items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
          <input
            type="checkbox"
            class="form-checkbox text-violet-600"
            :value="row.field"
            :checked="row.enabled"
            :data-testid="`dashboard-field-${row.field}`"
            @change="toggle(index)"
          />
          {{ LABELS[row.field] }}
        </label>
        <button
          type="button"
          class="rounded p-1 text-gray-400 hover:text-violet-600 disabled:opacity-30 disabled:hover:text-gray-400"
          :disabled="index === 0"
          :aria-label="`Move ${LABELS[row.field]} up`"
          :data-testid="`dashboard-field-up-${row.field}`"
          @click="move(index, -1)"
        >
          ↑
        </button>
        <button
          type="button"
          class="rounded p-1 text-gray-400 hover:text-violet-600 disabled:opacity-30 disabled:hover:text-gray-400"
          :disabled="index === rows.length - 1"
          :aria-label="`Move ${LABELS[row.field]} down`"
          :data-testid="`dashboard-field-down-${row.field}`"
          @click="move(index, 1)"
        >
          ↓
        </button>
      </li>
    </ul>
    <button
      type="button"
      class="mt-2 text-xs font-medium uppercase tracking-wide text-violet-600 hover:text-violet-700 dark:text-violet-400"
      data-testid="dashboard-fields-reset"
      @click="reset"
    >
      Reset to defaults
    </button>
  </div>
</template>
