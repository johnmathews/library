import { describe, it, expect, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import ToastContainer from '../ToastContainer.vue'
import { useNotificationsStore } from '@/stores/notifications'

const router = createRouter({
  history: createMemoryHistory(),
  routes: [
    { path: '/', name: 'home', component: { template: '<div/>' } },
    { path: '/jobs', name: 'jobs', component: { template: '<div/>' } },
  ],
})

function mountContainer() {
  return mount(ToastContainer, { global: { plugins: [router] } })
}

describe('ToastContainer', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('renders queued toasts with their title and message', async () => {
    const store = useNotificationsStore()
    const id = store.push({ variant: 'success', title: 'Processed', message: 'invoice.pdf' })
    const wrapper = mountContainer()
    await wrapper.vm.$nextTick()

    const toast = wrapper.get(`[data-testid="toast-${id}"]`)
    expect(toast.text()).toContain('Processed')
    expect(toast.text()).toContain('invoice.pdf')
  })

  it('marks error toasts as assertive alerts and info as polite status', async () => {
    const store = useNotificationsStore()
    const errId = store.push({ variant: 'error', title: 'Failed' })
    const infoId = store.push({ variant: 'info', title: 'FYI' })
    const wrapper = mountContainer()
    await wrapper.vm.$nextTick()

    const err = wrapper.get(`[data-testid="toast-${errId}"]`)
    expect(err.attributes('role')).toBe('alert')
    expect(err.attributes('aria-live')).toBe('assertive')

    const info = wrapper.get(`[data-testid="toast-${infoId}"]`)
    expect(info.attributes('role')).toBe('status')
    expect(info.attributes('aria-live')).toBe('polite')
  })

  it('dismiss button removes the toast', async () => {
    const store = useNotificationsStore()
    const id = store.push({ variant: 'error', title: 'Failed' })
    const wrapper = mountContainer()
    await wrapper.vm.$nextTick()

    await wrapper.get(`[data-testid="toast-dismiss-${id}"]`).trigger('click')
    expect(store.toasts).toHaveLength(0)
    expect(wrapper.find(`[data-testid="toast-${id}"]`).exists()).toBe(false)
  })

  it('renders a View link to the toast target', async () => {
    const store = useNotificationsStore()
    const id = store.push({ variant: 'error', title: 'Failed', to: '/jobs' })
    const wrapper = mountContainer()
    await wrapper.vm.$nextTick()

    const link = wrapper.get(`[data-testid="toast-${id}"] a`)
    expect(link.attributes('href')).toBe('/jobs')
  })
})
