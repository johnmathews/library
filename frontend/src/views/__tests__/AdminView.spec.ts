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
  createRecipient: vi.fn(),
  renameRecipient: vi.fn(),
  deleteRecipient: vi.fn(),
  createSender: vi.fn(),
  renameSender: vi.fn(),
  deleteSender: vi.fn(),
  renameKind: vi.fn(),
  deleteKind: vi.fn(),
  listCurrencies: vi.fn(),
  normalizeCurrency: vi.fn(),
  listFxRates: vi.fn(),
  seedFxRate: vi.fn(),
}))

vi.mock('@/api/taxonomy', () => ({
  listSenders: vi.fn(),
  listKinds: vi.fn(),
  createKind: vi.fn(),
}))

vi.mock('@/composables/taxonomyOptions', () => ({
  refreshTaxonomyOptions: vi.fn().mockResolvedValue(undefined),
}))

import {
  createRecipient,
  createSender,
  createUser,
  deleteKind,
  deleteRecipient,
  deleteSender,
  deleteUser,
  getArchitecture,
  getCoverage,
  getSystemInfo,
  listCurrencies,
  listFxRates,
  listRecipients,
  listUsers,
  normalizeCurrency,
  seedFxRate,
  renameKind,
  renameRecipient,
  renameSender,
  updateUser,
} from '@/api/admin'
import { createKind, listKinds, listSenders } from '@/api/taxonomy'
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

const senderList = [
  { id: 20, name: 'Acme', document_count: 0 },
  { id: 21, name: 'Globex', document_count: 4 },
  { id: 22, name: 'Initech', document_count: 2 },
]

const kindList = [
  { slug: 'invoice', name: 'Invoice', document_count: 0 },
  { slug: 'receipt', name: 'Receipt', document_count: 5 },
  { slug: 'letter', name: 'Letter', document_count: 2 },
]

const currencyList = [
  { code: 'EUR', document_count: 9 },
  { code: 'USD', document_count: 2 },
]

const fxList = [
  { code: 'EUR', document_count: 9, is_base: false, has_rate: false, rate_to_base: null, as_of: null },
  { code: 'GBP', document_count: 4, is_base: false, has_rate: true, rate_to_base: '1.27000000', as_of: '2026-07-01' },
  { code: 'USD', document_count: 2, is_base: true, has_rate: true, rate_to_base: '1', as_of: null },
]

function seedDefaults(): void {
  vi.mocked(getSystemInfo).mockResolvedValue(systemInfo)
  vi.mocked(getArchitecture).mockResolvedValue(architecture)
  vi.mocked(getCoverage).mockResolvedValue(coverageAvailable)
  vi.mocked(listUsers).mockResolvedValue(structuredClone(userList))
  vi.mocked(listRecipients).mockResolvedValue(structuredClone(recipientList))
  vi.mocked(listSenders).mockResolvedValue(structuredClone(senderList))
  vi.mocked(listKinds).mockResolvedValue(structuredClone(kindList))
  vi.mocked(listCurrencies).mockResolvedValue(structuredClone(currencyList))
  vi.mocked(listFxRates).mockResolvedValue(structuredClone(fxList))
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

  it('creates a recipient and refreshes the list and taxonomy cache', async () => {
    vi.mocked(createRecipient).mockResolvedValue({ id: 13, name: 'Dave' })
    const wrapper = await openMetadataTab()

    await wrapper.find('[data-testid="recipient-create-input"]').setValue('Dave')
    await wrapper.find('[data-testid="recipient-create-button"]').trigger('click')
    await flushPromises()

    expect(createRecipient).toHaveBeenCalledWith('Dave')
    // listRecipients: once on tab open, once after the successful create.
    expect(listRecipients).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  // --- Metadata tab: senders ------------------------------------------------

  it('loads senders on first opening the Metadata tab, with document counts', async () => {
    const wrapper = await openMetadataTab()

    expect(listSenders).toHaveBeenCalledTimes(1)
    const row = wrapper.find('[data-testid="sender-row-21"]')
    expect(row.text()).toContain('Globex')
    expect(row.text()).toContain('4 docs')
  })

  it('renames a sender and refreshes the list and taxonomy cache', async () => {
    vi.mocked(renameSender).mockResolvedValue({ id: 20, name: 'Acme Corp' })
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="sender-row-20"]')
    await row.find('[data-testid="sender-rename"]').trigger('click')
    await wrapper.find('#sender-rename-input-20').setValue('Acme Corp')
    await wrapper.find('[data-testid="sender-rename-save"]').trigger('click')
    await flushPromises()

    expect(renameSender).toHaveBeenCalledWith(20, 'Acme Corp', false)
    expect(listSenders).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('shows a merge prompt on a sender 409 collision and merges on confirm', async () => {
    vi.mocked(renameSender)
      .mockRejectedValueOnce(
        new ApiError(409, 'name in use', {
          detail: 'name in use',
          target_id: 21,
          target_name: 'Globex',
          target_document_count: 4,
        }),
      )
      .mockResolvedValueOnce({ id: 21, name: 'Globex' })
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="sender-row-20"]')
    await row.find('[data-testid="sender-rename"]').trigger('click')
    await wrapper.find('#sender-rename-input-20').setValue('Globex')
    await wrapper.find('[data-testid="sender-rename-save"]').trigger('click')
    await flushPromises()

    const warning = wrapper.find('[data-testid="sender-merge-warning"]')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toContain('Globex')
    expect(warning.text()).toContain('4 documents')

    await wrapper.find('[data-testid="sender-merge-confirm"]').trigger('click')
    await flushPromises()

    expect(renameSender).toHaveBeenNthCalledWith(1, 20, 'Globex', false)
    expect(renameSender).toHaveBeenNthCalledWith(2, 20, 'Globex', true)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('deletes a zero-document sender after an inline confirm', async () => {
    vi.mocked(deleteSender).mockResolvedValue()
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="sender-row-20"]')
    await row.find('[data-testid="sender-delete"]').trigger('click')
    expect(wrapper.find('[data-testid="sender-reassign-select"]').exists()).toBe(false)
    await wrapper.find('[data-testid="sender-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteSender).toHaveBeenCalledWith(20)
    expect(listSenders).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('deletes an in-use sender by reassigning its documents', async () => {
    vi.mocked(deleteSender).mockResolvedValue()
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="sender-row-21"]')
    await row.find('[data-testid="sender-delete"]').trigger('click')
    const select = wrapper.find('[data-testid="sender-reassign-select"]')
    expect(select.exists()).toBe(true)
    await wrapper.find('#sender-reassign-21').setValue('22')
    await wrapper.find('[data-testid="sender-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteSender).toHaveBeenCalledWith(21, 22)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('creates a sender and refreshes the list', async () => {
    vi.mocked(createSender).mockResolvedValue({ id: 23, name: 'Umbrella' })
    const wrapper = await openMetadataTab()

    await wrapper.find('[data-testid="sender-create-input"]').setValue('Umbrella')
    await wrapper.find('[data-testid="sender-create-button"]').trigger('click')
    await flushPromises()

    expect(createSender).toHaveBeenCalledWith('Umbrella')
    expect(listSenders).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  // --- Metadata tab: kinds --------------------------------------------------

  it('loads kinds on first opening the Metadata tab, with document counts', async () => {
    const wrapper = await openMetadataTab()

    expect(listKinds).toHaveBeenCalledTimes(1)
    const row = wrapper.find('[data-testid="kind-row-receipt"]')
    expect(row.text()).toContain('Receipt')
    expect(row.text()).toContain('5 docs')
  })

  it('renames a kind (name-only) and refreshes the list and taxonomy cache', async () => {
    vi.mocked(renameKind).mockResolvedValue({ slug: 'invoice', name: 'Tax invoice' })
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="kind-row-invoice"]')
    await row.find('[data-testid="kind-rename"]').trigger('click')
    await wrapper.find('#kind-rename-input-invoice').setValue('Tax invoice')
    await wrapper.find('[data-testid="kind-rename-save"]').trigger('click')
    await flushPromises()

    expect(renameKind).toHaveBeenCalledWith('invoice', 'Tax invoice')
    expect(listKinds).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('surfaces a kind rename 409 as a row error (no merge prompt)', async () => {
    vi.mocked(renameKind).mockRejectedValue(
      new ApiError(409, 'a kind named Receipt already exists', {
        detail: 'a kind named Receipt already exists',
        target_slug: 'receipt',
        target_name: 'Receipt',
      }),
    )
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="kind-row-invoice"]')
    await row.find('[data-testid="kind-rename"]').trigger('click')
    await wrapper.find('#kind-rename-input-invoice').setValue('Receipt')
    await wrapper.find('[data-testid="kind-rename-save"]').trigger('click')
    await flushPromises()

    // No merge UI for kinds — the collision is a hard row error.
    expect(wrapper.find('[data-testid="kind-merge-warning"]').exists()).toBe(false)
    const err = wrapper.find('[data-testid="kind-error-invoice"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('already exists')
  })

  it('deletes an in-use kind by reassigning its documents by slug', async () => {
    vi.mocked(deleteKind).mockResolvedValue()
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="kind-row-receipt"]')
    await row.find('[data-testid="kind-delete"]').trigger('click')
    const select = wrapper.find('[data-testid="kind-reassign-select"]')
    expect(select.exists()).toBe(true)
    await wrapper.find('#kind-reassign-receipt').setValue('letter')
    await wrapper.find('[data-testid="kind-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteKind).toHaveBeenCalledWith('receipt', 'letter')
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('deletes a zero-document kind after an inline confirm', async () => {
    vi.mocked(deleteKind).mockResolvedValue()
    const wrapper = await openMetadataTab()

    const row = wrapper.find('[data-testid="kind-row-invoice"]')
    await row.find('[data-testid="kind-delete"]').trigger('click')
    expect(wrapper.find('[data-testid="kind-reassign-select"]').exists()).toBe(false)
    await wrapper.find('[data-testid="kind-delete-confirm"]').trigger('click')
    await flushPromises()

    expect(deleteKind).toHaveBeenCalledWith('invoice')
    expect(listKinds).toHaveBeenCalledTimes(2)
  })

  it('creates a kind and surfaces a 409 near-duplicate error', async () => {
    vi.mocked(createKind).mockRejectedValue(
      new ApiError(409, 'similar to an existing kind', {
        detail: 'similar to an existing kind',
        existing_slug: 'receipt',
        existing_name: 'Receipt',
      }),
    )
    const wrapper = await openMetadataTab()

    await wrapper.find('[data-testid="kind-create-input"]').setValue('Reciept')
    await wrapper.find('[data-testid="kind-create-button"]').trigger('click')
    await flushPromises()

    expect(createKind).toHaveBeenCalledWith('Reciept')
    const err = wrapper.find('[data-testid="kind-create-error"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('similar to an existing kind')
  })

  // --- Metadata tab: currencies ---------------------------------------------

  it('lists currencies in use on opening the Metadata tab, with counts', async () => {
    const wrapper = await openMetadataTab()
    expect(listCurrencies).toHaveBeenCalledTimes(1)
    const row = wrapper.find('[data-testid="currency-row-EUR"]')
    expect(row.text()).toContain('EUR')
    expect(row.text()).toContain('9')
  })

  it('normalises a currency after a confirm step and surfaces the FX warning', async () => {
    vi.mocked(normalizeCurrency).mockResolvedValue({
      from_code: 'EUR',
      to_code: 'GBP',
      counts: { documents: 9 },
      fx_rate_missing: true,
    })
    const wrapper = await openMetadataTab()

    await wrapper.find('#currency-normalize-from').setValue('EUR')
    await wrapper.find('[data-testid="currency-normalize-to"]').setValue('gbp')
    await wrapper.find('[data-testid="currency-normalize-button"]').trigger('click')
    await flushPromises()

    // A confirm step appears first; the request is not sent yet.
    expect(wrapper.find('[data-testid="currency-normalize-confirm-box"]').exists()).toBe(true)
    expect(normalizeCurrency).not.toHaveBeenCalled()

    await wrapper.find('[data-testid="currency-normalize-confirm"]').trigger('click')
    await flushPromises()

    expect(normalizeCurrency).toHaveBeenCalledWith('EUR', 'gbp')
    expect(wrapper.find('[data-testid="currency-normalize-result"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="currency-fx-warning"]').exists()).toBe(true)
    // The list reloads: once on open, once after the rename.
    expect(listCurrencies).toHaveBeenCalledTimes(2)
  })

  it('refuses on an override collision and lists the conflicts (nothing reloaded)', async () => {
    vi.mocked(normalizeCurrency).mockRejectedValue(
      new ApiError(409, 'would collide with overrides', {
        detail: 'would collide with overrides',
        conflicts: [{ table: 'series_meta_overrides', sender_id: 3, kind_id: 5 }],
      }),
    )
    const wrapper = await openMetadataTab()

    await wrapper.find('#currency-normalize-from').setValue('EUR')
    await wrapper.find('[data-testid="currency-normalize-to"]').setValue('USD')
    await wrapper.find('[data-testid="currency-normalize-button"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="currency-normalize-confirm"]').trigger('click')
    await flushPromises()

    const conflict = wrapper.find('[data-testid="currency-conflict"]')
    expect(conflict.exists()).toBe(true)
    expect(conflict.text()).toContain('series_meta_overrides')
    // A refused rename must not have reloaded the list (still just the open call).
    expect(listCurrencies).toHaveBeenCalledTimes(1)
  })

  // --- Metadata tab: FX rates -----------------------------------------------

  it('lists FX-rate status per currency on opening the Metadata tab', async () => {
    const wrapper = await openMetadataTab()
    expect(listFxRates).toHaveBeenCalledTimes(1)
    // USD is the base; GBP has a rate; EUR has none.
    expect(wrapper.find('[data-testid="fx-status-USD"]').text()).toContain('Base')
    expect(wrapper.find('[data-testid="fx-status-GBP"]').text()).toContain('1.27')
    expect(wrapper.find('[data-testid="fx-status-EUR"]').text()).toContain('No rate')
    // Only the missing code offers a manual-entry toggle.
    expect(wrapper.find('[data-testid="fx-manual-toggle-EUR"]').exists()).toBe(true)
  })

  it('fetches a live rate for a code that lacks one and reloads the list', async () => {
    vi.mocked(seedFxRate).mockResolvedValue({
      currency: 'EUR',
      as_of: '2026-07-03',
      rate_to_base: '1.09000000',
    })
    const wrapper = await openMetadataTab()

    await wrapper.find('[data-testid="fx-fetch-EUR"]').trigger('click')
    await flushPromises()

    expect(seedFxRate).toHaveBeenCalledWith({ currency: 'EUR', source: 'live' })
    // Reloaded: once on open, once after seeding.
    expect(listFxRates).toHaveBeenCalledTimes(2)
  })

  it('falls back to manual entry when the live fetch fails', async () => {
    vi.mocked(seedFxRate).mockRejectedValueOnce(
      new ApiError(502, 'the live FX provider does not list EUR'),
    )
    const wrapper = await openMetadataTab()

    await wrapper.find('[data-testid="fx-fetch-EUR"]').trigger('click')
    await flushPromises()

    // The error is shown and the manual form is revealed.
    expect(wrapper.find('[data-testid="fx-row-error-EUR"]').text()).toContain('does not list EUR')
    expect(wrapper.find('[data-testid="fx-manual-input-EUR"]').exists()).toBe(true)

    // Entering a manual rate seeds it.
    vi.mocked(seedFxRate).mockResolvedValueOnce({
      currency: 'EUR',
      as_of: '2026-07-03',
      rate_to_base: '1.10000000',
    })
    await wrapper.find('[data-testid="fx-manual-input-EUR"]').setValue('1.10')
    await wrapper.find('[data-testid="fx-manual-form-EUR"]').trigger('submit')
    await flushPromises()

    expect(seedFxRate).toHaveBeenLastCalledWith({
      currency: 'EUR',
      source: 'manual',
      rateToBase: '1.10',
    })
  })

  it('rejects a non-positive manual rate without calling the API', async () => {
    const wrapper = await openMetadataTab()
    await wrapper.find('[data-testid="fx-manual-toggle-EUR"]').trigger('click')
    await wrapper.find('[data-testid="fx-manual-input-EUR"]').setValue('0')
    await wrapper.find('[data-testid="fx-manual-form-EUR"]').trigger('submit')
    await flushPromises()
    expect(seedFxRate).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="fx-row-error-EUR"]').text()).toContain('positive')
  })
})
