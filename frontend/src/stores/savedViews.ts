/**
 * Saved views store.
 *
 * Holds the caller's saved views (named homepage filter/search states) and
 * keeps them in sync with the REST API. `pinnedViews` drives the sidebar's
 * custom-dashboards section; the management page and the homepage save control
 * both read/write through the actions here so the list stays consistent across
 * every mount.
 *
 * `load()` is idempotent-ish: it fetches once and caches, so several mounts
 * (the sidebar, the save-view popover, the management page) can all call it on
 * mount without a storm of requests. Pass `force` to refetch.
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  reorderSavedViews,
  updateSavedView,
  type CreateSavedViewBody,
  type SavedView,
  type UpdateSavedViewBody,
} from '@/api/savedViews'

export const useSavedViewsStore = defineStore('savedViews', () => {
  const views = ref<SavedView[]>([])
  const loaded = ref(false)

  /** Pinned views in sort order — the sidebar's custom dashboards. */
  const pinnedViews = computed<SavedView[]>(() =>
    views.value
      .filter((view) => view.pinned)
      .sort((a, b) => a.sort_order - b.sort_order),
  )

  // Dedupe concurrent first loads (several components mount at once).
  let loadPromise: Promise<void> | null = null

  /** Fetch the caller's views once and cache them. `force` refetches. */
  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return
    if (loadPromise && !force) return loadPromise
    loadPromise = listSavedViews()
      .then((result) => {
        views.value = result
        loaded.value = true
      })
      .finally(() => {
        loadPromise = null
      })
    return loadPromise
  }

  async function create(body: CreateSavedViewBody): Promise<SavedView> {
    const created = await createSavedView(body)
    views.value = [...views.value, created]
    loaded.value = true
    return created
  }

  async function update(id: number, patch: UpdateSavedViewBody): Promise<SavedView> {
    const updated = await updateSavedView(id, patch)
    views.value = views.value.map((view) => (view.id === id ? updated : view))
    return updated
  }

  async function remove(id: number): Promise<void> {
    await deleteSavedView(id)
    views.value = views.value.filter((view) => view.id !== id)
  }

  /** Persist a new order; the server returns the reordered list (source of truth). */
  async function reorder(ids: number[]): Promise<void> {
    views.value = await reorderSavedViews(ids)
  }

  return { views, loaded, pinnedViews, load, create, update, remove, reorder }
})
