import { describe, it, expect, beforeEach } from 'vitest'
import { usePageTitle } from '../usePageTitle'

describe('usePageTitle', () => {
  const { pageTitle, pageTitleId, claimPageTitle, releasePageTitle } = usePageTitle()

  beforeEach(() => {
    // Drop whatever a previous test left claimed.
    const t = Symbol('reset')
    claimPageTitle(t, 'x')
    releasePageTitle(t)
  })

  it('starts empty and holds what a view claims', () => {
    expect(pageTitle.value).toBeNull()
    const view = Symbol('view')
    claimPageTitle(view, 'Documents', 'dashboard-title')
    expect(pageTitle.value).toBe('Documents')
    expect(pageTitleId.value).toBe('dashboard-title')
  })

  it('clears the id when a claim omits one', () => {
    const a = Symbol('a')
    claimPageTitle(a, 'Documents', 'dashboard-title')
    claimPageTitle(a, 'Jobs')
    expect(pageTitleId.value).toBeUndefined()
  })

  it('releases the title for the owner', () => {
    const view = Symbol('view')
    claimPageTitle(view, 'Jobs')
    releasePageTitle(view)
    expect(pageTitle.value).toBeNull()
    expect(pageTitleId.value).toBeUndefined()
  })

  it('ignores a release from a view that no longer owns the title', () => {
    // The hazard this guards: on a route change the incoming view may mount
    // before the outgoing one unmounts. If the outgoing view's release still
    // fired, the app bar would go blank on every such navigation — and which
    // order Vue uses is not something this component should depend on.
    const outgoing = Symbol('outgoing')
    const incoming = Symbol('incoming')
    claimPageTitle(outgoing, 'Ask')
    claimPageTitle(incoming, 'Charts')
    releasePageTitle(outgoing)
    expect(pageTitle.value).toBe('Charts')
  })
})
