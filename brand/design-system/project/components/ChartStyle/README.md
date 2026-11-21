Chart style: how Library draws the spending charts. Bars by month, split into bands by a value or drawn as one series, on a faint grid, with a legend that names each band and its total.

## Use

- A chart is a Card: the chart's name in `type-title-2`, its question in `type-caption`, and its total in `type-figure` with tabular figures.
- **Split by a value** (sender, a facet): stacked bands in `category-1` to `category-5` by the value's identity, never its rank, so a sender keeps its colour on a refetch. The tail folds into "Other (N)" in `category-other`. A colour the owner picked for a value wins.
- **No split:** one series in `chart-spending`. A refund goes below the zero line in `chart-income`.
- **A month with no documents** shows a 3px `quiet` stub, so the gap reads as empty.
- Gridlines in `chart-grid`, the zero line in `control-border`. Axis labels in `ink-secondary` at 11px, euro ticks as "€200" and "−€100".
- The legend sits under the chart in `type-caption`: a 10px swatch, the band's name and its total in `ink`. A swatch never stands alone.
- A line under the chart says what the total leaves out: "1 document is left out because its amount in pounds has no exchange rate."
- Green and red appear only here and in payments. Nowhere else in Library means money.

## You provide

The bands, the buckets and the total. Colours come from the tokens by role or from the owner's stored pick, never from the data. Today: `components/spending/SpendingChart.vue`, `SpendingLegend.vue`, `SpendingFooter.vue`, `spending/palette.ts` and `utils/splitPalette.ts`.
