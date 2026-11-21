<script setup lang="ts">
/**
 * Colour override picker for a chart split value (facet value or sender).
 *
 * Deliberately closed to the five-slot `SPLIT_PALETTE`: no `<input type="color">`
 * and no hex text field. A free field would let an owner pick something
 * invisible in dark mode or indistinguishable from its neighbour, and nothing
 * in the system could then prevent it — see splitPalette.ts's header for the
 * validated palette and its relief rule.
 *
 * The relief rule itself is why every swatch carries `aria-label="${slot.name}"`
 * and the default button names the slot the key derives to: a swatch must
 * never be the sole carrier of identity, accessibility included.
 *
 * A swatch paints the slot's role (`slot.colour`, a `var(--category-N)`
 * reference), so it follows the theme; what it emits is the slot's stored
 * identity hex (`slot.light`), never the painted colour.
 *
 * `modelValue` is the stored hex (nullable — null means "use the derived
 * slot", matching splitPalette.ts's `resolveSplitColour`). Selection is
 * decided by `splitPalette.ts`'s `slotForStored` — the one definition of
 * "is this stored hex this slot", shared with `resolveSplitColour` so the
 * swatch a row paints and the button this picker marks pressed can never
 * disagree about which slot a stored colour is.
 *
 * Ships standalone and unwired to any chart — see charts-view design §4.7 for
 * where 4b/5 will mount it against the legend swatch. `components/charts/`
 * is out of scope here.
 */
import { computed } from 'vue'
import { SPLIT_PALETTE, deriveSlot, slotForStored } from '@/utils/splitPalette'

const props = defineProps<{
  modelValue: string | null
  slotKey: string
  testid: string
}>()

const emit = defineEmits<{ 'update:modelValue': [string | null] }>()

const normalized = computed(() => props.modelValue?.toLowerCase() ?? null)

const defaultSlotName = computed(() => deriveSlot(props.slotKey).name)

const selectedSlot = computed(() => slotForStored(props.modelValue))

function isSelected(slot: (typeof SPLIT_PALETTE)[number]): boolean {
  return selectedSlot.value === slot
}

function choose(slot: (typeof SPLIT_PALETTE)[number]): void {
  emit('update:modelValue', slot.light)
}

function chooseDefault(): void {
  emit('update:modelValue', null)
}
</script>

<template>
  <div role="group" class="flex flex-wrap items-center gap-2" :data-testid="props.testid">
    <button
      v-for="(slot, index) in SPLIT_PALETTE"
      :key="slot.name"
      type="button"
      class="w-6 h-6 rounded border border-control-border"
      :class="{ 'ring-2 ring-brand ring-offset-1 ring-offset-panel': isSelected(slot) }"
      :style="{ backgroundColor: slot.colour }"
      :aria-pressed="isSelected(slot)"
      :aria-label="slot.name"
      :data-testid="`${props.testid}-swatch-${index}`"
      @click="choose(slot)"
    />
    <button
      type="button"
      class="btn-xs border border-control-border text-ink hover:bg-tile"
      :class="{ 'ring-2 ring-brand ring-offset-1 ring-offset-panel': normalized === null }"
      :aria-pressed="normalized === null"
      :data-testid="`${props.testid}-default`"
      @click="chooseDefault"
    >
      Default ({{ defaultSlotName }})
    </button>
  </div>
</template>
