/**
 * Shared formatting helpers for the document detail page and its extracted
 * child components (hero, metadata editor, note editor).
 *
 * These were previously defined inline in DocumentDetailView.vue; they are
 * lifted here so the parent view and the extracted NoteEditorPanel /
 * DocumentMetadataEditor components share a single implementation (notably the
 * DOMPurify sanitiser must not be duplicated).
 */
import { marked } from 'marked'
import DOMPurify from 'dompurify'

/** Render a markdown string to sanitised HTML (marked + DOMPurify). */
export function markdownPageHtml(md: string): string {
  return DOMPurify.sanitize(marked.parse(md, { async: false }) as string)
}

const dateFormat = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

/** Format an ISO date (`YYYY-MM-DD`) as `15 May 2026`, or null when absent. */
export function formatDate(iso: string | null): string | null {
  if (!iso) return null
  const parsed = new Date(`${iso}T00:00:00Z`)
  return Number.isNaN(parsed.getTime()) ? iso : dateFormat.format(parsed)
}

/** Format an ISO datetime as a long date + short time. */
export function formatDateTime(iso: string): string {
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return iso
  return new Intl.DateTimeFormat('en-GB', { dateStyle: 'long', timeStyle: 'short' }).format(parsed)
}

/** AppBadge colours that read as visually distinct in the Mosaic palette.
 * A tag's colour is derived from its name so it stays stable across renders
 * and pages without storing a colour on the tag itself. */
export const TAG_COLOURS = ['purple', 'blue', 'green', 'yellow', 'red', 'turquoise', 'pink'] as const

export function tagColour(name: string): (typeof TAG_COLOURS)[number] {
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  return TAG_COLOURS[hash % TAG_COLOURS.length]!
}
