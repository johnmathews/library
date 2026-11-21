A filter chip: one filter on the Documents screen, as a pill that opens a FilterPopover and shows its value once it is in use.

## Use

- 32px, `radius-full`, 1px `control-border` on `panel`, `type-label`. The name and a chevron when idle: "Kind", "Sender", "Recipient", "Date", "Tag", "Project", "Matter".
- **In use:** `brand-tint` fill, `brand` edge, `brand-on-tint` label showing the value ("Sender: Energiebedrijf Noord", "Date: last 3 months"), and an × that clears it.
- **Toggle chips** ("Needs review") have no panel: a press turns them on, and they turn violet. Each is a checkbox behind the pill, so a screen reader hears its state.
- **Brought by a link:** a filter no chip sets (one tag, from a document's detail) shows as an in-use chip with only the × that leaves it.
- **More:** a dashed edge in `ink-secondary`, for the filters that do not fit the row.
- The row wraps on desktop and scrolls sideways on a phone, inside its own box. It sits beside the search field, in the header's toolbar.

## You provide

The filter's name, its value as words, and the handlers to open and to clear. The chip and its × are two sibling buttons, named "Sender: Energiebedrijf Noord" and "Clear sender". Today: `components/app/FilterPill.vue` in `components/DocumentFilterBar.vue` and `components/facets/FacetFilterBar.vue`.
