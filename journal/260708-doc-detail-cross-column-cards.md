# Document-detail layout: cross-column cards & empty preview card

Bug report from the live site (`/documents/137`, a note): dragging the
**Comments** card to the right-hand column made it **disappear**; a **thin
horizontal line** showed above the markdown reader; and in Edit layout the
right-column **drag handles were misaligned** (top two overlapping, a third
detached from any panel).

## 1. Root causes

Two bugs in `frontend/src/views/DocumentDetailView.vue`, both surfacing on the
two-column card layout.

### 1.1 Disjoint column templates (comments vanished)

The two columns each rendered card bodies from their *own* `v-if cardId===…`
chain, and the two chains were **disjoint**: the right column only knew
`preview` / `markdown` / `series-chart`; the left only `notes` / `metadata` /
`comments` / `actions` / `history`. `moveCard` correctly moved a card's id into
the other column's persisted list, but the destination template had **no branch
for that id**, so the `v-for` wrapper rendered empty and Tailwind's
`empty:hidden` collapsed it. The card vanished — and, because the move was
persisted, stayed gone. This affected **every** card, not just comments:
cross-column drag was advertised in the UI ("move them between columns") but
never actually worked for any card.

### 1.2 `cardPresent('preview')` always true (thin line + stray handle)

`cardPresent` returned `true` for `preview` unconditionally. A note has no
image/PDF and its original is text, so every inner branch of the preview card
was false, yet the outer `#document-preview-card .card` still rendered — an
**empty card** that reads as a thin line. In edit mode this empty card's drag
handle sat almost on the markdown card's handle (the "overlapping" pair), and
the not-yet-resolved `series-chart` wrapper showed a lone handle (the "detached"
one).

## 2. Fix

1. **One shared card renderer.** Both columns now render from a single template
   defined once via VueUse's `createReusableTemplate`: `<DefineCard>` holds the
   drag handle + all eight card bodies; each column's `v-for` reuses it via
   `<ReuseCard :card-id>`. A card draws its body in whichever column holds it, so
   cross-column drag keeps it visible.
2. **Honest `cardPresent('preview')`.** Present only when the card would render
   real content — an image/PDF viewer, a downloadable binary original, or (once
   the text has loaded and turned out empty) the "no preview" fallback. A
   text-only note drops the preview card entirely, removing the thin line and its
   stray handle. The `markdownData !== null` guard waits for the text fetch so a
   note never flashes the fallback before its body arrives.

The lingering `series-chart` handle was already self-healing: `DocumentSeriesTrend`
emits `presence:false` after load for a doc with no series, which removes the card.

## 3. Tests

The pre-existing cross-column test only asserted the persisted `cardColumns`
*data*, never that the moved card's **body rendered** — exactly the gap that let
this ship. Added to `DocumentDetailView.spec.ts`:

- moving `comments` to the right column renders `document-comments` **inside**
  `#document-preview-column` (and no longer in the left);
- a text-only note (`source: 'note'`, `text/markdown`, no searchable PDF)
  renders **no** `section-card-preview` / `#document-preview-card`, and no
  `card-drag-handle-preview` in edit mode.

Full frontend unit suite green (902 tests), `type-check` and `lint` clean.
Real-stack behaviour is gated by the CI Playwright e2e before deploy.
