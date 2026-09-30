from fastapi.testclient import TestClient
from sqlalchemy import select, func
from app.main import app
from app.db import SessionLocal, Encounter, Patient, Job
from app.config import settings
from app.worker import process_one
from app.schemas import Consultation
from audio_fixture import audio_bytes
from test_workflow import patient, encounter
from app.lifecycle import audio_signature_valid


def start(client, p, **kwargs):
    response = client.post(f'/api/v1/patients/{p["id"]}/encounters', json=kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def materialize(client, e, fields=None, **extra):
    return client.put(f'/api/v1/encounters/{e["id"]}', json={
        'draft_token': e['draft_token'], 'fields': fields or {}, **extra})


def other_doctor():
    client = TestClient(app, headers={'X-Medhub-Request': '1'})
    response = client.post('/api/v1/auth/register', json={'name': 'Другой Синтетический Врач',
        'email': 'other@synthetic.test', 'iin': '000000000099', 'password': 'Synthetic-password-321'})
    assert response.status_code == 201
    return client


def upload(client, e, **data):
    if not e['persisted']:
        data['draft_token'] = e['draft_token']
    return client.post(f'/api/v1/encounters/{e["id"]}/audio', data=data,
                       files={'file': ('synthetic.wav', audio_bytes(), 'audio/wav')})


def test_empty_encounters_never_persist_and_finished_token_cannot_replay(client, doctor):
    p = patient(client)
    e = start(client, p)
    assert e['persisted'] is False and e['recording_deadline'] - e['started_at'] == 900
    assert materialize(client, e, {'visit_type': 'repeat', 'warnings': ['Уточнить жалобы']}).json()['persisted'] is False
    assert client.get(f'/api/v1/patients/{p["id"]}/encounters').json() == []
    assert client.get('/api/v1/encounters').json() == []
    ended = client.post(f'/api/v1/encounters/{e["id"]}/finish', json={'draft_token': e['draft_token']}).json()
    assert ended['discarded'] and ended['ended_at'] >= ended['started_at']
    assert materialize(client, e, {'complaints': 'Поздняя запись'}).status_code == 409
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Encounter)) == 0


def test_common_consent_does_not_elevate_legacy_permissions(client, doctor):
    p = patient(client, True)
    assert p['processing_consent'] is False
    e = encounter(client, p)
    other = other_doctor()
    duplicate = other.post('/api/v1/patients', json={'name': 'Дубликат', 'iin': p['iin'], 'birth_date': '1990-01-01'})
    assert duplicate.status_code == 409
    for query in ('', ' ', '!!!', 'А'):
        assert other.get('/api/v1/patients', params={'q': query}).json() == []
    assert other.get('/api/v1/patients', params={'q': p['iin']}).json()[0]['id'] == p['id']
    updated = other.patch(f'/api/v1/patients/{p["id"]}/consent', json={'processing_consent': True}).json()
    assert all(updated[key] for key in ('processing_consent', 'recording_consent', 'cloud_consent', 'cloud_audio_consent', 'openai_audio_consent'))
    assert client.get(f'/api/v1/encounters/{e["id"]}').json()['recording_allowed'] is True
    denied = other.patch(f'/api/v1/patients/{p["id"]}/consent', json={'processing_consent': False}).json()
    assert not any(denied[key] for key in ('processing_consent', 'recording_consent', 'cloud_consent', 'cloud_audio_consent', 'openai_audio_consent'))
    assert client.get(f'/api/v1/encounters/{e["id"]}').json()['recording_allowed'] is False


def test_draft_patient_binding_and_private_clinical_history(client, doctor):
    p = patient(client)
    draft = start(client, p)
    other = other_doctor()
    assert materialize(other, draft, {'complaints': 'Чужой токен'}).status_code == 404
    saved = materialize(client, draft, {'complaints': 'Тестовое описание'}, transcript=[{
        'speaker': 'SPEAKER_00', 'start': 0, 'end': 1, 'text': 'Синтетическая фраза'}]).json()
    assert other.get(f'/api/v1/patients/{p["id"]}/encounters').json() == []
    assert other.get(f'/api/v1/encounters/{saved["id"]}/pdf').status_code == 404
    approved = client.post(f'/api/v1/encounters/{saved["id"]}/approve', json={'version': saved['version']}).json()
    assert approved['ended_at'] and not approved['recording_allowed']
    shared = other.get(f'/api/v1/encounters/{saved["id"]}').json()
    assert shared['read_only'] and not shared['can_edit']
    assert shared['transcript'] == [] and shared['fields']['complaints'] == 'Тестовое описание'
    assert shared['doctor_name'] == doctor['name']
    assert other.get(f'/api/v1/encounters/{saved["id"]}/recordings').status_code == 404
    assert other.patch(f'/api/v1/encounters/{saved["id"]}', json={'version': approved['version'], 'fields': {}}).status_code == 404
    pdf = other.get(f'/api/v1/encounters/{saved["id"]}/pdf')
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
    assert pdf.headers['content-disposition'].startswith('inline;')
    assert pdf.headers['cache-control'] == 'no-store'


def test_pause_resume_rotates_draft_but_never_extends_recording_window(client, doctor, monkeypatch):
    p = patient(client, False)
    e = start(client, p)
    stamp = e['started_at']
    monkeypatch.setattr('app.main.now', lambda: stamp + 10)
    paused = client.post(f'/api/v1/encounters/{e["id"]}/pause', json={'draft_token': e['draft_token']}).json()
    assert paused['paused_at'] == stamp + 10 and not paused['recording_allowed']
    assert materialize(client, e, {'complaints': 'Устаревший токен'}).status_code == 409
    client.patch(f'/api/v1/patients/{p["id"]}/consent', json={'processing_consent': True})
    monkeypatch.setattr('app.main.now', lambda: stamp + 35)
    resumed = client.post(f'/api/v1/encounters/{e["id"]}/resume', json={'draft_token': paused['draft_token']}).json()
    assert resumed['paused_seconds'] == 25 and resumed['paused_at'] is None
    assert resumed['recording_deadline'] == e['recording_deadline'] and resumed['recording_allowed']
    saved = materialize(client, resumed, {'complaints': 'Синтетическая запись'}).json()
    assert saved['paused_seconds'] == 25 and saved['persisted']


def test_audio_only_materializes_after_validation_and_deadline_grace_is_one_use(client, doctor, monkeypatch):
    p = patient(client)
    e = start(client, p)
    monkeypatch.setattr(settings(), 'asr_provider', 'self_hosted')
    bad = client.post(f'/api/v1/encounters/{e["id"]}/audio', data={'draft_token': e['draft_token']},
        files={'file': ('fake.wav', b'not-a-wave' * 20, 'audio/wav')})
    assert bad.status_code == 422
    with SessionLocal() as db:
        assert db.get(Encounter, e['id']) is None
    lease = client.post(f'/api/v1/encounters/{e["id"]}/capture-lease', json={'draft_token': e['draft_token']}).json()
    monkeypatch.setattr('app.lifecycle.now', lambda: e['recording_deadline'] + 10)
    assert upload(client, e).status_code == 403
    assert upload(client, e, capture_token=lease['capture_token']).status_code == 202
    with SessionLocal() as db:
        db.get(Encounter, e['id']).status = 'ready'
        db.commit()
    saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert upload(client, saved, capture_token=lease['capture_token']).status_code == 409
    monkeypatch.setattr('app.lifecycle.now', lambda: e['recording_deadline'] + 31)
    assert upload(client, saved, capture_token=lease['capture_token']).status_code == 403


def test_finish_while_asr_queued_preserves_result_and_original_audio(client, doctor, monkeypatch):
    p = patient(client)
    e = start(client, p, visit_type='repeat')
    monkeypatch.setattr(settings(), 'asr_provider', 'self_hosted')
    monkeypatch.setattr('app.worker.transcribe', lambda path: [{'speaker': 'SPEAKER_00', 'start': 0, 'end': 1, 'text': 'Синтетическая жалоба'}])
    monkeypatch.setattr('app.worker.mask_audio', lambda *args: b'masked')
    monkeypatch.setattr('app.worker.generate', lambda *args: {'fields': Consultation(complaints='Синтетическая жалоба').model_dump(), 'speaker_roles': {'SPEAKER_00': 'patient'}})
    job = upload(client, e, analyze='true').json()
    finished = client.post(f'/api/v1/encounters/{e["id"]}/finish', json={'version': 1}).json()
    assert finished['ended_at'] and finished['version'] == 1 and finished['status'] == 'processing'
    assert upload(client, finished).status_code == 409
    assert process_one()
    saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert saved['ended_at'] == finished['ended_at'] and saved['fields']['visit_type'] == 'repeat'
    assert saved['transcript'] and saved['status'] == 'ready'
    recordings = client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()
    assert client.get(f'/api/v1/encounters/{e["id"]}/recordings/{recordings[0]["id"]}/audio').content == audio_bytes()
    edited = client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': saved['version'], 'fields': {'complaints': 'Проверенный текст'}}).json()
    assert edited['ended_at'] == saved['ended_at'] and not edited['recording_allowed']


def test_repeat_links_are_same_patient_and_acyclic(client, doctor):
    p = patient(client)
    first = encounter(client, p)
    second = start(client, p, visit_type='repeat', previous_encounter_id=first['id'])
    saved = materialize(client, second, {'visit_type': 'repeat', 'complaints': 'Повторный визит'}).json()
    assert saved['previous_encounter_id'] == first['id']
    bad_cycle = client.patch(f'/api/v1/encounters/{first["id"]}', json={'version': first['version'],
        'fields': first['fields'], 'previous_encounter_id': saved['id']})
    assert bad_cycle.status_code == 422
    other_patient = client.post('/api/v1/patients', json={'name': 'Другой пациент', 'iin': '000000000044', 'birth_date': '1990-01-01'}).json()
    bad_patient = client.post(f'/api/v1/patients/{other_patient["id"]}/encounters', json={'previous_encounter_id': first['id']})
    assert bad_patient.status_code == 422
    assert {row['id'] for row in client.get('/api/v1/encounters?status=draft').json()} == {first['id'], saved['id']}


def test_container_audio_requires_actual_packet_and_probe_error_never_persists(client, doctor, monkeypatch):
    from types import SimpleNamespace
    e = start(client, patient(client))
    monkeypatch.setattr(settings(), 'asr_provider', 'self_hosted')
    monkeypatch.setattr('app.lifecycle.subprocess.run', lambda *args, **kwargs: SimpleNamespace(stdout=b'{"packets":[]}'))
    response = client.post(f'/api/v1/encounters/{e["id"]}/audio', data={'draft_token': e['draft_token']},
        files={'file': ('fake.mp3', b'ID3' + b'garbage' * 20, 'audio/mpeg')})
    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.get(Encounter, e['id']) is None
    monkeypatch.setattr('app.lifecycle.subprocess.run', lambda *args, **kwargs: SimpleNamespace(stdout=b'{"packets":[{"codec_type":"audio","size":"100"}]}'))
    assert audio_signature_valid(b'ID3' + b'synthetic' * 20, 'audio/mpeg')


def test_ended_encounter_can_retry_existing_audio_but_never_add_new_recording(client, doctor, monkeypatch):
    from app.providers import ProviderError
    p = patient(client)
    e = start(client, p)
    monkeypatch.setattr(settings(), 'asr_provider', 'self_hosted')
    monkeypatch.setattr('app.worker.transcribe', lambda *args: (_ for _ in ()).throw(ProviderError('Временная ошибка ASR')))
    upload(client, e)
    client.post(f'/api/v1/encounters/{e["id"]}/finish', json={'version': 1})
    assert process_one()
    saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    recording = client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()[0]
    path = f'/api/v1/encounters/{e["id"]}/recordings/{recording["id"]}/transcribe'
    other = other_doctor()
    assert other.post(path, json={'version': saved['version'], 'analyze': False}).status_code == 404
    monkeypatch.setattr('app.worker.transcribe', lambda path: [{'speaker': 'SPEAKER_00', 'start': 0, 'end': 1, 'text': 'Синтетическая расшифровка после повтора'}])
    monkeypatch.setattr('app.worker.mask_audio', lambda *args: b'masked')
    response = client.post(path, json={'version': saved['version'], 'analyze': False})
    assert response.status_code == 202
    assert process_one()
    refreshed = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert refreshed['ended_at'] == saved['ended_at'] and refreshed['transcript']
    records = client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()
    assert len(records) == 1 and records[0]['id'] == recording['id'] and records[0]['attempts'] == 2
    assert records[0]['state'] == 'done' and records[0]['transcript']
    assert upload(client, refreshed).status_code == 409


def test_unsaved_draft_keeps_remote_format_after_token_reload(client, doctor):
    p = patient(client)
    e = start(client, p)
    saved = materialize(client, e, {'visit_type': 'repeat', 'visit_format': 'remote'}).json()
    assert not saved['persisted']
    reread = client.get(f'/api/v1/encounters/{e["id"]}', params={'draft_token': saved['draft_token']}).json()
    assert reread['fields']['visit_type'] == 'repeat' and reread['fields']['visit_format'] == 'remote'


def test_clinical_edit_preserves_manual_redaction_until_transcript_changes(client, doctor):
    p = patient(client)
    draft = start(client, p)
    raw = [{'speaker': 'SPEAKER_00', 'start': 0, 'end': 2,
            'text': 'Представитель: Синтетический Контакт.'}]
    e = materialize(client, draft, {'complaints': 'Синтетическая жалоба'}, transcript=raw).json()
    manually_masked = [{**raw[0], 'text': 'Представитель: [СКРЫТО ВРАЧОМ].'}]
    reviewed = client.post(f'/api/v1/encounters/{e["id"]}/privacy-review', json={
        'version': e['version'], 'segments': manually_masked}).json()
    assert reviewed['privacy_reviewed'] and reviewed['redacted_transcript'] == manually_masked
    saved = client.patch(f'/api/v1/encounters/{e["id"]}', json={
        'version': reviewed['version'], 'fields': {'complaints': 'Уточнённая жалоба'},
        'transcript': raw, 'speaker_roles': {'SPEAKER_00': 'patient'}}).json()
    assert saved['privacy_reviewed'] and saved['redacted_transcript'] == manually_masked
    changed = client.patch(f'/api/v1/encounters/{e["id"]}', json={
        'version': saved['version'], 'fields': saved['fields'],
        'transcript': [{**raw[0], 'text': 'Исправленная синтетическая фраза.'}]}).json()
    assert changed['privacy_reviewed'] is False
    assert changed['redacted_transcript'][0]['text'] == 'Исправленная синтетическая фраза.'


def test_integration_document_does_not_change_with_ui_clock(client, doctor, monkeypatch):
    e = encounter(client, patient(client))
    approved = client.post(f'/api/v1/encounters/{e["id"]}/approve', json={'version': e['version']}).json()
    key = client.post('/api/v1/integration-key').json()['api_key']
    headers = {'Authorization': 'Bearer ' + key}
    path = f'/api/v1/integration/encounters/{e["id"]}'
    first = client.get(path, headers=headers).json()
    monkeypatch.setattr('app.main.now', lambda: approved['ended_at'] + 60)
    second = client.get(path, headers=headers).json()
    assert first == second
    assert 'server_time' not in second['encounter']
    assert second['encounter']['started_at'] <= second['encounter']['ended_at']
