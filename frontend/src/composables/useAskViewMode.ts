import { computed, type ComputedRef, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * Ask transcript view mode — `document` (full-width role-labelled blocks, the
 * default on a wide screen) or `conversation` (chat bubbles).
 *
 * Document mode is for prose- and table-heavy answers, where a right-aligned
 * `max-w-[85%]` bubble wastes the width those answers need — which describes
 * most of what Ask produces, so it is the **default** on a wide screen. It is a
 * display-size preference about *this screen*, so it lives in localStorage
 * under the app's `library:` key convention rather than the server-side user
 * profile (docs/frontend-view-principles.md §4).
 *
 * Two refs, deliberately:
 *
 *   - `viewMode` is the stored *preference*, and it is what the toggle buttons
 *     reflect. It keeps its value at any width.
 *   - `effectiveViewMode` is the *render decision*, clamped to `conversation`
 *     on a narrow screen.
 *
 * Keeping them apart is what lets someone who chose document mode on a desktop
 * open the same app on a phone, get a sensible bubble layout, and still find
 * document mode selected when they are back at a real screen.
 *
 * The clamp takes the caller's `isLargeScreen` ref rather than calling
 * `matchMedia` here: AskView already computes one (`useMediaQuery`), so passing
 * it in avoids a second media-query listener that could disagree with the
 * first, and leaves this module free of import-time side effects — which is
 * what keeps it testable without `vi.resetModules()`.
 */
export type AskViewMode = 'conversation' | 'document'

/** Storage key for the per-machine transcript view preference. */
export const ASK_VIEW_MODE_STORAGE_KEY = 'library:ask-view-mode'

export interface AskViewModeApi {
  /** The persisted, two-way bindable preference. Not clamped. */
  viewMode: Ref<AskViewMode>
  /** What the transcript should actually render, clamped to the viewport. */
  effectiveViewMode: ComputedRef<AskViewMode>
  /** True when the document-mode layout is the one on screen. */
  isDocumentMode: ComputedRef<boolean>
  /** Toggle-button metadata, in the order the buttons are rendered. */
  modes: { value: AskViewMode; label: string }[]
}

export function useAskViewMode(isLargeScreen: Ref<boolean>): AskViewModeApi {
  // Document is the default on a wide screen: Ask's answers are prose- and
  // table-heavy, so the layout that suits them is the one most people should
  // get without going looking for a setting. `effectiveViewMode` still clamps
  // it away below `lg`, so a phone is unaffected by this default.
  const viewMode = useStorage<AskViewMode>(ASK_VIEW_MODE_STORAGE_KEY, 'document')

  const effectiveViewMode = computed<AskViewMode>(() =>
    isLargeScreen.value && viewMode.value === 'document' ? 'document' : 'conversation',
  )
  const isDocumentMode = computed<boolean>(() => effectiveViewMode.value === 'document')

  const modes: { value: AskViewMode; label: string }[] = [
    { value: 'conversation', label: 'Conversation' },
    { value: 'document', label: 'Document' },
  ]

  return { viewMode, effectiveViewMode, isDocumentMode, modes }
}
