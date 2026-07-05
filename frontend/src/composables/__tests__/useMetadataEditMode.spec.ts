import { describe, it, expect, beforeEach } from 'vitest'
import { useMetadataEditMode } from '../useMetadataEditMode'

describe('useMetadataEditMode', () => {
  beforeEach(() => {
    useMetadataEditMode().setEditMode(false)
  })

  it('starts false', () => {
    expect(useMetadataEditMode().editMode.value).toBe(false)
  })

  it('toggle() flips it', () => {
    const { editMode, toggle } = useMetadataEditMode()
    toggle()
    expect(editMode.value).toBe(true)
    toggle()
    expect(editMode.value).toBe(false)
  })

  it('setEditMode() sets it explicitly', () => {
    const { editMode, setEditMode } = useMetadataEditMode()
    setEditMode(true)
    expect(editMode.value).toBe(true)
    setEditMode(false)
    expect(editMode.value).toBe(false)
  })

  it('is a singleton: two callers share the same editMode', () => {
    const a = useMetadataEditMode()
    const b = useMetadataEditMode()
    a.toggle()
    expect(b.editMode.value).toBe(true)
    b.setEditMode(false)
    expect(a.editMode.value).toBe(false)
  })
})
