# Matters and Projects get a one-sentence lede

**Date:** 2026-08-22

## What

`/matters` and `/projects` now open with a one-sentence page lede, passed as
`PageHeader`'s `description` prop, saying what the feature is *for*:

- **Matters** — "A matter is an evergreen subject — car insurance, health
  insurance, subscriptions — that documents are filed into automatically, so
  everything on one topic stays together."
- **Projects** — "A project is a collection you put documents into yourself —
  a house purchase, a tax year — so everything about one undertaking stays
  together."

## Why

With the page title moved into the app bar (see
[page-title-in-the-app-bar](260822-page-title-in-the-app-bar.md)), both
views opened straight onto a toolbar and a list of names. Matters and projects
look alike on the surface — both are lists of labels with document counts —
and the distinction that matters to a user (the classifier files documents into
matters for you; you place documents into projects by hand) was nowhere on the
page. One sentence each fixes that, and each sentence deliberately names the
automatic-vs-manual split so the two pages explain themselves *relative to each
other*.

## How

- The wording follows the existing ledes on `/ask`, `/upload`, `/notes/new` and
  `/admin/held-emails`: muted, `max-w-2xl`, no heading of its own. On
  `/matters`, where the header has a `#controls` slot, `PageHeader` already
  gives the lede its own line above the toolbar (PR #93), so nothing in the
  component changed.
- The examples in each sentence are illustrative shapes (insurance, a house
  purchase), not real data — the repo is public.
- Each view's unit spec gains a test that mounts as a **non-admin** and asserts
  the lede is present and names the distinguishing idea (`automatically` /
  `yourself`). Mounting as non-admin is deliberate: the lede is for every
  reader, not just the people who can edit the vocabulary.
- `docs/frontend.md` view rows for both views updated.
