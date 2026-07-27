#!/usr/bin/env bash
#
# The CI aggregator gate (see the `ci-gate` job in .github/workflows/ci.yml).
#
# Usage: scripts/ci_gate.sh changes=<result> backend=<result> ...
#
# Takes `name=result` pairs — the `needs.<job>.result` values GitHub exposes —
# and exits 0 only if the run is genuinely clean.
#
# The rules are deliberately asymmetric:
#
#   * `changes` must be exactly `success`. It is unconditional in the workflow
#     (no `if:`), so success/failure/cancelled are its only legitimate results;
#     `skipped` means something is structurally wrong. Crucially, every other
#     job declares `needs: changes`, and GitHub reports a job whose dependency
#     failed as `skipped` — so a broken `changes` (checkout flake,
#     paths-filter outage, malformed `filters:`) skips all five others. Without
#     this rule the gate would find nothing to reject and launder a wholly
#     untested run into a green required check.
#
#   * Every other job may be `success` OR `skipped`. Per-area path skipping is
#     legitimate, and a skipped *required* check blocks a merge forever (this
#     is what stranded a Dependabot PR once). That tolerance is intentional.
#
# Tested by tests/test_ci_gate.py.
set -euo pipefail

REQUIRED_SUCCESS_JOB="changes"

if [ "$#" -eq 0 ]; then
    echo "::error::ci_gate: no job results supplied — refusing to pass vacuously"
    exit 1
fi

echo "upstream results: $*"

seen_required_job=0
for kv in "$@"; do
    name="${kv%%=*}"
    result="${kv#*=}"

    if [ "$name" = "$kv" ] || [ -z "$name" ] || [ -z "$result" ]; then
        echo "::error::ci_gate: malformed argument '$kv' (expected name=result)"
        exit 1
    fi

    if [ "$name" = "$REQUIRED_SUCCESS_JOB" ]; then
        seen_required_job=1
        if [ "$result" != "success" ]; then
            echo "::error::required job '$name' must succeed (result: $result) — every" \
                "other job needs it, so a non-success here skips them all and would" \
                "otherwise pass this gate having tested nothing"
            exit 1
        fi
        continue
    fi

    if [ "$result" != "success" ] && [ "$result" != "skipped" ]; then
        echo "::error::required job '$name' did not pass (result: $result)"
        exit 1
    fi
done

if [ "$seen_required_job" -eq 0 ]; then
    echo "::error::ci_gate: no result reported for job '$REQUIRED_SUCCESS_JOB' —" \
        "it must be in the ci-gate job's needs: list"
    exit 1
fi

echo "all upstream jobs passed or were legitimately skipped"
