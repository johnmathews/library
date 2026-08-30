import { beforeAll, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import SpendingDrillPanel from '../SpendingDrillPanel.vue'

/**
 * jsdom implements HTMLDialogElement's `open` property only — `showModal()`
 * and `close()` are missing — so stub a minimal happy-path approximation
 * (mirrors ConfirmDialog.spec / SearchModal.spec).
 */
beforeAll(() => {
  if (typeof HTMLDialogElement.prototype.showModal !== 'function') {
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.setAttribute('open', '')
    }
  }
  if (typeof HTMLDialogElement.prototype.close !== 'function') {
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.removeAttribute('open')
      this.dispatchEvent(new Event('close'))
    }
  }
})

function mountPanel(props: Partial<{ open: boolean; title: string; sheet: boolean }> = {}): VueWrapper {
  return mount(SpendingDrillPanel, {
    attachTo: document.body,
    props: { open: true, title: 'All spending', sheet: false, ...props },
    slots: { default: '<p data-testid="drill-body-content">Body content</p>' },
  })
}

describe('SpendingDrillPanel', () => {
  it('renders as a side panel when sheet is false and a bottom sheet when true', () => {
    expect(mountPanel({ sheet: false }).get('dialog').attributes('data-presentation')).toBe('panel')
    expect(mountPanel({ sheet: true }).get('dialog').attributes('data-presentation')).toBe('sheet')
  })

  it('emits close on Escape and on the close button', async () => {
    // Escape: the browser fires the native `close` event on the dialog
    // itself; jsdom doesn't run real dialog keyboard handling, so dispatch
    // the event it would produce (same technique as ConfirmDialog.spec).
    const escaped = mountPanel()
    escaped.get('dialog').element.dispatchEvent(new Event('close'))
    expect(escaped.emitted('close')).toHaveLength(1)

    const clicked = mountPanel()
    await clicked.get('[data-testid="drill-close"]').trigger('click')
    expect(clicked.emitted('close')).toHaveLength(1)
  })

  it('titles itself from the prop, so an unsplit chart can pass its own name', () => {
    // CellOutBody.label is "" for an unsplit chart, and "" is not a title —
    // the shell never reads that field, it only ever renders what its
    // caller resolved into `title`.
    expect(mountPanel({ title: 'All spending' }).get('[data-testid="drill-title"]').text()).toBe(
      'All spending',
    )
  })

  it('renders the default slot as the body, without knowing what it is', () => {
    const wrapper = mountPanel()
    expect(wrapper.get('[data-testid="drill-body-content"]').text()).toBe('Body content')
  })

  it('calls showModal when open flips true and close when it flips false', async () => {
    const wrapper = mountPanel({ open: false })
    const el = wrapper.get('dialog').element as HTMLDialogElement
    expect(el.open).toBe(false)

    await wrapper.setProps({ open: true })
    expect(el.open).toBe(true)

    await wrapper.setProps({ open: false })
    expect(el.open).toBe(false)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('opens without an opener to restore focus to, and still closes cleanly', async () => {
    // document.activeElement is not always an HTMLElement (it can be null,
    // or the <body> itself before anything has been focused) — the opener
    // capture must degrade to null rather than throw either way.
    const original = Object.getOwnPropertyDescriptor(Document.prototype, 'activeElement')
    Object.defineProperty(document, 'activeElement', { configurable: true, get: () => null })
    try {
      const wrapper = mountPanel({ open: false })
      await wrapper.setProps({ open: true })
      await wrapper.setProps({ open: false })
      expect(wrapper.emitted('close')).toHaveLength(1)
    } finally {
      if (original) Object.defineProperty(document, 'activeElement', original)
    }
  })

  it('closes on a backdrop click but not on a click inside the body', async () => {
    const wrapper = mountPanel({ open: false })
    await wrapper.setProps({ open: true })

    await wrapper.get('[data-testid="drill-body-content"]').trigger('click')
    expect(wrapper.emitted('close')).toBeUndefined()

    await wrapper.get('dialog').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  // Mutation check: an empty body renders no `data-presentation` at all
  // (e.g. a component that forgets the attribute, or spells it as a class)
  // would still pass every OTHER assertion above — this pins the exact
  // attribute name and both of its values so that defect is caught.
  it('never presents an unrecognised presentation value', () => {
    const panel = mountPanel({ sheet: false }).get('dialog').attributes('data-presentation')
    const sheet = mountPanel({ sheet: true }).get('dialog').attributes('data-presentation')
    expect(['panel', 'sheet']).toContain(panel)
    expect(['panel', 'sheet']).toContain(sheet)
    expect(panel).not.toBe(sheet)
  })
})
