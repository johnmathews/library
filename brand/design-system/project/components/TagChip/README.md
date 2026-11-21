A tag chip: a small neutral pill for a tag, project, matter or document type, and the three states that carry colour.

## Use

- `type-small` at 600, `tile` fill, `ink` text, `radius-full`, 22px tall.
- **Neutral by default.** Tags, projects, matters and a document's type are read by their names, so they share one look. They no longer take one of ten hashed colours.
- A tile shows up to 4 tags, then "+2" in `ink-secondary`.
- **Needs review:** `attention` on `attention-tint`.
- **Failed:** `error-ink` on `deficit-tint` with an alert icon.
- **Finished** (Indexed, Succeeded): neutral, with a check icon. **In progress** (Processing, Running, Queued): neutral, no icon.
- **In the editor** each chip has an × button named "Remove tag Energy".

## You provide

The name, and the state when it is one. Today: `components/app/AppBadge.vue`, whose `colour` prop narrows to neutral, attention and failed.
