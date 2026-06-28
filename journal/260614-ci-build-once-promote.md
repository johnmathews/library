# CI: build once early, promote on green

**Date:** 2026-06-14

## 1.1 Why

Time-to-deployable-image was ~7 min. Timing a real run showed the Docker
**build wasn't the slow part** — it was already GHA-layer-cached and took ~100s.
The cost was the **job graph**: the old `docker` job (build + push `:latest`/`:sha`)
was gated behind `e2e` (the ~3m36s long pole), so the published image didn't
exist until the whole suite finished.

Run 27500451308 critical path: backend (1m47s) → e2e (3m36s) → docker (1m40s) ≈ 7m.

## 1.2 Change

Split the old `docker` job into **`build`** + **`promote`** (`.github/workflows/ci.yml`):

- **`build`** — no test dependencies, starts at t=0 in parallel with the suite.
  Builds the image once, writes the shared `library-image` gha layer cache, and
  pushes the immutable `:sha` tag on `main` (push skipped on branches — cache
  warm only, so no per-branch ghcr clutter).
- **`promote`** — `needs: [build, backend, frontend, e2e, compose-smoke]`,
  `if: main`. Retags `:sha` → `:latest` with `docker buildx imagetools create`
  — a registry-side manifest copy (no rebuild, no pull, ~seconds).

`:latest` (what production deploys) still only moves once the full suite is
green, so the deploy safety gate is unchanged. The build now overlaps the tests
instead of following them, and publishing is a fast retag.

`e2e` and `compose-smoke` are unchanged: they still build their own ephemeral
test stacks from the shared cache (branch-safe, no registry dependency) — the
"build once" applies to the *published* artifact.

## 1.3 Tradeoff

On `main`, the `:sha` image is now pushed before tests finish (even if they
later fail). That's acceptable: `:sha` is an immutable build artifact / rollback
target, and `:latest` stays gated on green. Same source + Dockerfile as before.

## 1.4 Expected effect

Build (~100s) leaves the critical path (runs parallel); publish becomes a ~15s
retag. Estimated ~7m → ~5m30s to a deployable `:latest`, dominated now by the
e2e long pole.

## 1.5 Note

App code unchanged by this commit, so no redeploy is needed — the next `main`
run simply repoints `:latest` at the new sha.
