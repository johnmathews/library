import { computed, type ComputedRef, type Ref } from 'vue'
import { useStorage } from '@vueuse/core'

/**
 * Shared markdown-editor view mode for note authoring + in-place note editing.
 *
 * Split (editor + preview side-by-side) is the default and the best use of width
 * on large screens; Edit / Preview are single-pane focus modes that also serve
 * narrow screens. The preference is persisted per-machine under a single storage
 * key so the create view and the detail-view editor stay in sync — a
 * display-size preference (docs/frontend-view-principles.md §4).
 *
 * Note: the rendered/sanitised `previewHtml` is intentionally NOT part of this
 * composable — each view binds the preview to its own body ref.
 */
export type EditorMode = 'edit' | 'split' | 'preview'

/** Storage key shared by every markdown editor so the mode preference is global. */
export const EDITOR_MODE_STORAGE_KEY = 'library:note-editor-mode'

export interface MarkdownEditorMode {
  /** The persisted, two-way bindable editor mode. */
  editorMode: Ref<EditorMode>
  /** Whether the editor pane should be shown (true unless preview-only). */
  showEditor: ComputedRef<boolean>
  /** Whether the preview pane should be shown (true unless edit-only). */
  showPreview: ComputedRef<boolean>
  /** Toggle-button metadata; `wideOnly` modes hide on narrow screens. */
  modes: { value: EditorMode; label: string; wideOnly: boolean }[]
}

export function useMarkdownEditorMode(): MarkdownEditorMode {
  const editorMode = useStorage<EditorMode>(EDITOR_MODE_STORAGE_KEY, 'split')
  const showEditor = computed(() => editorMode.value !== 'preview')
  const showPreview = computed(() => editorMode.value !== 'edit')

  const modes: { value: EditorMode; label: string; wideOnly: boolean }[] = [
    { value: 'edit', label: 'Edit', wideOnly: false },
    { value: 'split', label: 'Split', wideOnly: true },
    { value: 'preview', label: 'Preview', wideOnly: false },
  ]

  return { editorMode, showEditor, showPreview, modes }
}
