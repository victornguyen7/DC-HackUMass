#!/usr/bin/env bash
# Give the server's Tiger CLI (user dcsim) the database password, so Ask Sam's Tiger lookups
# (`tiger mcp start` -> db_query) can log in. Run ON THE SERVER as root. setup.sh (TIGER_CLI=1)
# and push-secrets.sh call it; running it by hand is safe too.
#
# Why it's needed: a server has no system keyring, so the CLI's default password storage fails
# ("dbus-launch: executable file not found") and Sam's queries get "password authentication
# failed". This switches the CLI to ~/.pgpass and saves the password from DCSIM_TSDB_URL in
# engine/.env (which push-secrets.sh adds). The Tiger login itself stays a manual step:
#   sudo -iu dcsim tiger auth login --headless
set -euo pipefail

APP_DIR=/srv/dc-whatif
APP_USER=dcsim
ENV_FILE="$APP_DIR/engine/.env"

[ "$(id -u)" = 0 ] || { echo "Run as root." >&2; exit 1; }
if ! sudo -iu "$APP_USER" bash -lc 'command -v tiger' >/dev/null 2>&1; then
  echo "Tiger CLI not installed for $APP_USER (run setup.sh with TIGER_CLI=1): skipped."
  exit 0
fi

SID=$(grep '^TIGER_SERVICE_ID=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d "'\" " || true)
URL=$(grep '^DCSIM_TSDB_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d "'\"" || true)
if [ -z "$SID" ] || [ -z "$URL" ]; then
  echo "engine/.env has no TIGER_SERVICE_ID or DCSIM_TSDB_URL yet (run deploy/push-secrets.sh from your laptop): skipped."
  exit 0
fi
# The password from the URL, %-decoded; read from stdin so it never appears in a process list.
PW=$(printf %s "$URL" | python3 -c 'import sys, urllib.parse as u; print(u.unquote(u.urlsplit(sys.stdin.read().strip()).password or ""))')
[ -n "$PW" ] || { echo "DCSIM_TSDB_URL has no password in it: skipped." >&2; exit 0; }

# One line per sudo -i call: sudo -i joins a multi-line command into one line.
sudo -iu "$APP_USER" tiger config set password_storage pgpass >/dev/null
if sudo -iu "$APP_USER" env TIGER_NEW_PASSWORD="$PW" tiger db save-password "$SID" --password-storage pgpass >/dev/null; then
  echo "Tiger password saved to /home/$APP_USER/.pgpass."
else
  echo "Saving the Tiger password failed. Log in first: sudo -iu $APP_USER tiger auth login --headless" >&2
fi
if ! sudo -iu "$APP_USER" tiger auth status >/dev/null 2>&1; then
  echo "Not logged in to Tiger yet: sudo -iu $APP_USER tiger auth login --headless, then systemctl restart dcsim"
fi
