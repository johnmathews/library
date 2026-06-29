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
  deleteUser: vi.fn(),
  listRecipients: vi.fn(),
  renameRecipient: vi.fn(),
  deleteRecipient: vi.fn(),
}))

vi.mock('@/composables/taxonomyOptions', () => ({
  refreshTaxonomyOptions: vi.fn().mockResolvedValue(undefined),
}))

import {
  createUser,
  deleteRecipient,
  deleteUser,
  getArchitecture,
  getCoverage,
  getSystemInfo,
  listRecipients,
  listUsers,
  renameRecipient,
  updateUser,
} from '@/api/admin'
import { refreshTaxonomyOptions } from '@/composables/taxonomyOptions'
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
    {
      name: 'overview',
      title: 'Overview',
      markdown: '# Overview\n\nThe **stack**.\n\n| Col A | Col B |\n| --- | --- |\n| a | b |\n',
    },
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
  test_types: [
    { key: 'backend', label: 'Backend unit', runner: 'pytest', has_coverage: true, description: '' },
    {
      key: 'frontend',
      label: 'Frontend unit',
      runner: 'Vitest',
      has_coverage: true,
      description: '',
    },
    {
      key: 'e2e',
      label: 'End-to-end',
      runner: 'Playwright',
      has_coverage: false,
      description: 'Full-stack browser flows. Pass/fail gate in CI; no line coverage.',
    },
    {
      key: 'compose-smoke',
      label: 'Deployment smoke',
      runner: 'docker compose',
      has_coverage: false,
      description: 'Boots the production stack and verifies health + login.',
    },
  ],
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

const recipientList = [
  { id: 10, name: 'Alice', document_count: 0 },
  { id: 11, name: 'Bob', document_count: 3 },
  { id: 12, name: 'Carol', document_count: 1 },
]

function seedDefaults(): void {
  vi.mocked(getSystemInfo).mockResolvedValue(systemInfo)
  vi.mocked(getArchitecture).mockResolvedValue(architecture)
  vi.mocked(getCoverage).mockResolvedValue(coverageAvailable)
  vi.mocked(listUsers).mockResolvedValue(structuredClone(userList))
  vi.mocked(listRecipients).mockResolvedValue(structuredClone(recipientList))
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

  it('orders the tabs Users · Metadata · Architecture · Coverage · System with Users selected by default', async () => {
    const wrapper = mountView()
    await flushPromises()
    const labels = wrapper.findAll('[role="tab"]').map((t) => t.text())
    expect(labels).toEqual(['Users', 'Metadata', 'Architecture', 'Coverage', 'System'])
    // Users is the default selected tab.
    const usersTab = wrapper.find('[data-testid="admin-tab-users-btn"]')
    expect(usersTab.attributes('aria-selected')).toBe('true')
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
    // GFM tables render as real <table> markup (styled via .doc-markdown CSS).
    expect(content.find('table').exists()).toBe(true)
    expect(content.findAll('th')).toHaveLength(2)

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

  it('shows all four CI test types, with e2e/smoke as no-coverage gates', async () => {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('[data-testid="admin-tab-coverage-btn"]').trigger('click')
    // One card per CI test type.
    for (const key of ['backend', 'frontend', 'e2e', 'compose-smoke']) {
      expect(wrapper.find(`[data-testid="coverage-card-${key}"]`).exists()).toBe(true)
    }
    // e2e/compose-smoke have no line coverage — they show a description note,
    // not a percentage.
    expect(wrapper.find('[data-testid="coverage-e2e"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="coverage-note-e2e"]').text()).toContain('Pass/fail gate')
    expect(wrapper.find('[data-testid="coverage-note-compose-smoke"]').exists()).toBe(true)
  })

  it('shows a clear message when coverage is unavailable', async () => {
    vi.mocked(getCoverage).mockResolvedValue({
      available: false,
      backend: null,
      frontend: null,
      test_types: [],
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

  it('offers Delete for other users but not the current user', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    // The current user (id 1) shows no Delete button.
    expect(wrapper.find('[data-testid="user-delete-1"]').exists()).toBe(false)
    // Another user does.
    expect(wrapper.find('[data-testid="user-delete-2"]').exists()).toBe(true)
  })

  it('two-step deletes a user via deleteUser and drops the row', async () => {
    vi.mocked(deleteUser).mockResolvedValue()
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    // First click arms the confirm; it does not call the API yet.
    await wrapper.find('[data-testid="user-delete-2"]').trigger('click')
    expect(deleteUser).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="user-delete-confirm-2"]').exists()).toBe(true)

    // Confirm performs the delete and removes the row.
    await wrapper.find('[data-testid="user-delete-confirm-2"]').trigger('click')
    await flushPromises()

    expect(deleteUser).toHaveBeenCalledWith(2)
    expect(wrapper.find('[data-testid="user-row-2"]').exists()).toBe(false)
  })

  it('cancels an armed delete without calling the API', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    await wrapper.find('[data-testid="user-delete-2"]').trigger('click')
    await wrapper.find('[data-testid="user-delete-cancel-2"]').trigger('click')

    expect(deleteUser).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="user-delete-2"]').exists()).toBe(true)
  })

  it('surfaces a delete guard error inline and keeps the row', async () => {
    vi.mocked(deleteUser).mockRejectedValue(
      new ApiError(409, 'cannot delete the last active admin'),
    )
    const wrapper = mountView()
    await flushPromises()

    await wrapper.find('[data-testid="admin-tab-users-btn"]').trigger('click')
    await wrapper.find('[data-testid="user-delete-2"]').trigger('click')
    await wrapper.find('[data-testid="user-delete-confirm-2"]').trigger('click')
    await flushPromises()

    const err = wrapper.find('[data-testid="user-error-2"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('cannot delete the last active admin')
    expect(wrapper.find('[data-testid="user-row-2"]').exists()).toBe(true)
  })

  it('shows an error when system data fails to load', async () => {
    vi.mocked(getSystemInfo).mockRejectedValue(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="system-error"]').exists()).toBe(true)
  })

  // --- Metadata tab: recipients ---------------------------------------------

  async function openMetadataTab() {
    const wrapper = mountView()
    await flushPromises()
    await wrapper.find('[data-testid="admin-tab-metadata-btn"]').trigger('click')
    await flushPromises()
    return wrapper
  }

  it('loads recipients on first opening the Metadata tab, with document counts', async () => {
    const wrapper = await openMetadataTab()

    expect(listRecipients).toHaveBeenCalledTimes(1)
    const row = wrapper.find('[data-testid="recipient-row-11"]')
    expect(row.text()).toContain('Bob')
    expect(row.text()).toContain('3 docs')
  })

  it('renames a recipient and refreshes the list and taxonomy cache', async () => {
    vi.mocked(renameRecipient).mockResolvedValue({ id: 10, name: 'Alicia' })
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="recipient-row-10"]')
    await row.find('[data-testid="recipient-rename"]').trigger('click')
    await wrapper.find('#recipient-rename-input-10').setValue('Alicia')
    await wrapper.find('[data-testid="recipient-rename-save"]').trigger('click')
    await flushPromises()

    expect(renameRecipient).toHaveBeenCalledWith(10, 'Alicia', false)
    // listRecipients: once on tab open, once after the successful rename.
    expect(listRecipients).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('shows a merge prompt on a 409 collision and merges on confirm', async () => {
    vi.mocked(renameRecipient)
      .mockRejectedValueOnce(
        new ApiError(409, 'name in use', {
          detail: 'name in use',
          target_id: 11,
          target_name: 'Bob',
          target_document_count: 3,
        }),
      )
      .mockResolvedValueOnce({ id: 11, name: 'Bob' })
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="recipient-row-10"]')
    await row.find('[data-testid="recipient-rename"]').trigger('click')
    await wrapper.find('#recipient-rename-input-10').setValue('Bob')
    await wrapper.find('[data-testid="recipient-rename-save"]').trigger('click')
    await flushPromises()

    // The collision surfaces an inline merge warning, not an error.
    const warning = wrapper.find('[data-testid="recipient-merge-warning"]')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toContain('Bob')
    expect(warning.text()).toContain('3 documents')

    await wrapper.find('[data-testid="recipient-merge-confirm"]').trigger('click')
    await flushPromises()

    expect(renameRecipient).toHaveBeenNthCalledWith(1, 10, 'Bob', false)
    expect(renameRecipient).toHaveBeenNthCalledWith(2, 10, 'Bob', true)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('deletes a zero-document recipient after an inline confirm', async () => {
    vi.mocked(deleteRecipient).mockResolvedValue()
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="recipient-row-10"]')
    await row.find('[data-testid="recipient-delete"]').trigger('click')
    // No reassign picker for a zero-document recipient.
    expect(wrapper.find('[data-testid="recipient-reassign-select"]').exists()).toBe(false)
    await wrapper.find('[data-testid="recipient-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteRecipient).toHaveBeenCalledWith(10)
    expect(listRecipients).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('deletes an in-use recipient by reassigning its documents', async () => {
    vi.mocked(deleteRecipient).mockResolvedValue()
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="recipient-row-11"]')
    await row.find('[data-testid="recipient-delete"]').trigger('click')
    // An in-use recipient reveals the reassign picker.
    const select = wrapper.find('[data-testid="recipient-reassign-select"]')
    expect(select.exists()).toBe(true)
    await wrapper.find('#recipient-reassign-11').setValue('12')
    await wrapper.find('[data-testid="recipient-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteRecipient).toHaveBeenCalledWith(11, 12)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('clears the recipient on its documents when "None" is chosen', async () => {
    vi.mocked(deleteRecipient).mockResolvedValue()
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="recipient-row-11"]')
    await row.find('[data-testid="recipient-delete"]').trigger('click')
    // Leave the select on its default "None (clear)" option (value '').
    await wrapper.find('[data-testid="recipient-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteRecipient).toHaveBeenCalledWith(11, null)
  })
})
