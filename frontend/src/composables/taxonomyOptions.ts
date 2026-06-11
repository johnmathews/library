/**
 * Shared, lazily-fetched taxonomy options (kinds / senders / tags).
 *
 * The search modal and the list view's filter summary both need the
 * taxonomy lists; this module fetches each endpoint once per page load
 * (on first `ensureLoaded()` call) and caches the results app-wide.
 * Best-effort: a failed endpoint leaves its list empty and callers
 * degrade gracefully (the selects just offer "All …", the filter
 * summary falls back to raw slugs/ids).
 */

import { ref, type Ref } from 'vue'
import {
  listKinds,
  listSenders,
  listTags,
  type KindOption,
  type SenderOption,
  type TagOption,
} from '@/api/taxonomy'

const kinds = ref<KindOption[]>([])
const senders = ref<SenderOption[]>([])
const tags = ref<TagOption[]>([])
let loadPromise: Promise<void> | null = null

export interface TaxonomyOptions {
  kinds: Ref<KindOption[]>
  senders: Ref<SenderOption[]>
  tags: Ref<TagOption[]>
  /** Fetch the three lists on first call; later calls reuse the cache. */
  ensureLoaded: () => Promise<void>
}

export function useTaxonomyOptions(): TaxonomyOptions {
  function ensureLoaded(): Promise<void> {
    loadPromise ??= Promise.allSettled([listKinds(), listSenders(), listTags()]).then(
      ([kindResult, senderResult, tagResult]) => {
        if (kindResult.status === 'fulfilled') kinds.value = kindResult.value
        if (senderResult.status === 'fulfilled') senders.value = senderResult.value
        if (tagResult.status === 'fulfilled') tags.value = tagResult.value
      },
    )
    return loadPromise
  }

  return { kinds, senders, tags, ensureLoaded }
}

/** Test-only: drop the module-level cache between specs. */
export function resetTaxonomyOptionsForTests(): void {
  kinds.value = []
  senders.value = []
  tags.value = []
  loadPromise = null
}
