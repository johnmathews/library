import { beforeEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import {
  EDITOR_MODE_STORAGE_KEY,
  useMarkdownEditorMode,
} from '../useMarkdownEditorMode'

describe('useMarkdownEditorMode', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to split with both panes shown', () => {
    const { editorMode, showEditor, showPreview } = useMarkdownEditorMode()
    expect(editorMode.value).toBe('split')
    expect(showEditor.value).toBe(true)
    expect(showPreview.value).toBe(true)
  })

  it('hides the preview in edit mode and the editor in preview mode', () => {
    const { editorMode, showEditor, showPreview } = useMarkdownEditorMode()

    editorMode.value = 'edit'
    expect(showEditor.value).toBe(true)
    expect(showPreview.value).toBe(false)

    editorMode.value = 'preview'
    expect(showEditor.value).toBe(false)
    expect(showPreview.value).toBe(true)
  })

  it('persists the mode under the shared storage key', async () => {
    const { editorMode } = useMarkdownEditorMode()
    editorMode.value = 'preview'
    await nextTick()
    expect(localStorage.getItem(EDITOR_MODE_STORAGE_KEY)).toContain('preview')
  })

  it('shares the persisted preference across instances', async () => {
    const first = useMarkdownEditorMode()
    first.editorMode.value = 'edit'
    await nextTick()
    const second = useMarkdownEditorMode()
    expect(second.editorMode.value).toBe('edit')
  })

  it('exposes mode metadata with split flagged wide-only', () => {
    const { modes } = useMarkdownEditorMode()
    expect(modes.map((m) => m.value)).toEqual(['edit', 'split', 'preview'])
    expect(modes.find((m) => m.value === 'split')?.wideOnly).toBe(true)
  })
})
