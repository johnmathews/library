import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { listSavedViews } from '@/api/savedViews'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import AppSidebar from '../AppSidebar.vue'
import { useAuthStore } from '@/stores/auth'
import { useSavedViewsStore } from '@/stores/savedViews'
import type { SavedView } from '@/api/savedViews'

vi.mock('@/api/savedViews', () => ({
  listSavedViews: vi.fn().mockResolvedValue([]),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
  reorderSavedViews: vi.fn(),
}))

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'documents', component: { template: '<div/>' } },
    { path: '/deleted', name: 'documents-deleted', component: { template: '<div/>' } },
    { path: '/upload', name: 'upload', component: { template: '<div/>' } },
    { path: '/notes/new', name: 'note-new', component: { template: '<div/>' } },
    { path: '/ask', name: 'ask', component: { template: '<div/>' } },
    { path: '/settings', name: 'settings', component: { template: '<div/>' } },
    { path: '/jobs', name: 'jobs', component: { template: '<div/>' } },
    { path: '/charts', name: 'charts', component: { template: '<div/>' } },
    { path: '/projects', name: 'projects', component: { template: '<div/>' } },
    { path: '/saved-views', name: 'saved-views', component: { template: '<div/>' } },
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

function makeView(overrides: Partial<SavedView> = {}): SavedView {
  return {
    id: 1,
    name: 'View',
    filter_state: {},
    pinned: false,
    sort_order: 0,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  }
}

/** Directly seed the saved-views store (bypasses the load fetch). */
function seedViews(views: SavedView[]): void {
  const store = useSavedViewsStore()
  store.views = views
  store.loaded = true
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

  it('renders a Recently Deleted nav link to /deleted', async () => {
    seedAuth(false)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    const deletedLink = wrapper.find('[data-testid="sidebar-deleted-link"]')
    expect(deletedLink.exists()).toBe(true)
    expect(deletedLink.attributes('href')).toBe('/deleted')
    expect(deletedLink.text()).toContain('Recently Deleted')
  })

  it('orders the nav: Documents first, Recently Deleted last, with no Saved-views heading', async () => {
    seedAuth(true)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    const order = wrapper
      .findAll('#sidebar-nav a[data-testid]')
      .map((a) => a.attributes('data-testid'))
    expect(order).toEqual([
      'sidebar-documents-link',
      'sidebar-upload-link',
      'sidebar-notes-link',
      'sidebar-charts-link',
      'sidebar-ask-link',
      'sidebar-jobs-link',
      'sidebar-projects-link',
      'sidebar-settings-link',
      'sidebar-admin-link',
      'sidebar-deleted-link',
    ])
  })

  it('renders a Search button after pinned dashboards and before Upload', async () => {
    seedAuth(false)
    seedViews([makeView({ id: 7, name: 'Unpaid invoices', pinned: true })])
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    const search = wrapper.find('[data-testid="sidebar-search-button"]')
    expect(search.exists()).toBe(true)
    expect(search.element.tagName).toBe('BUTTON')
    expect(search.attributes('type')).toBe('button')
    expect(search.text()).toContain('Search')
    // Search is a modal trigger, not a route — it must stay out of the
    // `a[data-testid]` selectors the nav-order tests use.
    const order = wrapper
      .findAll('#sidebar-nav [data-testid]')
      .map((el) => el.attributes('data-testid'))
    expect(order.slice(0, 4)).toEqual([
      'sidebar-documents-link',
      'sidebar-dashboard-7',
      'sidebar-search-button',
      'sidebar-upload-link',
    ])
  })

  it('clicking the Search button emits open-search and closes the mobile sidebar', async () => {
    seedAuth(false)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    await wrapper.find('[data-testid="sidebar-search-button"]').trigger('click')
    expect(wrapper.emitted('open-search')).toBeTruthy()
    // The click bubbles through #sidebar-nav, which closes the mobile drawer —
    // same behavior as clicking any nav link.
    expect(wrapper.emitted('close-sidebar')).toBeTruthy()
  })

  it('no longer renders a standalone "Saved views" nav link (managed from the dashboard)', async () => {
    seedAuth(false)
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    expect(wrapper.find('[data-testid="sidebar-saved-views-link"]').exists()).toBe(false)
  })

  it('renders pinned saved views as dashboard links to / with their saved query', async () => {
    seedAuth(false)
    seedViews([
      makeView({ id: 7, name: 'Unpaid invoices', pinned: true, filter_state: { kind: 'invoice' } }),
      makeView({ id: 8, name: 'Drafts', pinned: false, filter_state: { status: 'draft' } }),
    ])
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    const pinned = wrapper.find('[data-testid="sidebar-dashboard-7"]')
    expect(pinned.exists()).toBe(true)
    expect(pinned.text()).toContain('Unpaid invoices')
    // Links home with the saved filter state in the query string.
    expect(pinned.attributes('href')).toBe('/?kind=invoice')
    // Unpinned views never appear as dashboards.
    expect(wrapper.find('[data-testid="sidebar-dashboard-8"]').exists()).toBe(false)
    // First-class citizen: it lives in the main nav, directly under Documents
    // (no "Saved views" heading, no separate subsection).
    const order = wrapper
      .findAll('#sidebar-nav a[data-testid]')
      .map((a) => a.attributes('data-testid'))
    expect(order.slice(0, 3)).toEqual([
      'sidebar-documents-link',
      'sidebar-dashboard-7',
      'sidebar-upload-link',
    ])
  })

  it('loads dashboards when auth resolves after mount (persistent shell mounts before the guard)', async () => {
    // The sidebar lives in a persistent DefaultLayout that mounts before the
    // router's async auth guard populates the user, so a mount-time check would
    // miss the just-authenticated user. Reproduce that ordering: unauthenticated
    // at mount, then the guard resolves the user.
    vi.mocked(listSavedViews).mockClear()
    const auth = useAuthStore()
    auth.user = null
    router.push('/')
    await router.isReady()
    mount(AppSidebar, { props: { sidebarOpen: false }, global: { plugins: [router] } })
    expect(listSavedViews).not.toHaveBeenCalled()

    // Guard resolves -> the watcher must now load the saved views.
    seedAuth(false)
    await flushPromises()
    expect(listSavedViews).toHaveBeenCalledTimes(1)
  })

  it('renders no dashboard links when no views are pinned', async () => {
    seedAuth(false)
    seedViews([makeView({ id: 8, pinned: false })])
    router.push('/')
    await router.isReady()
    const wrapper = mount(AppSidebar, {
      props: { sidebarOpen: false },
      global: { plugins: [router] },
    })
    // Pinned views are first-class nav links now (no separate subsection); an
    // unpinned view contributes no link.
    expect(wrapper.find('[data-testid="sidebar-dashboard-8"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid^="sidebar-dashboard-"]')).toHaveLength(0)
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
