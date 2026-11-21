A filter popover: the small panel a FilterChip opens, holding that one filter's controls and a button that applies it.

## Use

- `panel`, `radius-lg`, `shadow-pop`, a `line` edge, 290px wide, anchored under its chip.
- Long lists (senders, recipients, tags, matters) get a search field at the top and a list of checkboxes, each with its document count in `type-small` and tabular figures.
- The footer has a ghost "Clear" (only while the filter is in use) and a primary that names the count: "Show 12".
- Changes apply as they are made, so the count is live and "Show 12" only closes the panel. Escape or a press outside closes it too, and Escape puts focus back on the chip.
- On a phone it opens as a sheet from the bottom, over a scrim.

## States

A hovered row takes `tile`. A row with no documents under the other filters stays, with its count at 0.

## You provide

The filter's controls and the count the current choice would show. Today: `components/app/AppPopover.vue` and `AppMultiSelect.vue`.
