# GitHub Actions pinned to commit SHAs

**Date:** 2026-10-25
**Branch:** `eng-pin-actions-sha`

## 1. What changed

All 38 `uses:` references in `ci.yml` and `e2e-nightly.yml` (12 distinct actions) now name a
40-character commit SHA, with the release tag as a trailing comment. Nothing was upgraded: each
tag was resolved to the commit it pointed at on 2026-10-25, and the comment names the most
specific release tag on that commit.

- `actions/cache` v6 → v6.1.0 (`55cc8345863c`)
- `actions/checkout` v7 → v7.0.1 (`3d3c42e5aac5`)
- `actions/download-artifact` v8 → v8.0.2 (`9000827ccba6`)
- `actions/setup-node` v7 → v7.1.0 (`949feb2413d6`)
- `actions/upload-artifact` v7 → v7.0.2 (`cf430e030ddb`)
- `astral-sh/setup-uv` v10.0.1 (`20cfd1bf945f`)
- `docker/bake-action` v7 → v7.5.0 (`59cb1203cd42`)
- `docker/build-push-action` v7 → v7.4.0 (`c3c9e263c25d`)
- `docker/login-action` v4 → v4.7.0 (`81c6f93d4bc9`)
- `docker/setup-buildx-action` v4 → v4.4.1 (`f87e5991a6d7`)
- `dorny/paths-filter` v4 → v4.0.3 (`ceb8a2b8f2d8`)
- `marocchino/sticky-pull-request-comment` v3 → v3.0.5 (`5770ad5eb8f4`)

`.github/dependabot.yml` is unchanged. It already carried the `github-actions` ecosystem, which
until now bumped major tags; with SHA pins it opens pull requests that replace the SHA and rewrite
the version comment together.

## 2. Why

The home server auto-deploys this image: What's Up Docker redeploys the library within about a
minute of a new `latest` on ghcr (home-server `documentation/wud.md`). The `build` and `promote`
jobs hold a token with `packages: write` and ran `docker/login-action` and `docker/build-push-action` by major
tag. A tag can be moved by whoever controls the action's repository (the tj-actions/changed-files
compromise in 2025 did exactly that), so a hijacked tag would have pushed a malicious image that
deploys itself. A commit SHA cannot be moved.

## 3. One ref that was a branch

`marocchino/sticky-pull-request-comment@v3` is not a tag upstream: `v3` is a **branch**, which is
even more mutable than a tag. On 2026-10-25 its head was the `v3.0.5` release commit, so the pin
is that commit with `# v3.0.5`.
