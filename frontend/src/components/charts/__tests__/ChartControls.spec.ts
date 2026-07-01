import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ChartControls from '../ChartControls.vue'
import { TIMEFRAME_OPTIONS } from '@/composables/useChartsTimeframe'
import { GROUPING_OPTIONS } from '@/composables/useChartsGrouping'

function mountControls(props: Record<string, unknown> = {}) {
  return mount(ChartControls, {
    props: {
      timeframe: 'all',
      timeframeOptions: TIMEFRAME_OPTIONS,
      customFrom: null,
      customTo: null,
      grouping: 'none',
      groupingOptions: GROUPING_OPTIONS,
      ...props,
    },
  })
}

describe('ChartControls', () => {
  it('renders the range preset, both datepickers, and the grouping select', () => {
    const wrapper = mountControls()
    expect(wrapper.find('[data-testid="charts-timeframe"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="charts-range-from"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="charts-range-to"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="charts-grouping"]').exists()).toBe(true)
    // The new presets are offered.
    const labels = wrapper.find('[data-testid="charts-timeframe"]').text()
    expect(labels).toContain('Last quarter')
    expect(labels).toContain('Custom range')
  })

  it('emits select-timeframe when a preset is chosen', async () => {
    const wrapper = mountControls()
    await wrapper.find('[data-testid="charts-timeframe"]').setValue('lastq')
    expect(wrapper.emitted('select-timeframe')).toEqual([['lastq']])
  })

  it('emits update:grouping when the grouping changes', async () => {
    const wrapper = mountControls()
    await wrapper.find('[data-testid="charts-grouping"]').setValue('quarter')
    expect(wrapper.emitted('update:grouping')).toEqual([['quarter']])
  })

  it('emits set-custom when a datepicker is edited', async () => {
    const wrapper = mountControls()
    // The native date input carries an ISO yyyy-mm-dd value directly.
    await wrapper.find('[data-testid="charts-range-from"]').setValue('2025-03-15')
    const events = wrapper.emitted('set-custom')
    expect(events).toBeTruthy()
    expect(events!.at(-1)).toEqual(['from', '2025-03-15'])
  })

  it('emits set-custom with null when a datepicker is cleared', async () => {
    const wrapper = mountControls({ customTo: '2025-03-15' })
    await wrapper.find('[data-testid="charts-range-to"]').setValue('')
    const events = wrapper.emitted('set-custom')
    expect(events).toBeTruthy()
    expect(events!.at(-1)).toEqual(['to', null])
  })
})
