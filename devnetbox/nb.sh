#!/usr/bin/env bash
# nb.sh: manage throwaway dev NetBox v4.7.4 stacks (netbox-docker 5.1.1) with docker compose.
#
#   devnetbox/nb.sh up|down|destroy|seed|reset|status|check|env|tunnel   (NB_PROJECT and NB_PORT from the environment)
#
#   up       create secrets (once) and start the stack; wait (bounded) until /api/status/ answers
#   down     stop and remove containers; keep the data volumes
#   destroy  remove containers AND volumes AND the local secrets file (the stack is gone)
#   seed     create the iso custom fields and the synthetic seed data (idempotent)
#   reset    destroy + up + seed
#   status   print /api/status/ and object counts
#   check    compare every id the fixtures use, and the trace fixtures, with this stack (fresh seed only)
#   env [ro|rw|admin]  print NETBOX_URL and NETBOX_TOKEN lines for this stack (for .env); v2 token nbt_<key>.<token>.
#            ro (default): the proxy's own user `rackwalker-proxy`, token write_enabled=false (dry-run; NetBox refuses writes)
#            rw: same user, write_enabled=true, minimum add/change permissions (only with WRITE_ENABLED=1)
#            admin: the superuser token (seeding and test bookkeeping only; never for the proxy). See proxy_tokens.py.
#   tunnel   foreground ssh -L tunnel localhost:NB_PORT -> NB_HOST:NB_PORT (remote Docker host with a firewalled port)
#
# Defaults: NB_PROJECT=rackwalker NB_PORT=8100 NB_HOST=localhost. Docker runs wherever your docker CLI points: the
# local daemon by default, or a remote one if you export DOCKER_HOST (for example ssh://user@docker-host); then set
# NB_HOST to that host name so the URL reaches the published port.
# Several stacks can run side by side: each needs its own NB_PROJECT and NB_PORT.
# Secrets per stack live in devnetbox/.stacks/<project>.env on this machine (gitignored, mode 600). Synthetic data only.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
NB_PROJECT=${NB_PROJECT:-rackwalker}
NB_PORT=${NB_PORT:-8100}
NB_HOST=${NB_HOST:-localhost}
NB_WAIT_S=${NB_WAIT_S:-900}
export NB_PROJECT NB_PORT
SECRETS=$HERE/.stacks/$NB_PROJECT.env
PY=${PY:-$ROOT/.venv/bin/python}

die() { echo "nb.sh: $*" >&2; exit 1; }
log() { echo "nb.sh [$NB_PROJECT:$NB_PORT] $*"; }
[[ $NB_PROJECT =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "NB_PROJECT must be lower-case letters, digits, '-' or '_' (it names the compose project)"
[[ $NB_PORT =~ ^[0-9]+$ ]] || die "NB_PORT must be a number"
dc() { docker compose -f "$HERE/compose.yml" -p "$NB_PROJECT" "$@"; }
gen() { python3 -c "import secrets,sys; print(secrets.token_hex(int(sys.argv[1])))" "$1"; }
url() { echo "${NB_URL:-http://$NB_HOST:$NB_PORT}"; }
# v2 API token (NetBox >= 4.5): the plaintext is nbt_<12-char key>.<40-char secret>, sent as "Authorization: Bearer ...".
token() { echo "nbt_$(sed -n 's/^SUPERUSER_API_KEY=//p' "$SECRETS").$(sed -n 's/^SUPERUSER_API_TOKEN=//p' "$SECRETS")"; }

ensure_secrets() {
  mkdir -p "$HERE/.stacks"; chmod 700 "$HERE/.stacks"
  if [ -f "$SECRETS" ]; then
    grep -q '^API_TOKEN_PEPPER_1=' "$SECRETS" && return 0
    die "$SECRETS predates NetBox 4.7 (no API_TOKEN_PEPPER_1/SUPERUSER_API_KEY); run destroy first"
  fi
  local pg; pg=$(gen 20)
  umask 077
  cat > "$SECRETS" <<EOF
# dev NetBox stack $NB_PROJECT (port $NB_PORT); generated $(date -Is) by nb.sh. Throwaway, synthetic data only.
POSTGRES_PASSWORD=$pg
DB_PASSWORD=$pg
REDIS_PASSWORD=$(gen 20)
REDIS_CACHE_PASSWORD=$(gen 20)
SECRET_KEY=$(gen 32)
SUPERUSER_PASSWORD=$(gen 12)
SUPERUSER_API_KEY=$(gen 6)
SUPERUSER_API_TOKEN=$(gen 20)
API_TOKEN_PEPPER_1=$(gen 32)
EOF
  log "secrets created at $SECRETS (mode 600; values not shown)"
}

wait_status() {
  local deadline=$(( $(date +%s) + NB_WAIT_S )) code
  while :; do
    code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' -H "Authorization: Bearer $(token)" "$(url)/api/status/" || true)
    [ "$code" = 200 ] && { log "API up at $(url)"; return 0; }
    if dc ps -a --format '{{.Service}} {{.State}}' | grep -E ' (exited|dead)$' | grep -v -- '-init' >/dev/null; then
      dc ps -a; dc logs --tail 40 netbox; die "a container exited while waiting"
    fi
    [ "$(date +%s)" -ge "$deadline" ] && { dc ps -a; die "API did not answer 200 within ${NB_WAIT_S}s (last HTTP $code). If the containers are healthy, the port may be firewalled: try 'make netbox-tunnel' and NB_URL=http://localhost:$NB_PORT"; }
    sleep 10
  done
}

case ${1:-} in
  up)
    ensure_secrets
    log "starting (docker: ${DOCKER_HOST:-local daemon})"
    dc up -d --quiet-pull
    wait_status ;;
  down) dc down; log "stopped (volumes kept)" ;;
  destroy) dc down -v --remove-orphans; rm -f "$SECRETS"; log "destroyed (containers, volumes, secrets)" ;;
  seed)
    [ -f "$SECRETS" ] || die "no secrets for $NB_PROJECT; run up first"
    NETBOX_URL=$(url) NETBOX_TOKEN=$(token) "$PY" "$HERE/seed.py" ;;
  reset) "$0" destroy; "$0" up; "$0" seed ;;
  status)
    [ -f "$SECRETS" ] || die "no secrets for $NB_PROJECT"
    NETBOX_URL=$(url) NETBOX_TOKEN=$(token) "$PY" "$HERE/seed.py" --counts ;;
  check)
    [ -f "$SECRETS" ] || die "no secrets for $NB_PROJECT"
    NETBOX_URL=$(url) NETBOX_TOKEN=$(token) "$PY" "$HERE/check_fixtures.py" ;;
  env)
    [ -f "$SECRETS" ] || die "no secrets for $NB_PROJECT"
    case ${2:-ro} in
      admin) tok=$(token) ;;
      ro|rw) tok=$(NETBOX_URL=$(url) NETBOX_TOKEN=$(token) SECRETS=$SECRETS "$PY" "$HERE/proxy_tokens.py" "${2:-ro}") ;;
      *) die "env takes ro|rw|admin" ;;
    esac
    echo "NETBOX_URL=$(url)"; echo "NETBOX_TOKEN=$tok" ;;
  tunnel) log "tunnel localhost:$NB_PORT -> $NB_HOST:$NB_PORT (Ctrl-C to stop)"; exec ssh -N -L "$NB_PORT:localhost:$NB_PORT" "$NB_HOST" ;;
  *) sed -n 2,20p "$0"; exit 2 ;;
esac
