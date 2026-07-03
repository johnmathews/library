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
#     :latest BEFORE you deploy, or you will redeploy the old image.
#   - SSH access to $LIBRARY_DEPLOY_HOST is key-based (no password prompt).
#
# Environment overrides:
#   LIBRARY_DEPLOY_HOST   SSH host/alias            (default: paperless)
#   LIBRARY_DEPLOY_DIR    Compose dir on the host   (default: /srv/apps)
#
set -euo pipefail

HOST="${LIBRARY_DEPLOY_HOST:-paperless}"
DIR="${LIBRARY_DEPLOY_DIR:-/srv/apps}"

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
  deploy|"") deploy ;;
  *) err "Unknown option: $1"; err "Try: scripts/deploy.sh --help"; exit 2 ;;
esac
