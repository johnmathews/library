#!/usr/bin/env bash
#
# Deploy Library to the production host (the `paperless` LXC).
#
# Pulls the freshly-promoted `ghcr.io/johnmathews/library:latest` image, runs DB
# migrations via the one-shot `library-migrate` job, recreates the webserver and
# worker, and verifies the result (migrate exit code, /healthz, alembic head).
#
# This is the automation of docs/runbooks/deploy.md — read that for the why,
# the preconditions, and rollback. The procedure it encodes lives in
# docs/deployment.md §1.7.2.
#
# Usage:
#   scripts/deploy.sh            Deploy :latest to the prod host (default).
#   scripts/deploy.sh --status   Show the running stack; do not deploy.
#   scripts/deploy.sh --logs     Tail recent webserver + worker logs.
#   scripts/deploy.sh --help     This help.
#
# Preconditions (the script checks what it can, but you own these):
#   - `main` is green in CI: the build + promote jobs must have published the new
#     :latest BEFORE you deploy. The script now VERIFIES this (see the promote
#     gate below) by checking that :latest points at HEAD's image — but you still
#     own being on the commit you actually intend to ship.
#   - SSH access to $LIBRARY_DEPLOY_HOST is key-based (no password prompt).
#
# Usage (extra):
#   scripts/deploy.sh --force    Deploy without the promote gate (emergencies).
#
# Environment overrides:
#   LIBRARY_DEPLOY_HOST   SSH host/alias            (default: paperless)
#   LIBRARY_DEPLOY_DIR    Compose dir on the host   (default: /srv/apps)
#   SKIP_PROMOTE_CHECK    Set to 1 to bypass the promote gate (like --force).
#
set -euo pipefail

HOST="${LIBRARY_DEPLOY_HOST:-paperless}"
DIR="${LIBRARY_DEPLOY_DIR:-/srv/apps}"
IMAGE="ghcr.io/johnmathews/library"
SKIP_PROMOTE_CHECK="${SKIP_PROMOTE_CHECK:-0}"

# Prod service names differ from the repo compose file (api → library-webserver,
# etc. — see the deployment doc). These are the names on the live host.
MIGRATE_SVC="library-migrate"
WEB_SVC="library-webserver"
WORKER_SVC="library-worker"
DB_SVC="library-db"
DEPLOY_SVCS="$MIGRATE_SVC $WEB_SVC $WORKER_SVC"

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
image_digest() {
  docker buildx imagetools inspect "$1" --format '{{.Manifest.Digest}}' 2>/dev/null || true
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

deploy() {
  require_ssh
  check_promote_gate

  bold "Before:"
  remote "docker compose ps --format 'table {{.Service}}\t{{.Status}}' $WEB_SVC $WORKER_SVC" || true

  bold "Pulling :latest and recreating ($DEPLOY_SVCS) on $HOST ..."
  # --pull always fetches the freshly-promoted image; the one-shot migrate job
  # applies any new migrations transactionally before web/worker come up.
  remote "docker compose up -d --pull always $DEPLOY_SVCS"

  bold "Migration job result:"
  local code
  code="$(remote "docker inspect -f '{{.State.ExitCode}}' $MIGRATE_SVC" | tr -d '[:space:]')"
  remote "docker compose logs --tail 8 $MIGRATE_SVC" || true
  if [[ "$code" != "0" ]]; then
    err "library-migrate exited $code — migrations FAILED. The new web/worker may be running against an un-migrated DB."
    err "Inspect: ssh $HOST 'cd $DIR && docker compose logs $MIGRATE_SVC'"
    err "Roll back per docs/runbooks/deploy.md if needed."
    exit 1
  fi

  bold "Health check:"
  # Probe /healthz INSIDE the webserver container. The host's :8000 is
  # paperless-ngx (the library app publishes on :8010), and a bare
  # `curl localhost:8000` there gets paperless' 302 to /accounts/login/ —
  # which `curl -fsS` treats as success, so it never actually checked the
  # library app. Exec-in-container hits the app directly on its own port, and
  # uses python (guaranteed present — it's what the image's HEALTHCHECK uses;
  # curl may not be installed) so a non-2xx raises and fails the gate.
  if remote "docker compose exec -T $WEB_SVC \
      python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')\""; then
    echo "  /healthz OK"
  else
    err "/healthz did not return OK — check 'docker compose logs $WEB_SVC'."
    exit 1
  fi

  echo
  show_status
  echo
  bold "Deploy complete."
}

case "${1:-deploy}" in
  --status|status) require_ssh; show_status ;;
  --logs|logs) require_ssh; show_logs ;;
  --help|-h|help)
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//' ;;
  --force|force) SKIP_PROMOTE_CHECK=1; deploy ;;
  deploy|"") deploy ;;
  *) err "Unknown option: $1"; err "Try: scripts/deploy.sh --help"; exit 2 ;;
esac
