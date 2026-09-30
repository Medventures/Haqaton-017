#!/bin/sh
set -eu
release_id=${1:?Pass release ID}
case "$release_id" in *[!0-9-]*|'') exit 1;; esac
release="/opt/medhub/releases/$release_id"
test -f "$release/backend/requirements.lock"
test -f "$release/ui/index.html"
previous=$(readlink -f /opt/medhub/current)
case "$previous" in /opt/medhub/releases/*) ;; *) exit 1;; esac
# SDK не хранится в Git. Следующие релизы наследуют только библиотеки проверяющего модуля.
if [ ! -f "$release/backend/verifier/lib/knca_provider_jce_kalkan-0.7.5.jar" ]; then
    cp -a "$previous/backend/verifier/lib" "$release/backend/verifier/lib"
fi
if ! command -v javac >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends openjdk-17-jdk-headless
fi
mkdir -p "$release/backend/verifier/classes"
javac --release 17 -encoding UTF-8 -cp "$release/backend/verifier/lib/*" -d "$release/backend/verifier/classes" "$release/backend/verifier/Verifier.java"
java -cp "$release/backend/verifier/classes:$release/backend/verifier/lib/*" Verifier --self-test "$release/backend/verifier/trust"
python3 -m venv "$release/.venv"
"$release/.venv/bin/pip" install -q --disable-pip-version-check -r "$release/backend/requirements.txt" -c "$release/backend/requirements.lock"
set -a
. /opt/medhub/shared/app.env
set +a
cd "$release/backend"
runuser -u medhub -- "$release/.venv/bin/python" - <<'PY'
from app.main import app
from app.db import engine
assert engine.url.database == 'medhub' and engine.url.username.startswith('medhub_app.')
with engine.connect() as c:
    assert c.exec_driver_sql('SELECT current_database()').scalar() == 'medhub'
print('New release imports and database target verified')
PY
rollback() {
    ln -sfn "$previous" /opt/medhub/current
    systemctl restart medhub-api medhub-worker
}
systemctl stop medhub-api medhub-worker
trap rollback EXIT
"$release/.venv/bin/python" "$release/deploy/backup-database.py"
runuser -u medhub -- "$release/.venv/bin/alembic" upgrade head
ln -sfn "$release" /opt/medhub/current
systemctl restart medhub-api medhub-worker
attempt=0
until curl --fail --silent --output /dev/null http://127.0.0.1:8010/api/health; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 15 ] || exit 1
    sleep 2
done
systemctl is-active medhub-api medhub-worker
trap - EXIT
printf 'Active release: %s\n' "$release_id"
