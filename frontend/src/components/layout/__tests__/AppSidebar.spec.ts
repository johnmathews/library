import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import AppSidebar from '../AppSidebar.vue'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'documents', component: { template: '<div/>' } },
    { path: '/upload', name: 'upload', component: { template: '<div/>' } },
    { path: '/notes/new', name: 'note-new', component: { template: '<div/>' } },
    { path: '/settings', name: 'settings', component: { template: '<div/>' } },
    { path: '/jobs', name: 'jobs', component: { template: '<div/>' } },
    { path: '/charts', name: 'charts', component: { template: '<div/>' } },
    { path: '/admin', name: 'admin', component: { template: '<div/>' } },
  ],
})

/** Seed an active auth store; `admin` controls the is_admin flag. */
function seedAuth(admin: boolean): void {
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    username: 'a',
    display_name: 'A',
    is_admin: admin,
    preferences: { dashboard_fields: [] },
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  document.body.classList.remove('sidebar-expanded')
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
    seedAuth(false)
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

  it('renders a New note nav link to /notes/new', async () => {
    seedAuth(false)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    const notesLink = wrapper.find('[data-testid="sidebar-notes-link"]')
    expect(notesLink.exists()).toBe(true)
    expect(notesLink.attributes('href')).toBe('/notes/new')
    expect(notesLink.text()).toContain('New note')
  })

  it('renders a Charts nav link to /charts', async () => {
    seedAuth(false)
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

  it('renders the desktop expand/collapse toggle', async () => {
    seedAuth(false)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    // The toggle is available on all desktop widths (no 2xl gate).
    expect(wrapper.find('#sidebar-collapse-toggle').exists()).toBe(true)
  })

  it('toggles the body sidebar-expanded class and persists the choice', async () => {
    seedAuth(false)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    // Default (matchMedia stubbed to false) starts collapsed.
    expect(document.body.classList.contains('sidebar-expanded')).toBe(false)

    await wrapper.find('#sidebar-collapse-toggle').trigger('click')
    expect(document.body.classList.contains('sidebar-expanded')).toBe(true)
    expect(localStorage.getItem('library:sidebar-expanded')).toBe('true')

    await wrapper.find('#sidebar-collapse-toggle').trigger('click')
    expect(document.body.classList.contains('sidebar-expanded')).toBe(false)
    expect(localStorage.getItem('library:sidebar-expanded')).toBe('false')
  })

  it('honours a legacy sidebar-expanded preference on load', async () => {
    localStorage.setItem('sidebar-expanded', 'true')
    seedAuth(false)
    router.push('/')
    await router.isReady()
    mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    expect(document.body.classList.contains('sidebar-expanded')).toBe(true)
  })

  it('hides the Admin nav link from non-admins', async () => {
    seedAuth(false)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    expect(wrapper.find('[data-testid="sidebar-admin-link"]').exists()).toBe(false)
  })

  it('shows the Admin nav link to admins, linking to /admin', async () => {
    seedAuth(true)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    const adminLink = wrapper.find('[data-testid="sidebar-admin-link"]')
    expect(adminLink.exists()).toBe(true)
    expect(adminLink.attributes('href')).toBe('/admin')
    expect(adminLink.text()).toContain('Admin')
  })
})
