import base64
import hashlib
import json
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import select, text
from app.main import app
from app.db import SessionLocal, Patient, PatientConsent, PatientConsentAttempt
from app.config import settings
from app.consent import DOCUMENT_VERSION, signature_allows_processing, run_consent_qr, _complete
from app.consent_sigex import ConsentVerificationError, verify_document_signature
from test_workflow import patient
from test_visit_lifecycle import other_doctor

CMS = base64.b64encode(b'synthetic-cms-payload-for-mocked-verification').decode()


def prepared(client, monkeypatch, p=None):
    monkeypatch.setattr(settings(), 'sigex_enabled', True)
    p = p or patient(client, False)
    response = client.post(f'/api/v1/patients/{p["id"]}/consents', json={})
    assert response.status_code == 201, response.text
    return p, response.json()


def started(client, consent, method='eds'):
    response = client.post(f'/api/v1/patient-consents/{consent["id"]}/start', json={'method': method, 'acknowledged': True})
    assert response.status_code == 200, response.text
    attempt = response.json()
    return attempt, f'/api/v1/patient-consents/{consent["id"]}/attempts/{attempt["id"]}'


def valid_proof(monkeypatch, effect=None):
    def verify(pdf, cms, iin, **kwargs):
        assert pdf.startswith(b'%PDF') and cms == CMS and len(iin) == 12
        if effect:
            effect()
        return {'verified': True, 'signer_iin': iin, 'document_sha256': hashlib.sha256(pdf).hexdigest(), 'provider': 'synthetic-test'}
    monkeypatch.setattr('app.consent.verify_document_signature', verify)


def test_prepared_consent_is_immutable_encrypted_and_never_claims_signed(client, doctor, monkeypatch):
    p, consent = prepared(client, monkeypatch)
    pdf = client.get(consent['pdf_url'])
    assert pdf.status_code == 200 and pdf.content.startswith(b'%PDF')
    assert consent['document_sha256'] == hashlib.sha256(pdf.content).hexdigest()
    assert consent['state'] == 'draft' and consent['verified_at'] is None
    same = client.post(f'/api/v1/patients/{p["id"]}/consents', json={}).json()
    assert same['id'] == consent['id']
    assert client.get(f'/api/v1/patient-consents/{consent["id"]}/signature').status_code == 404
    with SessionLocal() as db:
        raw = db.execute(text('SELECT document FROM patient_consents')).scalar_one()
        assert p['iin'] not in raw and base64.b64encode(pdf.content).decode() not in raw
    state = client.get(f'/api/v1/patients/{p["id"]}/consents').json()
    assert state['patient']['consent_signature']['state'] == 'not_signed'
    assert state['patient']['processing_consent'] is False
    assert client.post(f'/api/v1/patient-consents/{consent["id"]}/start', json={'method': 'eds', 'acknowledged': False}).status_code == 422


def test_signed_status_requires_verified_pdf_iin_and_unlocks_processing(client, doctor, monkeypatch):
    p, consent = prepared(client, monkeypatch)
    monkeypatch.setattr(settings(), 'consent_signature_required', True)
    attempt, path = started(client, consent)
    assert base64.b64decode(attempt['data_base64']) == client.get(consent['pdf_url']).content
    assert client.get(f'/api/v1/patients/{p["id"]}').json()['processing_consent'] is False
    valid_proof(monkeypatch)
    response = client.post(path + '/signature', json={'signature': CMS})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['state'] == 'signed' and result['consent']['verified_at']
    assert result['consent']['signer_iin_masked'].endswith(p['iin'][-4:])
    assert result['patient']['processing_consent'] and result['patient']['ai_processing_allowed']
    assert client.post(path + '/signature', json={'signature': CMS}).status_code == 409
    signature_file = client.get(result['consent']['signature_url'])
    assert signature_file.content == base64.b64decode(CMS)
    with SessionLocal() as db:
        stored = db.get(Patient, p['id'])
        assert signature_allows_processing(stored, db)
        assert 'cms_base64' not in stored.data and 'pdf_base64' not in stored.data
        assert CMS not in db.execute(text('SELECT verification FROM patient_consents')).scalar_one()


@pytest.mark.parametrize('mismatch', ['iin', 'document', 'provider_rejected'])
def test_invalid_proof_does_not_grant_consent(client, doctor, monkeypatch, mismatch):
    p, consent = prepared(client, monkeypatch)
    attempt, path = started(client, consent)
    def verify(pdf, cms, iin, **kwargs):
        if mismatch == 'provider_rejected':
            raise ConsentVerificationError('SIGEX отклонил подпись')
        return {'verified': True, 'signer_iin': '999999999999' if mismatch == 'iin' else iin,
                'document_sha256': '0' * 64 if mismatch == 'document' else hashlib.sha256(pdf).hexdigest()}
    monkeypatch.setattr('app.consent.verify_document_signature', verify)
    assert client.post(path + '/signature', json={'signature': CMS}).status_code == 401
    state = client.get(path).json()
    assert state['state'] == 'failed' and state['consent']['state'] == 'draft'
    assert not state['patient']['processing_consent']


def test_attempt_belongs_to_session_but_document_status_is_shared(client, doctor, monkeypatch):
    p, consent = prepared(client, monkeypatch)
    attempt, path = started(client, consent)
    other = other_doctor()
    assert other.get(consent['pdf_url']).status_code == 200
    shared = other.get(f'/api/v1/patients/{p["id"]}/consents').json()
    assert shared['current']['id'] == consent['id'] and 'active_attempt' not in shared['current']
    assert other.get(path).status_code == 404
    assert other.post(path + '/signature', json={'signature': CMS}).status_code == 404
    assert other.post(path + '/cancel').status_code == 404
    assert other.post(f'/api/v1/patient-consents/{consent["id"]}/start', json={'method': 'eds', 'acknowledged': True}).status_code == 409
    same_doctor_new_session = TestClient(app, headers={'X-Medhub-Request': '1'})
    assert same_doctor_new_session.post('/api/v1/auth/login', json={'email': 'test@example.test', 'password': 'Very-safe-test-123'}).status_code == 200
    assert same_doctor_new_session.get(path).status_code == 404
    own = client.get(f'/api/v1/patients/{p["id"]}/consents').json()
    assert own['current']['active_attempt']['id'] == attempt['id']


@pytest.mark.parametrize('action', ['cancel', 'revoke', 'manual_revoke', 'expire', 'disable'])
def test_canceled_expired_revoked_disabled_attempt_never_grants_late_signature(client, doctor, monkeypatch, action):
    p, consent = prepared(client, monkeypatch)
    attempt, path = started(client, consent)
    valid_proof(monkeypatch)
    if action == 'cancel':
        assert client.post(path + '/cancel').status_code == 200
    elif action == 'revoke':
        assert client.post(f'/api/v1/patient-consents/{consent["id"]}/revoke', json={}).status_code == 200
    elif action == 'manual_revoke':
        assert client.patch(f'/api/v1/patients/{p["id"]}/consent', json={'processing_consent': False}).status_code == 200
    elif action == 'expire':
        monkeypatch.setattr('app.consent.now', lambda: attempt['expires_at'] + 1)
    else:
        monkeypatch.setattr(settings(), 'sigex_enabled', False)
    assert client.post(path + '/signature', json={'signature': CMS}).status_code in (409, 503)
    assert not client.get(f'/api/v1/patients/{p["id"]}').json()['processing_consent']


def test_manual_revocation_during_remote_verification_cannot_be_undone(client, doctor, monkeypatch):
    p, consent = prepared(client, monkeypatch)
    attempt, path = started(client, consent)
    valid_proof(monkeypatch, lambda: client.patch(f'/api/v1/patients/{p["id"]}/consent', json={'processing_consent': False}).raise_for_status())
    assert client.post(path + '/signature', json={'signature': CMS}).status_code == 409
    assert not client.get(f'/api/v1/patients/{p["id"]}').json()['processing_consent']


def test_returning_to_patient_card_clears_expired_pending_status(client, doctor, monkeypatch):
    p, consent = prepared(client, monkeypatch)
    attempt, path = started(client, consent)
    monkeypatch.setattr('app.consent.now', lambda: attempt['expires_at'] + 1)
    current = client.get(f'/api/v1/patients/{p["id"]}/consents').json()['current']
    assert current['state'] == 'draft' and 'active_attempt' not in current
    assert client.get(path).json()['state'] == 'expired'


def test_explicit_logout_prevents_background_completion(client, doctor, monkeypatch):
    from fastapi import HTTPException
    p, consent = prepared(client, monkeypatch)
    attempt, path = started(client, consent)
    client.post('/api/v1/auth/logout').raise_for_status()
    proof = {'verified': True, 'signer_iin': p['iin'], 'document_sha256': consent['document_sha256']}
    with SessionLocal() as db:
        with pytest.raises(HTTPException, match='Сессия врача завершена'):
            _complete(db, consent['id'], attempt['id'], CMS, proof)
        assert db.get(PatientConsent, consent['id']).state == 'pending'
        assert db.get(Patient, p['id']).recording_consent is False


def test_strict_guard_uses_document_record_and_renewal_keeps_signed_current(client, doctor, monkeypatch):
    p, consent = prepared(client, monkeypatch)
    attempt, path = started(client, consent)
    valid_proof(monkeypatch)
    client.post(path + '/signature', json={'signature': CMS}).raise_for_status()
    new_consent = client.post(f'/api/v1/patients/{p["id"]}/consents', json={}).json()
    assert new_consent['id'] != consent['id']
    current = client.get(f'/api/v1/patients/{p["id"]}/consents').json()['current']
    assert current['id'] == consent['id'] and current['state'] == 'signed'
    monkeypatch.setattr(settings(), 'consent_signature_required', True)
    with SessionLocal() as db:
        stored = db.get(Patient, p['id'])
        assert signature_allows_processing(stored, db)
        db.get(PatientConsent, consent['id']).state = 'revoked'
        db.commit()
        assert not signature_allows_processing(stored, db)  # Старый cache не является разрешением.


def test_qr_transfers_exact_pdf_then_verifies_cms_and_preserves_private_resume(client, doctor, monkeypatch):
    p, consent = prepared(client, monkeypatch)
    valid_proof(monkeypatch)
    png = base64.b64encode(b'\x89PNG\r\n\x1a\nsynthetic').decode()
    def sigex(method, path, **kwargs):
        if path == '/api/egovQr':
            from app.db import now
            return {'qrCode': png, 'eGovMobileLaunchLink': 'https://m.egov.kz/?link=https%3A%2F%2Fsigex.kz%2Fapi%2Fsynthetic',
                    'dataURL': '/api/synthetic-data', 'signURL': '/api/synthetic-sign', 'expireAt': (now() + 300) * 1000}
        if method == 'POST':
            document = kwargs['json']['documentsToSign'][0]['document']['file']
            assert document['mime'] == '@file/pdf'
            assert base64.b64decode(document['data']) == client.get(consent['pdf_url']).content
            return {'signURL': '/api/synthetic-sign'}
        return {'signMethod': 'CMS_WITH_DATA', 'documentsToSign': [{'id': 1, 'document': {'file': {'data': CMS}}}]}
    monkeypatch.setattr('app.consent.sigex_request', sigex)
    # Не блокируем HTTP тест BackgroundTasks: исполняем ту же задачу отдельно.
    monkeypatch.setattr('fastapi.BackgroundTasks.add_task', lambda *args, **kwargs: None)
    attempt, path = started(client, consent, 'qr')
    assert attempt['qr_image'].startswith('data:image/png;base64,')
    assert client.get(path).json()['launch_url'] == attempt['launch_url']
    run_consent_qr(consent['id'], attempt['id'])
    status = client.get(path).json()
    assert status['state'] == 'signed' and status['patient']['processing_consent']


@pytest.mark.parametrize('fault', [None, 'http200_error', 'attached_document', 'wrong_data_id', 'wrong_verify_id', 'string_sign_id', 'unknown_size', 'wrong_size', 'boolean_size'])
def test_sigex_private_document_verification_contract(monkeypatch, fault):
    pdf, iin, document_id = b'%PDF-synthetic-consent-document', '000000000000', 'synthetic-document-id'
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path == '/api':
            body = json.loads(request.content)
            assert body['settings']['private'] is True
            assert body['settings']['strictSignersRequirements'] is True
            assert body['settings']['signersRequirements'] == [{'iin': 'IIN' + iin, 'ca': 'nca'}]
            if fault == 'http200_error':
                return httpx.Response(200, json={'message': 'Failed to parse signature', 'requestID': 'synthetic'})
            return httpx.Response(200, json={'documentId': document_id, 'signId': '123' if fault == 'string_sign_id' else 123,
                'data': base64.b64encode(b'other' if fault == 'attached_document' else pdf).decode()})
        assert request.content == pdf and request.headers['content-type'] == 'application/octet-stream'
        if request.url.path.endswith('/data'):
            return httpx.Response(200, json={'documentId': 'other-document' if fault == 'wrong_data_id' else document_id,
                'signedDataSize': 0 if fault == 'unknown_size' else (True if fault == 'boolean_size' else (len(pdf) + 1 if fault == 'wrong_size' else len(pdf))), 'digests': {'synthetic': 'digest'}})
        return httpx.Response(200, json={'documentId': 'other-document' if fault == 'wrong_verify_id' else document_id,
            'dataArchived': False, 'tempStorage': False})
    original_client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    if fault and fault != 'unknown_size':
        with pytest.raises(ConsentVerificationError):
            verify_document_signature(pdf, CMS, iin)
    else:
        proof = verify_document_signature(pdf, CMS, iin)
        assert proof['verified'] and proof['signer_iin'] == iin and proof['provider_signature_id'] == 123
        assert proof['document_sha256'] == hashlib.sha256(pdf).hexdigest()
        assert len(requests) == 3


def test_received_signature_survives_temporary_verification_failure_and_reload(client, doctor, monkeypatch):
    p, consent = prepared(client, monkeypatch)
    attempt, path = started(client, consent)
    def temporary(*args, **kwargs):
        raise ConsentVerificationError('Временный сбой', retryable=True)
    monkeypatch.setattr('app.consent.verify_document_signature', temporary)
    response = client.post(path + '/signature', json={'signature': CMS})
    assert response.status_code == 200 and response.json()['state'] == 'verifying'
    assert response.json()['expires_at'] > attempt['expires_at']
    assert not response.json()['patient']['processing_consent']
    assert client.post(path + '/signature', json={'signature': CMS}).status_code == 409
    with SessionLocal() as db:
        saved = db.get(PatientConsentAttempt, attempt['id'])
        assert saved.data['cms'] == CMS
        saved.data = {**saved.data, 'retry_after': 0}
        db.commit()
    valid_proof(monkeypatch)
    client.get(path)  # Повторно запускает проверку без QR / NCALayer.
    assert client.get(path).json()['state'] == 'signed'
    assert client.get(f'/api/v1/patients/{p["id"]}').json()['processing_consent']


def test_qr_network_timeout_retries_same_request_without_losing_result(monkeypatch):
    from app.identity import qr_request
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path))
        if len(calls) == 1: raise httpx.ReadTimeout('synthetic timeout')
        return {'signMethod': 'XML'}
    monkeypatch.setattr('time.sleep', lambda _: None)
    from app.db import now
    assert qr_request('GET', '/api/synthetic', now() + 60, requester=request)['signMethod'] == 'XML'
    assert calls == [('GET', '/api/synthetic')] * 2


def test_verification_checkpoint_avoids_duplicate_registration_after_timeout(monkeypatch):
    pdf, iin = b'%PDF-synthetic', '000000000000'
    checkpoint, paths = {}, []
    def handle(request):
        paths.append(request.url.path)
        if request.url.path == '/api':
            return httpx.Response(200, json={'documentId': 'test-document', 'signId': 1, 'data': base64.b64encode(pdf).decode()})
        if len(paths) == 2: raise httpx.ReadTimeout('synthetic')
        if request.url.path.endswith('/data'):
            return httpx.Response(200, json={'documentId': 'test-document', 'signedDataSize': len(pdf), 'digests': {'test': 'digest'}})
        return httpx.Response(200, json={'documentId': 'test-document', 'dataArchived': False, 'tempStorage': False})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ConsentVerificationError) as error:
        verify_document_signature(pdf, CMS, iin, checkpoint=checkpoint.update)
    assert error.value.retryable and checkpoint['documentId'] == 'test-document'
    assert verify_document_signature(pdf, CMS, iin, resume=checkpoint)['verified']
    assert paths.count('/api') == 1


def test_strict_recording_routes_require_verified_signature_and_honor_revocation(client, doctor, monkeypatch):
    from uuid import uuid4
    from sqlalchemy import func
    from app.db import Encounter, Job
    from test_visit_lifecycle import start, upload as upload_file
    from test_live import upload as upload_part, part

    # Старая ручная отметка не должна обходить включённое требование ЭЦП.
    p = patient(client, True)
    client.patch(f'/api/v1/patients/{p["id"]}/consent', json={'processing_consent': True}).raise_for_status()
    p, consent = prepared(client, monkeypatch, p)
    monkeypatch.setattr(settings(), 'consent_signature_required', True)
    e = start(client, p)
    lease_url = f'/api/v1/encounters/{e["id"]}/capture-lease'
    body = {'draft_token': e['draft_token']}

    def denied(token=None):
        assert client.post(lease_url, json=body).status_code == 403
        assert upload_file(client, e, **({'capture_token': token} if token else {})).status_code == 403
        assert upload_part(client, e, str(uuid4()), 0, part(1)).status_code == 403
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Encounter)) == 0
            assert db.scalar(select(func.count()).select_from(Job)) == 0

    assert client.get(f'/api/v1/patients/{p["id"]}').json()['ai_processing_allowed'] is False
    denied()  # Документ ещё не подписан.
    attempt, path = started(client, consent)
    denied()  # Открытый QR / NCALayer не равен проверенной подписи.
    valid_proof(monkeypatch)
    signed = client.post(path + '/signature', json={'signature': CMS})
    assert signed.status_code == 200 and signed.json()['patient']['ai_processing_allowed']
    lease = client.post(lease_url, json=body)
    assert lease.status_code == 200, lease.text
    token = lease.json()['capture_token']
    client.post(f'/api/v1/patient-consents/{consent["id"]}/revoke', json={}).raise_for_status()
    denied(token)  # Уже выданное разрешение не обходит отзыв согласия.
