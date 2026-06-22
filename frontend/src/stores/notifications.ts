import { ref } from 'vue'
import { defineStore } from 'pinia'

export type ToastVariant = 'info' | 'success' | 'error'

/** A toast currently on screen. */
export interface Toast {
  id: number
  variant: ToastVariant
  title: string
  message?: string
  /** Optional in-app link target (a vue-router path) rendered as an action. */
  to?: string
}

/** Arguments to {@link useNotificationsStore.push}. */
export interface ToastInput {
  variant: ToastVariant
  title: string
  message?: string
  to?: string
  /**
   * Milliseconds before auto-dismiss; `null` keeps the toast until dismissed.
   * Omitted means: errors stay (so a failure is never missed), everything else
   * auto-dismisses after {@link DEFAULT_TIMEOUT}.
   */
  timeout?: number | null
}

const DEFAULT_TIMEOUT = 5000

/**
 * A queue of transient toast notifications shown by ToastContainer. Generic on
 * purpose: the jobs store raises document-lifecycle toasts through it, but any
 * call site can `push(...)`. Distinct from the one-shot {@link useFlashStore},
 * which survives a single navigation for the GOV.UK redirect-confirm pattern.
 */
export const useNotificationsStore = defineStore('notifications', () => {
  const toasts = ref<Toast[]>([])
  const timers = new Map<number, ReturnType<typeof setTimeout>>()
  let nextId = 0

  function push(input: ToastInput): number {
    const id = ++nextId
    toasts.value.push({
      id,
      variant: input.variant,
      title: input.title,
      message: input.message,
      to: input.to,
    })

    const timeout =
      input.timeout === undefined
        ? input.variant === 'error'
          ? null
          : DEFAULT_TIMEOUT
        : input.timeout
    if (timeout !== null) {
      timers.set(
        id,
        setTimeout(() => dismiss(id), timeout),
      )
    }
    return id
  }

  function dismiss(id: number): void {
    const timer = timers.get(id)
    if (timer !== undefined) {
      clearTimeout(timer)
      timers.delete(id)
    }
    toasts.value = toasts.value.filter((toast) => toast.id !== id)
  }

  /** Drop every toast and cancel pending timers (used on teardown). */
  function clear(): void {
    for (const timer of timers.values()) clearTimeout(timer)
    timers.clear()
    toasts.value = []
  }

  return { toasts, push, dismiss, clear }
})
