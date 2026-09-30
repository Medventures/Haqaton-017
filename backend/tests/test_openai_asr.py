from audio_fixture import audio_bytes
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.openai_asr import OPENAI_ASR_MODEL, ProviderError, parse_diarized, transcribe_openai
from app.worker import process_one
from test_workflow import patient, encounter


def configure(monkeypatch):
    monkeypatch.setattr(settings(), 'asr_provider', 'openai')
    monkeypatch.setattr(settings(), 'openai_api_key', 'test-key-not-a-real-secret')


def consent(client, p, allowed):
    return client.patch(f'/api/v1/patients/{p["id"]}/consent', json={
        'recording_consent': True, 'cloud_consent': True,
        'cloud_audio_consent': True, 'openai_audio_consent': allowed})


def upload(client, e):
    return client.post(f'/api/v1/encounters/{e["id"]}/audio',
        files={'file': ('a.wav', audio_bytes(), 'audio/wav')})


def test_direct_openai_requires_distinct_consent_and_key(client, doctor, monkeypatch):
    configure(monkeypatch)
    p = patient(client)
    e = encounter(client, p)
    consent(client, p, False)
    assert upload(client, e).status_code == 403
    consent(client, p, True)
    monkeypatch.setattr(settings(), 'openai_api_key', '')
    assert upload(client, e).status_code == 503
    config = client.get('/api/v1/settings').json()
    assert config['asr_model'] == OPENAI_ASR_MODEL
    assert config['asr_configured'] is False
    assert 'openai_api_key' not in config
    monkeypatch.setattr(settings(), 'openai_api_key', 'test-key-not-a-real-secret')
    config_response = client.get('/api/v1/settings')
    assert config_response.json()['asr_configured'] is True
    assert settings().openai_api_key not in config_response.text


@pytest.mark.parametrize('revoke_at', ['queued', 'processing', 'never'])
def test_openai_worker_consent_and_redaction(client, doctor, monkeypatch, revoke_at):
    configure(monkeypatch)
    p = patient(client)
    e = encounter(client, p)
    consent(client, p, True)
    job = upload(client, e).json()
    calls = []

    def recognize(path):
        calls.append(path)
        if revoke_at == 'processing':
            consent(client, p, False)
        return [{'speaker': 'SPEAKER_00', 'start': 0, 'end': 1, 'text': 'Алия. Тестовая жалоба.'}]

    monkeypatch.setattr('app.worker.transcribe', recognize)
    monkeypatch.setattr('app.worker.mask_audio', lambda *args: b'masked-test')
    if revoke_at == 'queued':
        consent(client, p, False)
    assert process_one()
    result = client.get(f'/api/v1/jobs/{job["job_id"]}').json()
    assert len(calls) == (0 if revoke_at == 'queued' else 1)
    assert result['state'] == ('done' if revoke_at == 'never' else 'failed')
    saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    if revoke_at == 'never':
        assert 'Алия' not in saved['redacted_transcript'][0]['text']
        assert saved['speaker_roles'] == {'SPEAKER_00': 'unknown'}
    else:
        assert saved['transcript'] == []
    assert client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()[0]['available'] is True


def test_queued_local_job_never_switches_to_cloud(client, doctor, monkeypatch):
    p = patient(client)
    e = encounter(client, p)
    consent(client, p, True)
    monkeypatch.setattr(settings(), 'asr_provider', 'self_hosted')
    job = upload(client, e).json()
    configure(monkeypatch)
    monkeypatch.setattr('app.worker.transcribe', lambda path: pytest.fail('Unexpected cloud upload'))
    assert process_one()
    assert client.get(f'/api/v1/jobs/{job["job_id"]}').json()['state'] == 'failed'


def test_openai_request_uses_diarization_contract_and_no_patient_metadata(monkeypatch, tmp_path):
    configure(monkeypatch)
    paths = []

    def compress(command, **kwargs):
        paths.append(Path(command[-1]))
        paths[-1].write_bytes(b'mp3' * 40)

    def post(self, url, **kwargs):
        assert url == 'https://api.openai.com/v1/audio/transcriptions'
        assert kwargs['data'] == {'model': OPENAI_ASR_MODEL, 'response_format': 'diarized_json', 'chunking_strategy': 'auto'}
        assert kwargs['headers']['Authorization'] == 'Bearer ' + settings().openai_api_key
        assert kwargs['files']['file'][0] == 'recording.mp3'
        assert kwargs['files']['file'][1].read() == b'mp3' * 40
        return httpx.Response(200, request=httpx.Request('POST', url), json={'segments': [
            {'speaker': 'A', 'start': 0, 'end': 1.2, 'text': 'Тестовый вопрос'},
            {'speaker': 'B', 'start': 1.2, 'end': 3, 'text': 'Тестовый ответ'},
            {'speaker': 'A', 'start': 3, 'end': 4, 'text': 'Уточнение'},
            {'speaker': 'C', 'start': 4, 'end': 5, 'text': 'Третий голос'}]})

    monkeypatch.setattr('app.openai_asr.subprocess.run', compress)
    monkeypatch.setattr(httpx.Client, 'post', post)
    result = transcribe_openai(tmp_path / 'source.audio')
    assert [s['speaker'] for s in result] == ['SPEAKER_00', 'SPEAKER_01', 'SPEAKER_00', 'SPEAKER_02']
    assert result[1]['start'] == 1.2
    assert all(not p.exists() for p in paths)


@pytest.mark.parametrize('code', [401, 403, 429, 500])
def test_openai_errors_do_not_expose_provider_body_or_key(monkeypatch, tmp_path, code):
    configure(monkeypatch)
    monkeypatch.setattr('app.openai_asr.subprocess.run', lambda command, **kw: Path(command[-1]).write_bytes(b'a' * 100))
    monkeypatch.setattr(httpx.Client, 'post', lambda self, url, **kw:
        httpx.Response(code, request=httpx.Request('POST', url), json={'error': 'PRIVATE_DATA ' + settings().openai_api_key}))
    with pytest.raises(ProviderError) as error:
        transcribe_openai(tmp_path / 'source.audio')
    assert 'PRIVATE_DATA' not in str(error.value)
    assert settings().openai_api_key not in str(error.value)


def test_missing_key_and_oversized_audio_do_not_call_api(monkeypatch, tmp_path):
    configure(monkeypatch)
    monkeypatch.setattr(httpx.Client, 'post', lambda *a, **kw: pytest.fail('Unexpected API call'))
    monkeypatch.setattr(settings(), 'openai_api_key', '')
    with pytest.raises(ProviderError, match='OPENAI_API_KEY'):
        transcribe_openai(tmp_path / 'source.audio')
    configure(monkeypatch)

    def compress(command, **kw):
        with Path(command[-1]).open('wb') as f:
            f.truncate(24_000_001)

    monkeypatch.setattr('app.openai_asr.subprocess.run', compress)
    with pytest.raises(ProviderError, match='лимит'):
        transcribe_openai(tmp_path / 'source.audio')


@pytest.mark.parametrize('segments', [[], [{'speaker': None, 'start': 0, 'end': 1, 'text': 'test'}],
    [{'speaker': 'A', 'start': 2, 'end': 1, 'text': 'test'}],
    [{'speaker': 'A', 'start': 0, 'end': float('inf'), 'text': 'test'}]])
def test_reject_unusable_diarization(segments):
    with pytest.raises(ProviderError):
        parse_diarized({'segments': segments})
