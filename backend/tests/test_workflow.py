from audio_fixture import audio_bytes
from pathlib import Path
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from app.main import app, rate_buckets
from app.db import SessionLocal, Encounter, Job, Patient
from app.config import settings
from app.schemas import Consultation
from app.privacy import redact
from app.worker import process_one
from app.clinical import AI_CONCLUSION_NOTICE


def patient(client, consent=True):
    r = client.post('/api/v1/patients', json={'name': 'Тестова Алия Макетовна', 'iin': '000000000000', 'birth_date': '1990-01-01', 'recording_consent': consent})
    assert r.status_code == 201, r.text
    return r.json()


def encounter(client, p):
    draft = client.post(f'/api/v1/patients/{p["id"]}/encounters').json()
    result = client.put(f'/api/v1/encounters/{draft["id"]}', json={
        'draft_token': draft['draft_token'], 'fields': {'complaints': 'Синтетическая запись для проверки'}})
    assert result.status_code == 200, result.text
    return result.json()


def test_manual_consultation_approval_and_integration(client, doctor):
    p = patient(client, False)
    e = encounter(client, p)
    key = client.post('/api/v1/integration-key').json()['api_key']
    headers = {'Authorization': 'Bearer ' + key}
    assert client.get('/api/v1/integration/encounters', headers=headers).json()['items'] == []
    fields = Consultation(complaints='Тестовая жалоба', anamnesis='Со слов пациента', ai_conclusion='Недостаточно данных для выводов.').model_dump()
    e = client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': e['version'], 'fields': fields}).json()
    e = client.post(f'/api/v1/encounters/{e["id"]}/approve', json={'version': e['version']}).json()
    assert e['status'] == 'approved'
    result = client.get('/api/v1/integration/encounters', headers=headers).json()
    assert len(result['items']) == 1
    assert result['items'][0]['encounter']['fields']['complaints'] == 'Тестовая жалоба'
    exported = result['items'][0]['encounter']
    assert 'ai_conclusion' not in exported['fields']
    assert 'ai_notice' not in exported
    detail = client.get(f'/api/v1/integration/encounters/{e["id"]}', headers=headers).json()
    assert 'ai_notice' not in detail['encounter']
    assert client.get(f'/api/v1/integration/encounters/{e["id"]}').status_code == 401
    client.delete('/api/v1/integration-key')
    assert client.get('/api/v1/integration/encounters', headers=headers).status_code == 401


def test_recording_consent_enforced_on_server(client, doctor):
    e = encounter(client, patient(client, False))
    r = client.post(f'/api/v1/encounters/{e["id"]}/audio', files={'file': ('a.wav', audio_bytes(), 'audio/wav')})
    assert r.status_code == 403


def test_shared_patient_is_accessible_but_drafts_are_private(client, doctor):
    p = patient(client)
    e = encounter(client, p)
    other = TestClient(app, headers={'X-Medhub-Request': '1'})
    other.post('/api/v1/auth/register', json={'name': 'Другой Врач', 'email': 'other@example.test', 'iin': '000000000002', 'password': 'Very-safe-test-123'})
    assert other.get(f'/api/v1/patients/{p["id"]}').status_code == 200
    assert other.get(f'/api/v1/encounters/{e["id"]}').status_code == 404
    assert len(other.get('/api/v1/patients?q=Алия').json()) == 1
    assert other.get('/api/v1/patients').json() == []


def test_phi_encrypted_and_search_works(client, doctor):
    patient(client)
    assert len(client.get('/api/v1/patients?q=Алия').json()) == 1
    assert len(client.get('/api/v1/patients?q=000000000000').json()) == 1
    with SessionLocal() as db:
        raw = db.execute(text('SELECT data FROM patients')).scalar()
        assert 'Алия' not in raw and '000000000000' not in raw


def test_duplicate_patient_and_version_conflict(client, doctor):
    p = patient(client)
    r = client.post('/api/v1/patients', json={'name': 'Дубликат', 'iin': '000000000000', 'birth_date': '1990-01-01'})
    assert r.status_code == 409
    e = encounter(client, p)
    patch = {'version': 1, 'fields': Consultation(complaints='Тест').model_dump()}
    assert client.patch(f'/api/v1/encounters/{e["id"]}', json=patch).status_code == 200
    assert client.patch(f'/api/v1/encounters/{e["id"]}', json=patch).status_code == 409


def test_redaction_covers_identifiers():
    result = redact('Меня зовут Алия Тестова. ИИН 900101123456, телефон +7 777 123 45 67. test@example.test. Адрес: Тестовая 10.', {'name': 'Алия Тестова', 'iin': '900101123456'})
    for value in ['Алия', 'Тестова', '900101123456', '777', 'test@example.test', 'Тестовая']:
        assert value not in result


def test_cloud_requires_consent_and_review(client, doctor):
    e = encounter(client, patient(client))
    e = client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': 1, 'fields': {}, 'transcript': [{'speaker': 'SPEAKER_00', 'start': 0, 'end': 2, 'text': 'Тестовый диалог'}]}).json()
    settings().llm_is_cloud = True
    assert client.post(f'/api/v1/encounters/{e["id"]}/generate', json={'version': e['version']}).status_code == 403


def test_worker_transcription_and_generation(client, doctor, monkeypatch):
    p = patient(client)
    e = encounter(client, p)
    settings().asr_provider = 'self_hosted'
    monkeypatch.setattr('app.worker.transcribe', lambda path: [{'speaker': 'SPEAKER_00', 'start': 0, 'end': 4, 'text': 'Меня зовут Алия. Тестовая жалоба.'}])
    monkeypatch.setattr('app.worker.mask_audio', lambda path, segments, indices: b'masked-audio-test')
    result = client.post(f'/api/v1/encounters/{e["id"]}/audio', files={'file': ('a.wav', audio_bytes(), 'audio/wav')})
    assert result.status_code == 202, result.text
    assert process_one()
    e = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert e['status'] == 'ready'
    assert 'Алия' not in e['redacted_transcript'][0]['text']
    assert client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()[0]['available'] is True
    e = client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': e['version'], 'fields': Consultation(diagnosis='Запись врача').model_dump()}).json()
    monkeypatch.setattr('app.worker.generate', lambda segments, *args: {'fields': Consultation(complaints='Тестовая жалоба', diagnosis='Ответ модели', ai_test_recommendations='Описаны жалобы; требуются уточнения.').model_dump(), 'speaker_roles': {'SPEAKER_00': 'patient'}})
    assert client.post(f'/api/v1/encounters/{e["id"]}/generate', json={'version': e['version']}).status_code == 202
    assert process_one()
    e = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert e['fields']['complaints'] == 'Тестовая жалоба' and e['speaker_roles']['SPEAKER_00'] == 'patient'
    assert e['fields']['diagnosis'] == 'Запись врача'
    assert e['fields']['ai_test_recommendations'] == 'Описаны жалобы; требуются уточнения.'
    assert e['ai_notice']['disclaimer'] == AI_CONCLUSION_NOTICE
    assert e['reviewed_at'] is None and e['status'] == 'ready'


def test_consent_revoked_while_job_queued(client, doctor, monkeypatch):
    p = patient(client)
    e = encounter(client, p)
    settings().asr_provider = 'self_hosted'
    result = client.post(f'/api/v1/encounters/{e["id"]}/audio', files={'file': ('a.wav', audio_bytes(), 'audio/wav')}).json()
    client.patch(f'/api/v1/patients/{p["id"]}/consent', json={'recording_consent': False, 'cloud_consent': False})
    monkeypatch.setattr('app.worker.transcribe', lambda path: (_ for _ in ()).throw(AssertionError('Must not call ASR')))
    process_one()
    assert client.get(f'/api/v1/jobs/{result["job_id"]}').json()['state'] == 'failed'
    assert client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()[0]['available'] is True


def test_csrf_and_validation_do_not_leak_password(client):
    external = TestClient(app)
    assert external.post('/api/v1/auth/login', json={'email': 'a', 'password': 'b'}).status_code == 403
    r = client.post('/api/v1/auth/register', json={'name': 'User', 'email': 'a@b.test', 'password': 'secret'})
    assert r.status_code == 422
    assert 'secret' not in r.text


def test_logout_revokes_session(client, doctor):
    assert client.get('/api/v1/auth/me').status_code == 200
    client.post('/api/v1/auth/logout')
    assert client.get('/api/v1/auth/me').status_code == 401


def test_cloud_asr_requires_separate_audio_consent(client, doctor):
    p = patient(client)
    e = encounter(client, p)
    client.patch(f'/api/v1/patients/{p["id"]}/consent', json={'recording_consent': True, 'cloud_consent': True})
    with SessionLocal() as db:
        row = db.get(Encounter, e['id'])
        row.privacy_reviewed = True
        db.commit()
    r = client.post(f'/api/v1/encounters/{e["id"]}/cloud-asr', json={'version': 1, 'audio_reviewed': True})
    assert r.status_code == 403


def test_export_only_after_review_and_retry_keeps_idempotency(client, doctor, monkeypatch):
    import httpx
    settings().mis_url = 'https://mis.example.test/v1/consultations'
    settings().mis_token = 'test-token'
    e = encounter(client, patient(client, False))
    assert client.post(f'/api/v1/encounters/{e["id"]}/send-to-mis', json={'version': 1}).status_code == 409
    e = client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': 1, 'fields': Consultation(complaints='Синтетический тест').model_dump()}).json()
    e = client.post(f'/api/v1/encounters/{e["id"]}/approve', json={'version': e['version']}).json()
    deliveries = []
    original_post = httpx.Client.post
    def post(self, url, **kwargs):
        if not str(url).startswith('https://mis.example.test/'):
            return original_post(self, url, **kwargs)
        assert url == settings().mis_url
        deliveries.append(kwargs)
        return httpx.Response(201, request=httpx.Request('POST', url), json={'accepted': True})
    monkeypatch.setattr(httpx.Client, 'post', post)
    clock = [e['started_at'] + 60]
    monkeypatch.setattr('app.main.now', lambda: clock[0])
    monkeypatch.setattr('app.worker.now', lambda: clock[0])
    # Подменяем только приёмник вымышленной МИС.
    for _ in range(2):
        assert client.post(f'/api/v1/encounters/{e["id"]}/send-to-mis', json={'version': e['version']}).status_code == 202
        assert process_one()
        e = client.get(f'/api/v1/encounters/{e["id"]}').json()
        assert e['status'] == 'exported'
        clock[0] += 60
    assert deliveries[0]['headers']['Idempotency-Key'] == deliveries[1]['headers']['Idempotency-Key']
    assert deliveries[0]['json'] == deliveries[1]['json']
    assert 'ai_notice' not in deliveries[0]['json']['encounter']
    assert e['sent_at'] == e['started_at'] + 60


def test_ai_notice_cannot_be_overridden_and_old_encounters_work(client, doctor):
    e = encounter(client, patient(client))
    with SessionLocal() as db:
        row = db.get(Encounter, e['id'])
        row.fields = {'anamnesis': 'Запись до добавления заключений ИИ'}
        db.commit()
    legacy = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert legacy['fields']['ai_conclusion'] == ''
    assert legacy['ai_notice']['disclaimer'] == AI_CONCLUSION_NOTICE
    r = client.patch(f'/api/v1/encounters/{e["id"]}', json={
        'version': e['version'], 'fields': legacy['fields'], 'ai_notice': {'is_diagnosis': True},
    })
    assert r.status_code == 422
