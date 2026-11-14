#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 droplet. Run as root:
#   ssh root@IP "DOMAIN=example.com REPO_URL=https://github.com/you/repo.git bash -s" < deploy/provision.sh
# Safe to re-run.
set -euo pipefail
: "${DOMAIN:?set DOMAIN}"
: "${REPO_URL:?set REPO_URL}"
export DEBIAN_FRONTEND=noninteractive
APP=/srv/uctballroom

apt-get update -y
apt-get install -y git curl sqlite3 ufw debian-keyring debian-archive-keyring apt-transport-https gpg

# Caddy (official repo)
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y
  apt-get install -y caddy
fi

# Users: `deploy` owns the code and runs deploys; `uctballroom` runs the service and owns the data.
id deploy >/dev/null 2>&1 || adduser --disabled-password --gecos "" deploy
id uctballroom >/dev/null 2>&1 || adduser --system --group --home "$APP" --shell /usr/sbin/nologin uctballroom
mkdir -p "$APP/data" /home/deploy/.ssh
chown deploy:deploy "$APP"
chown uctballroom:uctballroom "$APP/data"
chmod 755 "$APP"
# deploy may log in with the same keys as root (CI gets its own key appended later).
if [ -f /root/.ssh/authorized_keys ] && [ ! -s /home/deploy/.ssh/authorized_keys ]; then
  cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
fi
chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys

# deploy may restart the app and reload Caddy, nothing else.
cat > /etc/sudoers.d/deploy <<'SUDO'
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart uctballroom, /usr/bin/systemctl reload caddy, /usr/bin/journalctl -u uctballroom *
SUDO
chmod 440 /etc/sudoers.d/deploy && visudo -cf /etc/sudoers.d/deploy

# uv + code, as deploy
sudo -u deploy bash -c '
  set -euo pipefail
  command -v ~/.local/bin/uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  if [ ! -d '"$APP"'/app/.git ]; then git clone '"$REPO_URL"' '"$APP"'/app; fi
  cd '"$APP"'/app && git fetch origin && git reset --hard origin/main && uv sync --frozen --no-dev
'

# Environment file (created once; edit it to add the Space keys). Never overwritten.
if [ ! -f "$APP/.env" ]; then
  cat > "$APP/.env" <<ENV
APP_ENV=production
SECRET_KEY=$(openssl rand -hex 32)
DATABASE=$APP/data/app.db
SPACES_KEY=
SPACES_SECRET=
SPACES_BUCKET=
SPACES_REGION=
SPACES_PUBLIC_BASE_URL=
SPACES_PREFIX=
ENV
  chown root:root "$APP/.env"; chmod 600 "$APP/.env"
  echo ">>> Fill in the SPACES_* values in $APP/.env, then: systemctl restart uctballroom"
fi

# Service + Caddy
install -m 644 "$APP/app/deploy/uctballroom.service" /etc/systemd/system/uctballroom.service
sed "s/__DOMAIN__/$DOMAIN/g" "$APP/app/deploy/Caddyfile" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable uctballroom
systemctl restart uctballroom
systemctl reload caddy || systemctl restart caddy

# Firewall
ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo "Provisioned. Site: https://$DOMAIN"
