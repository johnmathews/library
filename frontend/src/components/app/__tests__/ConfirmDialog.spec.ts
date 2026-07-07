import { beforeAll, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ConfirmDialog from '../ConfirmDialog.vue'

/**
 * jsdom implements HTMLDialogElement's `open` property only — `showModal()` and
 * `close()` are missing — so stub a minimal happy-path approximation (mirrors
 * SearchModal.spec): showModal sets `open`, close removes it and fires `close`.
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

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(ConfirmDialog, {
    attachTo: document.body,
    props: { open: true, title: 'Permanently delete “Invoice”?', ...props },
  })
}

describe('ConfirmDialog', () => {
  it('renders the title, message and confirm label', () => {
    const wrapper = mountDialog({
      message: 'This cannot be undone.',
      confirmLabel: 'Delete forever',
    })
    expect(wrapper.text()).toContain('Permanently delete “Invoice”?')
    expect(wrapper.text()).toContain('This cannot be undone.')
    expect(wrapper.get('[data-testid="confirm-accept"]').text()).toBe('Delete forever')
  })

  it('emits confirm when the accept button is clicked', async () => {
    const wrapper = mountDialog()
    await wrapper.get('[data-testid="confirm-accept"]').trigger('click')
    expect(wrapper.emitted('confirm')).toHaveLength(1)
  })

  it('emits cancel when the cancel button is clicked', async () => {
    const wrapper = mountDialog()
    await wrapper.get('[data-testid="confirm-cancel"]').trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('emits cancel on the native close event (ESC) while open', async () => {
    const wrapper = mountDialog()
    wrapper.get('[data-testid="confirm-dialog"]').element.dispatchEvent(new Event('close'))
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })

  it('does not emit cancel when closed programmatically by the parent', async () => {
    const wrapper = mountDialog()
    await wrapper.setProps({ open: false }) // parent closes after confirm
    expect(wrapper.emitted('cancel')).toBeUndefined()
  })

  it('disables the confirm button and shows a pending label while busy', () => {
    const wrapper = mountDialog({ busy: true })
    const accept = wrapper.get('[data-testid="confirm-accept"]')
    expect(accept.text()).toBe('Deleting…')
    expect(accept.attributes('aria-disabled')).toBe('true')
  })

  it('does not cancel while busy: cancel is disabled and ESC/close is a no-op', async () => {
    // Guards the race where clicking Confirm then Cancel would close the dialog
    // as if nothing happened while the delete still completed underneath.
    const wrapper = mountDialog({ busy: true })
    const cancel = wrapper.get('[data-testid="confirm-cancel"]')
    expect(cancel.attributes('disabled')).toBeDefined()

    // A native close (ESC) while busy must not emit cancel either.
    wrapper.get('[data-testid="confirm-dialog"]').element.dispatchEvent(new Event('close'))
    expect(wrapper.emitted('cancel')).toBeUndefined()
  })
})
