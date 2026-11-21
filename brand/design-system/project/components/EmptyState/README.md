An empty state: what a view shows when it has nothing to show, saying why and what to do next.

## Use

- Centred in its card, `space-12` above. A title in `type-title-2`, one or two sentences in `ink-secondary`, and one button.
- Say why it is empty in the household's terms: "No documents yet" or "No documents match", never "No data".
- The action is the next step: "Upload documents", "Clear the filters".
- An empty filter result names what was searched ("Nothing matches “boiler” with the sender and date filters.") and offers to clear the filters.

## You provide

The reason, the sentence and the action. Today: the empty states in `views/DocumentListView.vue`, `views/JobsView.vue`, `views/HeldEmailsView.vue` and `components/spending/SpendingEmptyState.vue`.
