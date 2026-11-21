A document's tile: its thumbnail, title, chips, sender, date and amount on a `panel` card, with the type's colour as a 4px band across its top edge.

## Use

- `panel`, `radius-md`, `shadow-card`, no outline. The whole tile is one link (a stretched link on the title), so it is one tab stop.
- **The type band:** 4px across the top in the type's colour. The defaults are `category-1` Letter, `category-2` Receipt, `category-3` Invoice, `category-4` Warranty, `category-5` Contract. A colour the owner picked for a type in Vocabulary wins. A document with no kind, or a kind with no colour, has no band.
- **Thumbnail:** the first page on a `tile` ground, 4:3, either the whole page or the top of it at full width (Settings → Appearance). A text note or an email with no page image shows a facsimile instead: its title, sender and date.
- **Title** at 600 in `ink`, 15px (14px on a phone). **Chips** under it: Needs review first, then the type as a neutral TagChip. **Then** the sender, and the date with the amount in `type-caption` and `ink-secondary`. Then the summary, 2 lines.
- The owner chooses which fields show and in what order (Dashboard tile fields). Needs review is always first.
- Grid: as many as fit, or 3 to 6 per row as the owner picks, `space-6` apart on desktop. On a phone 2 per row, `space-3` apart, with tighter padding and no summary.

## States

- Hover: the title underlines.
- Needs review: the yellow Needs review chip, with the reason in `type-small` beside it when there is one.
- Processing: a neutral "Processing" chip. Failed: the red Failed chip with an alert icon.
- Protected PDF: a lock icon and "Protected PDF" in place of the thumbnail.

## You provide

The document's fields, the type's colour and the owner's field order. Today: the tile in `views/DocumentListView.vue` (`app-doc-card`) and `utils/kindColor.ts`, whose tile border becomes the band.
