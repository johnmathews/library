# Deploy script removes compose leftover containers

**Date:** 2026-11-04
**Branch:** `eng-deploy-cleanup`

## 1. What happened

To recreate a container, `docker compose up` renames the old one to
`<12 hex>_<name>` first. When that is interrupted the temporary container stays
behind, and the next `up` fails with "container name ... is already in use",
leaving every library container `Created`: production down.

- 2026-10-31: outage during the deploy of #200 (recorded in the operator's notes).
- 2026-11-04, deploy of #201: exit 0 and healthy, but a running duplicate
  `<hash>_library-embedder` and an exited `<hash>_library-migrate` were left.
- 2026-11-04, deploy of #202: outage from about 22:45 to 22:47:30 CET. A
  never-started `<hash>_library-worker` held the name; removing it by hand and
  re-running the script recovered (confirmed: `/healthz` ok, migration 0040
  applied).

Two of two deploys in one day made this the normal case, not a fluke.

## 2. What changed

`scripts/deploy.sh` now:

- removes containers of the compose project named `^[0-9a-f]{12}_library-`
  before `up` (compose's temporaries by construction; nothing else is touched);
- retries a failed `up` once after removing them again;
- afterwards removes any left, and aborts with the recovery steps if a
  `library-` container is `Created` (never started).

The script can now be sourced without deploying, which is how
`tests/test_deploy_script.py` runs its functions against a fake `ssh` and
`docker` replaying the observed host states.

## 3. Evidence

- The new tests failed on the old script (5 of 6; the sixth passed only because
  sourcing the old script ran a whole deploy) and pass now.
- The listing command was run read-only on `documents`: project `apps`, rows
  like `library-webserver running`; the project also holds non-library
  services, so the `Created` check is limited to `library-` names.
- Not yet observed: a real deploy through the new code path. The next deploy
  is that check.

## 4. What is deliberately not done

- The root cause inside compose is not investigated; the script handles its
  symptom.
- The host's other compose services are never touched.
