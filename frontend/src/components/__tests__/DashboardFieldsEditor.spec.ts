import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DashboardFieldsEditor from '../DashboardFieldsEditor.vue'
import type { DashboardField } from '@/api/settings'

function lastEmit(wrapper: ReturnType<typeof mount>): DashboardField[] {
  const events = wrapper.emitted('update:modelValue')
  expect(events).toBeTruthy()
  return events!.at(-1)![0] as DashboardField[]
}

describe('DashboardFieldsEditor', () => {
  it('lists enabled fields first (in given order), then the rest disabled', () => {
    const w = mount(DashboardFieldsEditor, { props: { modelValue: ['date', 'kind'] } })
    const rowOrder = w
      .findAll('[data-testid^="dashboard-field-row-"]')
      .map((el) => el.attributes('data-testid'))
    expect(rowOrder).toEqual([
      'dashboard-field-row-date',
      'dashboard-field-row-kind',
      // remaining catalog fields, disabled, in catalog order
      'dashboard-field-row-sender',
      'dashboard-field-row-tags',
      'dashboard-field-row-due_date',
      'dashboard-field-row-expiry_date',
      'dashboard-field-row-added_date',
      'dashboard-field-row-last_edited',
      'dashboard-field-row-language',
      'dashboard-field-row-status',
      'dashboard-field-row-amount',
      'dashboard-field-row-file_type',
    ])
    // Enabled boxes are checked; disabled are not.
    expect(
      (w.find('[data-testid="dashboard-field-date"]').element as HTMLInputElement).checked,
    ).toBe(true)
    expect(
      (w.find('[data-testid="dashboard-field-sender"]').element as HTMLInputElement).checked,
    ).toBe(false)
  })

  it('emits the ordered enabled list when a field is toggled on', async () => {
    const w = mount(DashboardFieldsEditor, { props: { modelValue: ['kind', 'sender'] } })
    await w.find('[data-testid="dashboard-field-tags"]').setValue(true)
    // tags sits in row index 2 (after kind, sender), so it lands third.
    expect(lastEmit(w)).toEqual(['kind', 'sender', 'tags'])
  })

  it('emits without a field when toggled off', async () => {
    const w = mount(DashboardFieldsEditor, { props: { modelValue: ['kind', 'sender'] } })
    await w.find('[data-testid="dashboard-field-kind"]').setValue(false)
    expect(lastEmit(w)).toEqual(['sender'])
  })

  it('reorders via the Up button and re-emits in the new order', async () => {
    const w = mount(DashboardFieldsEditor, { props: { modelValue: ['kind', 'sender'] } })
    // Move sender (row 1) up above kind.
    await w.find('[data-testid="dashboard-field-up-sender"]').trigger('click')
    expect(lastEmit(w)).toEqual(['sender', 'kind'])
  })

  it('disables Up on the first row and Down on the last row', () => {
    const w = mount(DashboardFieldsEditor, { props: { modelValue: ['kind'] } })
    // kind is first: its Up is disabled. file_type is last catalog field: Down disabled.
    expect(w.find('[data-testid="dashboard-field-up-kind"]').attributes('disabled')).toBeDefined()
    expect(
      w.find('[data-testid="dashboard-field-down-file_type"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('reset restores the default enabled fields in order', async () => {
    const w = mount(DashboardFieldsEditor, { props: { modelValue: ['amount', 'file_type'] } })
    await w.find('[data-testid="dashboard-fields-reset"]').trigger('click')
    expect(lastEmit(w)).toEqual(['kind', 'sender', 'tags', 'date', 'language', 'status'])
  })
})
