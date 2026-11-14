#!/usr/bin/env bash
# Run on the server as the `deploy` user: pull the latest main, sync deps, restart, health-check.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"

cd /srv/uctballroom/app
git fetch --prune origin
git reset --hard origin/main
uv sync --frozen --no-dev
sudo systemctl restart uctballroom

for i in $(seq 1 15); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null; then
    echo "Deployed $(git rev-parse --short HEAD): healthy."
    exit 0
  fi
  sleep 1
done
echo "Health check failed. Recent logs:" >&2
sudo journalctl -u uctballroom -n 30 --no-pager >&2
exit 1
