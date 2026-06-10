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
