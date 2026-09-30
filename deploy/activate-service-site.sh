#!/bin/sh
set -eu
test -s /opt/medhub/current/ui/index.html
curl --fail --silent --show-error http://127.0.0.1:8010/api/health
target=/etc/nginx/sites-available/medhub.ych.kz
backup="$target.before-service-20260930-1"
if [ ! -f "$backup" ]; then cp -p "$target" "$backup"; fi
install -m 644 /opt/medhub/current/deploy/nginx/medhub.https.conf "$target"
if ! nginx -t; then
    cp -p "$backup" "$target"
    exit 1
fi
systemctl reload nginx
curl --fail --silent --show-error https://medhub.ych.kz/api/health
systemctl is-active medhub-api medhub-worker nginx
