import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useNotificationsStore } from '../notifications'

describe('useNotificationsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('push adds a toast and returns its id', () => {
    const store = useNotificationsStore()
    const id = store.push({ variant: 'info', title: 'Hello' })
    expect(store.toasts).toHaveLength(1)
    expect(store.toasts[0]).toMatchObject({ id, variant: 'info', title: 'Hello' })
  })

  it('auto-dismisses non-error toasts after the default timeout', () => {
    const store = useNotificationsStore()
    store.push({ variant: 'success', title: 'Done' })
    expect(store.toasts).toHaveLength(1)
    vi.advanceTimersByTime(5000)
    expect(store.toasts).toHaveLength(0)
  })

  it('keeps error toasts until dismissed', () => {
    const store = useNotificationsStore()
    const id = store.push({ variant: 'error', title: 'Failed' })
    vi.advanceTimersByTime(60000)
    expect(store.toasts).toHaveLength(1)
    store.dismiss(id)
    expect(store.toasts).toHaveLength(0)
  })

  it('honours an explicit timeout override', () => {
    const store = useNotificationsStore()
    store.push({ variant: 'error', title: 'Brief', timeout: 1000 })
    vi.advanceTimersByTime(1000)
    expect(store.toasts).toHaveLength(0)
  })

  it('dismiss removes a specific toast and cancels its timer', () => {
    const store = useNotificationsStore()
    const first = store.push({ variant: 'info', title: 'A' })
    store.push({ variant: 'info', title: 'B' })
    store.dismiss(first)
    expect(store.toasts.map((toast) => toast.title)).toEqual(['B'])
  })

  it('clear drops everything and cancels timers', () => {
    const store = useNotificationsStore()
    store.push({ variant: 'info', title: 'A' })
    store.push({ variant: 'error', title: 'B' })
    store.clear()
    expect(store.toasts).toHaveLength(0)
    // No pending timer should resurrect or error after clearing.
    vi.advanceTimersByTime(10000)
    expect(store.toasts).toHaveLength(0)
  })
})
