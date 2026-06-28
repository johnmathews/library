import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PageHeader from '../PageHeader.vue'

describe('PageHeader', () => {
  it('renders the title in an h1 with the canonical heading classes', () => {
    const wrapper = mount(PageHeader, { props: { title: 'New note' } })
    const h1 = wrapper.get('h1')
    expect(h1.text()).toBe('New note')
    const cls = h1.classes().join(' ')
    expect(cls).toContain('text-2xl')
    expect(cls).toContain('md:text-3xl')
    expect(cls).toContain('font-bold')
  })

  it('renders the description when provided', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'Upload', description: 'Add documents to your library.' },
    })
    const p = wrapper.find('p')
    expect(p.exists()).toBe(true)
    expect(p.text()).toBe('Add documents to your library.')
  })

  it('omits the description paragraph when none is given', () => {
    const wrapper = mount(PageHeader, { props: { title: 'Jobs' } })
    expect(wrapper.find('p').exists()).toBe(false)
  })

  it('renders actions slot content', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'New note' },
      slots: { actions: '<button data-testid="save">Save</button>' },
    })
    expect(wrapper.find('[data-testid="save"]').exists()).toBe(true)
  })

  it('lays the header out full-width with a responsive title/actions split', () => {
    const wrapper = mount(PageHeader, { props: { title: 'New note' } })
    const root = wrapper.get('[data-testid="page-header"]')
    const cls = root.classes().join(' ')
    // full width, and a flex row that puts actions opposite the title on >= sm,
    // wrapping below the title on small screens.
    expect(cls).toContain('flex')
    expect(cls).toContain('justify-between')
  })
})
