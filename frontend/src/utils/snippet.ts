/**
 * Safe rendering for search snippets (docs/api.md §1.3.3).
 *
 * The backend's `snippet` is `ts_headline` output over raw OCR text with
 * `<b>`/`</b>` highlight markers. The OCR text is NOT HTML-escaped by the
 * server — a scanned document can contain literal HTML, including script.
 * The contract: escape *everything*, then convert only the exact `<b>` and
 * `</b>` marker sequences back into real elements. The result is safe for
 * `v-html` because no other markup can survive the escape.
 */

const HTML_ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

/** Escape the five HTML-special characters. */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]!)
}

/**
 * Convert a ts_headline snippet into HTML that is safe to bind with
 * v-html: everything is escaped except the `<b>`/`</b>` highlight markers,
 * which become real `<b>` elements.
 */
export function renderSnippet(snippet: string): string {
  return escapeHtml(snippet).replaceAll('&lt;b&gt;', '<b>').replaceAll('&lt;/b&gt;', '</b>')
}

/** Escape a string for literal use inside a RegExp pattern. */
function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Search terms worth highlighting from a raw `q` value: websearch
 * operators (`OR`), `-exclusions`, and quotes are dropped; very short
 * fragments are skipped to avoid highlighting every other character.
 */
function highlightTerms(query: string): string[] {
  return query
    .split(/\s+/)
    .map((term) => term.replace(/^["'-]+|["']+$/g, ''))
    .filter((term) => term.length >= 2 && term.toUpperCase() !== 'OR')
}

/**
 * Render plain text (e.g. raw OCR text, which may contain literal HTML —
 * docs/api.md §1.3.3) with occurrences of the query terms wrapped in
 * `<mark>`. Same safety contract as `renderSnippet`: every character of
 * the input is HTML-escaped; only the `<mark>` wrappers this function
 * adds survive, so the result is safe for v-html.
 */
export function renderHighlighted(text: string, query: string): string {
  const terms = highlightTerms(query)
  if (!terms.length) return escapeHtml(text)
  // One capture group around the whole alternation: split() then yields
  // [plain, match, plain, match, …] — odd indices are matches.
  const pattern = new RegExp(`(${terms.map(escapeRegExp).join('|')})`, 'gi')
  return text
    .split(pattern)
    .map((part, index) => (index % 2 === 1 ? `<mark>${escapeHtml(part)}</mark>` : escapeHtml(part)))
    .join('')
}
