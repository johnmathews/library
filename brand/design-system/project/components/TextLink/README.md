A link: `brand` text that goes somewhere, for navigation inside a card, a step back, and a following step that is not an action.

## Use

- **Card link:** `brand` at 600, no underline until hover. Top right of a card: "All jobs", "Show 4 changes".
- **Back link:** an arrow and the place it returns to, at 13px: "← Documents".
- **Inline link:** in running text, `brand` at 500 and underlined at a 3px offset ("clear the filters").
- **Quiet link:** `ink` and underlined, on a tinted ground where violet would clash: the to-do strip's "Review them one by one".
- A document's title on its tile is `ink`, not violet: the whole tile is the link, and its title underlines on hover.
- A link names where it goes. Never "click here" or "more".

## States

Hover underlines. Focus takes the 2px `focus` ring at a 2px offset.

## You provide

The words and the destination. Today: `components/app/AppBackLink.vue` and the `text-violet-600` links across the views, which move to `brand`.
