"""OpenAI ASR: исходное аудио допускается только с отдельным согласием в API/worker."""
import math
import subprocess
import tempfile
from pathlib import Path

import httpx

from .config import settings
from .schemas import Segment


class ProviderError(Exception):
    pass


OPENAI_ASR_MODEL = 'gpt-4o-transcribe-diarize'


def transcribe_openai(path):
    s = settings()
    if not s.openai_api_key.strip():
        raise ProviderError('Добавьте OPENAI_API_KEY в настройки сервера и перезапустите API и worker.')
    # Браузерные контейнеры и WAV приводим к поддерживаемому компактному формату.
    # 60 минут mono MP3 48 kbps помещаются в лимит API 25 MB; запись не обрезаем.
    with tempfile.TemporaryDirectory(prefix='medhub-openai-') as directory:
        compressed = Path(directory) / 'recording.mp3'
        try:
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(path),
                            '-vn', '-ac', '1', '-ar', '24000', '-codec:a', 'libmp3lame',
                            '-b:a', '48k', str(compressed)], check=True, timeout=180,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise ProviderError('Не удалось подготовить аудио. Проверьте запись и наличие ffmpeg на сервере.') from None
        if not compressed.exists() or compressed.stat().st_size < 32:
            raise ProviderError('В записи не найден звук')
        if compressed.stat().st_size > 24_000_000:
            raise ProviderError('Запись превышает лимит OpenAI после сжатия. Разделите её на более короткие приёмы.')
        with compressed.open('rb') as audio, httpx.Client(timeout=s.asr_timeout_seconds, follow_redirects=False) as client:
            try:
                response = client.post('https://api.openai.com/v1/audio/transcriptions',
                    headers={'Authorization': 'Bearer ' + s.openai_api_key},
                    files={'file': ('recording.mp3', audio, 'audio/mpeg')},
                    data={'model': OPENAI_ASR_MODEL, 'response_format': 'diarized_json', 'chunking_strategy': 'auto'})
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                code = error.response.status_code
                if code == 401:
                    message = 'OpenAI отклонил API-ключ. Проверьте OPENAI_API_KEY на сервере.'
                elif code == 429:
                    message = 'OpenAI: превышен лимит запросов или квота API. Проверьте баланс и повторите позже.'
                elif code == 403:
                    message = 'OpenAI запретил доступ. Проверьте разрешения проекта и доступность API.'
                else:
                    message = 'OpenAI не обработал запись. Повторите позже или проверьте настройки проекта API.'
                raise ProviderError(message) from None
            except httpx.HTTPError:
                raise ProviderError('Не удалось связаться с OpenAI. Запись можно отправить повторно.') from None
    return parse_diarized(response.json())


def parse_diarized(data):
    segments = data.get('segments')
    if not isinstance(segments, list) or not segments or len(segments) > 2000:
        raise ProviderError('OpenAI не вернул пригодную расшифровку с говорящими и таймкодами.')
    speakers, result = {}, []
    for raw in segments:
        label = raw.get('speaker')
        if not isinstance(label, str) or not label.strip():
            raise ProviderError('OpenAI не определил говорящего. Повторите распознавание.')
        if label not in speakers:
            if len(speakers) >= 100:
                raise ProviderError('Слишком много говорящих в ответе OpenAI')
            speakers[label] = f'SPEAKER_{len(speakers):02d}'
        try:
            segment = Segment(speaker=speakers[label], start=raw['start'], end=raw['end'], text=raw['text'])
        except (ValueError, KeyError, TypeError):
            raise ProviderError('OpenAI вернул некорректный фрагмент или таймкоды') from None
        if not math.isfinite(segment.start) or not math.isfinite(segment.end) or segment.end < segment.start:
            raise ProviderError('OpenAI вернул некорректные таймкоды')
        result.append(segment.model_dump())
    return result
