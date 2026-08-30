import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ValueMergeView from '../ValueMergeView.vue'
import { SPLIT_PALETTE } from '@/utils/splitPalette'
import { ApiError } from '@/api/client'

vi.mock('@/api/facets', () => ({ fetchFacets: vi.fn(), mergeValue: vi.fn() }))
import * as api from '@/api/facets'

const push = vi.fn()
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { facetKey: 'category', valueKey: 'alpha' } }),
  useRouter: () => ({ push }),
  RouterLink: { template: '<a><slot /></a>' },
}))

beforeEach(() => {
  push.mockClear()
  vi.mocked(api.fetchFacets).mockResolvedValue([
    {
      key: 'category', label: 'Category', ordinal: 0,
      values: [
        { key: 'alpha', label: 'Alpha', parent_id: null, aliases: ['a-one', 'shared'],
          colour: SPLIT_PALETTE[3]!.light },
        { key: 'beta', label: 'Beta', parent_id: null, aliases: ['shared'], colour: null },
        { key: 'gamma', label: 'Gamma', parent_id: null, aliases: [], colour: null },
      ],
    },
  ])
  vi.mocked(api.mergeValue).mockResolvedValue({ moved: 7 })
})

const open = async () => {
  const wrapper = mount(ValueMergeView)
  await flushPromises()
  return wrapper
}

const chooseTarget = async (wrapper: ReturnType<typeof mount>, value: string) => {
  await wrapper.find('[data-testid="merge-target"]').setValue(value)
  await flushPromises()
}

describe('ValueMergeView', () => {
  it('cannot apply before a preview has been run', async () => {
    const wrapper = await open()
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('runs a dry run when a target is chosen and shows the count', async () => {
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    expect(api.mergeValue).toHaveBeenCalledWith('category', 'alpha', 'beta', true)
    expect(wrapper.find('[data-testid="merge-diff"]').text()).toContain('7')
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('never offers the source as its own target', async () => {
    const wrapper = await open()
    const options = wrapper.findAll('[data-testid="merge-target"] option').map((o) => o.element.getAttribute('value'))
    expect(options).not.toContain('alpha')
    expect(options).toContain('beta')
    expect(options).toContain('gamma')
  })

  it('invalidates the preview when the target changes', async () => {
    // Otherwise the page shows a count computed for one target beside an Apply
    // button that merges into another — a preview that is worse than none.
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    await chooseTarget(wrapper, 'gamma')
    // Between the change and the new dry run resolving, apply must be off; and
    // the shown count must belong to the current target once it resolves.
    expect(api.mergeValue).toHaveBeenLastCalledWith('category', 'alpha', 'gamma', true)
  })

  it('disables apply the moment the target changes, before the new preview lands', async () => {
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    let resolve!: (v: { moved: number }) => void
    vi.mocked(api.mergeValue).mockReturnValueOnce(new Promise((r) => { resolve = r }))
    await wrapper.find('[data-testid="merge-target"]').setValue('gamma')
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(true)
    resolve({ moved: 2 })
    await flushPromises()
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('shows which aliases the target gains and which it already has', async () => {
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    const diff = wrapper.find('[data-testid="merge-diff"]').text()
    expect(diff).toContain('alpha')     // the source key becomes an alias
    expect(diff).toContain('a-one')     // moves across
    expect(diff).toContain('shared')    // already on the target
  })

  it("warns that the source's colour override is destroyed", async () => {
    // Invisible in the API's answer and irreversible.
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    expect(wrapper.find('[data-testid="merge-colour-loss"]').exists()).toBe(true)
  })

  it('says nothing about colour when the source has no override', async () => {
    vi.mocked(api.fetchFacets).mockResolvedValue([
      {
        key: 'category', label: 'Category', ordinal: 0,
        values: [
          { key: 'alpha', label: 'Alpha', parent_id: null, aliases: [], colour: null },
          { key: 'beta', label: 'Beta', parent_id: null, aliases: [], colour: null },
        ],
      },
    ])
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    expect(wrapper.find('[data-testid="merge-colour-loss"]').exists()).toBe(false)
  })

  it('applies the merge with dry_run false and returns to the panel', async () => {
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    await wrapper.find('[data-testid="merge-apply"]').trigger('click')
    await flushPromises()
    expect(api.mergeValue).toHaveBeenLastCalledWith('category', 'alpha', 'beta', false)
    expect(push).toHaveBeenCalledWith({ name: 'vocabulary' })
  })
  it("never lets a stale response for a superseded target attach to the current preview", async () => {
    // The async continuation's `if (target.value !== next) return` guard is the
    // only thing standing between an out-of-order dry-run resolution and a
    // count that belongs to the wrong target being shown as approved.
    const wrapper = await open()

    let resolveBeta!: (v: { moved: number }) => void
    vi.mocked(api.mergeValue).mockReturnValueOnce(new Promise((r) => { resolveBeta = r }))
    await wrapper.find('[data-testid="merge-target"]').setValue('beta')

    let resolveGamma!: (v: { moved: number }) => void
    vi.mocked(api.mergeValue).mockReturnValueOnce(new Promise((r) => { resolveGamma = r }))
    await wrapper.find('[data-testid="merge-target"]').setValue('gamma')

    // The newer request (gamma, the current selection) lands first.
    resolveGamma({ moved: 4 })
    await flushPromises()
    expect(wrapper.find('[data-testid="merge-diff"]').text()).toContain('4')
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(false)

    // The stale beta response — superseded before it ever resolved — lands
    // afterwards. It must not blank the diff or detach it from gamma.
    resolveBeta({ moved: 9 })
    await flushPromises()

    expect(wrapper.find('[data-testid="merge-diff"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="merge-diff"]').text()).toContain('4')
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it("never lets a stale REJECTION for a superseded target attach an error to a valid, approved preview", async () => {
    // Mirror of the test above, on the catch path: pick beta, then gamma.
    // Gamma's dry run resolves 200 first and enables Apply; beta's — for a
    // target the owner has already moved on from — then 404s. Without the
    // same `if (target.value !== next) return` guard in the `catch`, that
    // stale rejection would render an error beside gamma's still-valid,
    // still-approved preview: a superseded target's failure attached to the
    // current selection's success.
    const wrapper = await open()

    let rejectBeta!: (err: unknown) => void
    vi.mocked(api.mergeValue).mockReturnValueOnce(new Promise((_r, j) => { rejectBeta = j }))
    await wrapper.find('[data-testid="merge-target"]').setValue('beta')

    let resolveGamma!: (v: { moved: number }) => void
    vi.mocked(api.mergeValue).mockReturnValueOnce(new Promise((r) => { resolveGamma = r }))
    await wrapper.find('[data-testid="merge-target"]').setValue('gamma')

    resolveGamma({ moved: 4 })
    await flushPromises()
    expect(wrapper.find('[data-testid="merge-diff"]').text()).toContain('4')
    expect(wrapper.find('[data-testid="merge-error"]').exists()).toBe(false)

    // The stale beta rejection — superseded before it ever resolved — lands
    // afterwards. It must not render an error, and gamma's approved preview
    // must still stand.
    rejectBeta(new ApiError(404, 'invented-not-found-detail'))
    await flushPromises()

    expect(wrapper.find('[data-testid="merge-error"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="merge-diff"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="merge-diff"]').text()).toContain('4')
    expect(
      (wrapper.find('[data-testid="merge-apply"]').element as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('renders the server detail verbatim on a 409 (e.g. a self-merge)', async () => {
    // docs/facets.md §4: a merge into itself is refused on the real run and
    // the dry run alike, because the fold is a copy-then-delete that would
    // otherwise destroy the value and all its aliases.
    const wrapper = await open()
    await chooseTarget(wrapper, 'beta')
    vi.mocked(api.mergeValue).mockRejectedValueOnce(new ApiError(409, 'invented-conflict-detail'))
    await wrapper.find('[data-testid="merge-apply"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="merge-error"]').text()).toBe('invented-conflict-detail')
  })
})
