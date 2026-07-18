import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/matters', () => ({
  listMatters: vi.fn(),
  createMatter: vi.fn(),
  updateMatter: vi.fn(),
  deleteMatter: vi.fn(),
}))
vi.mock('@/composables/taxonomyOptions', () => ({
  refreshTaxonomyOptions: vi.fn().mockResolvedValue(undefined),
}))

import { createMatter, deleteMatter, listMatters, updateMatter } from '@/api/matters'
import MattersListView from '../MattersListView.vue'
import { useAuthStore } from '@/stores/auth'

const MATTERS = [
  { slug: 'acme-merger', name: 'Acme merger', hint: 'Anything about the Acme deal', document_count: 3, archived: false },
  { slug: 'audit-2026', name: 'Audit 2026', hint: null, document_count: 1, archived: false },
]

function setAdmin(isAdmin: boolean): void {
  const auth = useAuthStore()
  auth.user = {
    id: 1,
    username: 'root',
    display_name: 'Root',
    is_admin: isAdmin,
    preferences: { dashboard_fields: [] },
  }
}

function mountView(isAdmin = true) {
  setAdmin(isAdmin)
  return mount(MattersListView, { global: { stubs: { RouterLink: RouterLinkStub } } })
}

describe('MattersListView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(listMatters).mockResolvedValue(structuredClone(MATTERS))
    vi.mocked(createMatter).mockResolvedValue(MATTERS[0]!)
    vi.mocked(updateMatter).mockResolvedValue(MATTERS[0]!)
    vi.mocked(deleteMatter).mockResolvedValue(undefined)
  })
  afterEach(() => vi.clearAllMocks())

  it('lists matters with counts, hints and admin actions', async () => {
    const w = mountView(true)
    await flushPromises()
    expect(w.get('[data-testid="matter-count-acme-merger"]').text()).toContain('3 documents')
    expect(w.get('[data-testid="matter-count-audit-2026"]').text()).toContain('1 document')
    expect(w.get('[data-testid="matter-hint-acme-merger"]').text()).toContain('Acme deal')
    expect(w.find('[data-testid="matter-new-button"]').exists()).toBe(true)
    expect(w.find('[data-testid="matter-edit-acme-merger"]').exists()).toBe(true)
  })

  it('links each matter name to the matter-filtered dashboard', async () => {
    const w = mountView(true)
    await flushPromises()
    const link = w
      .findAllComponents(RouterLinkStub)
      .find((l) => l.props('to') === '/?matter=acme-merger')
    expect(link).toBeTruthy()
    expect(link!.text()).toBe('Acme merger')
  })

  it('hides all mutating controls for non-admins', async () => {
    const w = mountView(false)
    await flushPromises()
    expect(w.find('[data-testid="matter-new-button"]').exists()).toBe(false)
    expect(w.find('[data-testid="matter-edit-acme-merger"]').exists()).toBe(false)
    expect(w.find('[data-testid="matter-delete-acme-merger"]').exists()).toBe(false)
    // The list itself is still readable.
    expect(w.get('[data-testid="matter-link-acme-merger"]').text()).toBe('Acme merger')
  })

  it('creates a matter with name and hint', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="matter-new-button"]').trigger('click')
    await w.get('[data-testid="matter-create-name"]').setValue('Litigation')
    await w.get('[data-testid="matter-create-hint"]').setValue('Court filings and correspondence')
    await w.get('[data-testid="matter-create-form"]').trigger('submit')
    await flushPromises()
    expect(createMatter).toHaveBeenCalledWith('Litigation', 'Court filings and correspondence')
    expect(listMatters).toHaveBeenCalledTimes(2) // initial + reload
  })

  it('renames a matter and edits its hint via inline edit', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="matter-edit-acme-merger"]').trigger('click')
    await w.get('[data-testid="matter-edit-name-acme-merger"]').setValue('Acme acquisition')
    await w.get('[data-testid="matter-edit-hint-acme-merger"]').setValue('Updated hint')
    await w.get('[data-testid="matter-edit-save-acme-merger"]').trigger('click')
    await flushPromises()
    expect(updateMatter).toHaveBeenCalledWith('acme-merger', {
      name: 'Acme acquisition',
      hint: 'Updated hint',
    })
  })

  it('archives a matter', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="matter-archive-acme-merger"]').trigger('click')
    await flushPromises()
    expect(updateMatter).toHaveBeenCalledWith('acme-merger', { archived: true })
  })

  it('deletes a matter only after a confirm step', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="matter-delete-acme-merger"]').trigger('click')
    // Not deleted on the first click — a confirm button appears instead.
    expect(deleteMatter).not.toHaveBeenCalled()
    expect(w.find('[data-testid="matter-delete-confirm-acme-merger"]').exists()).toBe(true)
    await w.get('[data-testid="matter-delete-confirm-acme-merger"]').trigger('click')
    await flushPromises()
    expect(deleteMatter).toHaveBeenCalledWith('acme-merger')
  })

  it('reloads with archived when the toggle is enabled', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="matter-archived-toggle"]').setValue(true)
    await flushPromises()
    expect(listMatters).toHaveBeenLastCalledWith(true)
  })
})
