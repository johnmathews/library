# Ask learns who is asking and what the archive calls things

**Date:** 2026-08-25

## What

The Ask system prompt now ends with an **Archive context** block, built per
turn by the new `library.ask.context` module: the user's name, the recipient
names linked to that user, and the archive's vocabulary — kinds, tags, active
projects with descriptions, active matters with their classifier hints, and the
most frequent senders. The prompt tells the model to use those exact slugs in
tool calls and to read "my"/"me"/"I" as that user.

Alongside it, `query_documents` and `compare_to_series` gained the filters the
list API already had: `recipient_contains`, `projects`, `matters`, `tags`. Both
tools now parse their arguments through one `_filters_from_args` helper (blank
strings and empty lists are treated as absent — the model does send them).

## Why

Reading `engine.py` with the question "how much does this agent know about the
person asking?" gave an uncomfortable answer: the date, the tool schemas, and
the last three turns of the current thread. Not the user's name, not which
recipient rows are theirs, not a single kind, tag, project or matter slug. The
model was guessing `kind` from a concept→slug hint table and could not reach
matters or projects at all — the organisation the user curates by hand was
invisible to the one surface that answers questions about the archive. And
"who was my energy provider" could not tell the user's bills from a
housemate's, because nothing said who "my" was.

Three tiers were considered: (1) inject identity + taxonomy into the prompt,
(2) expose the missing filters on the tools, (3) a free-text user profile
("household facts") in Settings. This entry ships 1 and 2 together — both live
in code that already existed and had tests, and 1 without 2 would have the
model knowing a matter slug it cannot pass anywhere. Tier 3 was scoped as a
follow-up at that point (it needs a settings surface and a UI) and then built
on the same branch once 1 and 2 were green — see below. The confirm-gated
write pattern the metadata tool already uses remains the obvious way to let
Ask *propose* additions to it later.

## Decisions

- **One system block, one cache breakpoint.** The context is appended to the
  static prompt string rather than sent as a second `system` block with its
  own `cache_control`. The prompt cache TTL is minutes; the block changes only
  when the taxonomy does. A separate breakpoint would have used the fourth of
  the four the API allows and bought nothing measurable.
- **Deterministic rendering is a requirement, not a nicety.** Every list is
  sorted and nothing volatile (document counts, timestamps) is rendered. A
  block that reordered itself between requests would silently invalidate the
  cached prefix on every turn, and nobody would notice from the answers. The
  frequent-sender list is *selected* by document count but *rendered*
  alphabetically for the same reason. There is a test that asserts two
  differently-ordered inputs render byte-identical.
- **Archived projects and matters are omitted.** They are not vocabulary the
  user still files under. The live ones are capped (50 each, alphabetical),
  added at review: Ask's own write tool can create projects and matters, so
  without a cap the block could grow without bound.
- **`recipient_contains`, not `recipient_id`.** The tool speaks in names
  (that is what the context block lists); ids would force the model through a
  lookup it has no tool for.
- **No new settings.** The sender and tag caps are module constants with
  keyword overrides for tests. A setting nobody will tune is a setting to
  document, validate and keep in sync with ansible for nothing.

## Tier 3, same PR: the "About you" notes

Built after tiers 1 and 2 were green, on the same branch. A free-text
`ask_profile` in `user.preferences` (no migration), a `PUT
/api/settings/ask-profile` route, an **Ask** tab in Settings with an explicit
Save, and an "About the user" bullet in the archive-context block, framed like
document comments: authoritative, trusted over inference.

Two choices worth recording:

- **Not tolerant on write.** Every other preference coerces garbage to a
  default because a wrong tone is harmless. Here the text *is* the user's
  words, so an over-long body is a 422 that names the cap, not a silent
  truncation that changes what Ask is told. On read, a non-string blob still
  resolves to `""` and an over-long stored string is clipped — a hand-edited
  row must not take the prompt down.
- **Explicit Save, not save-per-keystroke.** The appearance tab saves on
  every click because each control is one atomic choice. Free text is not: a
  half-typed sentence must never reach the prompt, and nobody wants a request
  per keystroke. The draft is seeded from the store once and the store only
  changes from the server's echo.

## Not done

- **Ask proposing additions to the notes.** The confirm-gated write pattern
  `update_document_metadata` uses is the obvious shape for "shall I remember
  that the Volvo is the family car?"; not started.
- **Answer accuracy is still unmeasured.** The claim that this makes answers
  better rests on the model no longer having to guess slugs and identity; it
  has not been benchmarked before/after. The eval harness exists
  (`EvalRun`) and would be the place.
- **Cross-thread memory.** Threads still start cold.
