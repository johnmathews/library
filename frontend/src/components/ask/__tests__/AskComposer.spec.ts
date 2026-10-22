import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import AskComposer, { type PendingImage } from '../AskComposer.vue'

function mountComposer(opts: { isLargeScreen?: boolean; isAnswering?: boolean } = {}) {
  const question = ref('')
  const images = ref<PendingImage[]>([])
  const wrapper = mount(AskComposer, {
    props: {
      modelValue: question.value,
      'onUpdate:modelValue': (v: string) => {
        question.value = v
        void wrapper.setProps({ modelValue: v })
      },
      images: images.value,
      'onUpdate:images': (v: PendingImage[]) => {
        images.value = v
        void wrapper.setProps({ images: v })
      },
      isAnswering: opts.isAnswering ?? false,
      isLargeScreen: opts.isLargeScreen ?? true,
      placeholder: 'Ask a question…',
      fieldLabel: 'Your question',
      modelChip: 'Opus 4.8 · Subscription',
    },
    attachTo: document.body,
  })
  return { wrapper, question }
}

describe('AskComposer', () => {
  it('sends on plain Enter at lg+, but not on Shift+Enter, Ctrl+J or mid-composition', async () => {
    const { wrapper, question } = mountComposer({ isLargeScreen: true })
    const ta = wrapper.get('#ask-question')
    await ta.setValue('hello')

    await ta.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')).toHaveLength(1)

    await ta.trigger('keydown', { key: 'Enter', shiftKey: true })
    await ta.trigger('keydown', { key: 'Enter', isComposing: true })
    expect(wrapper.emitted('submit')).toHaveLength(1)

    // Ctrl+J inserts a newline at the caret and puts the caret after it; a
    // regression in the splice or the caret restore would pass silently here.
    const textarea = ta.element as HTMLTextAreaElement
    textarea.selectionStart = textarea.selectionEnd = 2
    await ta.trigger('keydown', { key: 'j', ctrlKey: true })
    await flushPromises()
    expect(wrapper.emitted('submit')).toHaveLength(1)
    expect(question.value).toBe('he\nllo')
    expect(textarea.selectionStart).toBe(3)
  })

  it('on a phone plain Enter is a newline; Cmd/Ctrl+Enter still sends', async () => {
    const { wrapper } = mountComposer({ isLargeScreen: false })
    const ta = wrapper.get('#ask-question')
    await ta.setValue('hello')

    await ta.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('submit')).toBeUndefined()

    await ta.trigger('keydown', { key: 'Enter', metaKey: true })
    await ta.trigger('keydown', { key: 'Enter', ctrlKey: true })
    expect(wrapper.emitted('submit')).toHaveLength(2)
  })

  it('disables Send until there is a question', async () => {
    const { wrapper } = mountComposer()
    expect(wrapper.get('[data-testid="ask-submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('#ask-question').setValue('hi')
    expect(wrapper.get('[data-testid="ask-submit"]').attributes('disabled')).toBeUndefined()
  })

  it('turns Send into a live Stop while answering', async () => {
    const { wrapper } = mountComposer({ isAnswering: true })
    const stop = wrapper.get('[data-testid="ask-submit"]')
    expect(stop.attributes('aria-label')).toBe('Stop answering')
    expect(stop.attributes('disabled')).toBeUndefined()
    await stop.trigger('click')
    expect(wrapper.emitted('stop')).toHaveLength(1)
  })

  it('puts attach and send inside the one rounded box, and the model under it', () => {
    const { wrapper } = mountComposer()
    const form = wrapper.get('[data-testid="ask-form"]')
    expect(form.find('[data-testid="ask-image-attach"]').exists()).toBe(true)
    expect(form.find('[data-testid="ask-submit"]').exists()).toBe(true)
    expect(form.find('[data-testid="ask-model-chip"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="ask-model-chip"]').text()).toBe('Opus 4.8 · Subscription')
  })
})
