#!/usr/bin/env bash
# DC What-If: set up (or update) a fresh Ubuntu 24.04 server. Run ON THE SERVER as root.
#
#   curl -fsSL https://raw.githubusercontent.com/victornguyen7/DC-HackUMass/main/deploy/setup.sh | DOMAIN=your-domain bash
#
# or, from a clone: DOMAIN=your-domain bash /srv/dc-whatif/deploy/setup.sh
#
# DOMAIN      the domain whose A record points at this server (Caddy gets HTTPS for it).
#             Leave it out to serve plain HTTP on the server's IP (for testing before DNS works).
#             No domain yet? Use DOMAIN=<ip-with-dashes>.sslip.io (e.g. 155-138-211-252.sslip.io):
#             a free hostname for the IP, and Caddy still gets HTTPS for it.
# TIGER_CLI=1 also install the Tiger CLI and turn on Ask Sam's Tiger lookups: stores the CLI's
#             password in ~/.pgpass (deploy/tiger-password.sh). The login stays manual, once:
#             `sudo -iu dcsim tiger auth login --headless` (see deploy/README.md).
# BRANCH      git branch to deploy (default main).
#
# Safe to re-run: it pulls the latest code, rebuilds and restarts. It never touches
# engine/.env (your secrets) or /etc/dcsim.env once they exist.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/victornguyen7/DC-HackUMass.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/srv/dc-whatif
APP_USER=dcsim
DOMAIN="${DOMAIN:-}"

log() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }
as_app() { sudo -iu "$APP_USER" bash -lc "$*"; }

[ "$(id -u)" = 0 ] || { echo "Run as root (ssh root@<server-ip>)." >&2; exit 1; }
case "$DOMAIN" in
  *your-domain*|*example.com*|*yourname*|*://*|*/*)
    echo "DOMAIN must be a real hostname without https://, e.g. 155-138-211-252.sslip.io or dcwhatif.xyz (got '$DOMAIN')." >&2
    exit 1 ;;
esac

log "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q git curl rsync ufw caddy ca-certificates
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 24 ]; then
  curl -fsSL https://deb.nodesource.com/setup_24.x | bash -
  apt-get install -y -q nodejs
fi

log "App user '$APP_USER' and uv"
id "$APP_USER" >/dev/null 2>&1 || useradd --create-home --shell /bin/bash "$APP_USER"
as_app 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
if [ "${TIGER_CLI:-0}" = 1 ]; then
  as_app 'command -v tiger >/dev/null || curl -fsSL https://cli.tigerdata.com | sh'
fi

log "Code ($BRANCH) in $APP_DIR"
mkdir -p "$APP_DIR"
# -R: an earlier clone, uv sync or npm ci run as root leaves root-owned files that git
# ("dubious ownership"), uv and npm would refuse or fail on as $APP_USER.
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  as_app "cd $APP_DIR && git fetch -q origin $BRANCH && git checkout -q $BRANCH && git reset -q --hard origin/$BRANCH"
else
  as_app "git clone -q --branch $BRANCH $REPO_URL $APP_DIR"
fi

log "Engine (uv sync) and web app (npm ci, build)"
as_app "cd $APP_DIR/engine && uv sync -q"
as_app "cd $APP_DIR/web && npm ci --no-audit --no-fund --loglevel=error && npm run build"

log "Settings"
touch "$APP_DIR/engine/.env"   # secrets: copied from your laptop by deploy/push-secrets.sh
chown "$APP_USER:$APP_USER" "$APP_DIR/engine/.env"
chmod 600 "$APP_DIR/engine/.env"
if [ ! -f /etc/dcsim.env ]; then
  ORIGIN="http://localhost"; [ -n "$DOMAIN" ] && ORIGIN="https://$DOMAIN"
  cat > /etc/dcsim.env <<EOF
# Server-only settings (systemd reads these before engine/.env, so they win).
DCSIM_WORKERS=$(nproc)
DCSIM_CORS=$ORIGIN
DCSIM_ADMIN_TOKEN=$(openssl rand -hex 24)
DCSIM_AI_RATE=10
# Adopt writes undeletable forecasts to the Tiger ledger. Off while the site is public;
# set to 1 and \`systemctl restart dcsim\` for the demo.
DCSIM_LEDGER_WRITES=0
EOF
  [ "${TIGER_CLI:-0}" = 1 ] || echo "DCSIM_TIGER_MCP=0" >> /etc/dcsim.env
  chmod 600 /etc/dcsim.env
elif [ -n "$DOMAIN" ]; then
  # A new domain on a re-run: allow it, keep the rest (admin token, ledger, Tiger settings).
  sed -i "s|^DCSIM_CORS=.*|DCSIM_CORS=https://$DOMAIN|" /etc/dcsim.env
fi
if [ "${TIGER_CLI:-0}" = 1 ]; then
  sed -i '/^DCSIM_TIGER_MCP=0$/d' /etc/dcsim.env   # Sam's Tiger lookups on
  log "Tiger CLI password (pgpass)"
  bash "$APP_DIR/deploy/tiger-password.sh" || true
fi

log "systemd service"
install -m 644 "$APP_DIR/deploy/dcsim.service" /etc/systemd/system/dcsim.service
systemctl daemon-reload
systemctl enable -q dcsim
systemctl restart dcsim

log "Caddy (${DOMAIN:-plain HTTP on :80})"
sed "s|__SITE__|${DOMAIN:-:80}|; s|__ROOT__|$APP_DIR/web/dist|" "$APP_DIR/deploy/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable -q caddy
systemctl reload caddy || systemctl restart caddy

log "Firewall (SSH, HTTP, HTTPS)"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

log "Health check"
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
  sleep 1
done
if curl -fsS http://127.0.0.1:8000/api/health; then
  echo
  IP=$(curl -fsS -4 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
  echo "API is up. Open ${DOMAIN:+https://$DOMAIN or }http://$IP"
  echo "Admin token: grep DCSIM_ADMIN_TOKEN /etc/dcsim.env"
else
  echo "API did not start. Look at: journalctl -u dcsim -n 50 --no-pager" >&2
  exit 1
fi
