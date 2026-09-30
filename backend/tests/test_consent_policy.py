from io import BytesIO

from pypdf import PdfReader

from app.config import settings
from app.consent_document import build_consent_pdf


def test_consent_pdf_has_patient_scope_and_document_identity():
    first = build_consent_pdf({'name': 'Тестовый Пациент', 'iin': '000000000002'}, 'document-one', 1790774400, 'v1')
    second = build_consent_pdf({'name': 'Тестовый Пациент', 'iin': '000000000002'}, 'document-two', 1790774400, 'v1')
    text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(first)).pages)
    assert len(PdfReader(BytesIO(first)).pages) == 1
    assert all(value in text for value in ('Тестовый Пациент', '000000000002', 'document-one', 'OpenAI', 'SIGEX', 'NCALayer', 'отозвать'))
    assert first != second


def test_mandatory_signature_blocks_manual_grant_and_capture(client, doctor, monkeypatch):
    patient = client.post('/api/v1/patients', json={
        'name': 'Тестовый Пациент', 'iin': '000000000002', 'birth_date': '1990-01-01', 'processing_consent': True}).json()
    assert patient['consent_signature']['state'] == 'not_signed'
    assert patient['ai_processing_allowed'] is True
    monkeypatch.setattr(settings(), 'consent_signature_required', True)
    view = client.get('/api/v1/patients/' + patient['id']).json()
    assert view['processing_consent'] is True  # Отметка врача не превращается в ЭЦП.
    assert view['ai_processing_allowed'] is False
    assert client.patch(f"/api/v1/patients/{patient['id']}/consent", json={'processing_consent': True}).status_code == 403
    assert client.post('/api/v1/patients', json={
        'name': 'Второй Пациент', 'iin': '000000000003', 'birth_date': '1990-01-01', 'processing_consent': True}).status_code == 403
    encounter = client.post(f"/api/v1/patients/{patient['id']}/encounters", json={}).json()
    assert encounter['recording_allowed'] is False
    assert client.post(f"/api/v1/encounters/{encounter['id']}/capture-lease",
                       json={'draft_token': encounter['draft_token'], 'version': encounter['version']}).status_code == 403
    assert client.patch(f"/api/v1/patients/{patient['id']}/consent", json={'processing_consent': False}).status_code == 200


def test_worker_rechecks_signature_policy_before_sending_audio(client, doctor, monkeypatch):
    from test_consultation_pipeline import prepare
    from audio_fixture import audio_bytes
    from app.worker import process_one

    patient, encounter = prepare(client, monkeypatch)
    uploaded = client.post(f"/api/v1/encounters/{encounter['id']}/audio",
                           files={'file': ('synthetic.wav', audio_bytes(), 'audio/wav')})
    assert uploaded.status_code == 202
    monkeypatch.setattr(settings(), 'consent_signature_required', True)
    called = []
    monkeypatch.setattr('app.worker.transcribe', lambda path: called.append(path))
    assert process_one()
    assert not called
    result = client.get('/api/v1/jobs/' + uploaded.json()['job_id']).json()
    assert result['state'] == 'failed'
    assert 'ЭЦП' in result['error']
