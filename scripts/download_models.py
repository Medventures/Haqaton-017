"""Скачать модели на GPU-ПК. Токен берётся из окружения или файла и не выводится."""
import argparse
import os
from pathlib import Path
from huggingface_hub import snapshot_download
from huggingface_hub.errors import GatedRepoError

parser = argparse.ArgumentParser()
parser.add_argument('--directory', required=True)
parser.add_argument('--token-file', type=Path, help='Local HF token file; its contents are never printed')
parser.add_argument('--only', choices=('all', 'whisper', 'diarization'), default='all')
args = parser.parse_args()
root = Path(args.directory)
root.mkdir(parents=True, exist_ok=True)
token = args.token_file.read_text(encoding='utf-8-sig').strip() if args.token_file else os.environ.get('HF_TOKEN')
if args.only in ('all', 'whisper'):
    snapshot_download('Systran/faster-whisper-large-v3', token=token, local_dir=root / 'faster-whisper-large-v3')
if args.only in ('all', 'diarization'):
    try:
        snapshot_download('pyannote/speaker-diarization-community-1', token=token, local_dir=root / 'pyannote-speaker-diarization-community-1')
    except GatedRepoError:
        parser.exit(1, 'Нет доступа к Community-1. Примите условия под аккаунтом токена: '
                    'https://huggingface.co/pyannote/speaker-diarization-community-1\n')
print('Модели сохранены. Проверьте offline-загрузку из каталога на GPU-ПК.')
