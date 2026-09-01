/**
 * The controlled facet vocabulary, fetched once and shared.
 *
 * The reason this exists is not the app-wide request count — it is that two
 * consumers can appear on **one screen**. The spending workspace's drill panel
 * fetches the vocabulary for its label editor, and the rule editor needs it for
 * its clause rows; without a shared cache that is two independent snapshots of
 * a mutable list. If a value is merged or deleted while the page is open, the
 * two disagree, and "this value no longer exists" is exactly the state the rule
 * editor is built to show and repair. One fetch, one snapshot.
 *
 * Modelled on `taxonomyOptions.ts`, deliberately: same Pinia-backed singleton,
 * same `ensureLoaded()` promise latch, same best-effort failure. A second
 * caching idiom would be a worse outcome than a second fetch.
 *
 * Best-effort by design: a failed fetch leaves the list empty rather than
 * throwing, and callers degrade to raw keys. The rule editor is built to render
 * a rule it cannot label, so an empty vocabulary costs labels and pickers — not
 * the ability to see what the rule says.
 */

import { defineStore, storeToRefs } from 'pinia'
import { ref, type Ref } from 'vue'
import { fetchFacets, type FacetRef } from '@/api/facets'

export const useFacetVocabularyStore = defineStore('facetVocabulary', () => {
  const facets = ref<FacetRef[]>([])
  // Private (not returned): the in-flight/completed fetch, so concurrent
  // `ensureLoaded()` calls from two components mounting together share one
  // request rather than racing two.
  let loadPromise: Promise<void> | null = null

  /** Fetch the vocabulary on first call; later calls reuse the cache. */
  function ensureLoaded(): Promise<void> {
    loadPromise ??= fetchFacets()
      .then((loaded) => {
        facets.value = loaded
      })
      .catch(() => {
        // Leave the list empty; callers render raw keys.
      })
    return loadPromise
  }

  /** Invalidate the cache and re-fetch now — after a vocabulary edit. */
  function refresh(): Promise<void> {
    loadPromise = null
    return ensureLoaded()
  }

  return { facets, ensureLoaded, refresh }
})

export interface FacetVocabulary {
  facets: Ref<FacetRef[]>
  /** Fetch the vocabulary on first call; later calls reuse the cache. */
  ensureLoaded: () => Promise<void>
  refresh: () => Promise<void>
}

/** Thin wrapper over the store, matching `useTaxonomyOptions()`'s shape. */
export function useFacetVocabulary(): FacetVocabulary {
  const store = useFacetVocabularyStore()
  const { facets } = storeToRefs(store)
  return { facets, ensureLoaded: store.ensureLoaded, refresh: store.refresh }
}
