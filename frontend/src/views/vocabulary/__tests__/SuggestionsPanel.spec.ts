import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SuggestionsPanel from '../SuggestionsPanel.vue'
import { ApiError } from '@/api/client'

vi.mock('@/api/facets', () => ({
  listSuggestions: vi.fn(), acceptSuggestion: vi.fn(), dismissSuggestion: vi.fn(),
}))
import * as api from '@/api/facets'

beforeEach(() => {
  vi.mocked(api.listSuggestions).mockResolvedValue([
    { id: 5, facet: 'category', suggested_label: 'Boat mooring',
      reason: 'the document is a mooring invoice', document_id: 42 },
  ])
})

const open = async () => {
  const wrapper = mount(SuggestionsPanel, {
    props: { active: true },
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
  await flushPromises()
  return wrapper
}

describe('SuggestionsPanel', () => {
  it('does not load until its tab is opened', async () => {
    mount(SuggestionsPanel, { props: { active: false } })
    await flushPromises()
    expect(api.listSuggestions).not.toHaveBeenCalled()
  })

  it('shows the facet, the label, the reason and a link to the document', async () => {
    const wrapper = await open()
    const row = wrapper.find('[data-testid="suggestion-5"]')
    expect(row.text()).toContain('category')
    expect(row.text()).toContain('Boat mooring')
    expect(row.text()).toContain('mooring invoice')
    expect(row.find('[data-testid="suggestion-5-document"]').exists()).toBe(true)
  })

  it('shows the key it will create before creating it', async () => {
    // Accept both widens the vocabulary and labels a document; the owner should
    // see the key that is about to enter the closed set.
    const wrapper = await open()
    expect(wrapper.find('[data-testid="suggestion-5-key"]').text()).toContain('boat-mooring')
  })

  it('accepts a suggestion', async () => {
    vi.mocked(api.acceptSuggestion).mockResolvedValue({ facet: 'category', value: 'boat-mooring' })
    const wrapper = await open()
    await wrapper.find('[data-testid="suggestion-5-accept"]').trigger('click')
    await flushPromises()
    expect(api.acceptSuggestion).toHaveBeenCalledWith(5)
  })

  it('dismisses a suggestion', async () => {
    vi.mocked(api.dismissSuggestion).mockResolvedValue({ state: 'dismissed' })
    const wrapper = await open()
    await wrapper.find('[data-testid="suggestion-5-dismiss"]').trigger('click')
    await flushPromises()
    expect(api.dismissSuggestion).toHaveBeenCalledWith(5)
  })

  it("renders the server's 409 when the derived key already exists", async () => {
    vi.mocked(api.acceptSuggestion).mockRejectedValue(
      new ApiError(409, 'category=boat-mooring already exists', {}),
    )
    const wrapper = await open()
    await wrapper.find('[data-testid="suggestion-5-accept"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="suggestion-5-error"]').text())
      .toContain('category=boat-mooring already exists')
  })

  it('says so plainly when the queue is empty', async () => {
    vi.mocked(api.listSuggestions).mockResolvedValue([])
    const wrapper = await open()
    expect(wrapper.find('[data-testid="suggestions-empty"]').exists()).toBe(true)
  })
})
