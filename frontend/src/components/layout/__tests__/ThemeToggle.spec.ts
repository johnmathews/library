import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ThemeToggle from '../ThemeToggle.vue'

describe('ThemeToggle', () => {
  beforeEach(() => {
    localStorage.clear()
    document.documentElement.classList.remove('dark')
    if (!window.matchMedia) {
      window.matchMedia = (() => ({
        matches: false,
        media: '',
        addEventListener() {},
        removeEventListener() {},
        addListener() {},
        removeListener() {},
        dispatchEvent() {
          return false
        },
      })) as unknown as typeof window.matchMedia
    }
  })
  it('toggles the dark class on <html> when checked', async () => {
    const wrapper = mount(ThemeToggle)
    const input = wrapper.get('input[type="checkbox"]')
    await input.setValue(true)
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
