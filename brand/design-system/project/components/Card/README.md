A card: a `panel` surface on `canvas` that holds one subject, with a title and at most one link. It has no outline.

## Use

- `panel` fill, `radius-md`, `shadow-card` (none in dark), padding `space-5` by 22px (`space-4` on a phone), 14px between its parts.
- Title in `type-title-2` at the top left; a TextLink to the full view at the top right ("Show 4 changes", "All jobs").
- A document's Summary card holds its summary in `type-lead` and `ink-secondary`.
- **Never an outline.** WCAG 1.4.11 asks 3:1 of a control's edge, not a card's, and outlined cards inside outlined panels are what make a screen feel boxed in. Separate parts inside a card with `line` hairlines or space.
- Never a card inside a card. A recessed part inside a card is a `tile` fill with `radius-sm` (a history entry, a code block).

## You provide

The title, the content, and at most one link. Today: the `.card` class in `frontend/src/assets/main.css` (which loses its border) and the detail view's sections in `views/DocumentDetailView.vue`.
