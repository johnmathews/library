import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppPagination from '../AppPagination.vue'

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))!
}

describe('AppPagination', () => {
  it('emits change with page + 1 when Next is clicked', async () => {
    const wrapper = mount(AppPagination, { props: { page: 2, totalPages: 5 } })
    await buttonByText(wrapper, 'Next').trigger('click')
    expect(wrapper.emitted('change')!.at(-1)).toEqual([3])
  })

  it('disables Previous on the first page', () => {
    const wrapper = mount(AppPagination, { props: { page: 1, totalPages: 5 } })
    const prev = buttonByText(wrapper, 'Previous')
    expect(prev.attributes('disabled')).toBeDefined()
    expect(prev.classes()).toContain('cursor-not-allowed')
  })

  it('disables Next on the last page', () => {
    const wrapper = mount(AppPagination, { props: { page: 5, totalPages: 5 } })
    expect(buttonByText(wrapper, 'Next').attributes('disabled')).toBeDefined()
  })

  it('emits change with page - 1 when Previous is clicked', async () => {
    const wrapper = mount(AppPagination, { props: { page: 3, totalPages: 5 } })
    await buttonByText(wrapper, 'Previous').trigger('click')
    expect(wrapper.emitted('change')!.at(-1)).toEqual([2])
  })

  it('marks the current page button active', () => {
    const wrapper = mount(AppPagination, { props: { page: 2, totalPages: 5 } })
    const current = wrapper.findAll('button').find((b) => b.text() === '2')!
    expect(current.attributes('aria-current')).toBe('page')
    expect(current.classes()).toContain('bg-violet-500')
  })

  it('emits the clicked page number', async () => {
    const wrapper = mount(AppPagination, { props: { page: 1, totalPages: 5 } })
    const target = wrapper.findAll('button').find((b) => b.text() === '5')!
    await target.trigger('click')
    expect(wrapper.emitted('change')!.at(-1)).toEqual([5])
  })

  it('renders ellipsis for gaps', () => {
    const wrapper = mount(AppPagination, { props: { page: 5, totalPages: 10 } })
    expect(wrapper.text()).toContain('⋯')
  })

  it('does not render when there is a single page', () => {
    const wrapper = mount(AppPagination, { props: { page: 1, totalPages: 1 } })
    expect(wrapper.find('nav').exists()).toBe(false)
  })
})
