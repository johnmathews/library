/**
 * Client-side key slugification for the create-value form's key field.
 *
 * Mirrors the server's `derive_value_key` (docs/facets.md) for convenience
 * only — the server remains the judge of a value key's validity, and its 422
 * is rendered verbatim rather than pre-empted here. Shared with the "add a
 * new facet value" suggestion-acceptance flow (Task 9) so the normalisation
 * rule has exactly one definition.
 */
export function slugify(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/ /g, '-')
    .replace(/[^a-z0-9_-]+/g, '')
    .replace(/([_-])\1+/g, '$1')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, 64)
    .replace(/^[-_]+|[-_]+$/g, '')
}
