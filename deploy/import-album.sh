#!/usr/bin/env bash
# Run on YOUR machine: upload a folder of photos to the droplet and import it into the LIVE site.
#
#   deploy/import-album.sh ~/Photos/oweek-2027 --album o-week-2027 --title "O-Week 2027" \
#       --credit "Jane Doe" --event-date 2027-02-20
#
# Everything after the folder is passed to `flask import-album` (see app/importer.py). Re-running with the same
# folder and album is safe: photos already in the album (same file content) are skipped.
#
# Why this exists: the import writes to whichever database it runs against, so importing on your laptop only
# fills your local DB. This copies the originals over, runs the import on the server with the live settings,
# and removes the copy again. Originals end up public in the Space, so check sizes and content first.
#
# Settings (environment overrides):
#   UCT_HOST   default root@143.110.171.187
#   UCT_KEY    default ~/.ssh/id_digital_ocean_nov26
set -euo pipefail

HOST="${UCT_HOST:-root@143.110.171.187}"
KEY="${UCT_KEY:-$HOME/.ssh/id_digital_ocean_nov26}"

if [ $# -lt 3 ] || [ ! -d "$1" ]; then
  echo "usage: $0 <photo-folder> --album <slug> [--title T] [--credit C] [--event-date YYYY-MM-DD]" >&2
  exit 2
fi
folder="${1%/}"; shift
case " $* " in *" --album "*) ;; *) echo "--album <slug> is required" >&2; exit 2 ;; esac

count=$(find "$folder" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) | wc -l)
size=$(du -sh "$folder" | cut -f1)
echo "About to import $count image(s), $size, from: $folder"
echo "Into the LIVE site on $HOST with: $*"
read -r -p "Continue? [y/N] " ok
[ "$ok" = "y" ] || [ "$ok" = "Y" ] || { echo "Cancelled."; exit 1; }

ssh_opts=(-i "$KEY" -o IdentitiesOnly=yes)
ssh_run() { ssh "${ssh_opts[@]}" "$HOST" "$@"; }

remote_dir=$(ssh_run 'mkdir -p /srv/uctballroom/incoming && mktemp -d /srv/uctballroom/incoming/import.XXXXXX')
cleanup() { ssh_run "rm -rf $(printf '%q' "$remote_dir")" || echo "Could not remove $remote_dir; delete it by hand." >&2; }
trap cleanup EXIT

echo "Uploading..."
rsync -az --info=progress2 -e "ssh ${ssh_opts[*]}" "$folder"/ "$HOST:$remote_dir/"
ssh_run "chown -R uctballroom:uctballroom $(printf '%q' "$remote_dir")"

echo "Importing on the server..."
# systemd-run reads the same EnvironmentFile as the website service, so it uses the live DB and Space settings
# without the secrets ever passing through this machine or the shell.
args=$(printf '%q ' "$@")
ssh_run "systemd-run --quiet --wait --pipe --collect \
  -p User=uctballroom -p Group=uctballroom \
  -p EnvironmentFile=/srv/uctballroom/.env -p Environment=FLASK_SKIP_DOTENV=1 \
  -p WorkingDirectory=/srv/uctballroom/app \
  /srv/uctballroom/app/.venv/bin/flask --app app import-album $(printf '%q' "$remote_dir") $args"
echo "Done. Albums appear on the front page straight away."
