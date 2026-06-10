// Spot-checks that the remaining wrappers emit the documented GOV.UK v6
// markup (classes, data-modules, ARIA wiring).
import { describe, expect, it } from 'vitest'
import { mount, RouterLinkStub } from '@vue/test-utils'
import GovBackLink from '../GovBackLink.vue'
import GovButton from '../GovButton.vue'
import GovCheckboxes from '../GovCheckboxes.vue'
import GovDetails from '../GovDetails.vue'
import GovErrorMessage from '../GovErrorMessage.vue'
import GovInput from '../GovInput.vue'
import GovNotificationBanner from '../GovNotificationBanner.vue'
import GovPagination from '../GovPagination.vue'
import GovPanel from '../GovPanel.vue'
import GovSelect from '../GovSelect.vue'
import GovServiceNavigation from '../GovServiceNavigation.vue'
import GovSummaryList from '../GovSummaryList.vue'
import GovTag from '../GovTag.vue'
import GovTextarea from '../GovTextarea.vue'

const routerStubs = { global: { stubs: { RouterLink: RouterLinkStub } } }

describe('GovButton', () => {
  it('renders a submit button with the govuk-button module', () => {
    const wrapper = mount(GovButton, { slots: { default: 'Save' } })
    const button = wrapper.find('button.govuk-button')
    expect(button.attributes('data-module')).toBe('govuk-button')
    expect(button.attributes('type')).toBe('submit')
    expect(button.text()).toBe('Save')
  })

  it('applies variant classes', () => {
    const wrapper = mount(GovButton, { props: { variant: 'secondary' } })
    expect(wrapper.find('button').classes()).toContain('govuk-button--secondary')
  })

  it('renders links as role=button', () => {
    const wrapper = mount(GovButton, { props: { href: '/start' } })
    const link = wrapper.find('a.govuk-button')
    expect(link.attributes('role')).toBe('button')
    expect(link.attributes('draggable')).toBe('false')
  })
})

describe('GovInput', () => {
  it('wires label, hint and error to the input', () => {
    const wrapper = mount(GovInput, {
      props: { id: 'title', label: 'Title', hint: 'A short name', errorMessage: 'Enter a title' },
    })

    expect(wrapper.find('.govuk-form-group').classes()).toContain('govuk-form-group--error')
    expect(wrapper.find('label.govuk-label').attributes('for')).toBe('title')
    expect(wrapper.find('#title-hint').classes()).toContain('govuk-hint')
    expect(wrapper.find('#title-error').classes()).toContain('govuk-error-message')

    const input = wrapper.find('input.govuk-input')
    expect(input.classes()).toContain('govuk-input--error')
    expect(input.attributes('aria-describedby')).toBe('title-hint title-error')
  })

  it('supports v-model', async () => {
    const wrapper = mount(GovInput, { props: { id: 'q', label: 'Search' } })
    await wrapper.find('input').setValue('belastingdienst')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['belastingdienst'])
  })
})

describe('GovTextarea', () => {
  it('renders the govuk-textarea with rows', () => {
    const wrapper = mount(GovTextarea, { props: { id: 'notes', label: 'Notes', rows: 8 } })
    const textarea = wrapper.find('textarea.govuk-textarea')
    expect(textarea.attributes('rows')).toBe('8')
    expect(wrapper.find('label[for="notes"]').exists()).toBe(true)
  })
})

describe('GovSelect', () => {
  it('renders options and supports v-model', async () => {
    const wrapper = mount(GovSelect, {
      props: {
        id: 'kind',
        label: 'Kind',
        items: [
          { value: 'letter', text: 'Letter' },
          { value: 'invoice', text: 'Invoice' },
        ],
      },
    })

    expect(wrapper.find('select.govuk-select').exists()).toBe(true)
    expect(wrapper.findAll('option')).toHaveLength(2)
    await wrapper.find('select').setValue('invoice')
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['invoice'])
  })
})

describe('GovCheckboxes', () => {
  it('renders checkboxes and reveals conditional content per checked item', async () => {
    const wrapper = mount(GovCheckboxes, {
      props: {
        id: 'tags',
        legend: 'Tags',
        items: [
          { value: 'tax', text: 'Tax', conditional: true },
          { value: 'home', text: 'Home' },
        ],
        modelValue: [],
        'onUpdate:modelValue': (value: string[]) => wrapper.setProps({ modelValue: value }),
      },
      slots: { 'conditional-tax': '<p>Tax details</p>' },
    })

    expect(wrapper.find('.govuk-checkboxes').attributes('data-module')).toBe('govuk-checkboxes')
    const conditional = wrapper.find('#conditional-tags')
    expect(conditional.classes()).toContain('govuk-checkboxes__conditional--hidden')

    await wrapper.find('input#tags').setValue(true)
    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([['tax']])
    expect(conditional.classes()).not.toContain('govuk-checkboxes__conditional--hidden')
  })
})

describe('GovErrorMessage', () => {
  it('prefixes a visually hidden "Error:"', () => {
    const wrapper = mount(GovErrorMessage, { slots: { default: 'Enter a date' } })
    expect(wrapper.find('p.govuk-error-message').exists()).toBe(true)
    expect(wrapper.find('.govuk-visually-hidden').text()).toBe('Error:')
    expect(wrapper.text()).toContain('Enter a date')
  })
})

describe('GovSummaryList', () => {
  it('renders rows with keys, values and actions', () => {
    const wrapper = mount(GovSummaryList, {
      props: {
        rows: [
          { key: 'Sender', value: 'Belastingdienst' },
          { key: 'Kind', value: 'Letter', actions: [{ text: 'Change', href: '/edit', visuallyHiddenText: 'kind' }] },
        ],
      },
    })

    expect(wrapper.find('dl.govuk-summary-list').exists()).toBe(true)
    const rows = wrapper.findAll('.govuk-summary-list__row')
    expect(rows).toHaveLength(2)
    expect(rows[0]!.find('dt.govuk-summary-list__key').text()).toBe('Sender')
    expect(rows[0]!.find('dd.govuk-summary-list__value').text()).toBe('Belastingdienst')
    const action = rows[1]!.find('dd.govuk-summary-list__actions a.govuk-link')
    expect(action.attributes('href')).toBe('/edit')
    expect(action.find('.govuk-visually-hidden').text()).toBe('kind')
  })
})

describe('GovTag', () => {
  it('renders a strong tag with an optional colour modifier', () => {
    const wrapper = mount(GovTag, { props: { colour: 'green' }, slots: { default: 'Done' } })
    expect(wrapper.find('strong.govuk-tag').classes()).toContain('govuk-tag--green')
    expect(wrapper.text()).toBe('Done')
  })
})

describe('GovPagination', () => {
  it('renders pages with current marker, ellipses, prev/next, and emits change', async () => {
    const wrapper = mount(GovPagination, { props: { page: 5, totalPages: 10 } })

    expect(wrapper.find('nav.govuk-pagination').attributes('aria-label')).toBe('Pagination')
    expect(wrapper.find('.govuk-pagination__prev').exists()).toBe(true)
    expect(wrapper.find('.govuk-pagination__next').exists()).toBe(true)
    expect(wrapper.findAll('.govuk-pagination__item--ellipses')).toHaveLength(2)

    const current = wrapper.find('.govuk-pagination__item--current a')
    expect(current.attributes('aria-current')).toBe('page')
    expect(current.text()).toBe('5')

    await wrapper.find('.govuk-pagination__next a').trigger('click')
    expect(wrapper.emitted('change')!.at(-1)).toEqual([6])
  })

  it('renders nothing for a single page', () => {
    const wrapper = mount(GovPagination, { props: { page: 1, totalPages: 1 } })
    expect(wrapper.find('nav').exists()).toBe(false)
  })
})

describe('GovNotificationBanner', () => {
  it('renders the success variant with role=alert', () => {
    const wrapper = mount(GovNotificationBanner, {
      props: { variant: 'success' },
      slots: { default: '<p class="govuk-notification-banner__heading">Uploaded</p>' },
    })

    const banner = wrapper.find('.govuk-notification-banner')
    expect(banner.classes()).toContain('govuk-notification-banner--success')
    expect(banner.attributes('role')).toBe('alert')
    expect(banner.attributes('data-module')).toBe('govuk-notification-banner')
    expect(wrapper.find('.govuk-notification-banner__title').text()).toBe('Success')
  })

  it('defaults to a neutral region', () => {
    const wrapper = mount(GovNotificationBanner, { slots: { default: 'Heads up' } })
    expect(wrapper.find('.govuk-notification-banner').attributes('role')).toBe('region')
    expect(wrapper.find('.govuk-notification-banner__title').text()).toBe('Important')
  })
})

describe('GovPanel', () => {
  it('renders the confirmation panel', () => {
    const wrapper = mount(GovPanel, { props: { title: 'Upload complete' }, slots: { default: 'Ref 42' } })
    expect(wrapper.find('.govuk-panel--confirmation').exists()).toBe(true)
    expect(wrapper.find('.govuk-panel__title').text()).toBe('Upload complete')
    expect(wrapper.find('.govuk-panel__body').text()).toBe('Ref 42')
  })
})

describe('GovDetails', () => {
  it('renders a native details element with GOV.UK classes', () => {
    const wrapper = mount(GovDetails, { props: { summary: 'Help' }, slots: { default: 'More info' } })
    expect(wrapper.find('details.govuk-details').exists()).toBe(true)
    expect(wrapper.find('.govuk-details__summary-text').text()).toBe('Help')
    expect(wrapper.find('.govuk-details__text').text()).toBe('More info')
    expect(wrapper.find('details').attributes('open')).toBeUndefined()
  })

  it('renders initially expanded when open is set', () => {
    const wrapper = mount(GovDetails, { props: { summary: 'Help', open: true } })
    expect(wrapper.find('details').attributes('open')).toBeDefined()
  })
})

describe('GovBackLink', () => {
  it('renders a RouterLink when given a route', () => {
    const wrapper = mount(GovBackLink, { props: { to: '/' }, ...routerStubs })
    const link = wrapper.findComponent(RouterLinkStub)
    expect(link.props('to')).toBe('/')
    expect(wrapper.find('.govuk-back-link').text()).toBe('Back')
  })
})

describe('GovServiceNavigation', () => {
  it('renders the service navigation with active item and menu toggle', () => {
    const wrapper = mount(GovServiceNavigation, {
      props: {
        items: [
          { text: 'Documents', to: '/', active: true },
          { text: 'Sign out' },
        ],
      },
      ...routerStubs,
    })

    const section = wrapper.find('section.govuk-service-navigation')
    expect(section.attributes('data-module')).toBe('govuk-service-navigation')
    expect(section.attributes('aria-label')).toBe('Service information')

    const toggle = wrapper.find('button.govuk-js-service-navigation-toggle')
    expect(toggle.attributes('aria-controls')).toBe('navigation')

    const active = wrapper.find('.govuk-service-navigation__item--active')
    expect(active.find('.govuk-service-navigation__active-fallback').text()).toBe('Documents')
    expect(wrapper.findAll('.govuk-service-navigation__item')).toHaveLength(2)
  })

  it('emits select for action items', async () => {
    const wrapper = mount(GovServiceNavigation, {
      props: { items: [{ text: 'Sign out' }] },
      ...routerStubs,
    })

    await wrapper.find('.govuk-service-navigation__link').trigger('click')
    expect(wrapper.emitted('select')!.at(-1)).toEqual([{ text: 'Sign out' }])
  })
})
