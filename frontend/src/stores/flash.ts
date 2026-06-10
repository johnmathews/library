import { ref } from 'vue'
import { defineStore } from 'pinia'

/**
 * One-shot flash message for the GOV.UK "do something, redirect, confirm
 * with a notification banner" pattern (e.g. delete document → back to the
 * list with a success banner). The next view consumes the message exactly
 * once; a refresh does not re-show it (unlike a query parameter).
 */
export const useFlashStore = defineStore('flash', () => {
  const message = ref<string | null>(null)

  function set(text: string): void {
    message.value = text
  }

  /** Read and clear the pending message. */
  function consume(): string | null {
    const current = message.value
    message.value = null
    return current
  }

  return { message, set, consume }
})
