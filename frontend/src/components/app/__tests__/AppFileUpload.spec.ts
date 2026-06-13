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
    // outer heading is now a <p>, not a <label>; the drop-zone <label for> is the only formal label
    expect(wrapper.find('p').text()).toBe('Upload a document')
    expect(wrapper.find('label[for="upload"]').exists()).toBe(true)
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

  it('drops two files with multiple:false → only first file set', async () => {
    const wrapper = mount(AppFileUpload, { props: { id: 'upload', label: 'Upload', multiple: false } })
    const fileA = new File(['a'], 'a.pdf', { type: 'application/pdf' })
    const fileB = new File(['b'], 'b.pdf', { type: 'application/pdf' })

    await wrapper.find('label.border-dashed').trigger('drop', {
      dataTransfer: { files: [fileA, fileB] },
    })

    const emitted = (wrapper.emitted('update:modelValue')!.at(-1) as [File[]])[0]
    expect(emitted).toHaveLength(1)
    expect(emitted[0]!.name).toBe('a.pdf')
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
