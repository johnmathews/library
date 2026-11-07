"""Tests for scripts/deploy.sh: verifying WUD's deploys, and the manual deploy.

What's Up Docker deploys every new ``:latest`` on the host by itself, so the
script's default action only verifies: it waits for ``/healthz`` to report
HEAD's commit, then checks the stack. Running a second deploy beside WUD's ran
the migration twice at once (2026-11-06, both deploys). ``--deploy`` is the
manual deploy for when WUD is paused: it takes the host lock WUD also takes and
runs the migration exactly once, with ``compose run``.

Compose's leftover containers:

`docker compose up` recreates a container by first renaming the old one to
``<12 hex>_<name>``. When that dance is interrupted it leaves the temporary
container behind, and the next ``up`` fails with "container name ... is
already in use", leaving every library container ``Created`` — production
down. It happened on 2026-10-31 and twice on 2026-11-04 (once as a running
duplicate embedder after a successful deploy, once as an outage).

The script is sourced with a fake ``ssh`` (runs the remote command locally)
and a fake ``docker`` (a container table in a state file) on ``PATH``, so the
functions run against scripted host states without touching a real host.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"
_HEAD = subprocess.run(
    ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=_SCRIPT.parent
).stdout.strip()

_FAKE_SSH = """#!/usr/bin/env bash
# Drop ssh options and the host, run the remote command locally.
while [[ "$1" == -* ]]; do
  case "$1" in -o) shift 2 ;; *) shift ;; esac
done
shift
exec bash -c "$*"
"""

# State file: one container per line, "<name> <state>". `up` failing once is
# driven by a marker file holding the stray name it leaves behind.
_FAKE_DOCKER = """#!/usr/bin/env bash
state="$FAKE_DOCKER_STATE"
echo "docker $*" >> "$FAKE_DOCKER_LOG"
if [[ "$1" == "ps" ]]; then
  cat "$state"
  exit 0
fi
if [[ "$1" == "rm" ]]; then
  shift
  [[ "$1" == "-f" ]] && shift
  for name in "$@"; do
    grep -v "^$name " "$state" > "$state.tmp" || true
    mv "$state.tmp" "$state"
  done
  exit 0
fi
if [[ "$1" == "compose" && "$2" == "pull" ]]; then
  exit 0
fi
if [[ "$1" == "compose" && "$2" == "run" ]]; then
  exit "${FAKE_MIGRATE_EXIT:-0}"
fi
if [[ "$1" == "buildx" ]]; then
  echo "Name: fake"
  echo "Digest: sha256:0123"
  exit 0
fi
if [[ "$1" == "compose" && "$2" == "up" ]]; then
  if [[ -f "$FAKE_UP_FAILS_ONCE" ]]; then
    stray="$(cat "$FAKE_UP_FAILS_ONCE")"
    rm "$FAKE_UP_FAILS_ONCE"
    echo "$stray created" >> "$state"
    echo 'Error response from daemon: Conflict. The container name is already in use' >&2
    exit 1
  fi
  if grep -q '^[0-9a-f]\\{12\\}_library-' "$state"; then
    echo 'Error response from daemon: Conflict. The container name is already in use' >&2
    exit 1
  fi
  exit 0
fi
if [[ "$1" == "compose" && "$2" == "exec" ]]; then
  case "$*" in
    *psql*) [[ -n "${FAKE_DB_EXEC_FAILS:-}" ]] && { echo "service not running" >&2; exit 1; }
            echo "${FAKE_DB_REV:-0041}" ;;
    *"alembic heads"*) echo "${FAKE_HEAD_REV:-0041} (head)" ;;
    *) printf '{"status":"ok","git_sha":"%s"}\n' "${FAKE_GIT_SHA:-}" ;;
  esac
  exit 0
fi
if [[ "$1" == "image" && "$2" == "prune" ]]; then
  [[ -f "$FAKE_PRUNE_FAILS" ]] && exit 1
  echo 'Deleted Images:'
  echo 'Total reclaimed space: 1.42GB'
  exit 0
fi
exit 0
"""


# The host's util-linux flock, logged: `flock [-w N] [-E N] <path> <cmd...>`.
_FAKE_FLOCK = """#!/usr/bin/env bash
echo "flock $*" >> "$FAKE_DOCKER_LOG"
while [[ "$1" == -* ]]; do shift 2; done
shift
exec "$@"
"""


def _executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def host(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "ssh", _FAKE_SSH)
    _executable(bin_dir / "docker", _FAKE_DOCKER)
    _executable(bin_dir / "flock", _FAKE_FLOCK)
    (tmp_path / "srv").mkdir()
    (tmp_path / "docker.log").write_text("")
    return tmp_path


def _containers(host: Path, *rows: str) -> None:
    (host / "state").write_text("".join(f"{row}\n" for row in rows))


def _run(host: Path, call: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{host / 'bin'}:{os.environ['PATH']}",
        "LIBRARY_DEPLOY_HOST": "fake-host",
        "LIBRARY_DEPLOY_DIR": str(host / "srv"),
        "FAKE_DOCKER_STATE": str(host / "state"),
        "FAKE_DOCKER_LOG": str(host / "docker.log"),
        "FAKE_UP_FAILS_ONCE": str(host / "up-fails-once"),
        "FAKE_PRUNE_FAILS": str(host / "prune-fails"),
        "FAKE_GIT_SHA": _HEAD,
        "LIBRARY_VERIFY_INTERVAL": "0",
        "LIBRARY_VERIFY_TIMEOUT": "2",
    }
    return subprocess.run(
        ["bash", "-c", f'source "{_SCRIPT}"; {call}'],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _names(host: Path) -> list[str]:
    return [line.split()[0] for line in (host / "state").read_text().splitlines() if line]


HEALTHY = (
    "library-db running",
    "library-embedder running",
    "library-webserver running",
    "library-worker running",
    "library-migrate exited",
)


def test_sourcing_the_script_does_not_deploy(host: Path) -> None:
    _containers(host, *HEALTHY)
    result = _run(host, "true")
    assert result.returncode == 0, result.stderr
    assert (host / "docker.log").read_text() == ""


def test_leftovers_are_removed_and_nothing_else(host: Path) -> None:
    _containers(
        host,
        *HEALTHY,
        "310a240f486b_library-embedder running",
        "24f6f982cd05_library-worker created",
        "alloy running",
        "notahash_library-worker running",
    )
    result = _run(host, "remove_compose_leftovers")
    assert result.returncode == 0, result.stderr
    assert sorted(_names(host)) == sorted(
        [row.split()[0] for row in HEALTHY] + ["alloy", "notahash_library-worker"]
    )


def _log(host: Path) -> list[str]:
    return (host / "docker.log").read_text().splitlines()


def _calls(host: Path, prefix: str) -> list[str]:
    return [line for line in _log(host) if line.startswith(prefix)]


def test_failed_up_is_cleaned_and_retried_once(host: Path) -> None:
    _containers(host, *HEALTHY)
    (host / "up-fails-once").write_text("0d400af8e750_library-worker")
    result = _run(host, "SKIP_PROMOTE_CHECK=1 deploy")
    assert result.returncode == 0, result.stderr
    assert "0d400af8e750_library-worker" not in _names(host)
    assert len(_calls(host, "docker compose up")) == 2


def test_up_failing_twice_fails_the_deploy(host: Path) -> None:
    # A leftover that rm cannot clear (re-added by every up) must not loop.
    _containers(host, *HEALTHY)
    (host / "up-fails-once").write_text("0d400af8e750_library-worker")
    result = _run(host, 'LEFTOVER_RE="^never$" SKIP_PROMOTE_CHECK=1 deploy')
    assert result.returncode != 0
    assert len(_calls(host, "docker compose up")) == 2


def test_a_created_container_after_up_fails_loudly(host: Path) -> None:
    _containers(
        host,
        "library-db created",
        "library-webserver created",
        "library-worker running",
        "library-migrate created",
    )
    result = _run(host, "check_stack_clean")
    assert result.returncode != 0
    assert "library-webserver" in result.stderr
    assert "docs/runbooks/deploy.md" in result.stderr


def test_a_created_container_of_another_service_is_not_libraries_problem(host: Path) -> None:
    _containers(host, *HEALTHY, "wud created")
    result = _run(host, "check_stack_clean")
    assert result.returncode == 0, result.stderr


def test_a_clean_stack_passes_and_a_running_duplicate_is_removed(host: Path) -> None:
    _containers(host, *HEALTHY, "310a240f486b_library-embedder running")
    result = _run(host, "check_stack_clean")
    assert result.returncode == 0, result.stderr
    assert "310a240f486b_library-embedder" not in _names(host)


def _prune_calls(host: Path) -> list[str]:
    log = (host / "docker.log").read_text().splitlines()
    return [line for line in log if line.startswith("docker image prune")]


def test_a_successful_deploy_prunes_dangling_images_only(host: Path) -> None:
    # Each deploy leaves its predecessor's 1.4GB image behind; 14 of them filled
    # the 16G host disk to 97% (2026-11-06).
    _containers(host, *HEALTHY)
    result = _run(host, "SKIP_PROMOTE_CHECK=1 deploy")
    assert result.returncode == 0, result.stderr
    assert "Deploy complete." in result.stdout
    # Dangling only: `-a` would also delete tagged images no container runs.
    assert _prune_calls(host) == ["docker image prune -f"]
    assert "Total reclaimed space: 1.42GB" in result.stdout


def test_a_failed_prune_does_not_fail_the_deploy(host: Path) -> None:
    _containers(host, *HEALTHY)
    (host / "prune-fails").write_text("")
    result = _run(host, "SKIP_PROMOTE_CHECK=1 deploy")
    assert result.returncode == 0, result.stderr
    assert "Deploy complete." in result.stdout
    assert _prune_calls(host) == ["docker image prune -f"]


def test_a_failed_migration_stops_before_web_and_worker(host: Path) -> None:
    _containers(host, *HEALTHY)
    result = _run(host, "FAKE_MIGRATE_EXIT=1 SKIP_PROMOTE_CHECK=1 deploy")
    assert result.returncode != 0
    assert "migrations FAILED" in result.stderr
    assert _calls(host, "docker compose up") == []
    assert _prune_calls(host) == []


# --- The manual deploy: one migration run, under the lock WUD takes. --------


def test_manual_deploy_runs_the_migration_once_under_the_lock(host: Path) -> None:
    _containers(host, *HEALTHY)
    result = _run(host, "SKIP_PROMOTE_CHECK=1 deploy")
    assert result.returncode == 0, result.stderr
    log = _log(host)
    # The lock is the compose directory itself: WUD mounts it read-only, so a
    # lock file could not be created there, and a directory can be locked.
    # One lock held for the whole deploy (its steps arrive on `bash -s`); the
    # second, brief one is post_deploy_checks waiting before it touches containers.
    locks = [line for line in _calls(host, "flock") if line.endswith("bash -s")]
    assert len(locks) == 1
    assert str(host / "srv") in locks[0].split()
    runs = _calls(host, "docker compose run")
    # --no-deps: `run` would otherwise start (and, when the host's compose and
    # WUD's disagree on the config hash, RECREATE) library-db: a Postgres restart
    # under the running app, seen on WUD's first locked deploy (2026-11-06).
    assert runs == ["docker compose run --rm --no-deps -T library-migrate"]
    ups = _calls(host, "docker compose up")
    assert len(ups) == 1
    assert "--no-deps" in ups[0].split()
    assert "library-migrate" not in ups[0].split()
    # Order: pull, then the migration, then web/worker.
    order = [
        i
        for i, line in enumerate(log)
        if line.startswith(("docker compose pull", "docker compose run", "docker compose up"))
    ]
    assert [log[i].split()[2] for i in order] == ["pull", "run", "up"]


def test_manual_deploy_checks_the_migration_by_state_not_by_container(host: Path) -> None:
    # The migrate job ran under `compose run --rm`, so no `library-migrate`
    # container holds its exit code; the DB's revision must match the image's.
    _containers(host, *HEALTHY)
    result = _run(host, "FAKE_DB_REV=0040 FAKE_HEAD_REV=0041 SKIP_PROMOTE_CHECK=1 deploy")
    assert result.returncode != 0
    assert "0040" in result.stderr and "0041" in result.stderr
    assert _prune_calls(host) == []


# --- The default: WUD deploys, the script verifies. -------------------------


def test_verify_never_deploys(host: Path) -> None:
    _containers(host, *HEALTHY)
    result = _run(host, "verify")
    assert result.returncode == 0, result.stderr
    assert "verified" in result.stdout.lower()
    for mutating in ("docker compose up", "docker compose run", "docker compose pull"):
        assert _calls(host, mutating) == [], mutating


def test_verify_waits_for_the_deploy_lock_before_touching_containers(host: Path) -> None:
    # HEAD's sha on /healthz means WUD has migrated and started the webserver,
    # not that its script is done: it may still be recreating the worker, and
    # compose's in-flight `<12 hex>_library-…` rename looks like a leftover.
    # WUD holds the lock until its health wait ends, so wait for it first.
    _containers(host, *HEALTHY, "cb8ba3b95f13_library-worker exited")
    result = _run(host, "verify")
    assert result.returncode == 0, result.stderr
    log = _log(host)
    locks = [i for i, line in enumerate(log) if line.startswith("flock")]
    removals = [i for i, line in enumerate(log) if line.startswith("docker rm")]
    assert locks and removals, log
    assert locks[0] < removals[0], log
    assert str(host / "srv") in log[locks[0]].split()


def test_a_failed_db_read_reports_instead_of_exiting_silently(host: Path) -> None:
    # set -e + pipefail kill `x="$(ssh … | tr …)"` on a failed exec before the
    # script can say why; it must reach its own message.
    _containers(host, *HEALTHY)
    result = _run(host, "FAKE_DB_EXEC_FAILS=1 verify")
    assert result.returncode != 0
    assert "revision '?'" in result.stderr


def test_verify_waits_for_heads_commit_and_times_out_naming_the_manual_deploy(host: Path) -> None:
    _containers(host, *HEALTHY)
    result = _run(host, "FAKE_GIT_SHA=0000000000000000000000000000000000000000 verify")
    assert result.returncode != 0
    assert "--deploy" in result.stderr
    assert _prune_calls(host) == []


def test_verify_fails_when_the_db_is_behind_the_image(host: Path) -> None:
    _containers(host, *HEALTHY)
    result = _run(host, "FAKE_DB_REV=0040 FAKE_HEAD_REV=0041 verify")
    assert result.returncode != 0
    assert "0040" in result.stderr and "0041" in result.stderr


def test_verify_removes_a_stray_and_prunes(host: Path) -> None:
    _containers(host, *HEALTHY, "cb8ba3b95f13_library-migrate exited")
    result = _run(host, "verify")
    assert result.returncode == 0, result.stderr
    assert "cb8ba3b95f13_library-migrate" not in _names(host)
    assert _prune_calls(host) == ["docker image prune -f"]
