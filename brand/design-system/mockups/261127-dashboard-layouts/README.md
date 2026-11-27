# Which dashboard layout by default

**Question:** which of the trial's four dashboard layouts (PR #248) a new device starts on (issue
#246). All four stay in the Layout menu; the owner chose that before this mockup.

**Status:** decided (2026-11-27)

**Link:** https://claude.ai/artifact/6ozj7FthncW8wTECh1nJpA

## Options

Each option is the shipped layout, redrawn by `proto.js` from the app's rules with invented
documents and the default fields. `try.html` draws one at the device's own width.

- **A. Tiles.** Today's default: a page preview, the summary, 4 per row wide and 2 on a phone.
- **B. Rows.** Wide, thin cards: title, then fields; type colour down the left edge.
- **C. Table.** A column per field. Shows as the list below a ~912px results area (every phone,
  portrait tablets, a desktop window under ~1230px with the sidebar open). Recommended: the owner
  said the tiles don't help them stay organised, and columns are the layout built for scanning;
  it also gives the list on a phone with one setting.
- **D. List.** One line per document, fields as quiet text on the right (under the title on a
  phone).

## Decision

**B · Rows**, on every device, 2026-11-27. Asked which layout a phone and which a desktop or
tablet should start on, the owner answered "Rows" to both, over the recommended C. The other
three stay in the Layout menu, and the tile settings stay ("Keep them"). Shipped under
`library:doc-layout-v2`; recorded in `journal/261127-dashboard-rows-default.md`.

## Rounds

- **1, 2026-11-27:** the four layouts at 1440px and 375px in both themes, plus `try.html`. Decided: B.
