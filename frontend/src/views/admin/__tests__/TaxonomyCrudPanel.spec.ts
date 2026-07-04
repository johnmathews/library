import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'

// Only the shared taxonomy cache is mocked — the descriptor's own
// list/create/rename/remove are vi.fn() stubs supplied per test, and ApiError
// is the real class so `err instanceof ApiError` narrows correctly.
vi.mock('@/composables/taxonomyOptions', () => ({
  refreshTaxonomyOptions: vi.fn().mockResolvedValue(undefined),
}))

import { refreshTaxonomyOptions } from '@/composables/taxonomyOptions'
import { ApiError } from '@/api/client'
import TaxonomyCrudPanel from '../TaxonomyCrudPanel.vue'
import type { TaxonomyDescriptor, TaxonomyRow } from '../taxonomyCrud'

/** An id-keyed row (senders/recipients). */
interface SenderRow extends TaxonomyRow {
  id: number
}
/** A slug-keyed row (kinds). */
interface KindRow extends TaxonomyRow {
  slug: string
}

const SENDER_ROWS: SenderRow[] = [
  { id: 20, name: 'Acme', document_count: 0 },
  { id: 21, name: 'Globex', document_count: 4 },
  { id: 22, name: 'Initech', document_count: 2 },
]

const KIND_ROWS: KindRow[] = [
  { slug: 'invoice', name: 'Invoice', document_count: 0 },
  { slug: 'receipt', name: 'Receipt', document_count: 5 },
  { slug: 'letter', name: 'Letter', document_count: 2 },
]

/** A hasMerge=true, id-keyed "sender-like" descriptor backed by fresh stubs. */
function makeSenderDescriptor() {
  const list = vi.fn().mockResolvedValue(structuredClone(SENDER_ROWS))
  const create = vi.fn()
  const rename = vi.fn()
  const remove = vi.fn()
  const descriptor: TaxonomyDescriptor<SenderRow> = {
    testid: 'sender',
    heading: 'Senders',
    addLabel: 'Add sender',
    renameLabel: 'Sender name',
    clearText: 'None (clear sender)',
    noun: 'sender',
    hasMerge: true,
    keyOf: (row) => row.id,
    list,
    create,
    rename,
    remove,
    parseReassign: (value) => (value === '' ? null : Number(value)),
    readMergeBody: (body) => ({
      target_id: Number(body.target_id),
      target_name: String(body.target_name),
      target_document_count: Number(body.target_document_count),
    }),
  }
  return { descriptor, list, create, rename, remove }
}

/** A hasMerge=false, slug-keyed "kind-like" descriptor backed by fresh stubs. */
function makeKindDescriptor() {
  const list = vi.fn().mockResolvedValue(structuredClone(KIND_ROWS))
  const create = vi.fn()
  const rename = vi.fn()
  const remove = vi.fn()
  const descriptor: TaxonomyDescriptor<KindRow> = {
    testid: 'kind',
    heading: 'Kinds',
    addLabel: 'Add kind',
    renameLabel: 'Kind name',
    clearText: 'None (clear kind)',
    noun: 'kind',
    hasMerge: false,
    keyOf: (row) => row.slug,
    list,
    create,
    rename,
    remove,
    parseReassign: (value) => (value === '' ? null : value),
  }
  return { descriptor, list, create, rename, remove }
}

// The panel is a generic SFC; its `descriptor` prop resolves to the default
// `TaxonomyDescriptor<TaxonomyRow>` at the mount boundary. Row-specific
// descriptors are invariant against that, so cast when handing them to `mount`.
type PanelDescriptor = TaxonomyDescriptor<TaxonomyRow>
const asProp = (descriptor: TaxonomyDescriptor<SenderRow> | TaxonomyDescriptor<KindRow>) =>
  descriptor as unknown as PanelDescriptor

/** Mount inactive, then flip active true (the first Metadata-open) and settle. */
async function openPanel(
  descriptor: TaxonomyDescriptor<SenderRow> | TaxonomyDescriptor<KindRow>,
): Promise<VueWrapper> {
  const wrapper = mount(TaxonomyCrudPanel, { props: { descriptor: asProp(descriptor), active: false } })
  await wrapper.setProps({ active: true })
  await flushPromises()
  return wrapper
}

describe('TaxonomyCrudPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads lazily: no fetch while inactive, one fetch on false→true, none on a re-true', async () => {
    const { descriptor, list } = makeSenderDescriptor()
    const wrapper = mount(TaxonomyCrudPanel, { props: { descriptor: asProp(descriptor), active: false } })
    await flushPromises()
    // Nothing fetches while the tab is closed.
    expect(list).not.toHaveBeenCalled()

    await wrapper.setProps({ active: true })
    await flushPromises()
    expect(list).toHaveBeenCalledTimes(1)

    // Toggling closed then open again must NOT re-fetch (already loaded).
    await wrapper.setProps({ active: false })
    await wrapper.setProps({ active: true })
    await flushPromises()
    expect(list).toHaveBeenCalledTimes(1)
  })

  it('creates via descriptor.create, then reloads and refreshes the taxonomy cache once', async () => {
    const { descriptor, list, create } = makeSenderDescriptor()
    create.mockResolvedValue({ id: 23, name: 'Umbrella', document_count: 0 })
    const wrapper = await openPanel(descriptor)

    await wrapper.find('#sender-create-input').setValue('Umbrella')
    await wrapper.find('[data-testid="sender-create-button"]').trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledWith('Umbrella')
    // list: once on open, once after the successful create.
    expect(list).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('renames (happy path) via descriptor.rename(key, name, false), reloading and refreshing once', async () => {
    const { descriptor, list, rename } = makeSenderDescriptor()
    rename.mockResolvedValue({ id: 20, name: 'Acme Corp', document_count: 0 })
    const wrapper = await openPanel(descriptor)

    const row = wrapper.find('[data-testid="sender-row-20"]')
    await row.find('[data-testid="sender-rename"]').trigger('click')
    await wrapper.find('#sender-rename-input-20').setValue('Acme Corp')
    await wrapper.find('[data-testid="sender-rename-save"]').trigger('click')
    await flushPromises()

    expect(rename).toHaveBeenCalledWith(20, 'Acme Corp', false)
    expect(list).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('surfaces a merge prompt on a 409 (hasMerge=true) and merges on confirm', async () => {
    const { descriptor, rename } = makeSenderDescriptor()
    rename
      .mockRejectedValueOnce(
        new ApiError(409, 'name in use', {
          detail: 'name in use',
          target_id: 21,
          target_name: 'Globex',
          target_document_count: 4,
        }),
      )
      .mockResolvedValueOnce({ id: 21, name: 'Globex', document_count: 4 })
    const wrapper = await openPanel(descriptor)

    const row = wrapper.find('[data-testid="sender-row-20"]')
    await row.find('[data-testid="sender-rename"]').trigger('click')
    await wrapper.find('#sender-rename-input-20').setValue('Globex')
    await wrapper.find('[data-testid="sender-rename-save"]').trigger('click')
    await flushPromises()

    // The collision reveals the merge warning (read via readMergeBody), not an error.
    const warning = wrapper.find('[data-testid="sender-merge-warning"]')
    expect(warning.exists()).toBe(true)
    expect(warning.text()).toContain('Globex')
    expect(warning.text()).toContain('4 documents')
    expect(wrapper.find('[data-testid="sender-error-20"]').exists()).toBe(false)

    await wrapper.find('[data-testid="sender-merge-confirm"]').trigger('click')
    await flushPromises()

    expect(rename).toHaveBeenNthCalledWith(1, 20, 'Globex', false)
    expect(rename).toHaveBeenNthCalledWith(2, 20, 'Globex', true)
    // Only the successful (merge) mutation refreshes the cache.
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('surfaces a 409 rename as a row error (hasMerge=false) with no merge prompt', async () => {
    const { descriptor, rename } = makeKindDescriptor()
    rename.mockRejectedValue(
      new ApiError(409, 'a kind named Receipt already exists', {
        detail: 'a kind named Receipt already exists',
      }),
    )
    const wrapper = await openPanel(descriptor)

    const row = wrapper.find('[data-testid="kind-row-invoice"]')
    await row.find('[data-testid="kind-rename"]').trigger('click')
    await wrapper.find('#kind-rename-input-invoice').setValue('Receipt')
    await wrapper.find('[data-testid="kind-rename-save"]').trigger('click')
    await flushPromises()

    // Kinds never merge — a collision is a hard row error, no merge UI.
    expect(wrapper.find('[data-testid="kind-merge-warning"]').exists()).toBe(false)
    const err = wrapper.find('[data-testid="kind-error-invoice"]')
    expect(err.exists()).toBe(true)
    expect(err.text()).toContain('already exists')
    expect(refreshTaxonomyOptions).not.toHaveBeenCalled()
  })

  it('deletes a zero-document row with a single argument (no reassign)', async () => {
    const { descriptor, list, remove } = makeSenderDescriptor()
    remove.mockResolvedValue(undefined)
    const wrapper = await openPanel(descriptor)

    const row = wrapper.find('[data-testid="sender-row-20"]')
    await row.find('[data-testid="sender-delete"]').trigger('click')
    // A zero-document row offers no reassign picker.
    expect(wrapper.find('[data-testid="sender-reassign-select"]').exists()).toBe(false)
    await wrapper.find('[data-testid="sender-delete-confirm"]').trigger('click')
    await flushPromises()

    // Called with exactly one arg so the descriptor deletes outright (no reassign).
    expect(remove.mock.calls[0]).toEqual([20])
    expect(list).toHaveBeenCalledTimes(2)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('deletes an in-use id-keyed row via remove(key, Number(reassign))', async () => {
    const { descriptor, remove } = makeSenderDescriptor()
    remove.mockResolvedValue(undefined)
    const wrapper = await openPanel(descriptor)

    const row = wrapper.find('[data-testid="sender-row-21"]')
    await row.find('[data-testid="sender-delete"]').trigger('click')
    expect(wrapper.find('[data-testid="sender-reassign-select"]').exists()).toBe(true)
    await wrapper.find('#sender-reassign-21').setValue('22')
    await wrapper.find('[data-testid="sender-delete-confirm"]').trigger('click')
    await flushPromises()

    // parseReassign coerces the id-keyed select value to a Number.
    expect(remove).toHaveBeenCalledWith(21, 22)
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })

  it('deletes an in-use slug-keyed row via remove(key, rawString(reassign))', async () => {
    const { descriptor, remove } = makeKindDescriptor()
    remove.mockResolvedValue(undefined)
    const wrapper = await openPanel(descriptor)

    const row = wrapper.find('[data-testid="kind-row-receipt"]')
    await row.find('[data-testid="kind-delete"]').trigger('click')
    expect(wrapper.find('[data-testid="kind-reassign-select"]').exists()).toBe(true)
    await wrapper.find('#kind-reassign-receipt').setValue('letter')
    await wrapper.find('[data-testid="kind-delete-confirm"]').trigger('click')
    await flushPromises()

    // parseReassign passes the slug through untouched (raw string).
    expect(remove).toHaveBeenCalledWith('receipt', 'letter')
    expect(refreshTaxonomyOptions).toHaveBeenCalledTimes(1)
  })
})
