# CI apt retries switch away from the slow mirror

> Purpose
>
> Records why W1's same-runner apt retry was not enough, and the mirror
> switch that replaced it. Work unit W8 of the MCP follow-ups run, a
> follow-up to `261106-ci-time-limits-and-docs-only-e2e.md`.

**Date:** 2026-11-06
**Branch:** `eng-mcp-followups-ci-mirror`

## 1. What the live runs showed

W1 capped each apt attempt at 300 s and retried up to three times. The
first live run caught one slow mirror, and attempt 2 fetched the rest in
59 s. After that, three e2e shards across two runs failed all three
attempts on the same runner:

- PR #213, tablet;
- main `7dfb945`, mobile and tablet, which skipped `promote` and held a
  deploy back;
- PR #215, mobile, hit it as well.

Every one of those attempts fetched from `azure.archive.ubuntu.com` at
about 2 MB a minute: "Need to get 114 MB", then 102 MB left after 300 s,
then 93.6 MB. In scoping it was an open question whether a retry on the
same runner would get faster. The answer is no: the slow part is the
mirror, not the runner.

## 2. The change

`scripts/ci_apt_retry.sh` now holds the retry loop that was copied into
two steps of `ci.yml`. Both the OCR apt step and Playwright's
`install-deps` call it.

- **The mirror switch.** Before attempts 2 and 3 it rewrites
  `/etc/apt/apt-mirrors.txt`, the runner's mirror list, which apt reads
  through `mirror+file:`. The Azure mirror is dropped,
  `http://archive.ubuntu.com/ubuntu/` goes first, and `security.ubuntu.com`
  is kept.
- **What attempts 2 and 3 do.** Each one runs `apt-get update` again, so it
  reads the new list. Apt keeps the partial downloads between attempts.
- **What is unchanged.** `timeout` still runs inside `sudo`, so a stuck
  root apt process is killed, and the step limit is still 16 minutes.
- **The file format.** It follows the runner image's
  `configure-apt-sources.sh`: one `URL<TAB>priority:N` line per mirror,
  with Azure at priority 1.

## 3. Proof

- `tests/test_ci_apt_retry.py` runs the script with no sudo, a 1 s limit
  and a temporary mirror file. It covers four cases:
  - a slow first attempt switches the mirror and the second succeeds;
  - three failures exit 1 with the error annotation;
  - a missing mirror file is tolerated;
  - a first-attempt success leaves the file alone.

  The tests were red before the script existed.
- A workflow test checks that both apt steps call the script. It went red
  when one step was reverted by hand.
- **Container run.** In an `ubuntu:24.04` container, as a non-root sudo
  user, with the runner's 144-byte mirror list, a stalled first attempt was
  killed. The list was rewritten to 92 bytes, and the next `apt-get update`
  and install fetched only from `archive.ubuntu.com`.
- **Still to see live.** A slow mirror has not yet been caught by the new
  script on a real runner. When one is, the job log will show the
  `::notice::` about the switch, and attempt 2 fetching from
  `archive.ubuntu.com`.

## 4. Related

The runner image's maintainers are discussing demoting the Azure mirror and
adding apt timeouts (actions/runner-images#14596). If that lands, this
script becomes a second line of defence, not the only one.
