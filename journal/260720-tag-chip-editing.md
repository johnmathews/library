# Tag editing as a chip multiselect

> Upgrade the document edit-mode tags control from a plain comma-separated text
> input to a chip-based multiselect matching Projects/Matters, and render
> read-mode tags as clickable badges. Frontend-only; no backend change.

## 1. Context

The request was "in edit mode for a particular document, I should be able to
add, edit, remove tags." Reconnaissance found the capability **already existed**
end-to-end: `PATCH /api/documents/{id}` does full-replacement of a document's
tags (creating unknown slugs via `get_or_create_tag`, which stores the slug
verbatim), and the editor already exposed an editable tags field — as a plain
comma-separated text input, while sibling Projects/Matters used a chip
`AppMultiSelect`. So the real work was a UX upgrade, confirmed with the user
before building.

## 2. What changed

All in `frontend/src/components/DocumentMetadataEditor.vue`:

- **Edit mode:** replaced the tags `AppInput` with `AppMultiSelect` bound to a
  `tagsDraft = ref<string[]>` of **slugs** (mirroring `projectsDraft`/
  `mattersDraft`). Options come from the shared taxonomy cache's tag list
  (`tagOptionSlugs`), already fetched by `ensureLoaded`. Dirty-check and
  `buildPatch` now operate on the slug array; the PATCH body is unchanged
  (`{tags: [...slugs]}`). A successful tags save now also calls
  `refreshTaxonomyOptions()` so an inline-created tag appears everywhere.
- **Read mode:** tags render as `AppBadge` chips wrapped in `RouterLink`s to
  `/?tag=<slug>` (`data-testid="tag-badge"`), mirroring the project/matter
  badge blocks.

## 3. Design decision: bind slugs, not names

Tags are slug-keyed (unlike Projects/Matters, keyed by name). The backend takes
tag slugs **verbatim** — no server-side slugification — and the dashboard
filters on `?tag=<slug>`. Binding the multiselect to slugs keeps the PATCH
contract identical and avoids a fragile client-side name→slug derivation for
newly-typed tags. Chips therefore show slugs, matching the approved mockup.

## 4. Tests

- Unit (`DocumentMetadataEditor.spec.ts`): chips render from the doc; add via
  Enter PATCHes the full slug list; inline-create refreshes the taxonomy cache;
  remove PATCHes the reduced list; read-mode badge links to `/?tag=<slug>`. Two
  pre-existing tests that drove the old comma input (one in
  `DocumentDetailView.spec.ts`) were updated to the chip interaction; the
  matter-badge test was scoped to its own badge (a tag badge now also renders a
  `RouterLink`, so "first RouterLink" was ambiguous).
- e2e (`frontend/e2e/tags-editing.spec.ts`): add → persist (reload, badge with
  `tag=` href) → remove → gone. Follows the `topics-readonly` template
  (env-skip, API-seed, unique-per-project slug).

Full frontend suite green (1021), lint + type-check clean.

## 5. Verification note

The e2e could **not** be run locally: the stack's embedder image
(`ghcr.io/huggingface/text-embeddings-inference`) is amd64-only and won't pull
on this Apple-Silicon host, and `api` depends on it. The spec typechecks,
parses, and lists across all three browser projects, and runs in CI's `e2e` job
(amd64), which gates `promote`. Local behavioural confidence comes from the
`DocumentDetailView` integration test, which drives the real component tree
(real `AppMultiSelect`, real autosave) through a mocked fetch boundary.

## 6. Out of scope / follow-up

- No backend change. No tag rename/merge/delete-from-taxonomy management UI.
- Minor pre-existing inconsistency noticed: in the detail hero/summary strip,
  projects render as linked badges but tags render as unlinked pills. Left
  as-is (different surface); a small follow-up could link them for consistency.
