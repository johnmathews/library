import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/admin', () => ({
  getSystemInfo: vi.fn(),
  getArchitecture: vi.fn(),
  getCoverage: vi.fn(),
  listUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
}))

import {
  createUser,
  getArchitecture,
  getCoverage,
  getSystemInfo,
  listUsers,
  updateUser,
} from '@/api/admin'
import { ApiError } from '@/api/client'
import AdminView from '../AdminView.vue'
import { useAuthStore } from '@/stores/auth'

const systemInfo = {
  version: '1.2.3',
  git_sha: 'abc123',
  deployment: [{ name: 'library-webserver', role: 'web' }],
  config: { debug: false, retention_days: 30 },
  stats: {
    documents_total: 10,
    documents_deleted: 1,
    documents_by_status: { indexed: 9, failed: 1 },
    users_total: 2,
    users_active: 2,
    jobs_total: 5,
    jobs_active: 1,
    extraction_cost_usd_total: 1.5,
  },
}

const architecture = {
  docs: [
    { name: 'overview', title: 'Overview', markdown: '# Overview\n\nThe **stack**.' },
    { name: 'data', title: 'Data model', markdown: '## Data' },
  ],
}

const coverageAvailable = {
  available: true,
  backend: {
    pct: 95.2,
    threshold: 85,
    files_total: 42,
    files_below_gate: 1,
    worst_files: [{ path: 'src/library/series.py', pct: 71.0 }],
  },
  frontend: {
    pct: 88,
    threshold: 85,
    files_total: 30,
    files_below_gate: 0,
    worst_files: [],
  },
  generated_at: '2026-06-28T00:00:00Z',
  git_sha: 'deadbeef',
}

const userList = [
  {
    id: 1,
    username: 'root',
    display_name: 'Root',
    is_admin: true,
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    username: 'bob',
    display_name: 'Bob',
    is_admin: false,
    is_active: true,
    created_at: '2026-02-01T00:00:00Z',
  },
]

function seedDefaults(): void {
  vi.mocked(getSystemInfo).mockResolvedValue(systemInfo)
  vi.mocked(getArchitecture).mockResolvedValue(architecture)
  vi.mocked(getCoverage).mockResolvedValue(coverageAvailable)
  vi.mocked(listUsers).mockResolvedValue(structuredClone(userList))
}

function mountView() {
  const auth = useAuthStore()
  // The current admin user is id 1 ("root").
  auth.user = {
    id: 1,
    username: 'root',
    display_name: 'Root',
    is_admin: true,
    preferences: { dashboard_fields: [] },
  }
  return mount(AdminView, { global: { stubs: { RouterLink: true } } })
}

describe('AdminView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    seedDefaults()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the heading and the System tab data', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('h1').text()).toBe('Admin')
    expect(wrapper.find('[data-testid="system-version"]').text()).toBe('1.2.3')
    expect(wrapper.find('[data-testid="system-git-sha"]').text()).toBe('abc123')
    expect(wrapper.find('[data-testid="system-deployment-row"]').text()).toContain('library-webserver')
    expect(wrapper.find('[data-testid="stat-documents-total"]').text()).toBe('10')
    expect(wrapper.find('[data-testid="stat-cost"]').text()).toContain('$1.50')
    expect(wrapper.findAll('[data-testid="system-config-row"]')).toHaveLength(2)
    expect(wrapper.find('[data-testid="stat-by-status"]').text()).toContain('indexed')
  })

  it('renders the Architecture tab with sanitised markdown and lets you switch docs', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-architecture-btn"]').trigger('click')
    const content = wrapper.find('[data-testid="arch-content"]')
    expect(content.html()).toContain('<strong>stack</strong>')
    expect(content.html()).toContain('<h1')

    await wrapper.find('[data-testid="arch-doc-data"]').trigger('click')
    expect(wrapper.find('[data-testid="arch-content"]').html()).toContain('Data')
  })

  it('renders coverage figures, gate verdicts and worst files when available', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-coverage-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="coverage-backend"]').text()).toBe('95.2%')
    expect(wrapper.find('[data-testid="coverage-frontend"]').text()).toBe('88%')
    // Backend card shows the gate, file counts and the one below-gate file.
    const backendCard = wrapper.find('[data-testid="coverage-card-backend"]')
    expect(backendCard.text()).toContain('Gate 85%')
    expect(backendCard.text()).toContain('42 files')
    expect(backendCard.text()).toContain('1 below gate')
    expect(backendCard.text()).toContain('src/library/series.py')
    expect(backendCard.text()).toContain('71%')
    expect(wrapper.find('[data-testid="coverage-unavailable"]').exists()).toBe(false)
  })

  it('shows a clear message when coverage is unavailable', async () => {
    vi.mocked(getCoverage).mockResolvedValue({
      available: false,
      backend: null,
      frontend: null,
      generated_at: null,
      git_sha: null,
    })
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-coverage-btn"]').trigger('click')
    const banner = wrapper.find('[data-testid="coverage-unavailable"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('generated by CI')
  })

  it('lists users and hides actions for the current user', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    expect(wrapper.find('[data-testid="user-row-1"]').text()).toContain('root')
    // The current user (id 1) has no toggle buttons.
    expect(wrapper.find('[data-testid="user-toggle-admin-1"]').exists()).toBe(false)
    // Other users do.
    expect(wrapper.find('[data-testid="user-toggle-admin-2"]').exists()).toBe(true)
  })

  it('promotes another user via updateUser and reflects the result', async () => {
    vi.mocked(updateUser).mockResolvedValue({
      ...userList[1]!,
      is_admin: true,
    })
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    await wrapper.find('[data-testid="user-toggle-admin-2"]').trigger('click')
    await flushPromises()

    expect(updateUser).toHaveBeenCalledWith(2, { is_admin: true })
    // The button now offers the inverse action.
    expect(wrapper.find('[data-testid="user-toggle-admin-2"]').text()).toBe('Demote')
  })

  it('surfaces a 409 error inline when a user action is rejected', async () => {
    vi.mocked(updateUser).mockRejectedValue(
      new ApiError(409, 'cannot remove the last active admin'),
    )
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    await wrapper.find('[data-testid="user-toggle-active-2"]').trigger('click')
    await flushPromises()

    const err = wrapper.find('[data-testid="user-error-2"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('cannot remove the last active admin')
  })

  it('creates a user and refreshes the list', async () => {
    vi.mocked(createUser).mockResolvedValue({
      id: 3,
      username: 'carol',
      display_name: 'Carol',
      is_admin: false,
      is_active: true,
      created_at: '2026-06-28T00:00:00Z',
    })
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    await wrapper.find('#new-username').setValue('carol')
    await wrapper.find('#new-password').setValue('s3cret')
    await wrapper.find('[data-testid="new-is-admin"]').setValue(true)
    await wrapper.find('[data-testid="create-user-form"]').trigger('submit.prevent')
    await flushPromises()

    expect(createUser).toHaveBeenCalledWith({
      username: 'carol',
      password: 's3cret',
      display_name: undefined,
      is_admin: true,
    })
    // listUsers is called once on mount and again after a successful create.
    expect(listUsers).toHaveBeenCalledTimes(2)
  })

  it('surfaces a create-user error', async () => {
    vi.mocked(createUser).mockRejectedValue(new ApiError(409, 'username already taken'))
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    await wrapper.find('#new-username').setValue('root')
    await wrapper.find('#new-password').setValue('x')
    await wrapper.find('[data-testid="create-user-form"]').trigger('submit.prevent')
    await flushPromises()

    const err = wrapper.find('[data-testid="create-user-error"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('username already taken')
  })

  it('shows an error when system data fails to load', async () => {
    vi.mocked(getSystemInfo).mockRejectedValue(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="system-error"]').exists()).toBe(true)
  })
})
