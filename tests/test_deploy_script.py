"""Tests for scripts/deploy.sh's handling of compose's leftover containers.

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
exit 0
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


def test_failed_up_is_cleaned_and_retried_once(host: Path) -> None:
    _containers(host, *HEALTHY)
    (host / "up-fails-once").write_text("0d400af8e750_library-worker")
    result = _run(host, "compose_up")
    assert result.returncode == 0, result.stderr
    assert "0d400af8e750_library-worker" not in _names(host)
    ups = [line for line in (host / "docker.log").read_text().splitlines() if "compose up" in line]
    assert len(ups) == 2


def test_up_failing_twice_fails_the_deploy(host: Path) -> None:
    # A leftover that rm cannot clear (re-added by every up) must not loop.
    _containers(host, *HEALTHY)
    (host / "up-fails-once").write_text("0d400af8e750_library-worker")
    result = _run(host, "remove_compose_leftovers() { :; }; compose_up")
    assert result.returncode != 0


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
