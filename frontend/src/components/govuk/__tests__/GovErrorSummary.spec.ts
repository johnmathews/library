import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import GovErrorSummary from '../GovErrorSummary.vue'

let wrapper: VueWrapper | undefined
let host: HTMLDivElement

beforeEach(() => {
  // govuk-frontend components only initialise when the page opts in.
  document.body.classList.add('govuk-frontend-supported')
  // jsdom does not implement scrollIntoView (used by ErrorSummary links).
  Element.prototype.scrollIntoView = vi.fn()
  host = document.createElement('div')
  document.body.appendChild(host)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  host.remove()
  document.body.classList.remove('govuk-frontend-supported')
})

describe('GovErrorSummary', () => {
  it('renders the GOV.UK error summary structure with role=alert and links', () => {
    wrapper = mount(GovErrorSummary, {
      props: {
        errors: [
          { text: 'Enter your username', href: '#username' },
          { text: 'Enter your password', href: '#password' },
        ],
      },
      attachTo: host,
    })

    const root = wrapper.find('.govuk-error-summary')
    expect(root.attributes('data-module')).toBe('govuk-error-summary')
    expect(root.find('div[role="alert"]').exists()).toBe(true)
    expect(root.find('.govuk-error-summary__title').text()).toBe('There is a problem')

    const links = root.findAll('.govuk-error-summary__list a')
    expect(links).toHaveLength(2)
    expect(links[0]!.attributes('href')).toBe('#username')
    expect(links[0]!.text()).toBe('Enter your username')
  })

  it('receives focus when it appears', () => {
    wrapper = mount(GovErrorSummary, {
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

    wrapper = mount(GovErrorSummary, {
      props: { errors: [{ text: 'Enter your username', href: '#username' }] },
      attachTo: host,
    })

    await wrapper.find('.govuk-error-summary__list a').trigger('click')

    expect(document.activeElement).toBe(input)
    label.remove()
    input.remove()
  })

  it('re-focuses itself when the error list changes while mounted', async () => {
    wrapper = mount(GovErrorSummary, {
      props: { errors: [{ text: 'first', href: '#a' }] },
      attachTo: host,
    })
    ;(wrapper.element as HTMLElement).blur()
    expect(document.activeElement).not.toBe(wrapper.element)

    await wrapper.setProps({ errors: [{ text: 'second', href: '#b' }] })

    expect(document.activeElement).toBe(wrapper.element)
  })
})
