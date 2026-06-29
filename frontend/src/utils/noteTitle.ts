/**
 * Derive a note's title from the first line of its markdown body.
 *
 * Notes no longer have a separate title field in the authoring UI: the first
 * non-empty line of the body *is* the title (a common notes-app convention).
 * The backend still stores title and body separately and requires a non-empty
 * title, so the views send `deriveNoteTitle(body)` alongside the raw body.
 *
 * Rules:
 *  - The first line with non-whitespace content becomes the title.
 *  - A leading markdown heading marker (`#`..`######`) is stripped, so
 *    `# Groceries` and `Groceries` yield the same title.
 *  - The result is trimmed and capped at MAX_TITLE_LENGTH characters so a long
 *    opening line can't produce an unwieldy title.
 *  - An empty/whitespace-only body yields '' (callers treat that as "can't save").
 */
const MAX_TITLE_LENGTH = 200

export function deriveNoteTitle(body: string): string {
  const firstContentLine =
    body.split('\n').find((line) => line.trim() !== '') ?? ''
  const withoutHeading = firstContentLine.replace(/^\s{0,3}#{1,6}\s+/, '')
  return withoutHeading.trim().slice(0, MAX_TITLE_LENGTH)
}
