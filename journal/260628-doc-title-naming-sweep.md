# Documentation title & naming sweep

**Date:** 2026-06-28

Swept every doc in `docs/` and `journal/` so the **titles** render cleanly in
the documentation viewer. Filenames were already correct — the problem was the
first `# H1` heading of each file, which the viewer uses as the display title.

## 1. Diagnosis

The documentation app derives each entry's title from its first `# H1`
heading. The headings had drifted into three inconsistent styles:

1. **Stray section numbers** — `# 1. REST API`, `# 1. Architecture`,
   `# 2. MCP server`. These came from the engineering-team "number every
   heading hierarchically" convention being applied to reference docs and
   journal entries, where a leading `1.` is meaningless as a *title*.
   `mcp.md` carried a stale `2.` (and `## 2.1`/`## 2.2` sections) from when
   it was section 2 of a larger document.
2. **Redundant / inconsistent date prefixes** in journal titles — three
   formats coexisted (`260622 — …`, `2026-06-22 — …`, `1. 2026-06-10 — …`)
   plus trailing `(2026-06-23)` suffixes. The date is already in the filename.
3. **Clean titles** in the remainder, so the set looked arbitrary.

The fix is entirely in the files; the viewer needs no change.

## 2. Convention applied

For `docs/` and `journal/` going forward:

- **H1 title** = clean, descriptive, unnumbered, no date. The H1 owns the
  implicit "section 1"; the date lives in the filename.
- **Section headings** keep hierarchical numbering where present
  (`## 1.1`, `### 1.2.1`, …) — the engineering-team decimal convention still
  applies *below* the H1, just not *on* it.
- **Filenames** unchanged: `journal/` stays `yymmdd-<slug>.md`; `docs/` stays
  `<lowercase-slug>.md`. All current filenames already conform.
- The full decimal convention (numbered H1 included) remains correct for
  engineering-team run-dir reports and plans, which are cross-referenced in
  long form — it just does not belong on viewer-titled reference docs.

## 3. What changed

- **30 H1 titles rewritten** across `journal/` (21) and `docs/` (8) plus
  `docs/benchmarks/260610-ocr-benchmark.md` — stripped leading `N.` numbers,
  leading dates (`yymmdd`/`yyyy-mm-dd`), and trailing `(date)` suffixes.
  Meaningful `W#` work-unit labels were preserved.
- **`docs/mcp.md`** — H1 `# 2. MCP server` → `# MCP server`; orphaned
  `## 2.x` sections renumbered to `## 1.x`.
- `docs/ingestion.md` left with unnumbered sections (a 1,100-line file; not
  worth the churn/risk to force-number, and its H1 was already clean).

## 4. Verification

- Re-ran the title proposer after applying: 0 remaining changes (idempotent).
- Diff confirmed **only heading lines** changed across all 30 files — no body
  or code-block lines touched (the `# →` shell comment in `mcp.md` was left
  intact).
- Filename conformance checked: all `journal/` and `docs/` names already match
  their conventions.

## 5. Noted, not fixed

- `docs/api.md` has a depth quirk: `## 1.8.1`–`## 1.8.5` are `##` (H2) but
  numbered three-deep. Left as-is — it renders fine and is unrelated to the
  title complaint; renumbering an 891-line API doc carries more risk than value.
