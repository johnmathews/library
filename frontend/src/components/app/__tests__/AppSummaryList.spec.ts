import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AppSummaryList from '../AppSummaryList.vue'

describe('AppSummaryList', () => {
  it('renders key/value rows', () => {
    const wrapper = mount(AppSummaryList, {
      props: {
        rows: [
          { key: 'Title', value: 'Invoice' },
          { key: 'Kind', value: 'PDF' },
        ],
      },
    })
    expect(wrapper.findAll('dt')).toHaveLength(2)
    expect(wrapper.text()).toContain('Invoice')
  })

  it('renders a single action link with its href and visually hidden text', () => {
    const wrapper = mount(AppSummaryList, {
      props: {
        rows: [
          {
            key: 'Title',
            value: 'Invoice',
            actions: [{ text: 'Change', href: '/edit', visuallyHiddenText: 'title' }],
          },
        ],
      },
    })
    const link = wrapper.find('a')
    expect(link.attributes('href')).toBe('/edit')
    expect(link.classes()).toContain('text-violet-500')
    expect(link.text()).toContain('Change')
    expect(wrapper.find('.sr-only').text()).toBe('title')
  })

  it('renders multiple actions as a list', () => {
    const wrapper = mount(AppSummaryList, {
      props: {
        rows: [
          {
            key: 'Title',
            value: 'Invoice',
            actions: [
              { text: 'Change', href: '/edit' },
              { text: 'Remove', href: '/remove' },
            ],
          },
        ],
      },
    })
    expect(wrapper.findAll('a')).toHaveLength(2)
  })
})
