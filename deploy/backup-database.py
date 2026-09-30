"""Резервная копия отдельной medhub перед миграцией; запускается root на сервере."""
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.db import engine

url = engine.url
assert url.database == 'medhub' and url.username.startswith('medhub_app.'), 'Unexpected database target'
with engine.connect() as connection:
    assert connection.exec_driver_sql('SELECT current_database()').scalar() == 'medhub'

os.umask(0o077)
folder = Path('/opt/medhub/shared/backups')
folder.mkdir(mode=0o700, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
output = folder / f'medhub-before-migration-{stamp}.dump'
env = {key: value for key, value in os.environ.items() if key in ('PATH', 'LANG', 'LC_ALL')}
env.update(PGPASSWORD=url.password, PGSSLMODE=url.query.get('sslmode', 'require'), PGCONNECT_TIMEOUT='20')
with output.open('xb') as stream:
    result = subprocess.run(['pg_dump', '--format=custom', '--no-owner', '--no-acl', '--host', url.host,
                             '--port', str(url.port or 5432), '--username', url.username, '--dbname', 'medhub'],
                            env=env, stdout=stream, stderr=subprocess.PIPE, timeout=300)
if result.returncode:
    output.with_suffix('.error.log').write_bytes(result.stderr)
    raise SystemExit('Database backup failed; protected error log saved next to the dump. Migration cancelled.')
assert output.stat().st_size > 0
print(f'Database backup saved: {output.name}')
