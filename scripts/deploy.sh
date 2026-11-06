#!/usr/bin/env bash
#
# Verify (default) or run a deploy of Library on the production host (the
# `documents` LXC, CT 117).
#
# What's Up Docker (WUD) on the host deploys every new
# `ghcr.io/johnmathews/library:latest` by itself, within about a minute of CI's
# `promote` job (home-server role `document_library_lxc`, wud-deploy-library.sh).
# So the default action does NOT deploy: it waits until /healthz reports HEAD's
# commit, then checks the stack (migration state, health detail, compose
# leftovers) and prunes old images. Running a second deploy beside WUD's ran
# the migration twice at once (2026-11-06).
#
# `--deploy` is the manual deploy, for when WUD is paused or broken. It takes
# the host lock WUD's script also takes, pulls, runs the migration exactly once
# (`docker compose run --rm library-migrate`), then recreates the webserver and
# worker with `--no-deps`, and runs the same checks.
#
# This is the automation of docs/runbooks/deploy.md — read that for the why,
# the preconditions, and rollback. The procedure it encodes lives in
# docs/deployment.md §1.7.2.
#
# Usage:
#   scripts/deploy.sh            Verify WUD's deploy of HEAD (default).
#   scripts/deploy.sh --deploy   Deploy :latest by hand (WUD paused), then verify.
#   scripts/deploy.sh --force    Like --deploy, without the promote gate (emergencies).
#   scripts/deploy.sh --status   Show the running stack; do not deploy.
#   scripts/deploy.sh --logs     Tail recent webserver + worker logs.
#   scripts/deploy.sh --help     This help.
#
# Preconditions (the script checks what it can, but you own these):
#   - `main` is green in CI and you are on the commit you intend to ship: the
#     promote gate below checks that :latest points at HEAD's image.
#   - SSH access to $LIBRARY_DEPLOY_HOST is key-based (no password prompt).
#
# Environment overrides:
#   LIBRARY_DEPLOY_HOST      SSH host/alias                 (default: documents)
#   LIBRARY_DEPLOY_DIR       Compose dir on the host        (default: /srv/apps)
#   LIBRARY_VERIFY_TIMEOUT   Seconds to wait for HEAD to be live (default: 600)
#   LIBRARY_VERIFY_INTERVAL  Seconds between /healthz polls      (default: 10)
#   SKIP_PROMOTE_CHECK       Set to 1 to bypass the promote gate (like --force).
#
set -euo pipefail

HOST="${LIBRARY_DEPLOY_HOST:-documents}"
DIR="${LIBRARY_DEPLOY_DIR:-/srv/apps}"
IMAGE="ghcr.io/johnmathews/library"
SKIP_PROMOTE_CHECK="${SKIP_PROMOTE_CHECK:-0}"
VERIFY_TIMEOUT="${LIBRARY_VERIFY_TIMEOUT:-600}"
VERIFY_INTERVAL="${LIBRARY_VERIFY_INTERVAL:-10}"
# How long `--deploy` waits for a WUD deploy that holds the host lock.
LOCK_WAIT=900

# Prod service names differ from the repo compose file (api → library-webserver,
# etc. — see the deployment doc). These are the names on the live host.
MIGRATE_SVC="library-migrate"
WEB_SVC="library-webserver"
WORKER_SVC="library-worker"
DB_SVC="library-db"
APP_SVCS="$WEB_SVC $WORKER_SVC"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
err() { printf '\033[31m%s\033[0m\n' "$*" >&2; }

# Run a command in the compose directory on the prod host. $DIR and the command
# are intentionally expanded client-side (they come from this script's config,
# not from untrusted input), so SC2029 does not apply.
# shellcheck disable=SC2029
remote() { ssh "$HOST" "cd '$DIR' && $*"; }

require_ssh() {
  if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$HOST" true 2>/dev/null; then
    err "Cannot SSH to '$HOST' non-interactively. Check your SSH config/keys, or set LIBRARY_DEPLOY_HOST."
    exit 1
  fi
}

# Digest that a tag currently resolves to on the registry, or "" if the tag
# does not exist. Uses buildx imagetools (no pull) so it's cheap.
#
# We parse the `Digest:` line of the default output rather than
# `--format '{{.Manifest.Digest}}'`: for a MULTI-ARCH image the top-level is an
# OCI *index* (``.Index``, not ``.Manifest``), and buildx (v0.29) silently
# renders the full human output for `{{.Manifest.Digest}}` instead of the
# digest — so two tags pointing at the SAME index compared unequal and the gate
# always false-failed. The first `Digest:` line is the index digest, which is
# exactly what a tag resolves to, so identical tags compare equal.
image_digest() {
  # Retry a few times: ghcr occasionally throttles rapid imagetools calls and
  # returns nothing, which would make the promote gate false-fail on a transient
  # blip. A genuinely-absent tag returns empty after all attempts (caller treats
  # empty as "not on the registry").
  local out
  for _ in 1 2 3; do
    out="$(docker buildx imagetools inspect "$1" 2>/dev/null | awk '/^Digest:/ {print $2; exit}')"
    if [[ -n "$out" ]]; then
      printf '%s' "$out"
      return 0
    fi
    sleep 2
  done
}

# Guard the documented footgun: `docker compose up --pull always` fetches
# whatever :latest points at, so deploying before CI's `promote` job has retagged
# the current commit silently redeploys the PREVIOUS image. CI tags every build
# `:<full-sha>` and promote retags that to `:latest` (see .github/workflows/ci.yml).
# So :latest is current iff it resolves to the same digest as :<HEAD-sha>.
check_promote_gate() {
  if [[ "$SKIP_PROMOTE_CHECK" == "1" ]]; then
    bold "Promote gate BYPASSED (--force / SKIP_PROMOTE_CHECK=1) — you own verifying :latest is current."
    return 0
  fi
  if ! command -v docker >/dev/null 2>&1; then
    err "docker not found locally, so the promote gate can't run. Verify :latest is current yourself (gh run list --branch main), then re-run with --force."
    exit 1
  fi
  local sha sha_digest latest_digest
  if ! sha="$(git rev-parse HEAD 2>/dev/null)"; then
    err "Not in a git checkout — can't resolve the commit to verify. Re-run with --force once you've confirmed :latest is current."
    exit 1
  fi
  bold "Verifying CI promoted ${sha:0:12} to :latest ..."
  sha_digest="$(image_digest "$IMAGE:$sha")"
  if [[ -z "$sha_digest" ]]; then
    err "No image tagged $IMAGE:${sha:0:12} on the registry yet — CI's build/promote for this commit hasn't finished"
    err "(or HEAD was never pushed to main). Wait for the promote job: gh run list --branch main --workflow ci.yml"
    err "Then re-run, or --force to bypass."
    exit 1
  fi
  latest_digest="$(image_digest "$IMAGE:latest")"
  if [[ "$latest_digest" != "$sha_digest" ]]; then
    err ":latest does not point at ${sha:0:12} yet — the promote job hasn't retagged this commit."
    err "  :latest  -> ${latest_digest:-<none>}"
    err "  :$( printf %.12s "$sha")… -> $sha_digest"
    err "Deploying now would redeploy the previous image. Wait for promote, or --force to bypass."
    exit 1
  fi
  echo "  :latest is ${sha:0:12} — promote gate OK."
}

show_status() {
  bold "Stack on $HOST:$DIR"
  remote "docker compose ps --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}' \
    $DB_SVC $WEB_SVC $WORKER_SVC library-embedder"
  bold "Alembic head (prod DB):"
  remote "docker compose exec -T $DB_SVC psql -U library -tAc \
    'select version_num from alembic_version;'" || true
}

show_logs() {
  bold "Recent $WEB_SVC logs:"
  remote "docker compose logs --tail 40 $WEB_SVC"
  bold "Recent $WORKER_SVC logs:"
  remote "docker compose logs --tail 40 $WORKER_SVC"
}

# Compose's leftover containers. To recreate a container, `docker compose up`
# first renames the old one to `<12 hex>_<name>`; when that is interrupted the
# temporary container stays behind, and the next `up` fails with "container
# name ... is already in use" and leaves every library container `Created`:
# production down (2026-10-31, and 2026-11-04 twice — once as a running
# duplicate embedder after a deploy that otherwise succeeded).
COMPOSE_PROJECT="${LIBRARY_DEPLOY_PROJECT:-$(basename "$DIR")}"
LEFTOVER_RE='^[0-9a-f]{12}_library-'

# "<name> <state>" for every container of the compose project on the host.
list_project_containers() {
  remote "docker ps -a --filter label=com.docker.compose.project=$COMPOSE_PROJECT \
    --format '{{.Names}} {{.State}}'"
}

# Remove compose's temporary `<12 hex>_library-…` containers, and only those.
remove_compose_leftovers() {
  local leftovers
  leftovers="$(list_project_containers | awk '{print $1}' | grep -E "$LEFTOVER_RE" || true)"
  if [[ -n "$leftovers" ]]; then
    echo "  removing compose leftovers: $(tr '\n' ' ' <<<"$leftovers")"
    remote "docker rm -f $(tr '\n' ' ' <<<"$leftovers")" >/dev/null
  fi
}

# The manual deploy's mutating steps, as one script run on the host under the
# host lock. One ssh session holds the lock for the whole sequence; WUD's deploy
# script locks the same directory (it mounts it read-only, so the lock is on the
# directory itself, not on a file in it). `flock -E 75` tells "WUD is still
# deploying" apart from a failure. The migration runs once, here, by
# `compose run`: `up --no-deps` then starts web and worker without waiting on
# (or re-running) the one-shot `library-migrate` service. `run -T </dev/null`
# matters: this script reaches the host on bash's stdin, and `compose run`
# attaches stdin by default, so it would swallow the lines after it.
manual_deploy_steps() {
  cat <<EOF
set -euo pipefail
leftovers() {
  docker ps -a --filter label=com.docker.compose.project=$COMPOSE_PROJECT \\
    --format '{{.Names}} {{.State}}' | awk '{print \$1}' | grep -E '$LEFTOVER_RE' || true
}
clear_leftovers() {
  local names
  names="\$(leftovers)"
  if [[ -n "\$names" ]]; then
    echo "  removing compose leftovers: \$(tr '\\n' ' ' <<<"\$names")"
    # shellcheck disable=SC2086  # one name per line: split on purpose
    docker rm -f \$names >/dev/null
  fi
}
clear_leftovers
docker compose pull $MIGRATE_SVC $APP_SVCS
docker compose run --rm -T $MIGRATE_SVC </dev/null || exit 3
if ! docker compose up -d --no-deps $APP_SVCS; then
  echo "docker compose up failed; removing compose leftovers and retrying once." >&2
  clear_leftovers
  docker compose up -d --no-deps $APP_SVCS
fi
EOF
}

run_manual_deploy() {
  local code=0
  # shellcheck disable=SC2029  # $DIR is this script's own config (see remote()).
  manual_deploy_steps | ssh "$HOST" "cd '$DIR' && flock -w $LOCK_WAIT -E 75 '$DIR' bash -s" || code=$?
  case "$code" in
    0) ;;
    3)
      err "library-migrate failed — migrations FAILED. The webserver and worker were NOT recreated."
      err "Inspect: ssh $HOST 'cd $DIR && docker compose logs --tail 50 $MIGRATE_SVC' (the run container is removed; its output is above)."
      err "Roll back per docs/runbooks/deploy.md if needed."
      exit 1 ;;
    75)
      err "Could not take the deploy lock within ${LOCK_WAIT}s: a WUD deploy is probably still running."
      err "Check: ssh $HOST 'docker logs --tail 50 wud'. Then re-run, or just verify: scripts/deploy.sh"
      exit 1 ;;
    *)
      err "The manual deploy failed (exit $code); see the output above."
      exit 1 ;;
  esac
}

# After `up`: no leftovers, and nothing stuck in `Created` (= never started).
check_stack_clean() {
  remove_compose_leftovers
  local stuck
  # The host's compose project also runs non-library services (alloy, wud, …).
  stuck="$(list_project_containers | awk '$2 == "created" && $1 ~ /library-/ {print $1}')"
  if [[ -n "$stuck" ]]; then
    err "These containers were created but never started (the stack may be DOWN):"
    err "  $(tr '\n' ' ' <<<"$stuck")"
    err "Inspect: ssh $HOST 'docker ps -a --filter name=library'; recovery in docs/runbooks/deploy.md."
    exit 1
  fi
}

# Each deploy leaves the previous 1.4GB image behind, untagged, and the host's
# root disk is 16G: 14 of them took it to 97% full by 2026-11-06. Prune the
# dangling (untagged, unused) images once the new one is healthy. Never `-a`:
# that would also delete tagged images no container happens to run right now.
# Rollback does not need them — it pins a `:<sha>` tag, which CI keeps on ghcr.
# A prune failure only costs disk, so it never fails the deploy.
prune_old_images() {
  bold "Pruning old images:"
  remote "docker image prune -f" | grep -E '^Total reclaimed space' || true
  remote "df -h / | awk 'NR==2 {print \"  root disk: \" \$4 \" free (\" \$5 \" used)\"}'" || true
}

# The /healthz body, read inside the webserver container, or "" if it does not
# answer. Inside the container, not via a host port: the host publishes the app
# on :8010, and when paperless-ngx still ran on :8000 (until 2026-07) a bare
# `curl localhost:8000` got its 302 and passed. python, not curl: python is what
# the image's own HEALTHCHECK uses, and curl may not be installed. Compact JSON,
# no spaces after the colons.
healthz_body() {
  remote "docker compose exec -T $WEB_SVC python -c \
      \"import urllib.request; print(urllib.request.urlopen('http://localhost:8000/healthz').read().decode())\"" \
      2>/dev/null || true
}

# Wait until /healthz answers, and — given a commit — reports it as `git_sha`.
# WUD notices a new :latest within a minute, then pulls, migrates and starts the
# app (~20 s boot), so the default allows 10 minutes. Sets HEALTH_BODY.
HEALTH_BODY=""
wait_until_live() {
  local want="$1" waited=0 body
  if [[ -n "$want" ]]; then
    bold "Waiting for ${want:0:12} to be live (up to ${VERIFY_TIMEOUT}s) ..."
  else
    bold "Waiting for /healthz (up to ${VERIFY_TIMEOUT}s) ..."
  fi
  while :; do
    body="$(healthz_body)"
    if [[ -n "$body" ]] && { [[ -z "$want" ]] || grep -q "\"git_sha\": *\"$want\"" <<<"$body"; }; then
      HEALTH_BODY="$body"
      echo "  /healthz OK${want:+ on ${want:0:12}}"
      return 0
    fi
    if (( waited >= VERIFY_TIMEOUT )); then
      err "/healthz did not report ${want:-OK} within ${VERIFY_TIMEOUT}s."
      [[ -n "$body" ]] && err "  last body: $body"
      err "WUD deploys each new :latest by itself; if it is paused or failing (ssh $HOST 'docker logs --tail 50 wud'),"
      err "deploy by hand with: scripts/deploy.sh --deploy"
      exit 1
    fi
    sleep "$VERIFY_INTERVAL"
    waited=$(( waited + VERIFY_INTERVAL ))
    (( VERIFY_INTERVAL == 0 )) && waited=$(( waited + 1 ))
  done
}

# The migration check, by state rather than by container: the database's
# revision must be the running image's head. True whoever ran the migration
# (WUD, or `--deploy`'s `compose run --rm`, which leaves no container to inspect).
check_migration_state() {
  bold "Migration state:"
  local db head
  db="$(remote "docker compose exec -T $DB_SVC psql -U library -tAc 'select version_num from alembic_version;'" | tr -d '[:space:]')"
  head="$(remote "docker compose exec -T $WEB_SVC alembic heads" | awk 'NF {print $1; exit}')"
  if [[ -z "$db" || -z "$head" || "$db" != "$head" ]]; then
    err "The database is at revision '${db:-?}' but the running image expects '${head:-?}' — migrations FAILED or did not run."
    err "Inspect: ssh $HOST 'docker logs --tail 50 wud' (WUD's deploy) or 'cd $DIR && docker compose logs $MIGRATE_SVC'."
    err "Roll back per docs/runbooks/deploy.md if needed."
    exit 1
  fi
  echo "  alembic_version = $db = image head"
}

# A 200 is not the whole story: /healthz stays 200 while reporting
# `status: "degraded"`, because the app IS serving. The OCR weights are fatal
# (GH #109): they are read lazily, so an image without them deploys green and
# then fails one document at a time, hours later. Credential degradation is only
# reported: it needs a human, not a rollback.
check_health_detail() {
  bold "Health detail:"
  echo "  $HEALTH_BODY"
  if grep -q '"ocr_models": *"missing"' <<<"$HEALTH_BODY"; then
    err "OCR model weights are MISSING from the running image."
    err "Photo OCR (JPEG/PNG/HEIC, and any scan that falls through the"
    err "confidence gate) will fail per-document. The image did not ship"
    err "models/ocr/ — roll back (docs/runbooks/deploy.md) and rebuild."
    exit 1
  fi
  if grep -q '"status": *"degraded"' <<<"$HEALTH_BODY"; then
    err "  /healthz reports status=degraded — see the fields above."
    err "  Not fatal: the app is serving. See docs/deployment.md §1.4.2."
  fi
}

# Everything after the deploy itself, whoever ran it.
post_deploy_checks() {
  wait_until_live "$1"
  check_stack_clean
  check_migration_state
  check_health_detail
  prune_old_images
  echo
  show_status
  echo
}

head_sha() { git rev-parse HEAD 2>/dev/null || true; }

# Default: WUD deployed (or is deploying) :latest; confirm HEAD is live and sound.
verify() {
  require_ssh
  check_promote_gate
  post_deploy_checks "$(head_sha)"
  bold "Deploy verified."
}

# Manual: deploy :latest ourselves, under the lock, migrating once.
deploy() {
  require_ssh
  check_promote_gate

  bold "Before:"
  remote "docker compose ps --format 'table {{.Service}}\t{{.Status}}' $APP_SVCS" || true

  bold "Deploying :latest by hand on $HOST (lock, pull, migrate once, then $APP_SVCS) ..."
  run_manual_deploy

  # Forced past the promote gate, :latest may not be HEAD, so don't wait for it.
  if [[ "$SKIP_PROMOTE_CHECK" == "1" ]]; then
    post_deploy_checks ""
  else
    post_deploy_checks "$(head_sha)"
  fi
  bold "Deploy complete."
}

# Sourced (tests/test_deploy_script.py) → define the functions only.
[[ "${BASH_SOURCE[0]}" != "$0" ]] && return 0

case "${1:-verify}" in
  --status|status) require_ssh; show_status ;;
  --logs|logs) require_ssh; show_logs ;;
  --help|-h|help)
    sed -n '2,44p' "$0" | sed 's/^# \{0,1\}//' ;;
  --deploy|deploy) deploy ;;
  --force|force) SKIP_PROMOTE_CHECK=1; deploy ;;
  verify|"") verify ;;
  *) err "Unknown option: $1"; err "Try: scripts/deploy.sh --help"; exit 2 ;;
esac
