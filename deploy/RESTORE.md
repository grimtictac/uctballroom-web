# Restoring the database from backup

The live SQLite database (`/srv/uctballroom/data/app.db`) is replicated continuously by
Litestream to a private Space bucket. Run these as root on the server.

**Check backups are restorable** (safe; does not touch the live DB):

    /srv/uctballroom/app/deploy/verify-backup.sh

**Replace a lost or corrupt database with the latest backup:**

    systemctl stop uctballroom litestream
    mv /srv/uctballroom/data/app.db /srv/uctballroom/data/app.db.broken   # keep it, just in case
    rm -f /srv/uctballroom/data/app.db-wal /srv/uctballroom/data/app.db-shm
    set -a; . /etc/litestream.env; set +a
    litestream restore -config /etc/litestream.yml -o /srv/uctballroom/data/app.db /srv/uctballroom/data/app.db
    chown uctballroom:uctballroom /srv/uctballroom/data/app.db
    systemctl start uctballroom litestream

**Restore to a point in time** (e.g. before a bad change): add `-timestamp 2027-03-01T10:00:00Z`
to the `litestream restore` command above.

Backups: continuous replication, a full snapshot every 24 h, snapshots kept for 7 days.
Logs: `journalctl -u litestream`.
