import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import AppHeader from '../AppHeader.vue'

beforeEach(() => {
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

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'documents', component: { template: '<div/>' } },
    { path: '/settings', name: 'settings', component: { template: '<div/>' } },
    { path: '/login', name: 'login', component: { template: '<div/>' } },
  ],
})

describe('AppHeader', () => {
  it('emits open-search when the search button is clicked', async () => {
    setActivePinia(createPinia())
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppHeader, { props: { sidebarOpen: false }, global: { plugins: [router] } })
    await wrapper.get('[data-testid="header-search-button"]').trigger('click')
    expect(wrapper.emitted('open-search')).toBeTruthy()
  })
  it('emits toggle-sidebar from the hamburger', async () => {
    setActivePinia(createPinia())
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppHeader, { props: { sidebarOpen: false }, global: { plugins: [router] } })
    await wrapper.get('[aria-controls="sidebar"]').trigger('click')
    expect(wrapper.emitted('toggle-sidebar')).toBeTruthy()
  })
})
