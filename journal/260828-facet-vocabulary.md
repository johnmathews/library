# A controlled label vocabulary, in place of 771 drifted tags

**Date:** 2026-08-28
**Branch:** `charts-redesign`

## Why

The archive had 771 distinct free-form tags. 454 of them (59%) were used
exactly once. That is not a slow-growing vocabulary someone hasn't gotten
around to tidying — it is a system with no pressure against drift, and the
count is the proof: nothing stops a document from getting a sixth spelling of
a concept that already has five.

Reading through the actual tag list turned up four separate failure modes,
not one:

- **Synonym sprawl.** Several separate tags for the same real-world concept —
  seven separate tags for vehicle servicing alone, five more for
  accountancy.
- **Encoding and spelling drift.** A mojibake-corrupted variant of a proper
  noun sitting alongside its correctly-spelled twin, splitting one thing's
  documents across two tags neither side of a filter would find.
- **Unrelated axes sharing one namespace.** Places, people, years, in/out of
  scope, document shape, and vendor names were all just tags — a flat list
  with no structure, so a search for "the vehicle axis" had no way to
  distinguish itself from "the year axis."
- **Redundant with a column that already exists.** Roughly 40% of the tag
  vocabulary just restated `document_date`, `sender`, or `kind` as a string —
  information the schema already stored properly, tagged again by hand.

None of these are fixable by cleaning the existing tags up once. A free-form
field re-drifts the moment someone reaches for the "add a tag" box instead of
the (much less discoverable) existing one.

## What replaced it: facets, not cleaner tags

A **facet** is a closed dimension — `category`, `scope`, `cost_type` — where
a document holds at most one value, chosen from a fixed list. The fix isn't a
smarter tag; it's removing the ability to type a new synonym at all. Three
more facets — one per vehicle, one per address, one per household member —
ship as empty shells with no seed values, because their values would name
real vehicles, addresses, and people, and this repository is public. Those
get populated at runtime, once, on a live instance.

**The design decision that made this safe to build in the first place: tags
inform the vocabulary, but documents determine the labels.** The 771 tags
were read once, as evidence of which dimensions actually mattered to this
archive — that evidence is what produced the `category`/`scope`/`cost_type`
shortlist — and then discarded entirely. No tag was ever mapped onto a facet
value. Every document gets labelled fresh, by a model reading its own title,
summary, sender, kind, amount and OCR text against the closed vocabulary.
Mapping an old, corrupt tag onto a new, clean-looking value would have
laundered the exact drift this whole effort exists to remove — the label
would look authoritative and be exactly as wrong as the tag it came from.

## Keeping it closed

A closed vocabulary that can be silently widened is not closed, it's just
smaller today. The labeller is structurally unable to invent a value: the
model is shown every allowed value (and every known alias) for every facet in
the prompt, and the parser that reads its response looks each returned value
up against that list. Anything that isn't an exact match — including
something on-topic and well-intentioned — becomes "no answer" for that facet,
plus a suggestion, which lands in a pending queue for a human to approve or
reject. Nothing on the write path can take a suggestion and quietly promote
it into the vocabulary on its own; only one deliberate action does that, and
it exists specifically so the vocabulary can still grow when it genuinely
needs to, without the growth being invisible.

Below the labeller, the same rule holds at every other boundary: setting a
document's label through the API to a value that isn't in that facet's
vocabulary is rejected, not silently accepted as a new value. The one thing
that is free is *how a value is displayed* — renaming a value's label costs
nothing, because every label and every filter references the value by its
row id, never by its text.

## Worth remembering

The 771/454 numbers were not a warning sign that got ignored — they were the
signal that a free-form field had already failed at the one job it had. The
fix wasn't "clean the tags," it was "remove the field that let them drift,"
and building the closed-set enforcement into the schema and the parser (not
just into the labelling prompt) is what keeps this from becoming tag drift
again, five years and a few well-meaning quick tags from now.
