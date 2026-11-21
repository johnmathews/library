This is the Library design system. A design system is a product's design language (its colour, type, spacing, shapes and voice), the components built from it, and the rules for using them. Library is a household's own document archive: scanned letters, invoices, receipts, contracts and emails, made searchable in Dutch and English.

This document shows each part of the web app in light and dark. New screens are built from it, and the app is checked against it. It does not describe how the app works, the data model or the code. It is also not a picture of every screen.

The direction is Family Finances' Quiet, taken whole: functional and clear, but friendly and welcoming. The friendliness comes from space, soft surfaces and plain words rather than extra colour. Library and Family Finances share every token value, so the two apps read as one family. Both keep the Mosaic sidebar shell they share with journal insights.

## Content fundamentals

- Plain, and nobody speaks. No "we", and no "you" where a fact will do. Full rules and examples are in the Voice section.
- Sentence case everywhere, buttons, headings, sidebar items and field labels included. Never uppercase letter-spaced labels.
- A to-do says the fact, then the action: "3 documents need review. Review them one by one".
- An error says what failed, then what to do: "Projects didn't load. Check the connection, then try again".
- Name things as the household knows them: "Kind", "Sender", "Recipient", "Date on document", "Needs review". Never a field name or a model's jargon.
- Money is in euros with two decimals and a true minus sign: €1,284.50, −€60.00, and a + for money in. Figures are always tabular.
- Dates are short: "14 Nov 2026", "14 Nov, 09:12".
- Document data in examples is invented. Previews never show a real document, sender, name or address.

## Visual foundations

### Colour

- The page is `canvas`; every card and document tile is `panel`; quiet fills inside a card are `tile`. A card stands off the page by its fill and `shadow-card`, never by an outline.
- Settings → Appearance keeps its six page tones. Each is a value of `canvas`, and Quiet's `#f4f5f7` is the default.
- Text is `ink`. Supporting text is `ink-secondary`, at 13px or larger.
- Every control has a 1px `control-border` edge: inputs, selects, outline buttons, filter chips, checkboxes and the upload drop zone. Hairlines between rows are `line`, which is never a control's edge.
- **Violet is chrome.** `brand` marks what can be acted on: links, the active sidebar item (`brand-tint` with `brand-on-tint`), an active filter chip, the current page, and the primary button (`brand-fill` with `on-brand`). Never a state, never a document type.
- **A meaningful colour means one thing:**
  - **Yellow, the owner has something to do.** `attention` on `attention-tint`: Needs review, held emails, a job waiting on input, the to-do strip.
  - **Red, something failed.** `error-ink` on `deficit-tint`, always with an alert icon and words: a failed job, a failed upload, a form error.
  - **Green and red figures are money, in the spending and payments views only.** `income` is money in and a positive net. `deficit` is a negative net. An amount on a document is `ink`.
  - **A document type's colour** is a 4px band across the top of its tile. The defaults are `category-1` to `category-5`: Letter, Receipt, Invoice, Warranty, Contract. A colour the owner picked for a type wins.
- **Everything else is neutral**, `ink` on `tile`: tags, projects, matters, a document's type chip, and finished states. A job that succeeded or a document that was indexed takes a check icon, not green.
- Text pairs clear 5:1 in both themes, and accent pairs 4.5:1. Control edges, the type bands and chart marks clear 3:1. The checks are in `checks.json` beside this system.

### Type

- Inter Variable throughout, self-hosted. Headings at 650.
- IBM Plex Mono (`type-code`) only for strings a person copies character by character or a machine wrote: file names, checksums, API keys, job ids, tool names in Ask.
- Page titles in `type-title-1` (24px), card titles in `type-title-2` (16px), a page's lede and a document's summary in `type-lead` (15px).
- `type-body` (14px) for running text and table cells, `type-label` (13px, 600) for labels, chips and buttons, `type-caption` and `type-small` for supporting lines. Nothing is smaller than 12.5px.
- Fields are 16px on a touch screen, so iOS does not zoom into them.

### Space and shape

- 4px steps: `space-2` between chips, `space-3` between tiles on a phone, `space-4` gutters on a phone, `space-6` between cards and tiles on desktop, `space-8` page padding on desktop.
- `radius-sm` for controls, `radius-md` for cards, tiles and banners, `radius-lg` for popovers, dialogs and the question in Ask, `radius-full` for chips.
- `shadow-card` in light only. `shadow-pop` for anything that floats.

### Layout

- A sidebar of sections, a header with the page title and that view's toolbar, then cards and tiles on `canvas`. Below 640px the sidebar folds into a phone bar.
- A view's filter bar joins the header: view controls on the left, page commands on the right. The merge is a container query on the header, never a viewport one.
- Documents opens with a search field and a row of filter chips, then the to-do strip when something needs the owner, then the grid of tiles.
- A document's detail puts the page preview beside its cards: Summary, Details, Comments and History.
- Tables become lists on a phone. The page body never scrolls sideways.
- Layout is checked in Playwright at 1280, 656 and 375px.

### States

- Focus is a 2px ring in `focus` (ink) at a 2px offset. Not `currentColor`: the offset draws the ring on the surface around the control, so a violet button's white label would give a white ring on a white card.
- Hover gives outline buttons and rows a `tile` fill, and underlines a link or a tile's title.
- Work in progress keeps full colour and says what is happening ("Uploading… 2 of 5"). Only a control that cannot be used fades, and it keeps its focus (`aria-disabled`).
- Motion is short colour and opacity changes. All of it stops under `prefers-reduced-motion`.

### Charts

- A chart split by a value stacks its bands in `category-1` to `category-5` by the value's identity, never its rank, and folds the tail into "Other (N)" in `category-other`.
- A chart with no split draws spending in `chart-spending`. A refund goes below the zero line in `chart-income`.
- A month with no documents shows a `quiet` stub, so the gap reads as empty rather than missing.
- Gridlines are `chart-grid`, and the zero line is `control-border`. Labels are `ink-secondary` at 11px.
- The legend names each band with its total. A line under the chart says what the total leaves out.

## Iconography

- **The app icon** is a page drawn in lines: a page with a folded corner and two lines of writing, with a lens over its lower corner. The mark is white (`on-brand`) round-capped strokes on Family Finances' `brand-fill` square, so the two icons sit side by side in a tab bar and on a home screen. AppIcon has its sizes and files.
- **The logo** is that square at 32px beside "Library" in Inter at 650, in sentence case. When the sidebar collapses, the square stays.
- **Icons** are inline SVG on a 24px grid with 2px round-capped strokes in `currentColor`, drawn at 16px beside a label. The app draws its icons itself and loads no icon library.
- An icon never stands alone without an `aria-label`, and never replaces a word a person must read.
