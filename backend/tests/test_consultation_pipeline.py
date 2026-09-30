from audio_fixture import audio_bytes
from pathlib import Path
import os
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from app.main import app
from app.config import settings
from app.db import SessionLocal, Job, Encounter
from app.schemas import Consultation
from app.worker import process_one, cleanup
from app.providers import ProviderError
from test_workflow import patient, encounter


def prepare(client, monkeypatch):
    p = patient(client)
    client.patch(f'/api/v1/patients/{p["id"]}/consent', json={
        'recording_consent': True, 'cloud_consent': True, 'openai_audio_consent': True})
    e = encounter(client, p)
    for key, value in {'asr_provider': 'openai', 'openai_api_key': 'synthetic-test', 'llm_provider': 'openai', 'llm_is_cloud': True}.items():
        monkeypatch.setattr(settings(), key, value)
    monkeypatch.setattr('app.worker.transcribe', lambda path: [{'speaker': 'SPEAKER_00', 'start': 0, 'end': 2, 'text': 'Алия. Болит горло.'}])
    monkeypatch.setattr('app.worker.mask_audio', lambda *args: b'masked')
    return p, e


def test_automatic_analysis_archive_approval_and_mis(client, doctor, monkeypatch):
    p, e = prepare(client, monkeypatch)
    def generate(segments, *args):
        assert 'Алия' not in segments[0]['text']
        return {'fields': Consultation(complaints='Боль в горле', ai_conclusion='Тестовый черновик',
            sources=[{'field': 'complaints', 'segments': [0]}]).model_dump(), 'speaker_roles': {'SPEAKER_00': 'patient'}}
    monkeypatch.setattr('app.worker.generate', generate)
    audio = audio_bytes()
    job = client.post(f'/api/v1/encounters/{e["id"]}/audio', data={'analyze': 'true'}, files={'file': ('test.wav', audio, 'audio/wav')}).json()
    assert process_one()
    assert client.get(f'/api/v1/jobs/{job["job_id"]}').json()['state'] == 'done'
    saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert saved['fields']['complaints'] == 'Синтетическая запись для проверки\n\nБоль в горле'
    assert saved['speaker_roles']['SPEAKER_00'] == 'patient'
    records = client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()
    assert records[0]['available'] and records[0]['transcript']
    audio_path = f'/encounters/{e["id"]}/recordings/{records[0]["id"]}/audio'
    assert client.get('/api/v1' + audio_path).content == audio
    with SessionLocal() as db:
        row = db.get(Job, job['job_id'])
        path = Path(settings().audio_dir) / row.payload['audio']
        assert audio not in path.read_bytes()
        assert Fernet(settings().encryption_key.encode()).decrypt(path.read_bytes()) == audio
        assert 'Боль в горле' not in db.execute(text('SELECT payload FROM jobs WHERE kind = :kind'), {'kind': 'revision'}).scalars().all()
    os.utime(path, (1, 1))
    cleanup()
    assert path.exists()
    key = client.post('/api/v1/integration-key').json()['api_key']
    headers = {'Authorization': 'Bearer ' + key}
    assert client.get('/api/v1/integration' + audio_path, headers=headers).status_code == 404
    approved = client.post(f'/api/v1/encounters/{e["id"]}/approve', json={'version': saved['version']})
    assert approved.status_code == 200
    assert client.get('/api/v1/integration' + audio_path, headers=headers).content == audio
    exported = client.get(f'/api/v1/integration/encounters/{e["id"]}', headers=headers).json()
    assert exported['encounter']['fields']['sources'][0]['segments'] == [0]
    history = client.get(f'/api/v1/encounters/{e["id"]}/history').json()
    assert any(x['action'] == 'approve' for x in history)
    outsider = TestClient(app, headers={'X-Medhub-Request': '1'})
    outsider.post('/api/v1/auth/register', json={'name':'Чужой врач','email':'outsider@test.test','iin':'000000009901','password':'synthetic-only-password'})
    assert outsider.get('/api/v1' + audio_path).status_code == 404
    assert outsider.get(f'/api/v1/encounters/{e["id"]}/history').status_code == 404


def test_llm_failure_preserves_recording_and_transcript(client, doctor, monkeypatch):
    p, e = prepare(client, monkeypatch)
    monkeypatch.setattr('app.worker.generate', lambda *a: (_ for _ in ()).throw(ProviderError('Недостаточно квоты')))
    result = client.post(f'/api/v1/encounters/{e["id"]}/audio', data={'analyze':'true'}, files={'file':('a.wav', audio_bytes(), 'audio/wav')})
    assert result.status_code == 202
    assert process_one()
    saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert saved['transcript'] and saved['status'] == 'ready'
    assert saved['last_job']['error'] == 'Недостаточно квоты'
    assert client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()[0]['available']


def test_empty_metadata_cannot_be_approved_and_invalid_diagnosis_rejected(client, doctor):
    e = encounter(client, patient(client, False))
    with SessionLocal() as db:
        db.get(Encounter, e['id']).fields = Consultation().model_dump()
        db.commit()
    assert client.post(f'/api/v1/encounters/{e["id"]}/approve', json={'version':e['version']}).status_code == 422
    assert client.patch(f'/api/v1/encounters/{e["id"]}', json={'version':e['version'], 'fields': {'diagnosis_code':'ZZ99'}}).status_code == 422
    assert client.get('/api/v1/diagnoses?q=I10').json()['items'][0]['code'] == 'I10'
