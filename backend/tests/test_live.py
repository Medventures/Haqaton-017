import io
import wave
from uuid import uuid4
import pytest
from app.db import SessionLocal, Encounter, Job, now
from app.schemas import Consultation
from app.live import wav_bytes, read_audio, recover_and_expire
from app.worker import process_one
from test_consultation_pipeline import prepare


def part(seconds=20, silent=False):
    return wav_bytes((b'\x00\x00' if silent else b'\x00\x10') * int(16000 * seconds))


def upload(client, e, stream, seq, data=None):
    return client.post(f'/api/v1/encounters/{e["id"]}/live/{stream}/parts', data={'sequence': seq,
        **({'draft_token': e['draft_token']} if e.get('draft_token') else {})},
        files={'file': ('part.wav', data if data is not None else part(), 'audio/wav')})


def setup_live(client, monkeypatch):
    _, e = prepare(client, monkeypatch)
    calls = []
    monkeypatch.setattr('app.live.transcribe', lambda path: [{'speaker': 'SPEAKER_00', 'start': 0, 'end': 2, 'text': 'Синтетическая жалоба'}])
    monkeypatch.setattr('app.live.mask_audio', lambda path, *args: path.read_bytes())
    def generate(segments, context, target):
        calls.append((segments, context))
        return {'fields': Consultation(complaints='Дополнение', diagnosis='Не заменять врача',
            ai_test_recommendations='Справочная подсказка', ai_diagnosis_variants='Предположение').model_dump(),
            'speaker_roles': {s['speaker']: 'patient' for s in segments}}
    monkeypatch.setattr('app.live.generate', generate)
    e = client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': e['version'],
        'fields': Consultation(complaints='Правка врача', diagnosis='Ручной диагноз', diagnosis_code='I10').model_dump()}).json()
    return e, str(uuid4()), calls


def test_live_ack_preview_final_archive_and_no_repeat_asr(client, doctor, monkeypatch):
    e, stream, calls = setup_live(client, monkeypatch)
    for seq in range(3):
        response = upload(client, e, stream, seq)
        assert response.status_code == 202, response.text
        assert upload(client, e, stream, seq).json()['duplicate'] is True
        assert process_one()
    progress = client.get(f'/api/v1/encounters/{e["id"]}/live/{stream}').json()
    assert progress['received'] == progress['completed'] == 3
    assert [s['start'] for s in progress['transcript']] == [0, 20, 40]
    assert len({s['speaker'] for s in progress['transcript']}) == 3
    assert len(calls) == 1
    assert progress['encounter']['fields']['diagnosis'] == 'Ручной диагноз'
    assert progress['encounter']['fields']['complaints'] == 'Правка врача\n\nДополнение'
    assert client.post(f'/api/v1/encounters/{e["id"]}/generate', json={'version': progress['encounter']['version']}).status_code == 409
    assert upload(client, e, stream, 3, part(2)).status_code == 202
    finish_url = f'/api/v1/encounters/{e["id"]}/live/{stream}/finish'
    assert client.post(finish_url, json={'count': 3}).status_code == 409
    job = client.post(finish_url, json={'count': 4}).json()
    assert client.post(finish_url, json={'count': 4}).json() == job
    assert process_one() and process_one()
    saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert saved['status'] == 'ready' and len(saved['transcript']) == 4
    assert len(calls) == 2
    assert saved['fields']['diagnosis_code'] == 'I10'
    records = client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()
    assert len(records) == 1 and records[0]['available']
    assert records[0]['timeline_offset'] == 0 and records[0]['duration'] == 62
    audio = client.get(f'/api/v1/encounters/{e["id"]}/recordings/{records[0]["id"]}/audio').content
    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getnframes() / wav.getframerate() == 62
    with SessionLocal() as db:
        session = db.get(Job, stream)
        assert session.state == 'done'
        assert len(session.payload['parts']) == 4
    assert not process_one()


def test_live_rejects_gaps_changed_retries_and_closed_recording(client, doctor, monkeypatch):
    e, stream, _ = setup_live(client, monkeypatch)
    assert upload(client, e, stream, 1).status_code == 409
    assert upload(client, e, stream, 0).status_code == 202
    assert upload(client, e, stream, 0, part(1)).status_code == 409
    assert client.post(f'/api/v1/encounters/{e["id"]}/finish', json={'version': e['version']}).status_code == 409
    assert client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': e['version'], 'fields': {}}).status_code == 409
    client.post(f'/api/v1/encounters/{e["id"]}/live/{stream}/finish', json={'count': 1})
    assert upload(client, e, stream, 1).status_code == 409
    assert upload(client, e, stream, 0).json()['duplicate'] is True


def test_live_failed_asr_can_retry_and_consent_is_checked_again(client, doctor, monkeypatch):
    e, stream, _ = setup_live(client, monkeypatch)
    def fail(path):
        raise RuntimeError('Synthetic provider outage')
    monkeypatch.setattr('app.live.transcribe', fail)
    assert upload(client, e, stream, 0).status_code == 202
    assert process_one()
    progress = client.get(f'/api/v1/encounters/{e["id"]}/live/{stream}').json()
    assert progress['completed'] == 0 and progress['error']
    saved = client.get(f'/api/v1/encounters/{e["id"]}/live/{stream}/audio')
    assert saved.status_code == 200 and saved.content == part()
    # Чужой врач не получает ни аудио, ни состояние потока.
    client.post('/api/v1/auth/register', json={'name': 'Другой Врач', 'email': 'other@example.test',
        'iin': '000000000002', 'password': 'Other-safe-test-123'})
    assert client.get(f'/api/v1/encounters/{e["id"]}/live/{stream}/audio').status_code == 404
    assert client.get(f'/api/v1/encounters/{e["id"]}/live/{stream}').status_code == 404
    client.post('/api/v1/auth/login', json={'email': 'test@example.test', 'password': 'Very-safe-test-123'})
    monkeypatch.setattr('app.live.transcribe', lambda path: [])
    assert client.post(f'/api/v1/encounters/{e["id"]}/live/{stream}/finish', json={'count': 1}).status_code == 202
    assert process_one() and process_one()
    assert client.get(f'/api/v1/encounters/{e["id"]}').json()['status'] == 'ready'
    client.patch(f'/api/v1/patients/{e["patient_id"]}/consent', json={'processing_consent': False})
    assert upload(client, e, str(uuid4()), 0).status_code == 403


def test_silent_audio_is_archived_without_asr_and_stale_session_finalizes(client, doctor, monkeypatch):
    e, stream, calls = setup_live(client, monkeypatch)
    monkeypatch.setattr('app.live.transcribe', lambda path: pytest.fail('Silence must not reach ASR'))
    assert upload(client, e, stream, 0, part(1, silent=True)).status_code == 202
    assert process_one()
    with SessionLocal() as db:
        session = db.get(Job, stream)
        session.payload = {**session.payload, 'deadline': now() - 100}
        db.commit()
    recover_and_expire()
    assert process_one()
    assert not calls
    assert client.get(f'/api/v1/encounters/{e["id"]}/recordings').json()[0]['available']


@pytest.mark.parametrize('data', [b'not audio', part(33)], ids=['invalid-header', 'too-long'])
def test_live_invalid_audio_rejected(client, doctor, monkeypatch, data):
    e, stream, _ = setup_live(client, monkeypatch)
    assert upload(client, e, stream, 0, data).status_code in (413, 422)
    with SessionLocal() as db:
        assert db.get(Job, stream) is None


def test_empty_draft_is_not_materialized_until_valid_audio_and_restart_resumes(client, doctor, monkeypatch):
    from test_workflow import patient
    from app.config import settings
    from app.worker import recover
    p = patient(client)
    e = client.post(f'/api/v1/patients/{p["id"]}/encounters', json={}).json()
    monkeypatch.setattr(settings(), 'asr_provider', 'self_hosted')
    stream = str(uuid4())
    assert upload(client, e, stream, 0, b'bad').status_code == 422
    with SessionLocal() as db:
        assert db.get(Encounter, e['id']) is None
    assert upload(client, e, stream, 0, part(1, True)).status_code == 202
    with SessionLocal() as db:
        session = db.get(Job, stream)
        db.get(Job, session.payload['parts'][0]).state = 'running'
        db.commit()
    recover()
    assert process_one()
    assert client.get(f'/api/v1/encounters/{e["id"]}/live/{stream}').json()['completed'] == 1
