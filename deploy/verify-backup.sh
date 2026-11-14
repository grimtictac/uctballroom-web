#!/usr/bin/env bash
# Restore the latest backup into a temp file and sanity-check it. Run as root on the server.
# Does not touch the live database.
set -euo pipefail
set -a; . /etc/litestream.env; set +a
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

litestream restore -config /etc/litestream.yml -o "$tmp/restored.db" /srv/uctballroom/data/app.db
echo "integrity_check: $(sqlite3 "$tmp/restored.db" 'PRAGMA integrity_check;')"
for db in "$tmp/restored.db" /srv/uctballroom/data/app.db; do
  label=$([ "$db" = "$tmp/restored.db" ] && echo restored || echo live)
  printf '%-9s schema v%s | albums=%s photos=%s users=%s\n' "$label" \
    "$(sqlite3 "$db" 'PRAGMA user_version;')" \
    "$(sqlite3 "$db" 'SELECT COUNT(*) FROM albums;')" \
    "$(sqlite3 "$db" 'SELECT COUNT(*) FROM photos;')" \
    "$(sqlite3 "$db" 'SELECT COUNT(*) FROM users;')"
done
