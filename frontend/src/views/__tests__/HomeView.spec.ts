import { describe, it, expect } from 'vitest'

import { mount } from '@vue/test-utils'
import HomeView from '../HomeView.vue'

describe('HomeView', () => {
  it('renders the documents heading with an empty state', () => {
    const wrapper = mount(HomeView)
    expect(wrapper.find('h1.govuk-heading-xl').text()).toBe('Documents')
    expect(wrapper.find('.govuk-inset-text').text()).toContain('no documents to show yet')
  })
})
