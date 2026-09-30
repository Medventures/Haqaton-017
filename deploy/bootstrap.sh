#!/bin/sh
set -eu
# Запуск из /opt/medhub/deploy. Другие virtual hosts не изменяются.
install -d -m 755 /opt/medhub/ui /opt/medhub/backend /var/www/letsencrypt
if [ ! -f /opt/medhub/ui/index.html ]; then install -m 644 placeholder.html /opt/medhub/ui/index.html; fi
target=/etc/nginx/sites-available/medhub.ych.kz
if [ -f "$target" ]; then cp -p "$target" "$target.backup.$(date +%Y%m%d%H%M%S)"; fi
install -m 644 nginx/medhub.http.conf "$target"
ln -sfn "$target" /etc/nginx/sites-enabled/medhub.ych.kz
nginx -t
systemctl reload nginx
certbot certonly --webroot -w /var/www/letsencrypt -d medhub.ych.kz --non-interactive --agree-tos --register-unsafely-without-email
install -m 644 nginx/medhub.https.conf "$target"
nginx -t
systemctl reload nginx
install -d /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nnginx -t && systemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/medhub-nginx.sh
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/medhub-nginx.sh
curl --fail --silent --show-error https://medhub.ych.kz/nginx-health
