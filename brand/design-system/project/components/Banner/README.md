A banner: one line across the top of a screen's content that says the owner has something to do, that something is worth knowing, that something finished, or that something failed.

## Use

- **To do:** `attention-tint` ground, the label "To do" in `attention` at 650, then each item as a fact and a quiet link: "3 documents need review. Review them one by one", "2 emails are held. See held emails". It replaces the red Needs review button and the violet held-emails button on Documents.
- **Information:** `tile` ground, `ink` text, an info icon. A state that is fine but worth knowing: "4 documents are processing."
- **Done:** `tile` ground with a check icon, neutral: "2 documents uploaded and indexed."
- **Error:** `deficit-tint` ground, an alert icon and the words in `error-ink`, then what to do: "Projects didn't load. Check the connection, then try again."
- `radius-md`, 10px by 14px, 13.5px text. Items wrap on a phone.
- A banner names what happened. It never opens with a default heading such as "Success" or "Important".

## You provide

The kind, and the items, each a fact and a link or button. Today: `components/app/AppBanner.vue` and the attention row in `views/DocumentListView.vue`.
