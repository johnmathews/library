import { ref, type Ref } from 'vue'

/**
 * Shared, ephemeral "editing the metadata" flag for the document-detail page.
 *
 * `DocumentMetadataEditor`'s single page-wide Edit toggle used to own this as a
 * local ref. It is lifted here (mirroring `useDocumentLayout`'s `editMode`) so
 * the floating island — a separate component on the same page — can also read
 * and flip it: clicking the island's Edit/Done button must open the very same
 * per-field editors the Details card's own toggle does, not a second
 * independent mode.
 *
 * This is singleton state (a module-level ref, not a `useStorage`): it is
 * intentionally NOT persisted, so a reload or SPA navigation never comes back
 * with the editors already open. The document-detail view resets it to false
 * on unmount, exactly like `useDocumentLayout`'s `editMode`.
 */

const editMode = ref(false)

function toggle(): void {
  editMode.value = !editMode.value
}

function setEditMode(value: boolean): void {
  editMode.value = value
}

export interface MetadataEditMode {
  /** Ephemeral "the metadata editors are open" flag (not persisted). */
  editMode: Ref<boolean>
  toggle: () => void
  setEditMode: (value: boolean) => void
}

export function useMetadataEditMode(): MetadataEditMode {
  return {
    editMode,
    toggle,
    setEditMode,
  }
}
