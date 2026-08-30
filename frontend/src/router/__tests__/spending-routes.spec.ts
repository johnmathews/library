import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { routes } from '../index'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes,
  })
}

// encode_series_id produces `{sender}-{kind}-{currency}`; encode_authored_series_id
// produces `a-{id}`. Neither is ever a bare integer, so the two shapes coexist —
// but only if the digit-constrained workspace route and the `legacy` literal are
// both declared before `:seriesId`.
describe('/charts route resolution', () => {
  it('routes a numeric id to the workspace and a series id to the old view', () => {
    const router = makeRouter()
    const resolve = (path: string) => router.resolve(path)

    expect(resolve('/charts').name).toBe('charts')
    expect(resolve('/charts/7').name).toBe('spending-workspace')
    expect(resolve('/charts/legacy').name).toBe('charts-legacy')
    expect(resolve('/charts/a-12').name).toBe('series-chart')
    expect(resolve('/charts/4-9-EUR').name).toBe('series-chart')
  })
})
