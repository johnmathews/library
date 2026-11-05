#!/usr/bin/env bash
#
# The CI aggregator gate (see the `ci-gate` job in .github/workflows/ci.yml).
#
# Usage: scripts/ci_gate.sh changes=<result> backend=<result> ... \
#            [expect.<job>=true|false ...]
#
# Takes `name=result` pairs — the `needs.<job>.result` values GitHub exposes —
# and exits 0 only if the run is genuinely clean. `expect.<job>` pairs say
# whether this change needed `<job>` to run (a `changes` job output).
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
#   * Except when `expect.<job>=true`: then `<job>` must be exactly `success`.
#     `needs.<job>.result` reads `skipped` both for a deliberate path skip
#     (docs-only PR) and for an accident (an `if:` typo that stops e2e on a
#     code PR); only the job's own trigger condition, passed in here, tells
#     them apart. `expect.<job>=false` keeps the success-or-skipped rule. Any
#     other value, or an expectation for a job with no result, is an error:
#     an empty value is what a missing `changes` output renders as.
#
# Tested by tests/test_ci_gate.py.
set -euo pipefail

REQUIRED_SUCCESS_JOB="changes"

if [ "$#" -eq 0 ]; then
    echo "::error::ci_gate: no job results supplied — refusing to pass vacuously"
    exit 1
fi

echo "upstream results: $*"

# The result reported for job $1, or the empty string if none was supplied.
# A second pass over the arguments rather than an associative array, so this
# stays bash 3.2-safe (macOS /bin/bash).
result_of() {
    local job="$1" kv
    shift
    for kv in "$@"; do
        if [ "${kv%%=*}" = "$job" ]; then
            echo "${kv#*=}"
            return
        fi
    done
}

seen_required_job=0
for kv in "$@"; do
    name="${kv%%=*}"
    result="${kv#*=}"

    if [ "$name" = "$kv" ] || [ -z "$name" ]; then
        echo "::error::ci_gate: malformed argument '$kv' (expected name=result)"
        exit 1
    fi

    # Expectations are checked after every result has been judged.
    case "$name" in
        expect.*)
            if [ "$result" != "true" ] && [ "$result" != "false" ]; then
                echo "::error::ci_gate: '$kv' must be $name=true or $name=false" \
                    "(an empty value means the changes job set no such output)"
                exit 1
            fi
            if [ -z "${name#expect.}" ]; then
                echo "::error::ci_gate: malformed argument '$kv' (expected expect.<job>=true|false)"
                exit 1
            fi
            continue
            ;;
    esac

    if [ -z "$result" ]; then
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

for kv in "$@"; do
    name="${kv%%=*}"
    case "$name" in
        expect.*) ;;
        *) continue ;;
    esac
    job="${name#expect.}"
    expected="${kv#*=}"
    result="$(result_of "$job" "$@")"

    if [ -z "$result" ]; then
        echo "::error::ci_gate: '$kv' names job '$job', but no result was reported for it —" \
            "it must be in the ci-gate job's needs: list and passed as $job=<result>"
        exit 1
    fi
    if [ "$expected" = "true" ] && [ "$result" != "success" ]; then
        echo "::error::job '$job' was required to run for this change" \
            "(changes.outputs.$job=true) but result: $result"
        exit 1
    fi
done

echo "all upstream jobs passed or were legitimately skipped"
