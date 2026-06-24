import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import AppSidebar from '../AppSidebar.vue'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'documents', component: { template: '<div/>' } },
    { path: '/upload', name: 'upload', component: { template: '<div/>' } },
    { path: '/settings', name: 'settings', component: { template: '<div/>' } },
    { path: '/jobs', name: 'jobs', component: { template: '<div/>' } },
    { path: '/charts', name: 'charts', component: { template: '<div/>' } },
  ],
})

beforeEach(() => {
  localStorage.clear()
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

describe('AppSidebar', () => {
  it('renders the three library nav links', async () => {
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    expect(wrapper.find('[data-testid="sidebar-documents-link"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="sidebar-upload-link"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="sidebar-settings-link"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('LIBRARY')
  })

  it('renders a Charts nav link to /charts', async () => {
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    const chartsLink = wrapper.find('[data-testid="sidebar-charts-link"]')
    expect(chartsLink.exists()).toBe(true)
    expect(chartsLink.attributes('href')).toBe('/charts')
    expect(chartsLink.text()).toContain('Charts')
  })
})
