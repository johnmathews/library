import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import VocabularyView from '../VocabularyView.vue'
import { routes } from '@/router'

const stubs = {
  PageHeader: true,
  FacetsPanel: true,
  SendersPanel: true,
  SuggestionsPanel: true,
}

// Mounted `attachTo: document.body` (repo convention — see LoginView.spec.ts,
// UploadView.spec.ts et al.): jsdom's getComputedStyle does not reliably
// re-resolve `display` on a v-show toggle for a tree that isn't attached to
// the document, so `isVisible()` assertions need a real attachment point.
let wrapper: VueWrapper | undefined

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
})

describe('VocabularyView', () => {
  it('is routed at /vocabulary', () => {
    expect(routes.some((r) => r.path === '/vocabulary' && r.name === 'vocabulary')).toBe(true)
  })

  it('opens on the Facets tab', () => {
    wrapper = mount(VocabularyView, { global: { stubs }, attachTo: document.body })
    expect(wrapper.find('[data-testid="vocab-tab-facets-btn"]').attributes('aria-selected'))
      .toBe('true')
  })

  it('shows the facets panel and hides the others until their tab is chosen', async () => {
    // v-show, so assert on the rendered element's visibility, not on classes.
    wrapper = mount(VocabularyView, { global: { stubs }, attachTo: document.body })
    expect(wrapper.find('[data-testid="vocab-tab-senders"]').isVisible()).toBe(false)

    await wrapper.find('[data-testid="vocab-tab-senders-btn"]').trigger('click')

    expect(wrapper.find('[data-testid="vocab-tab-senders"]').isVisible()).toBe(true)
    expect(wrapper.find('[data-testid="vocab-tab-facets"]').isVisible()).toBe(false)
  })

  it('tells each panel whether it is the open tab, so it can load lazily', async () => {
    wrapper = mount(VocabularyView, { global: { stubs }, attachTo: document.body })
    const senders = () => wrapper!.findComponent({ name: 'SendersPanel' })
    expect(senders().props('active')).toBe(false)

    await wrapper.find('[data-testid="vocab-tab-senders-btn"]').trigger('click')

    expect(senders().props('active')).toBe(true)
  })

  it('offers all three tabs', () => {
    wrapper = mount(VocabularyView, { global: { stubs }, attachTo: document.body })
    for (const tab of ['facets', 'senders', 'suggestions']) {
      expect(wrapper.find(`[data-testid="vocab-tab-${tab}-btn"]`).exists()).toBe(true)
    }
  })
})
