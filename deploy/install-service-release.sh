#!/bin/sh
set -eu
release=/opt/medhub/releases/20260930-1
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends python3-venv ffmpeg libgomp1
if ! id medhub >/dev/null 2>&1; then useradd --system --home-dir /opt/medhub/shared --shell /usr/sbin/nologin medhub; fi
chown root:medhub /opt/medhub/shared
chmod 750 /opt/medhub/shared
chmod 600 /opt/medhub/shared/app.env
install -d -m 700 -o medhub -g medhub /opt/medhub/shared/audio
python3 -m venv "$release/.venv"
"$release/.venv/bin/pip" install -q --disable-pip-version-check -r "$release/backend/requirements.txt" -c "$release/backend/requirements.lock"
# Миграции запускаются только в отдельной БД medhub.
python3 - <<'PY'
from pathlib import Path
p = Path('/opt/medhub/shared/app.env')
p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))
PY
set -a
. /opt/medhub/shared/app.env
set +a
"$release/.venv/bin/python" - <<'PY'
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
url = make_url(os.environ['DATABASE_URL'])
assert url.database == 'medhub' and url.username.startswith('medhub_app.'), 'Unexpected database target'
with create_engine(url).connect() as c:
    assert c.exec_driver_sql('SELECT current_database()').scalar() == 'medhub'
print('Verified migration target: medhub')
PY
cd "$release/backend"
runuser -u medhub -- "$release/.venv/bin/alembic" upgrade head
ln -sfn "$release" /opt/medhub/current
install -m 644 "$release/deploy/systemd/medhub-api.service" /etc/systemd/system/medhub-api.service
install -m 644 "$release/deploy/systemd/medhub-worker.service" /etc/systemd/system/medhub-worker.service
systemctl daemon-reload
systemctl enable --now medhub-api medhub-worker
systemctl is-active medhub-api medhub-worker
