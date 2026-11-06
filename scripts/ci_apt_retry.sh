#!/usr/bin/env bash
# Run an apt-using command up to 3 times, switching away from the slow mirror.
#
# Usage: scripts/ci_apt_retry.sh <label> <command...>
#
# GitHub's ubuntu-24.04 runners read /etc/apt/apt-mirrors.txt through
# `mirror+file:` (apt-get update logs "Get:1 file:/etc/apt/apt-mirrors.txt
# Mirrorlist"). The file has one mirror per line, `URL<TAB>priority:N`, with
# azure.archive.ubuntu.com first. When that mirror is slow, every retry on the
# same VM is slow too (all three 300 s attempts failed on PR #213 and main
# 7dfb945), so after a failed attempt this puts archive.ubuntu.com first and
# drops the Azure line. Apt keeps the .debs it already fetched, so the retry
# resumes rather than restarts.
#
# Each attempt is `$APT_RETRY_SUDO env PATH=... $APT_RETRY_TIMEOUT ...`:
# `timeout` runs INSIDE sudo so it can kill root's apt-get, and it signals the
# whole process group, so `bash -c` and `sh -c` children go too.
#
# Overridable for tests (tests/test_ci_apt_retry.py):
#   APT_RETRY_SUDO      default "sudo"; empty runs the command directly
#   APT_RETRY_TIMEOUT   default "timeout" (GNU coreutils)
#   APT_RETRY_SECONDS   default 300, the per-attempt limit
#   APT_MIRRORS_FILE    default /etc/apt/apt-mirrors.txt
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "usage: $0 <label> <command...>" >&2
  exit 2
fi

label=$1
shift

sudo_cmd=${APT_RETRY_SUDO-sudo}
timeout_cmd=${APT_RETRY_TIMEOUT:-timeout}
seconds=${APT_RETRY_SECONDS:-300}
mirrors=${APT_MIRRORS_FILE:-/etc/apt/apt-mirrors.txt}
max_attempts=3

switch_mirror() {
  if [ ! -f "$mirrors" ]; then
    echo "::notice::$mirrors not found; retrying without switching mirror"
    return 0
  fi
  local rewritten
  # archive.ubuntu.com first; drop Azure and any other archive.ubuntu.com line
  # (so it is not listed twice); keep everything else, e.g. security.ubuntu.com.
  rewritten=$(
    printf 'http://archive.ubuntu.com/ubuntu/\tpriority:1\n'
    awk '$1 !~ /azure\.archive\.ubuntu\.com/ && $1 !~ /\/\/archive\.ubuntu\.com\// && NF' "$mirrors"
  )
  # shellcheck disable=SC2086
  printf '%s\n' "$rewritten" | $sudo_cmd tee "$mirrors" >/dev/null
  echo "::notice::$label: switched apt mirror to archive.ubuntu.com (Azure mirror removed from $mirrors)"
}

for attempt in $(seq 1 "$max_attempts"); do
  # shellcheck disable=SC2086
  if $sudo_cmd env "PATH=$PATH" $timeout_cmd --kill-after=30 "$seconds" "$@"; then
    exit 0
  fi
  echo "::warning::$label attempt $attempt of $max_attempts failed or ran over ${seconds}s"
  if [ -n "$sudo_cmd" ]; then
    # Finish any unpack a killed attempt interrupted.
    $sudo_cmd dpkg --configure -a || true
  fi
  if [ "$attempt" -lt "$max_attempts" ]; then
    switch_mirror
  fi
done

echo "::error::$label failed $max_attempts times; usually a slow apt mirror, so rerun the job"
exit 1
