#!/usr/bin/env bash
# Set the admin password. You type it here (hidden); only a salted hash is stored, and the password
# never appears in chat, in shell history or on a command line.
#
#   deploy/set-admin-password.sh            # the LIVE server (updates /srv/uctballroom/.env and restarts)
#   deploy/set-admin-password.sh --local    # this machine's .env, for trying it out locally
#
# Settings (environment overrides): UCT_HOST (default root@143.110.171.187), UCT_KEY (default ~/.ssh/id_digital_ocean_nov26)
set -euo pipefail
cd "$(dirname "$0")/.."

read -r -s -p "New admin password: " pw; echo
read -r -s -p "Again: " pw2; echo
[ "$pw" = "$pw2" ] || { echo "Those differ." >&2; exit 1; }
[ "${#pw}" -ge 12 ] || { echo "Use at least 12 characters." >&2; exit 1; }
hash=$(PW="$pw" uv run python -c 'import os; from werkzeug.security import generate_password_hash as g; print(g(os.environ["PW"]), end="")')
unset pw pw2

if [ "${1:-}" = "--local" ]; then
  touch .env
  sed -i '/^ADMIN_PASSWORD_HASH=/d' .env
  printf 'ADMIN_PASSWORD_HASH=%s\n' "$hash" >> .env
  echo "Saved to .env. Restart the local server."
else
  HOST="${UCT_HOST:-root@143.110.171.187}"
  KEY="${UCT_KEY:-$HOME/.ssh/id_digital_ocean_nov26}"
  printf '%s' "$hash" | ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
    'h=$(cat); f=/srv/uctballroom/.env; sed -i "/^ADMIN_PASSWORD_HASH=/d" "$f"; printf "ADMIN_PASSWORD_HASH=%s\n" "$h" >> "$f"; systemctl restart uctballroom'
  echo "Saved on the server and restarted. Sign in at https://www.uctballroom.co.za/admin"
fi
