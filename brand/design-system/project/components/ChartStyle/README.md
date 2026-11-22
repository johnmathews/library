Chart style: how Library draws the spending charts. Bars by month, split into bands by a value or drawn as one series, on a faint grid, with a legend that names each band and its total.

## Use

- A chart is a Card: the chart's name in `type-title-2`, its question in `type-caption`, and its total in `type-figure` with tabular figures.
- **Split by a value** (sender, a facet): stacked bands in `category-1` to `category-5` by the value's identity, never its rank, so a sender keeps its colour on a refetch. The tail folds into "Other (N)" in `category-other`. A colour the owner picked for a value wins.
- A bar fills about two thirds of its period's slot, at most 120px wide: thick when a wide card holds few periods, thinner to fit when it holds many, never a slab.
- **One order everywhere:** the largest band (by the range's total) sits on the baseline and Other on top; the legend lists largest first. Hovering a bar shows every band with money in it, top segment first so the tooltip reads like the bar, then the bar's total. The tooltip is `type-body` (14px), its period and total at 600. A canvas does not inherit the page's font, so the chart names `--font-sans`.
- **No split:** one series in `chart-spending`. A refund goes below the zero line in `chart-income`.
- **A month with no documents** keeps its place on the axis and shows a 3px `quiet` stub at the zero line, so the gap reads as empty. The stub has no tooltip and opens nothing.
- Gridlines in `chart-grid`, the zero line in `control-border`. Axis labels in `ink-secondary` in `type-small` (12.5px), the smallest text in the app; money ticks name the currency like every figure on the card: "EUR 200", "−EUR 100".
- The legend sits under the chart in `type-caption`: a 10px swatch, the band's name and its total in `ink`. A swatch never stands alone.
- A line under the chart says what the total leaves out: "1 document is left out because its amount in pounds has no exchange rate."
- Green and red appear only here and in payments. Nowhere else in Library means money.

## You provide

The bands, the buckets and the total. Colours come from the tokens by role or from the owner's stored pick, never from the data. Today: `components/spending/SpendingChart.vue`, `SpendingLegend.vue`, `SpendingFooter.vue`, `spending/palette.ts`, `spending/periods.ts`, `spending/money.ts` and `utils/splitPalette.ts`.
