"""Создать небольшой коммит с записью в changelog: python scripts/commit.py 'Описание'."""
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
git = ['git', '-c', 'safe.directory=' + root.as_posix()]


def run(*args):
    return subprocess.check_output([*git, *args], cwd=root).decode('utf-8').strip()


message = ' '.join(sys.argv[1:]).strip()
if not message or not re.search('[А-Яа-я]', message):
    raise SystemExit('Укажите описание коммита на русском')
names = [name for name in run('diff', '--cached', '--name-only', '-z').split('\0') if name]
if not any(names):
    raise SystemExit('Сначала добавьте изменения в индекс')
for name in names:
    if (name.endswith('.example')):
        continue
    if re.search(r'(^|/)(artifacts|data|secrets|credentials)(/|\.)|(^|/)\.env($|\.)|postgres.*\.conf$|\.(ppk|pem|key|pfx|p12)$', name, re.I):
        raise SystemExit('В индексе файл с потенциальными секретами: ' + name)
stamp = datetime.now(timezone(timedelta(hours=5))).strftime('%Y-%m-%d %H:%M:%S +05:00')
path = root / 'CHANGELOG.md'
old = path.read_text(encoding='utf-8') if path.exists() else '# Журнал изменений\n\nКаждая запись соответствует одному коммиту. Время — Asia/Qyzylorda (UTC+05:00).\n'
entry = f'\n## {stamp} — {message}\n\n'
entry += 'Изменены: ' + ', '.join(f'`{n}`' for n in names if n != 'CHANGELOG.md') + '.\n'
path.write_text(old + entry, encoding='utf-8', newline='\n')
subprocess.run([*git, 'add', '--', 'CHANGELOG.md'], cwd=root, check=True)
subprocess.run([*git, 'commit', '-m', f'{stamp} — {message}'], cwd=root, check=True)
