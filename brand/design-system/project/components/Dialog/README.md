A dialog: a `panel` box over the page for a decision that needs the owner's attention before anything else.

## Use

- `panel`, `radius-lg`, `shadow-pop`, `space-6` padding, at most 420px wide, centred, over a dark scrim.
- Title in `type-title-2` as a question ("Delete permanently?"); one or two sentences in `ink-secondary` that say what will happen, naming the document.
- Buttons bottom right: the outline "Cancel" first, then the primary that names the action ("Delete permanently"). The primary is violet, because red means something failed.
- On a phone a dialog with a form opens as a drawer from the bottom, `radius-lg` on its top corners.
- Focus moves into it, Escape closes it, and focus returns to the button that opened it.

## States

Working: the primary keeps its colour and says "Deleting…" with `aria-busy`.

## You provide

The title, the sentence and the two actions. Today: `components/app/ConfirmDialog.vue`.
