The page header: a view's title, an optional lede, and one toolbar with the view's controls on the left and its page commands on the right.

## Use

- **Title** in `type-title-1`, one per screen, sentence case ("Recently deleted").
- **Lede** in `type-lead` and `ink-secondary`, at most 60 characters a line. One or two sentences that say what the view holds ("A project is a collection of documents gathered by hand, such as a house purchase or a tax year.").
- **Controls** (left): the search field and the filter chips, or a view's selects. Labels above selects are `type-label` in sentence case, never the old uppercase `.filter-label`.
- **Commands** (right): at most one primary ("New project"), the rest outline ("Date on document" for the sort).
- The toolbar joins the header row when there is room. The merge is a container query on the header (`@5xl`), never a viewport one, because the sidebar's width changes the room (`docs/frontend-view-principles.md` §5.1).
- Below the toolbar's width the controls wrap; the search field takes the full width on a phone.

## States

None of its own. Each control keeps its own states.

## You provide

The title, the lede, the controls and the commands. Today: `components/app/PageHeader.vue` (its `description`, `#controls` and `#actions` slots).
