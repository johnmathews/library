# Library on Quiet — brand design

**Status:** approved by the owner on 2026-11-21, with all of §8 ("approve all, but show me some mockups as artifacts"). **Last updated:** 2026-11-21. **Last verified:** 2026-11-21 — values copied from Family Finances' `brand/design-system/project/tokens.json` at its 2026-11-18 release; contrast figures are that system's measurements. **Covers:** brand/design-system. **Supersedes:** none.

> Purpose
>
> Says how Library adopts Family Finances' "Quiet" design direction, what it keeps of its own,
> and every call made on the owner's behalf, so the design system and the restyle can be built
> from it. Nothing is built until this is approved. §8 lists each call so it can be vetoed.

## 1. Intent

In the owner's words (2026-11-21): give Library "a good favicon and logo", and "bring it more in
line with the principles and design choices of the finances application (primarily) and the
journal insights app (secondary)".

Family Finances' direction is called Quiet: "functional and clear, but also friendly and
welcoming". Its friendliness comes from subtraction: cards without outlines, plain words, sentence
case, and colour only where it means something. Library takes Quiet whole, so the two apps read as
one family, and keeps the Mosaic sidebar shell that both share with journal insights.

The two apps differ in what they show. Finances is figures; Library is documents. So Library's
first impression is a grid of document tiles, not a lead figure, and its colours carry document
states rather than money.

## 2. Layers

- **First impression:** Documents. A search field, a row of filters, and the grid of tiles, each
  with a title, a sender, a date and a type. Calm, with few controls on show.
- **Depth:** a document's detail (preview, details, comments, history), Ask, the spending charts,
  Vocabulary, Jobs and Admin. Dense, in the same quiet surfaces.

## 3. Rules

### 3.1 Taken from Quiet unchanged

- **Surfaces and edges:** cards have no outline; they stand off `canvas` by `panel` and
  `shadow-card` (light only). Controls keep a 3:1 `control-border` edge. Hairlines between rows use
  `line`. Radius 8px for controls, 10px for cards, 12px for popovers and dialogs, full for chips.
- **Colour roles and values:** every role in Finances' `tokens.json` (`canvas`, `panel`, `tile`,
  `line`, `control-border`, `ink`, `on-ink`, `ink-secondary`, `error-ink`, `focus`, `brand`,
  `brand-fill`, `on-brand`, `brand-tint`, `brand-on-tint`, `brand-solid`, `attention`,
  `attention-tint`), at the same values in both themes. Text pairs clear 5:1, accent pairs 4.5:1.
- **Violet is chrome:** `brand` marks what can be acted on (links, the active sidebar item, an
  active filter chip, the primary button). Never a state, never decoration.
- **Type:** Inter Variable throughout, self-hosted, headings at 650. IBM Plex Mono only for strings
  copied character by character or written by a machine (file names, checksums, API keys, job ids).
  Scale: 24 page title, 16 card title, 15 lead, 14 body, 13 labels and captions, 12.5 the smallest.
  Labels in sentence case. Figures in `tabular-nums`.
- **Space:** 4px steps; 16px phone gutter, 24px between cards on desktop, 32px page padding.
- **States:** focus is a 2px `currentColor` ring at 2px offset. Work in progress keeps full colour.
  Motion is short colour and opacity changes, off under `prefers-reduced-motion`.
- **Icons:** inline SVG on a 24px grid, 2px round-capped strokes in `currentColor`, 16px beside a
  label, never alone without an `aria-label`.

### 3.2 Library's own colour meanings

Quiet's rule stands: a meaningful colour means one thing.

| Colour | Library means | Where |
| --- | --- | --- |
| `attention` on `attention-tint` (yellow) | the owner has something to do | "Needs review", held emails, a job waiting on input |
| `error-ink` on `deficit-tint` (red) | something failed | failed jobs, errors, a failed check |
| `income` / `deficit` (green / red figures) | money in / a negative net | the spending and payments views only |
| `category-1` … `category-5`, `category-other` | a series in a chart | charts only |
| a document type's colour | the type the owner picked a colour for | the document tile (shape decided in a later mockup) |

Everything else is neutral: `ink` on `tile`. That includes finished jobs, tags, projects and
matters, which today get one of ten hashed colours (§8 call 3).

### 3.3 The logo and icon

The app icon is option B of the [app-icon mockup](../../../brand/design-system/mockups/261121-app-icon/README.md):
a page with a folded corner and two lines of writing, drawn in white round-capped strokes, with a
lens over its lower corner, on Finances' `brand-fill` square. The sidebar's logo is that square at
32px beside "Library" in Inter 650, sentence case. When the sidebar collapses, the square stays.

### 3.4 Layout

- The sidebar and header as today (the Mosaic shell shared with journal insights). The page title
  sits in the header, and a view's filter bar joins it (`docs/frontend-view-principles.md` §5.1).
- Tables become lists on a phone. The page body never scrolls sideways.
- Settings → Appearance keeps its six page tones; each is a value of `canvas`, and the default
  becomes Quiet's `#f4f5f7`.

## 4. Voice

Finances' rules, unchanged: plain, nobody speaks (no "we", no "you" where a fact will do),
sentence case, a fact then an action, buttons as a verb and its object, no exclamation marks, no
"seamless", "effortless", "powerful", "Oops". Rewrites from Library's own strings (the full table
lives in `voice.md`):

| Instead of | Write |
| --- | --- |
| LIBRARY (sidebar) | Library |
| SENDER (field label) | Sender |
| Needs review (count only) | 3 documents need review. Review them |

## 5. Scope: what the design system will contain

Foundations: tokens (§3), type, space, radius, shadow, voice. Components: AppShell, AppIcon,
Button, TextLink, Card, Banner, InfoHint, TextField, Select, Choice, FilterChip, FilterPopover,
PageHeader, DocumentTile, SummaryList, TagChip, Pagination, Dialog, Toast, EmptyState, AskTurn,
ChartStyle, DataTable, FileUpload, and the cover.

## 6. Non-goals and anti-goals

- **Non-goals:** new features or routes; a component library swap; removing Tailwind; changing
  what the charts show; changing journal insights or Finances.
- **Anti-goals:** outlines around cards; boxes inside boxes; capitalised labels; colour as
  decoration; a different violet from Finances'.

## 7. Constraints carried over

Library's own rules stay: dark mode on every surface, layout asserted in Playwright at 1280, 656
and 375px, container queries for nested panes, the asset-licensing check. Two of its rules change:
the uppercase label recipe (`docs/frontend-view-principles.md` §5) becomes sentence case, and
`.card`'s outline goes.

## 8. Calls made on the owner's behalf

1. **Same values as Finances, not a Library variant.** One violet, one canvas, one ink. The two
   apps look like one family because they are the same system with different content.
2. **The primary button darkens** from violet-500 to `brand-fill` (`#5d47de`): white on it goes
   from 3.66:1 to 6.14:1. Violet text moves to `brand` for the same reason (3.66:1 → 6.14:1).
3. **Tags, projects and matters lose their hashed colours** and become neutral chips. Ten
   decorative colours are what Quiet subtracts; a tag is read by its name. Veto this and they keep
   colours drawn from `category-1…5`.
4. **Finished states are neutral.** A job that succeeded, an active user, a passing check: `ink`
   on `tile` with a check icon, not green. Green stays money-in only, as in Finances.
5. **The page tones stay** (the owner's answer), with Quiet's canvas as the new default.
6. **Inter is self-hosted** as Inter Variable, as in Finances, instead of loaded from Google Fonts.
7. **Document-type colours stay a feature** but their shape on an outline-less tile is a separate
   mockup before the document screens are restyled.
