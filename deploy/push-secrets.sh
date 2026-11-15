#!/usr/bin/env bash
# Run ON YOUR LAPTOP, from anywhere in the repo, after deploy/setup.sh has run on the server.
#
#   deploy/push-secrets.sh root@<server-ip>              # engine/.env + cache/
#   deploy/push-secrets.sh root@<server-ip> --no-cache   # engine/.env only
#
# Sends engine/.env (Gemini/Groq keys, TIGER_SERVICE_ID) to the server. If it has no
# DCSIM_TSDB_URL, it adds one from `tiger db uri --with-password`, so the server can reach
# Tiger without the Tiger CLI. Then it copies cache/ (cached simulation results, so the first
# requests don't re-simulate) and restarts the API. Nothing is committed or stored elsewhere.
set -euo pipefail

HOST="${1:?usage: deploy/push-secrets.sh root@<server-ip> [--no-cache]}"
NO_CACHE="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR=/srv/dc-whatif
ENV_FILE="$ROOT/engine/.env"

[ -f "$ENV_FILE" ] || { echo "No $ENV_FILE on this laptop." >&2; exit 1; }

echo "==> engine/.env -> $HOST"
{
  cat "$ENV_FILE"
  if ! grep -q '^DCSIM_TSDB_URL=' "$ENV_FILE"; then
    if command -v tiger >/dev/null && URI=$(tiger db uri --with-password 2>/dev/null) && [ -n "$URI" ]; then
      printf '\n# Added by deploy/push-secrets.sh\nDCSIM_TSDB_URL=%s\n' "$URI"
    else
      echo "   (no DCSIM_TSDB_URL and \`tiger db uri\` failed: Tiger features will be off on the server)" >&2
    fi
  fi
} | ssh "$HOST" "umask 077 && cat > $APP_DIR/engine/.env && chown dcsim:dcsim $APP_DIR/engine/.env"

if [ "$NO_CACHE" != "--no-cache" ] && [ -d "$ROOT/cache" ]; then
  echo "==> cache/ -> $HOST ($(du -sh "$ROOT/cache" | cut -f1))"
  rsync -az --info=progress2 "$ROOT/cache/" "$HOST:$APP_DIR/cache/" 2>/dev/null \
    || rsync -az "$ROOT/cache/" "$HOST:$APP_DIR/cache/"   # macOS's old rsync has no --info
  ssh "$HOST" "chown -R dcsim:dcsim $APP_DIR/cache"
fi

echo "==> restart API"
ssh "$HOST" "systemctl restart dcsim && sleep 3 && curl -fsS http://127.0.0.1:8000/api/health && echo"
echo "Done."
