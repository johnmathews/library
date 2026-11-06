"""Behaviour of scripts/ci_apt_retry.sh, the apt retry loop shared by CI's two apt steps.

The script runs its command under `sudo timeout`; here sudo is emptied and the
timeout shortened so the whole loop runs in a few seconds. The mirrors file is
in the format GitHub's ubuntu-24.04 runner image writes
(`URL<TAB>priority:N`, one mirror per line).
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_apt_retry.sh"

_RUNNER_MIRRORS = (
    "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
    "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
    "https://security.ubuntu.com/ubuntu/\tpriority:3\n"
)


@pytest.fixture(scope="module")
def gnu_timeout() -> str:
    found = shutil.which("timeout") or shutil.which("gtimeout")
    if found is None:
        pytest.fail("GNU timeout not found: install coreutils (macOS: brew install coreutils)")
    return found


def _run(
    tmp_path: Path,
    gnu_timeout: str,
    command: list[str],
    mirrors: Path,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "APT_RETRY_SUDO": "",
        "APT_RETRY_SECONDS": "1",
        "APT_RETRY_TIMEOUT": gnu_timeout,
        "APT_MIRRORS_FILE": str(mirrors),
    }
    return subprocess.run(
        [str(_SCRIPT), "test apt", *command],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=tmp_path,
    )


def _mirrors(tmp_path: Path) -> Path:
    path = tmp_path / "apt-mirrors.txt"
    path.write_text(_RUNNER_MIRRORS)
    return path


def test_slow_first_attempt_switches_mirror_and_retries(tmp_path: Path, gnu_timeout: str) -> None:
    mirrors = _mirrors(tmp_path)
    counter = tmp_path / "counter"
    # Attempt 1 outlasts the 1 s limit; attempt 2 returns at once.
    flaky = (
        f'n=$(cat "{counter}" 2>/dev/null || echo 0); echo $((n + 1)) > "{counter}"; '
        '[ "$n" -ge 1 ] || sleep 5'
    )

    result = _run(tmp_path, gnu_timeout, ["bash", "-c", flaky], mirrors)

    assert result.returncode == 0, result.stdout + result.stderr
    lines = mirrors.read_text().splitlines()
    assert lines[0] == "http://archive.ubuntu.com/ubuntu/\tpriority:1"
    assert not any("azure" in line for line in lines)
    assert any("security.ubuntu.com" in line for line in lines)
    assert result.stdout.count("::warning::") == 1
    assert "attempt 1 of 3" in result.stdout
    assert result.stdout.count("::notice::") == 1
    assert "::error::" not in result.stdout
    assert counter.read_text().strip() == "2"


def test_three_failures_exit_one_with_error(tmp_path: Path, gnu_timeout: str) -> None:
    result = _run(tmp_path, gnu_timeout, ["false"], _mirrors(tmp_path))

    assert result.returncode == 1
    assert result.stdout.count("::warning::") == 3
    assert "::error::test apt failed 3 times; usually a slow apt mirror, so rerun the job" in (
        result.stdout
    )


def test_missing_mirrors_file_is_tolerated(tmp_path: Path, gnu_timeout: str) -> None:
    missing = tmp_path / "no-such-file"
    counter = tmp_path / "counter"
    flaky = f'[ -e "{counter}" ] || {{ touch "{counter}"; exit 1; }}'

    result = _run(tmp_path, gnu_timeout, ["bash", "-c", flaky], missing)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::notice::" in result.stdout
    assert "no-such-file" in result.stdout
    assert not missing.exists()


def test_first_attempt_success_leaves_mirrors_untouched(tmp_path: Path, gnu_timeout: str) -> None:
    mirrors = _mirrors(tmp_path)

    result = _run(tmp_path, gnu_timeout, ["true"], mirrors)

    assert result.returncode == 0
    assert mirrors.read_text() == _RUNNER_MIRRORS
    assert "::warning::" not in result.stdout
    assert "::notice::" not in result.stdout
