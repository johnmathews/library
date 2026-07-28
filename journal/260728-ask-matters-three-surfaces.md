# One field, three surfaces, and only one of them derivable

W8. Ask could be asked to set a document's business matters and would answer that it had —
`status: updated`, no error, nothing changed. The evaluation named one cause. There were
three, and they fail in three different ways.

## 1. What was actually broken

**Surface 1 — the writable set.** `_WRITABLE_FIELDS` was a hand-written tuple of 13 names;
`DocumentUpdate` has 14. `matters` was the only gap, so `_run_update_document`'s
`{name: args[name] for name in _WRITABLE_FIELDS if name in args}` dropped the key and the
tool reported success over an empty change set.

**Grading: confirmed** by introspection — `DocumentUpdate.model_fields` has exactly 14 entries,
which also confirms the evaluation's "16" was wrong.

Fixed by deriving: `_WRITABLE_FIELDS = tuple(DocumentUpdate.model_fields)`. `docs/ask.md`
already described this tool as "the same surface as `PATCH /api/documents/{id}`", so
`DocumentUpdate` *is* the specification and any copy of it can only drift.

The old comment claimed the tuple was "a safe subset of DocumentUpdate (no status/review
fields)". That reads like a security boundary and is not one: **`DocumentUpdate` contains no
status or review fields at all.** There was nothing being subsetted — the "subset" was just an
out-of-date copy wearing a justification. Worth noting because a comment like that is what
stops the next person deriving it.

**Surface 2 — the tool schema.** Even with the forwarding fixed, the model could never send
`matters`: the `input_schema` advertised no such property. This half **cannot be derived** —
each property carries a hand-authored description the model reads — so it gets its own test
rather than a clever generation step.

**Surface 3 — the preview.** `_preview_current` had branches for `kind`, `tags` and `projects`
and fell through to `getattr` for everything else. For a relationship that returns an
`InstrumentedList` of ORM objects, and tool output is serialised with
`json.dumps(..., default=str)` — so it does not raise. It renders:

```
matters previews as InstrumentedList ([<library.models.Matter object at 0x11ad04410>])
```

in the preview the user is asked to approve before the write commits. `default=str` is the
thing that makes this silent: without it, JSON serialisation would have failed loudly the
first time.

## 2. Why three tests and not one

Each surface fails in a distinguishable way, so each gets a guard that reproduces its own
failure. All three mutation-checked:

| Mutation | Test that reds | Failure |
| --- | --- | --- |
| hand-write the tuple without `matters` | `test_writable_fields_match_document_update` | set inequality |
| drop the `matters` branch from `_preview_current` | `test_preview_current_is_json_primitive_for_every_writable_field` | `matters previews as InstrumentedList` |
| delete `matters` from the tool schema | `test_write_tool_schema_declares_every_writable_field` | `writable but undeclared: ['matters']` |

The preview test is the one that generalises: it walks *every* writable field and asserts the
value is something JSON renders honestly, so a future relationship field is covered without
anyone remembering this bug. `Decimal` and `date` are allowed through deliberately —
`default=str` renders those correctly and readably; an ORM object is the failure mode.

The schema test also checks the reverse direction (declared but not writable), so a property
advertised to the model that would be silently dropped is caught too.

Plus the end-to-end case: a confirmed write of `{"matters": [...]}` returns
`updated_fields == ["matters"]` and the membership persists. With `matters` removed from the
writable set it fails with `KeyError: 'status'` — the tool does not even reach a result shape.

## 3. Result

1401 passed, coverage 95%. The promise at `ask/engine.py`'s write tool — that its surface
matches the PATCH route — is now true by construction rather than by maintenance.
