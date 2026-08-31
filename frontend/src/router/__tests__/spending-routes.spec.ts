import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { routes } from '../index'

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes,
  })
}

describe('/charts route resolution', () => {
  it('routes the bare path to the board and a numeric id to the workspace', () => {
    const router = makeRouter()
    const resolve = (path: string) => router.resolve(path)

    expect(resolve('/charts').name).toBe('charts')
    expect(resolve('/charts/7').name).toBe('spending-workspace')
  })

  it('a non-numeric /charts child no longer resolves to a legacy route', () => {
    const router = makeRouter()
    const resolved = router.resolve('/charts/12-3-EUR')
    expect(resolved.name).not.toBe('series-chart')
    expect(resolved.matched.length).toBe(0)
  })
})
