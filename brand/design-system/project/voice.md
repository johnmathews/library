# Voice

Library writes plainly and calmly, and nobody speaks. The app is a household's own archive, not a company, so it says what is there, what happened and what can be done next, in the fewest words that are still exact. It is friendly through being clear and never blaming, not through exclamation marks. These are Family Finances' rules, unchanged, so the two apps sound like one.

## Rules

- **Lead with the thing.** The first sentence says what is there or what happened: "312 documents. 3 need review."
- **Nobody speaks.** No "we" and no "I". Address the household as "you" only where a sentence needs a subject. "The library has no documents yet" rather than "There are no documents in your library yet".
- **A fact, then an action.** A to-do or an error says what is true, then the next step as a link or button: "3 documents need review. Review them one by one".
- **Sentence case everywhere**, buttons, headings, sidebar items, tabs and labels included. Never uppercase letter-spaced labels.
- **Buttons are a verb and its object.** "Upload documents", "Delete permanently", "Show 12". Never "OK", "Submit" or "Click here".
- **Numbers as figures.** "3 documents", never "three". Euros with two decimals, a true minus sign (−) and a + for money in. Short dates: "14 Nov 2026".
- **Count exactly.** Write the singular or the plural for the number on screen, never "document(s)".
- **Name things as the household knows them.** "Kind", "Sender", "Date on document", "held email", never a column name, a MIME type or a model's jargon.
- **Say what a figure leaves out.** "1 document is left out because its amount in pounds has no exchange rate."
- **Never blame, never guilt, never apologise.** "That date doesn't exist", not "You entered an invalid date". A failure says what failed and what to do, not "Sorry".
- **No exclamation marks, and no hype.** None of "seamless", "effortless" or "powerful".
- **One idea per sentence.** No semicolons, and no dashes joining two clauses.

## Instead of, write

Each row is a string in the app today.

| Instead of | Write |
| --- | --- |
| LIBRARY (the sidebar's heading) | Library, beside the app icon |
| Recently Deleted (sidebar and page title) | Recently deleted |
| SORT · TILES PER ROW (`.filter-label`, uppercase) | Sort · Tiles per row |
| YOU · LIBRARY (Ask's role labels, uppercase) | You · Library |
| 3 documents need review (a red button) · Showing · clear | To do · 3 documents need review. Review them one by one |
| Review these one by one → | Review them one by one |
| 2 emails held → (a violet button) | 2 emails are held. See held emails |
| 3 background job(s) running | 3 background jobs running |
| Something went wrong — check your connection and try again. | Projects didn't load. Check the connection, then try again |
| Sorry, the document could not be loaded. Try again later. | The document didn't load. Try again |
| Success · Important (a banner's default heading) | Name what happened: "2 documents uploaded and indexed" |
| Your settings have been saved. | Settings saved |
| Drop files or click to browse | Drop files here, or choose files |
| PDFs, photos, and text/markdown notes, up to 100MB each. | PDFs, photos and text notes, up to 100 MB each. |
| There are no documents in your library yet. Upload your first document. | No documents yet. Documents arrive by upload, by email or as a note. Upload documents |
| Accept all senders — no allowlist is configured. | No sender allowlist is set, so every sender is accepted. |

## Banned words

"Seamless", "effortless" and "powerful" claim rather than say. "Oops" and "Whoops" make light of a failure the household has to deal with. "Sorry" adds a sentence and says nothing about what to do: the fact and the next step are the courtesy. The gate in `checks.json` rejects all six in previews.
