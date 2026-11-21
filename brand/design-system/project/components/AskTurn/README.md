An Ask turn: the household's question, Library's answer with numbered citations, and the sources it drew on.

## Use

- **The question** sits right, in a `tile` bubble with `radius-lg`, `type-body` in `ink`, at most 85% of the column. An edit icon beside it on hover or focus.
- **The answer** sits left, full width, at 15px with 26px lines, in `ink`. No bubble.
- **Citations** in the answer are small numbered pills, `brand-on-tint` on `brand-tint`, each a link to its source and named "Source 1".
- **Citations (2)** opens a list of the sources, numbered to match: each a `tile` row with the number, the document's title and the page ("p. 2"). A row opens the document at that page.
- **Used 2 tools** opens the tool names in `type-code`.
- Under the answer: copy and retry as icon buttons in `ink-secondary`.
- In a document's own Ask, each side is labelled "You" and "Library" in `type-label`, sentence case.

## States

- Thinking: three dots in `ink-secondary` that pulse, and "Generating answer…" for a screen reader. No pulse under `prefers-reduced-motion`.
- Editing: the question becomes a textarea with "Cancel" and "Send", and a note that sending replaces the answers after it.
- Failed: an error Banner under the question with "Retry".

## You provide

The question, the answer's HTML with its citation markers, the citations, and the tools used. Today: `components/ask/AskTurn.vue` and `components/ask/renderAnswer.ts`.
