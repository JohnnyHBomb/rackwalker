#!/usr/bin/env bash
# dev.sh: start the proxy (uvicorn) and the vite dev server together; used by `make dev` / `make dev-fixtures`.
#   ./dev.sh netbox    proxy against NETBOX_URL / NETBOX_TOKEN / WRITE_ENABLED from .env
#   ./dev.sh fixtures  proxy serves fixtures/ (no NetBox)
# Fails fast if PROXY_PORT is already in use, or if the new proxy exits or does not answer /api/health within 20 s.
set -euo pipefail
cd "$(dirname "$0")"
PROXY_PORT=${PROXY_PORT:-8710}
VENV=${VENV:-$PWD/.venv}
case ${1:-netbox} in
  netbox) [ -f .env ] || { echo "no .env: run 'make netbox-env' or use 'make dev-fixtures'"; exit 1; }
          set -a; . ./.env; set +a ;;
  fixtures) export RACKWALKER_FIXTURES=1 ;;
  *) echo "usage: $0 netbox|fixtures"; exit 2 ;;
esac
# Refuse a busy port: otherwise the health check below would reach the old listener while the new proxy exits.
if (exec 3<>"/dev/tcp/127.0.0.1/$PROXY_PORT") 2>/dev/null; then
  echo "port $PROXY_PORT is already in use (another proxy?): stop it or set PROXY_PORT=..."; exit 1
fi
"$VENV/bin/uvicorn" rackwalker_proxy.app:create_app --factory --host 127.0.0.1 --port "$PROXY_PORT" &
PROXY=$!
trap 'kill "$PROXY" 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -sf -m 1 "http://127.0.0.1:$PROXY_PORT/api/health" >/dev/null && break
  kill -0 "$PROXY" 2>/dev/null || { echo "proxy failed to start (port $PROXY_PORT busy? set PROXY_PORT=...)"; exit 1; }
  sleep 0.5
done
curl -sf -m 1 "http://127.0.0.1:$PROXY_PORT/api/health" >/dev/null || { echo "proxy did not answer within 20 s"; exit 1; }
kill -0 "$PROXY" 2>/dev/null || { echo "proxy exited after start (port $PROXY_PORT taken?)"; exit 1; }
cd web && RACKWALKER_PROXY_URL=http://127.0.0.1:$PROXY_PORT npx vite
