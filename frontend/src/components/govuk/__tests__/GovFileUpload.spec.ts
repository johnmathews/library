import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import GovFileUpload from '../GovFileUpload.vue'

// Assert that the wrapper initialises the govuk-frontend FileUpload module
// (the v6.2 enhanced drop-zone) without pulling its full DOM machinery
// into jsdom.
const { fileUploadCtor } = vi.hoisted(() => ({ fileUploadCtor: vi.fn() }))
vi.mock('govuk-frontend', () => {
  class FileUpload {
    static moduleName = 'govuk-file-upload'
    constructor($root: Element | null, config?: object) {
      fileUploadCtor($root, config)
    }
  }
  return { FileUpload, isSupported: () => true }
})

describe('GovFileUpload', () => {
  beforeEach(() => fileUploadCtor.mockClear())

  it('renders the GOV.UK file upload markup', () => {
    const wrapper = mount(GovFileUpload, {
      props: { id: 'upload', label: 'Upload a document', hint: 'PDF or photo' },
    })

    const dropZone = wrapper.find('.govuk-file-upload-wrapper')
    expect(dropZone.attributes('data-module')).toBe('govuk-file-upload')

    const input = wrapper.find('input.govuk-file-upload')
    expect(input.attributes('type')).toBe('file')
    expect(input.attributes('id')).toBe('upload')
    expect(input.attributes('aria-describedby')).toBe('upload-hint')
    expect(wrapper.find('label[for="upload"]').text()).toBe('Upload a document')
  })

  it('initialises the govuk-frontend FileUpload component on mount', () => {
    const wrapper = mount(GovFileUpload, { props: { id: 'upload', label: 'Upload' } })

    expect(fileUploadCtor).toHaveBeenCalledTimes(1)
    expect(fileUploadCtor.mock.calls[0]![0]).toBe(
      wrapper.find('.govuk-file-upload-wrapper').element,
    )
  })

  it('shows the error state', () => {
    const wrapper = mount(GovFileUpload, {
      props: { id: 'upload', label: 'Upload', errorMessage: 'Select a file' },
    })

    expect(wrapper.find('.govuk-form-group').classes()).toContain('govuk-form-group--error')
    expect(wrapper.find('input').classes()).toContain('govuk-file-upload--error')
    expect(wrapper.find('input').attributes('aria-describedby')).toBe('upload-error')
  })

  it('emits the selected files through v-model', async () => {
    const wrapper = mount(GovFileUpload, { props: { id: 'upload', label: 'Upload' } })
    const file = new File(['data'], 'scan.pdf', { type: 'application/pdf' })

    const input = wrapper.find('input').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [file] })
    await wrapper.find('input').trigger('change')

    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([[file]])
  })
})
