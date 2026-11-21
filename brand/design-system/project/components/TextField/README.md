A text field: a labelled input for words, amounts, dates, comments and search.

## Use

- 36px tall (search 38px), `panel` fill, 1px `control-border`, `radius-sm`, `type-body`. The label sits above in `type-label`, in sentence case.
- **16px on a touch screen** (`pointer: coarse`), whatever the desktop size. iOS zooms the page into any field under 16px when it is focused, and stays zoomed.
- **Search** leads with a search icon and a placeholder that says what it searches: "Search documents…".
- **Amounts** carry the unit in the label ("Amount (€)") and use tabular figures.
- **A comment** is a textarea of 3 lines that grows; its button is "Add comment".
- Placeholders are examples or scope, never the label.

## States

- Focus: the 2px `focus` ring at a 2px offset. The field keeps its `control-border` edge; no coloured glow.
- Error: the border turns `error-ink`, and a line under it with an alert icon says what is wrong and how to fix it: "That date doesn't exist. Enter one like 28 Feb 2026."
- Hint: a line in `type-caption` and `ink-secondary` under the field, tied by `aria-describedby`.

## You provide

The label, the value, the hint and the error text. Today: `components/app/AppInput.vue`, `AppTextarea.vue`, `AppDateInput.vue` and `AppErrorMessage.vue`, and the `.form-input` class.
