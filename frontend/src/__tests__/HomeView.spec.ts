import { describe, it, expect } from 'vitest'

import { mount } from '@vue/test-utils'
import HomeView from '../views/HomeView.vue'

describe('HomeView', () => {
  it('renders the heading', () => {
    const wrapper = mount(HomeView)
    expect(wrapper.find('h1').text()).toBe('Library')
    expect(wrapper.text()).toContain('Personal document archive.')
  })
})
