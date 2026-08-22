import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PageHeader from '../PageHeader.vue'
import { usePageTitle } from '@/composables/usePageTitle'

describe('PageHeader', () => {
  it('claims the title for the app bar instead of rendering an h1 of its own', () => {
    const wrapper = mount(PageHeader, { props: { title: 'New note' } })
    // The page's single <h1> lives in AppHeader now; this component must not
    // render a second one.
    expect(wrapper.find('h1').exists()).toBe(false)
    expect(usePageTitle().pageTitle.value).toBe('New note')
  })

  it('passes titleId through to the app bar title', () => {
    mount(PageHeader, { props: { title: 'Documents', titleId: 'dashboard-title' } })
    expect(usePageTitle().pageTitleId.value).toBe('dashboard-title')
  })

  it('re-claims the title when the prop changes', async () => {
    const wrapper = mount(PageHeader, { props: { title: 'Jobs' } })
    await wrapper.setProps({ title: 'Charts' })
    expect(usePageTitle().pageTitle.value).toBe('Charts')
  })

  it('releases the title on unmount so the bar does not keep a dead page name', () => {
    const wrapper = mount(PageHeader, { props: { title: 'Upload documents' } })
    wrapper.unmount()
    expect(usePageTitle().pageTitle.value).toBeNull()
  })

  it('renders the description as a capped, muted lede', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'Upload', description: 'Add documents to your library.' },
    })
    const lede = wrapper.get('[data-testid="page-lede"]')
    expect(lede.text()).toBe('Add documents to your library.')
    const cls = lede.classes().join(' ')
    // Muted and capped to a readable measure — with no title above it, a
    // full-width black paragraph would read as body copy, not as a lede.
    expect(cls).toContain('text-sm')
    expect(cls).toContain('max-w-2xl')
  })

  it('renders nothing at all when there is no description and no actions', () => {
    // A bare title-only header must not leave an empty mb-6 band above the
    // content — reclaiming that band is the point of moving the title out.
    const wrapper = mount(PageHeader, { props: { title: 'Jobs' } })
    expect(wrapper.find('[data-testid="page-header"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="page-lede"]').exists()).toBe(false)
  })

  it('renders actions slot content', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'New note' },
      slots: { actions: '<button data-testid="save">Save</button>' },
    })
    expect(wrapper.find('[data-testid="save"]').exists()).toBe(true)
  })

  it('right-aligns actions even when there is no description beside them', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'Charts' },
      slots: { actions: '<button data-testid="create">Create</button>' },
    })
    const actions = wrapper.get('[data-testid="create"]').element.parentElement!
    // justify-between alone would park a lone child on the LEFT.
    expect(actions.className).toContain('sm:ml-auto')
  })

  it('lays the header out full-width with a responsive lede/actions split', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'New note', description: 'Write a markdown note.' },
      slots: { actions: '<button data-testid="save">Save</button>' },
    })
    const root = wrapper.get('[data-testid="page-header"]')
    const cls = root.classes().join(' ')
    expect(cls).toContain('flex')
    expect(cls).toContain('justify-between')
  })
})
