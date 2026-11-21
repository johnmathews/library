A select: a native dropdown in the field's shape, for a filter, a sort or a setting.

## Use

- 34px tall (compact 30px), `panel`, 1px `control-border`, `radius-sm`, a chevron in `ink`. Native `<select>`, so the phone's own picker opens.
- The label sits above in `type-label`, sentence case: "Sort", "Kind", "Tiles per row", "Status".
- **Sort** pairs the select with an icon button for the direction, named by its state ("Descending, press for ascending").
- **16px text on a touch screen**, the compact form included, for the reason TextField gives.
- Many options with search (senders, tags) is a FilterPopover, not a select.

## States

- Unavailable: 40% opacity with `aria-disabled`, and a hint that says why ("Sorted by relevance while searching.").
- Focus: the 2px `focus` ring at a 2px offset.

## You provide

The options and the current value. Today: `components/app/AppSelect.vue` and the `.form-select` class in the filter bars.
