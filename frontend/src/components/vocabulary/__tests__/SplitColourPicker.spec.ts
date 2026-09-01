import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import SplitColourPicker from '../SplitColourPicker.vue'
import { SPLIT_PALETTE, deriveSlot } from '@/utils/splitPalette'

const factory = (modelValue: string | null = null) =>
  mount(SplitColourPicker, { props: { modelValue, slotKey: 'alpha', testid: 'v-alpha' } })

describe('SplitColourPicker', () => {
  it('offers every palette slot plus a default choice', () => {
    const wrapper = factory()
    expect(wrapper.findAll('[data-testid^="v-alpha-swatch-"]')).toHaveLength(SPLIT_PALETTE.length)
    expect(wrapper.find('[data-testid="v-alpha-default"]').exists()).toBe(true)
  })

  it('offers no free-text colour input', () => {
    // Restricted to the palette by design: a free field lets the owner pick
    // something invisible in dark mode and nothing could prevent it.
    const wrapper = factory()
    expect(wrapper.find('input[type="color"]').exists()).toBe(false)
    expect(wrapper.find('input[type="text"]').exists()).toBe(false)
  })

  it('emits the slot light hex when a swatch is chosen', async () => {
    const wrapper = factory()
    await wrapper.find('[data-testid="v-alpha-swatch-2"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([SPLIT_PALETTE[2]!.light])
  })

  it('emits null when the default is chosen, which is how a colour is cleared', async () => {
    const wrapper = factory(SPLIT_PALETTE[1]!.light)
    await wrapper.find('[data-testid="v-alpha-default"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual([null])
  })

  it('marks the stored colour as selected', () => {
    const wrapper = factory(SPLIT_PALETTE[4]!.light)
    expect(wrapper.find('[data-testid="v-alpha-swatch-4"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.find('[data-testid="v-alpha-default"]').attributes('aria-pressed')).toBe('false')
  })

  it('marks the default as selected when nothing is stored', () => {
    const wrapper = factory(null)
    expect(wrapper.find('[data-testid="v-alpha-default"]').attributes('aria-pressed')).toBe('true')
  })

  it('names the slot the key would derive, so the owner sees what default means', () => {
    const wrapper = factory(null)
    expect(wrapper.find('[data-testid="v-alpha-default"]').text()).toContain(
      deriveSlot('alpha').name,
    )
  })

  it('labels every swatch by name, never by colour alone', () => {
    // The relief rule: three slots fall below 3:1 against the chart surface, so
    // a swatch must never be the only carrier of identity.
    const wrapper = factory()
    for (const [index, slot] of SPLIT_PALETTE.entries()) {
      expect(
        wrapper.find(`[data-testid="v-alpha-swatch-${index}"]`).attributes('aria-label'),
      ).toContain(slot.name)
    }
  })
})
