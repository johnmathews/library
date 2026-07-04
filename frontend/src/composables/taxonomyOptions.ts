/**
 * Shared, lazily-fetched taxonomy options (kinds / senders / tags / projects).
 *
 * The search modal and the list view's filter summary both need the
 * taxonomy lists; this store fetches each endpoint once per page load
 * (on first `ensureLoaded()` call) and caches the results app-wide.
 * Best-effort: a failed endpoint leaves its list empty and callers
 * degrade gracefully (the selects just offer "All …", the filter
 * summary falls back to raw slugs/ids).
 *
 * Backed by a Pinia store so the cache is a single app-wide singleton with the
 * rest of the app's shared state. The `useTaxonomyOptions()` /
 * `refreshTaxonomyOptions()` helpers below are thin wrappers over the store so
 * call sites keep their existing shape.
 */

import { defineStore, storeToRefs } from 'pinia'
import { ref, type Ref } from 'vue'
import {
  listKinds,
  listSenders,
  listRecipients,
  listTags,
  type KindOption,
  type SenderOption,
  type RecipientOption,
  type TagOption,
} from '@/api/taxonomy'
import { listProjects, type ProjectOption } from '@/api/projects'

export const useTaxonomyOptionsStore = defineStore('taxonomyOptions', () => {
  const kinds = ref<KindOption[]>([])
  const senders = ref<SenderOption[]>([])
  const recipients = ref<RecipientOption[]>([])
  const tags = ref<TagOption[]>([])
  const projects = ref<ProjectOption[]>([])
  // Private (not returned): the in-flight/completed fetch, so `ensureLoaded`
  // fetches each endpoint at most once until `refresh()` invalidates it.
  let loadPromise: Promise<void> | null = null

  /** Fetch the lists on first call; later calls reuse the cache. */
  function ensureLoaded(): Promise<void> {
    loadPromise ??= Promise.allSettled([
      listKinds(),
      listSenders(),
      listRecipients(),
      listTags(),
      listProjects(),
    ]).then(([kindResult, senderResult, recipientResult, tagResult, projectResult]) => {
      if (kindResult.status === 'fulfilled') kinds.value = kindResult.value
      if (senderResult.status === 'fulfilled') senders.value = senderResult.value
      if (recipientResult.status === 'fulfilled') recipients.value = recipientResult.value
      if (tagResult.status === 'fulfilled') tags.value = tagResult.value
      if (projectResult.status === 'fulfilled') projects.value = projectResult.value
    })
    return loadPromise
  }

  /** Invalidate the cache and re-fetch the taxonomy lists now. */
  function refresh(): Promise<void> {
    loadPromise = null
    return ensureLoaded()
  }

  return { kinds, senders, recipients, tags, projects, ensureLoaded, refresh }
})

export interface TaxonomyOptions {
  kinds: Ref<KindOption[]>
  senders: Ref<SenderOption[]>
  recipients: Ref<RecipientOption[]>
  tags: Ref<TagOption[]>
  projects: Ref<ProjectOption[]>
  /** Fetch the lists on first call; later calls reuse the cache. */
  ensureLoaded: () => Promise<void>
}

/**
 * Thin wrapper over the taxonomy-options store: exposes the shared lists as refs
 * (so `kinds.value` etc. keep working at call sites) plus `ensureLoaded()`.
 */
export function useTaxonomyOptions(): TaxonomyOptions {
  const store = useTaxonomyOptionsStore()
  const { kinds, senders, recipients, tags, projects } = storeToRefs(store)
  return { kinds, senders, recipients, tags, projects, ensureLoaded: store.ensureLoaded }
}

/**
 * Invalidate the cache and re-fetch the taxonomy lists now, updating the shared
 * store in place. Call after creating a taxonomy entry inline (e.g. a new
 * recipient on the detail page) so every consumer of the shared cache — the list
 * view's filter bar, the search modal — picks it up without a reload.
 * Best-effort: a failed endpoint leaves its list unchanged.
 */
export function refreshTaxonomyOptions(): Promise<void> {
  return useTaxonomyOptionsStore().refresh()
}
