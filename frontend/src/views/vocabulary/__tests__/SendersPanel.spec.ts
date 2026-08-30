import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SendersPanel from '../SendersPanel.vue'
import { SPLIT_PALETTE } from '@/utils/splitPalette'

vi.mock('@/api/taxonomy', () => ({ listSenders: vi.fn(), setSenderColour: vi.fn() }))
import * as api from '@/api/taxonomy'

beforeEach(() => {
  vi.mocked(api.listSenders).mockResolvedValue([
    { id: 1, name: 'Aardvark Testing Ltd', document_count: 3, colour: null },
    { id: 2, name: 'Zebra Fixture Co', document_count: 11, colour: SPLIT_PALETTE[0]!.light },
  ])
})

const open = async () => {
  const wrapper = mount(SendersPanel, {
    props: { active: true },
    global: { stubs: { SplitColourPicker: true } },
  })
  await flushPromises()
  return wrapper
}

describe('SendersPanel', () => {
  it('does not load until its tab is opened', async () => {
    mount(SendersPanel, { props: { active: false }, global: { stubs: { SplitColourPicker: true } } })
    await flushPromises()
    expect(api.listSenders).not.toHaveBeenCalled()
  })

  it('lists the busiest senders first, since those are the ones charts split by', async () => {
    const wrapper = await open()
    const names = wrapper.findAll('[data-testid^="sender-row-"]').map((r) => r.text())
    expect(names[0]!).toContain('Zebra Fixture Co')
  })

  it('says there are no senders at all on a fresh archive, not that a filter matched nothing', async () => {
    // The filter is empty in this scenario — naming it would be false. Zero
    // senders and zero matching senders are different facts and need
    // different copy.
    vi.mocked(api.listSenders).mockResolvedValue([])
    const wrapper = await open()
    expect(wrapper.find('[data-testid="senders-empty"]').text()).toContain('No senders yet')
    expect(wrapper.find('[data-testid="senders-filter-empty"]').exists()).toBe(false)
  })

  it('says no senders match the filter when senders exist but none match', async () => {
    const wrapper = await open()
    await wrapper.find('[data-testid="sender-filter"]').setValue('no such sender anywhere')
    await flushPromises()
    expect(wrapper.find('[data-testid="senders-filter-empty"]').text()).toContain(
      'No senders match that filter.',
    )
    expect(wrapper.find('[data-testid="senders-empty"]').exists()).toBe(false)
  })

  it('filters by name', async () => {
    const wrapper = await open()
    await wrapper.find('[data-testid="sender-filter"]').setValue('aardvark')
    await flushPromises()
    const rows = wrapper.findAll('[data-testid^="sender-row-"]')
    expect(rows).toHaveLength(1)
    expect(rows[0]!.text()).toContain('Aardvark')
  })

  it('sets a colour', async () => {
    vi.mocked(api.setSenderColour).mockResolvedValue({
      id: 1, name: 'Aardvark Testing Ltd', document_count: 3, colour: SPLIT_PALETTE[2]!.light,
    })
    const wrapper = await open()
    wrapper.findAllComponents({ name: 'SplitColourPicker' })[1]!
      .vm.$emit('update:modelValue', SPLIT_PALETTE[2]!.light)
    await flushPromises()
    expect(api.setSenderColour).toHaveBeenCalledWith(1, SPLIT_PALETTE[2]!.light)
  })

  it('clears a colour', async () => {
    vi.mocked(api.setSenderColour).mockResolvedValue({
      id: 2, name: 'Zebra Fixture Co', document_count: 11, colour: null,
    })
    const wrapper = await open()
    wrapper.findAllComponents({ name: 'SplitColourPicker' })[0]!
      .vm.$emit('update:modelValue', null)
    await flushPromises()
    expect(api.setSenderColour).toHaveBeenCalledWith(2, null)
  })

  it('offers no rename or delete — those are admin taxonomy operations', async () => {
    const wrapper = await open()
    expect(wrapper.find('[data-testid="sender-row-1-rename-btn"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="sender-row-1-delete-btn"]').exists()).toBe(false)
  })
})
