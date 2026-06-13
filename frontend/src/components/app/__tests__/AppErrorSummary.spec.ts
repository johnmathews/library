import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import AppErrorSummary from '../AppErrorSummary.vue'

let wrapper: VueWrapper | undefined
let host: HTMLDivElement

beforeEach(() => {
  // jsdom does not implement scrollIntoView (used when focusing a field).
  Element.prototype.scrollIntoView = vi.fn()
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  host.remove()
})

describe('AppErrorSummary', () => {
  it('renders the Mosaic error summary structure with role=alert and links', () => {
    wrapper = mount(AppErrorSummary, {
      props: {
        errors: [
          { text: 'Enter your username', href: '#username' },
          { text: 'Enter your password', href: '#password' },
        ],
      },
      attachTo: host,
    })

    const root = wrapper.find('[role="alert"]')
    expect(root.exists()).toBe(true)
    expect(root.find('h2').text()).toBe('There is a problem')

    const links = root.findAll('ul a')
    expect(links).toHaveLength(2)
    expect(links[0]!.attributes('href')).toBe('#username')
    expect(links[0]!.text()).toBe('Enter your username')
  })

  it('receives focus when it appears', () => {
    wrapper = mount(AppErrorSummary, {
      props: { errors: [{ text: 'Enter your username', href: '#username' }] },
      attachTo: host,
    })

    expect(document.activeElement).toBe(wrapper.element)
  })

  it('moves focus to the field when an error link is clicked', async () => {
    const input = document.createElement('input')
    input.id = 'username'
    const label = document.createElement('label')
    label.setAttribute('for', 'username')
    document.body.append(label, input)

    wrapper = mount(AppErrorSummary, {
      props: { errors: [{ text: 'Enter your username', href: '#username' }] },
      attachTo: host,
    })

    await wrapper.find('ul a').trigger('click')

    expect(document.activeElement).toBe(input)
    label.remove()
    input.remove()
  })

  it('re-focuses itself when the error list changes while mounted', async () => {
    wrapper = mount(AppErrorSummary, {
      props: { errors: [{ text: 'first', href: '#a' }] },
      attachTo: host,
    })
    ;(wrapper.element as HTMLElement).blur()
    expect(document.activeElement).not.toBe(wrapper.element)

    await wrapper.setProps({ errors: [{ text: 'second', href: '#b' }] })

    expect(document.activeElement).toBe(wrapper.element)
  })
})
