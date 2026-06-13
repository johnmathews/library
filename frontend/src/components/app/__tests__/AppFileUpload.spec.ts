import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppFileUpload from '../AppFileUpload.vue'

describe('AppFileUpload', () => {
  it('renders the drop-zone markup', () => {
    const wrapper = mount(AppFileUpload, {
      props: { id: 'upload', label: 'Upload a document', hint: 'PDF or photo' },
    })

    const input = wrapper.find('input[type="file"]')
    expect(input.attributes('id')).toBe('upload')
    expect(input.attributes('aria-describedby')).toBe('upload-hint')
    expect(wrapper.find('label[for="upload"]').text()).toBe('Upload a document')
    expect(wrapper.text()).toContain('Drop files or click to browse')
  })

  it('shows the error state', () => {
    const wrapper = mount(AppFileUpload, {
      props: { id: 'upload', label: 'Upload', errorMessage: 'Select a file' },
    })

    expect(wrapper.find('#upload-error').text()).toContain('Select a file')
    expect(wrapper.find('input[type="file"]').attributes('aria-describedby')).toBe('upload-error')
  })

  it('emits the selected files through v-model', async () => {
    const wrapper = mount(AppFileUpload, { props: { id: 'upload', label: 'Upload' } })
    const file = new File(['data'], 'scan.pdf', { type: 'application/pdf' })

    const input = wrapper.find('input[type="file"]').element as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [file] })
    await wrapper.find('input[type="file"]').trigger('change')

    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([[file]])
  })

  it('adds dropped files through v-model', async () => {
    const wrapper = mount(AppFileUpload, { props: { id: 'upload', label: 'Upload' } })
    const file = new File(['data'], 'dropped.pdf', { type: 'application/pdf' })

    await wrapper.find('label.border-dashed').trigger('drop', {
      dataTransfer: { files: [file] },
    })

    expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual([[file]])
  })

  it('toggles the dragover ring state', async () => {
    const wrapper = mount(AppFileUpload, { props: { id: 'upload', label: 'Upload' } })
    const zone = wrapper.find('label.border-dashed')

    await zone.trigger('dragover')
    expect(zone.classes()).toContain('ring-2')

    await zone.trigger('dragleave')
    expect(zone.classes()).not.toContain('ring-2')
  })
})
