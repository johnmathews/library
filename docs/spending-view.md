# The spending view

**Status:** active. **Last updated:** 2026-09-02 (§3's outcome table gains the **second reason** a filter goes unused. Until now the view could only say a word was not in the vocabulary; a drafted filter can also be dropped because it cannot be *combined* with one already there — a document carries at most one value per facet, so "software and services" is expressible as either but not both, and asking for both matched nothing at all. That drew an empty chart, which looks exactly like having spent nothing. The draft now keeps the first filter and says the second was left out. No row was added or removed from the table; the two "could not express" cells are reworded to "could not use", since the words were fine and the combination was not.) Earlier: 2026-09-01 (the rule editor, issue #135. New §4.1 "Editing what a chart matches" — the clause rows, the split-axis picker, preview-then-apply, the three things the editor warns about, and why the chart's question text is deliberately left alone. §4's toolbar list gains **Edit rule** and its **Split** entry now points at §4.1 for changing the axis; §2's card-menu paragraph says where rule editing lives, since a closed list of menu actions otherwise reads as "there is no such thing". §8 loses two entries that this change makes false — "a saved chart's rule cannot be edited" and "the split axis is a toggle, not a picker" — and gains the question-text limit in their place.) Earlier: 2026-08-31 (new: the user-facing guide to `/charts` — what a spending question is, the board and the workspace, asking in plain language, reading the footer, drilling through to documents, colour, and the current limits. Companion to [`charts.md`](charts.md), which documents the engine underneath).
**Last verified:** 2026-09-02 — method: partial, scoped to §3's new paragraph, and checked against the behaviour rather than against `charts.md`. The claim that the narrowed draft previews a real number and saves is pinned by two route tests (`test_a_drafted_same_facet_pair_narrows_and_says_so_instead_of_previewing_zero` asserts `60.00` and a 201 sibling), both confirmed to red with the fix reverted. The claim that the message gives the combination reason rather than the vocabulary one is asserted in both directions — `"could not be combined" in message` and `"not in the vocabulary" not in message` — because every value in that fixture IS in the vocabulary. The three outcome rows themselves were re-read against `QuestionDraft.vue`'s branches and are unchanged. §§1-2, 4-9 were not re-checked. Earlier: 2026-09-01 — method: partial, scoped to §2, §4 and the new §4.1; checked against the shipped code claim by claim. Workspace controls re-enumerated from `SpendingWorkspaceView.vue`'s `data-testid`s — five in `workspace-toolbar` plus `workspace-edit-rule`, which is in `PageHeader`'s `#actions` slot and so is NOT inside the element hidden below `@3xl/workspace` (that placement is what §4's "there at every window width" rests on). The clause row's three controls read from `ChartRuleEditor.vue`'s template, with the operator `<select>`'s two options read verbatim as `is` / `is not`; the split picker's options confirmed to come from the facet vocabulary plus a "No split" entry, **not** from `chart.default_split` alone — that is the claim §4 and §8 changed, so it was read rather than assumed. `SpendingCard.vue`'s menu testids re-enumerated as rename / move up / move down / delete, stated here positively (rule editing is in the workspace) so the sentence cannot rot the way its predecessor did. "No language model" checked twice: the `preview_rule` handler in `src/library/api/spending.py` contains no model call, and `test_previewing_does_not_call_the_model` leaves the Anthropic stub unconfigured so any call would raise. "The question text is not rewritten" read at the call site — the editor's `apply()` sends only `rule` and `default_split` — and pinned by `test_patching_a_rule_leaves_question_text_untouched`. Carried forward unchanged and not re-checked: the six-band fold, the five drillable buckets, the four grains, and everything in §§5-7 and §9. **Not deployed:** this describes branch work, and the previous stamp's `git_sha` claim is withdrawn rather than copied — re-observe `/healthz` after the deploy.
**Covers:** frontend/src/views/SpendingWorkspaceView.vue, frontend/src/views/SpendingBoardView.vue, frontend/src/components/spending/, src/library/api/spending.py

> **Note on examples.** This repository is public. Every sender, category and
> amount below is invented.

## 1. What it is

`/charts` answers one shape of question about your archive:

> *How much am I spending on X, per month, and where is that money going?*

You write the question once, it is saved, and it stays answerable as new
documents arrive. A saved question is called a **chart**. The board at
`/charts` is your collection of them; opening one gives you the **workspace**,
where you change how it is sliced and follow any number back to the documents
it came from.

It is built on the money the archive already knows about — the amounts
extraction pulled out of your documents, the facet labels on them, and the
payment-identity rules that stop one payment documented twice from counting
twice. [`charts.md`](charts.md) documents that engine; this document is about
using it.

## 2. The board (`/charts`)

One card per saved chart. Each card shows:

- **the chart's name**;
- **a headline figure** — the most recent **complete** period, named ("July"),
  with the change from the period before it. The current, partial period is
  drawn on the chart but is never the headline: a half-finished month compared
  against a full one is a comparison that is always wrong and never looks it;
- **a compact chart** of the last twelve periods;
- **a legend** naming the split values in play;
- **a needs-attention line**, when the chart's own accounting has something
  unresolved (see §5).

The delta is deliberately **not coloured** red or green. Spending going up is
not good or bad without knowing what it was spent on, and colouring it would
assert a judgement the archive cannot make.

Cards are ordered by you, never by size. Reorder by dragging a card, or from
its overflow menu — **Move up** / **Move down**, which is the keyboard-reachable
path and does exactly the same thing. The menu also holds **Rename** and
**Delete** (delete asks for confirmation first). Changing *what a chart
matches* is not on this menu: open the chart and use the rule editor (§4.1).

If you have no charts yet, the board offers **proposals** instead: "All
spending" first, then the facet values with the most money behind them, each
with its document count and date span. Accepting one saves it as an ordinary
chart. Ignoring them costs nothing — they are proposals, not something the
system creates on your behalf.

## 3. Asking a question

The board's input takes plain language — *"how much do I spend on software"* —
and drafts a chart from it. Because the vocabulary of facets is a closed set,
there are **three** possible outcomes, and the view distinguishes them:

| outcome | what you see |
| --- | --- |
| **expressible** | the rule it derived, the split it proposes, and a live preview. Save it. |
| **partly expressible** | the same, **labelled an approximation**, plus what it could not use. Save it if the approximation is what you meant. |
| **not expressible** | what it could not use, and **no preview**. Save is disabled. |

There are two reasons something goes unused, and the message says which. Most
often a word is simply **not in the vocabulary**. Less obviously, a filter can
be dropped because it **cannot be combined** with one already there: a document
carries at most one value per facet, so *"software and services spending"* can
be filtered to one of them but not to both at once. Asking for both would match
nothing at all, and a chart of nothing looks exactly like having spent nothing
— so the draft keeps the first filter, tells you the second was left out, and
labels the result an approximation.

The third case matters more than it looks. If none of your question survived
translation, the rule left over would match *every* row in the archive — so
previewing it would answer a narrow question with your total spending, which
is the most confidently wrong answer this feature could give. It shows you
nothing instead, and tells you which words it did not recognise.

## 4. The workspace (`/charts/:id`)

Opening a card gives you the chart at full size with a toolbar:

- **Grain** — week, month, quarter or year.
- **From / To** — the date range.
- **Split** — the chart's split axis, on or off. To change *which* facet it
  splits by — or to give a chart a split axis it never had — use the rule
  editor (§4.1).
- **Currency** — the display currency.
- **Edit rule** — opens the rule editor (§4.1). It sits beside the chart's
  title rather than in the toolbar, so it is there at every window width.

The range **filters the data** rather than clamping the axis, so the headline
figure and the drawing can never disagree with each other.

The chart is a **stacked bar** per period. The stack's height is the total,
and that total does not change when you turn the split on or off — that
invariance is the point: slicing the money differently must never change how
much of it there is. The y-axis always includes zero, because a refund can
push a bucket below the line.

**The legend** names every split value with its total. Clicking one **isolates**
it; modifier-clicking **excludes** it. This is a display filter only — the
headline stays the number the archive reported, and a separate line tells you
what you are currently looking at. An isolate that quietly rewrote the headline
would break the one promise the chart makes.

### 4.1 Editing what a chart matches

**Edit rule** opens the chart's rule as a list of rows. Each row is a facet, an
**is** / **is not**, and one or more values — for example *Category is Software
or Services*. Add rows, remove them, change them. The values are a closed list
you pick from: a value that is not in the vocabulary cannot be saved, so there
is nothing to type and nothing to spell wrong. (If a value is genuinely missing,
add it in `/vocabulary` first — that is a change to the archive's vocabulary,
not to this chart.)

The same panel sets the **split axis**, including on a chart that was created
without one.

**Preview, then apply.** Preview answers the edited rule and shows you the
total and the bars it would produce. Nothing is written until you press Apply,
and Cancel leaves the saved chart exactly as it was. Unlike asking a question in
plain language (§3), previewing an edited rule involves no language model — it
is just the archive, answered again.

After you apply, the chart re-reads its numbers, so the rows you can see and the
figures underneath them are never describing different rules.

Three things it will tell you about rather than silently allow:

- **Removing the last row** leaves a chart that matches *all* spending in the
  archive. That is a legitimate chart — it is what "All spending" is — but it is
  rarely what you meant by deleting a filter, so it asks you to confirm.
- **Two "is" rows on the same facet** can never both be true: a document has one
  category, one cost type, and so on. Such a rule matches nothing at all, and
  the editor says so rather than letting you save a chart that reads "you spent
  nothing".
- **A value that has been deleted or merged** in the vocabulary since the chart
  was saved shows as a flagged row you can fix. It is never quietly dropped —
  quietly dropping it would change what the chart matches without telling you,
  and this editor is how you repair exactly that situation.

**The chart's question text is not rewritten.** The heading keeps the question
you originally asked; the rows are the authoritative statement of what the chart
matches. That is deliberate: only you can say whether a reworded rule still
answers the same question, and a heading the system rewrote for you would be
asserting something it cannot check. If the heading no longer reads right,
rename the chart (§2).

## 5. The footer: nothing is excluded silently

Under every chart is an accounting of everything the chart's rule touched but
its total did **not** count. This is the part worth reading.

```
EUR 1,155.18 across 15 payments from 18 documents
  including 1 refund netted off              -EUR 49.00

  excluded from the total
     2 coverage_limit                     EUR 20,000.00
     1 estimate                              EUR 450.00
  needs attention
     3 documents uncategorised                EUR 89.20
  could not be converted
     SEK · 1 document                        SEK 450.00
```

Three blocks, and the distinction between them is the whole idea:

- **A refund is netted, never excluded.** It *is* in the total and it lowers
  it. Listing it as excluded would read as money the chart ignored, which is
  the opposite of what happened.
- **Excluded from the total** is money correctly not counted as spending — a
  policy's coverage ceiling, a quote, an opening balance.
- **Needs attention** is money nothing has *decided* about yet: a document
  whose amount has no kind, one carrying no label the rule could match, one
  with no usable date. This is the archive's worst failure mode made visible —
  a document the extractor mislabelled would otherwise vanish from every chart
  with no way to notice.
- **Could not be converted** is money with no exchange rate for its date. It
  always shows a document count beside the amount, because an unconvertible
  payment and an equal unconvertible refund net to `0.00` across two
  documents — which without the count would read as "nothing missing".

Every count in the first two blocks is a button: click it to see exactly which
documents are behind it. Refund count and the unconvertible rows are plain
figures — there is no per-document list behind those (see §8).

One thing to know when reading it: **"documents" means three different things**
in that footer, and they are not additive. The header line counts documents
that reached the total; a needs-attention row counts documents in that
category; the unconvertible row is an upper bound. Each is correct on its own
terms. Do not add them together.

## 6. Following a number to its documents

Clicking a bar opens a panel listing the payments that make up it, each
expandable to the documents behind it. It is a panel, not a tooltip — it stays
open while you work.

From there you can fix what you find, in place:

- **edit a document's facet labels** — the usual editor, inline;
- **split or merge a payment** — when two documents were wrongly treated as one
  payment, or one payment was wrongly counted twice.

That is the point of the design: a correction is made where the problem is
noticed, not somewhere else afterwards.

The payments listed always add up to the bar you clicked. The **documents**
under a payment may not — a merged pair, a group member dated outside the
period, or an unconvertible member can all appear in the list without
contributing. The payment's own figure is the one that matches the bar.

The same panel opens from a footer count, listing that category's documents
instead. On a large category it pages, and says so — "100 of 340" — rather
than silently showing you the first hundred.

## 7. Colour

A split value keeps the same colour across a chart's whole lifetime, so a
category does not change colour when you change the range or the grain. The
palette has **six** slots, validated for colour-blind separation and for
contrast in both light and dark mode.

Past six values, the smallest are folded into a single grey **Other** band.
The fold never changes the total — it is a grouping, not an omission — and
clicking it shows what went into it, with each value clickable in turn.

If you want a specific colour for a specific value, set it in the vocabulary
panel; a stored colour always wins.

## 8. What it does not do yet

Stated plainly, so you do not go looking:

- **A chart's question text is not rewritten when you edit its rule** (§4.1).
  The heading can end up describing a question the rule no longer answers;
  renaming it is manual and deliberate.
- **Unconvertible money has no drill-through.** It is a merge of two separately
  reported lists and does not carry document ids, so it shows as a figure with
  a count and no list. [`charts.md`](charts.md) §13 has the detail.
- **"Needs attention" is scoped to the chart's own window.** A document with an
  undecided amount outside every chart's date range is counted nowhere. There
  is no archive-wide backlog view.
- **There is no export.** No CSV or image download from this view.

## 9. Where the numbers come from

Briefly, because it explains several behaviours above:

- Amounts come from documents' extracted `amount_total`, with an
  `amount_kind` saying what the number *means* — a payment, a refund, a
  coverage ceiling, an estimate. The kind decides the sign and whether it
  counts at all.
- One real payment documented twice (an emailed invoice and a downloaded
  receipt, say) is collapsed to a single payment, so it is counted once.
- A document split across several categories contributes each part
  separately.
- Conversion to the display currency uses the rate for each document's own
  date, not today's.

The full model is in [`money-facts.md`](money-facts.md) (what an amount means,
and payment identity) and [`charts.md`](charts.md) (the query engine, the
footer's categories, and the API).
