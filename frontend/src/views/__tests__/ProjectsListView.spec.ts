import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, RouterLinkStub } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('@/api/projects', () => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
}))
vi.mock('@/composables/taxonomyOptions', () => ({
  refreshTaxonomyOptions: vi.fn().mockResolvedValue(undefined),
}))

import { createProject, deleteProject, listProjects, updateProject } from '@/api/projects'
import ProjectsListView from '../ProjectsListView.vue'
import { useAuthStore } from '@/stores/auth'

const PROJECTS = [
  { slug: 'house-purchase', name: 'House purchase', description: 'Buying a flat', document_count: 3, archived: false },
  { slug: 'taxes', name: 'Taxes', description: null, document_count: 1, archived: false },
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
  return mount(ProjectsListView, { global: { stubs: { RouterLink: RouterLinkStub } } })
}

describe('ProjectsListView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(listProjects).mockResolvedValue(structuredClone(PROJECTS))
    vi.mocked(createProject).mockResolvedValue(PROJECTS[0]!)
    vi.mocked(updateProject).mockResolvedValue(PROJECTS[0]!)
    vi.mocked(deleteProject).mockResolvedValue(undefined)
  })
  afterEach(() => vi.clearAllMocks())

  it('lists projects with counts and admin actions', async () => {
    const w = mountView(true)
    await flushPromises()
    expect(w.get('[data-testid="project-count-house-purchase"]').text()).toContain('3 documents')
    expect(w.get('[data-testid="project-count-taxes"]').text()).toContain('1 document')
    expect(w.find('[data-testid="project-new-button"]').exists()).toBe(true)
    expect(w.find('[data-testid="project-edit-house-purchase"]').exists()).toBe(true)
  })

  it('hides all mutating controls for non-admins', async () => {
    const w = mountView(false)
    await flushPromises()
    expect(w.find('[data-testid="project-new-button"]').exists()).toBe(false)
    expect(w.find('[data-testid="project-edit-house-purchase"]').exists()).toBe(false)
    expect(w.find('[data-testid="project-delete-house-purchase"]').exists()).toBe(false)
    // The list itself is still readable.
    expect(w.get('[data-testid="project-link-house-purchase"]').text()).toBe('House purchase')
  })

  it('creates a project', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="project-new-button"]').trigger('click')
    await w.get('[data-testid="project-create-name"]').setValue('Renovation')
    await w.get('[data-testid="project-create-description"]').setValue('Kitchen redo')
    await w.get('[data-testid="project-create-form"]').trigger('submit')
    await flushPromises()
    expect(createProject).toHaveBeenCalledWith('Renovation', 'Kitchen redo')
    expect(listProjects).toHaveBeenCalledTimes(2) // initial + reload
  })

  it('renames a project via inline edit', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="project-edit-house-purchase"]').trigger('click')
    await w.get('[data-testid="project-edit-name-house-purchase"]').setValue('Home purchase')
    await w.get('[data-testid="project-edit-save-house-purchase"]').trigger('click')
    await flushPromises()
    expect(updateProject).toHaveBeenCalledWith('house-purchase', {
      name: 'Home purchase',
      description: 'Buying a flat',
    })
  })

  it('archives a project', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="project-archive-house-purchase"]').trigger('click')
    await flushPromises()
    expect(updateProject).toHaveBeenCalledWith('house-purchase', { archived: true })
  })

  it('deletes a project only after a confirm step', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="project-delete-house-purchase"]').trigger('click')
    // Not deleted on the first click — a confirm button appears instead.
    expect(deleteProject).not.toHaveBeenCalled()
    expect(w.find('[data-testid="project-delete-confirm-house-purchase"]').exists()).toBe(true)
    await w.get('[data-testid="project-delete-confirm-house-purchase"]').trigger('click')
    await flushPromises()
    expect(deleteProject).toHaveBeenCalledWith('house-purchase')
  })

  it('reloads with archived when the toggle is enabled', async () => {
    const w = mountView(true)
    await flushPromises()
    await w.get('[data-testid="project-archived-toggle"]').setValue(true)
    await flushPromises()
    expect(listProjects).toHaveBeenLastCalledWith(true)
  })
})
