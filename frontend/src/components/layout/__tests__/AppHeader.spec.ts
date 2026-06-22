import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import AppHeader from '../AppHeader.vue'
import { useJobsStore, type DocumentEvent } from '@/stores/jobs'

function docEvent(id: number, status: string, title: string): DocumentEvent {
  return { document_id: id, event: 'status_changed', status, title }
}

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
    { path: '/jobs', name: 'jobs', component: { template: '<div/>' } },
    { path: '/documents/:id', name: 'document-detail', component: { template: '<div/>' } },
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

  it('hides the jobs indicator when nothing is running', async () => {
    setActivePinia(createPinia())
    await router.push('/')
    await router.isReady()
    const wrapper = mount(AppHeader, { props: { sidebarOpen: false }, global: { plugins: [router] } })
    expect(wrapper.find('[data-testid="header-jobs-button"]').exists()).toBe(false)
  })

  it('shows a count badge and dropdown of active jobs when work is in flight', async () => {
    setActivePinia(createPinia())
    const jobs = useJobsStore()
    jobs.handle(docEvent(1, 'ocr', 'Invoice'))
    jobs.handle(docEvent(2, 'extract', 'Receipt'))
    await router.push('/')
    await router.isReady()

    const wrapper = mount(AppHeader, { props: { sidebarOpen: false }, global: { plugins: [router] } })
    expect(wrapper.get('[data-testid="header-jobs-count"]').text()).toBe('2')

    await wrapper.get('[data-testid="header-jobs-button"]').trigger('click')
    const items = wrapper.findAll('[data-testid="header-jobs-item"]')
    expect(items).toHaveLength(2)
    expect(wrapper.text()).toContain('Invoice')
    expect(wrapper.text()).toContain('Receipt')
    expect(wrapper.get('[data-testid="header-jobs-viewall"]').attributes('href')).toBe('/jobs')
  })
})
