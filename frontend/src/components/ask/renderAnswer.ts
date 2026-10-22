import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Wrap every rendered <table> in its own horizontal scroll container.
//
// Without this a wide GFM table has nowhere to overflow *into*: the transcript
// is `overflow-y-auto`, which makes the browser compute overflow-x to `auto`
// too, so the whole transcript pans sideways — question bubbles and all. The
// wrapper confines that scrolling to the table itself. It only works together
// with `table { width: max-content }` and `min-width: 0` on `.ask-answer` (the
// scoped styles in AskTurn.vue): max-content lets columns take their natural
// width instead of being crushed to single characters, and min-width:0 stops
// the flex chain being burst by the intrinsic width. Change one, check all three.
//
// `role="region"` + `tabindex="0"` make the scroll area reachable by keyboard,
// which a scrollable region needs in order to be operable at all.
function wrapTables(html: string): string {
  return html.replace(
    /<table\b([^>]*)>([\s\S]*?)<\/table>/g,
    (_match, attrs: string, inner: string) =>
      `<div class="ask-table-wrap" role="region" aria-label="Table" tabindex="0">` +
      `<table${attrs}>${inner}</table>` +
      `</div>`,
  )
}

/**
 * The answer is markdown from Claude; render it to HTML and sanitize before
 * v-html (it can echo document text, so it is never trusted raw). The table
 * wrap is applied BEFORE sanitizing so the wrapper goes through DOMPurify like
 * the rest of the markup rather than being trusted around it; `tabindex` is not
 * in DOMPurify's default allow-list, so it has to be added explicitly or the
 * keyboard affordance is silently stripped.
 */
export function renderAnswer(answer: string): string {
  return DOMPurify.sanitize(wrapTables(marked.parse(answer, { async: false }) as string), {
    ADD_ATTR: ['tabindex'],
  })
}
