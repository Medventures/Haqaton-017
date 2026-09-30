import base64
from app.config import settings


def test_sigex_registration_one_time_and_browser_binding(client, monkeypatch):
    settings().sigex_enabled = True
    monkeypatch.setattr('app.identity.verify_xml', lambda *args, **kwargs: {'identity': 'IIN000000000001', 'name': 'Тестовый Врач'})
    start = client.post('/api/v1/auth/identity/eds/start', json={'purpose': 'register', 'iin': '000000000001'})
    assert start.status_code == 200
    identity_id = start.json()['id']
    assert client.post(f'/api/v1/auth/identity/{identity_id}/finish').status_code == 409
    from fastapi.testclient import TestClient
    from app.main import app
    stranger = TestClient(app, headers={'X-Medhub-Request': '1'})
    assert stranger.get(f'/api/v1/auth/identity/{identity_id}').status_code == 410
    assert client.post(f'/api/v1/auth/identity/{identity_id}/signature', json={'signature': 'valid-signature-value-123'}).status_code == 200
    result = client.post(f'/api/v1/auth/identity/{identity_id}/finish')
    assert result.status_code == 200 and result.json()['name'] == 'Тестовый Врач'
    assert client.get('/api/v1/auth/me').status_code == 200
    assert client.post(f'/api/v1/auth/identity/{identity_id}/finish').status_code == 410


def test_sigex_invalid_signature_fails_closed(client, monkeypatch):
    settings().sigex_enabled = True
    def sigex(method, path, **kwargs):
        if not kwargs['json']:
            return {'nonce': base64.b64encode(b'nonce').decode()}
        raise ValueError('signature_rejected')
    monkeypatch.setattr('app.identity.sigex_request', sigex)
    identity_id = client.post('/api/v1/auth/identity/eds/start', json={'purpose': 'register', 'iin': '000000000001'}).json()['id']
    assert client.post(f'/api/v1/auth/identity/{identity_id}/signature', json={'signature': 'invalid-signature-value-123'}).status_code == 401
    assert client.get('/api/v1/auth/me').status_code == 401


import pytest
from sqlalchemy import select, func, text
from app.db import Doctor, SessionLocal
from app.security import digest


def mock_sigex(monkeypatch, iin='000000000001'):
    settings().sigex_enabled = True
    def verified(signature, expected, expected_iin, **kwargs):
        assert '<authentication>' in expected
        if expected_iin and expected_iin != iin: raise ValueError('iin_mismatch')
        return {'identity': 'IIN' + iin, 'name': 'Тестовый Врач'}
    monkeypatch.setattr('app.identity.verify_xml', verified)
    def request(method, path, **kwargs):
        if path == '/api/egovQr':
            return {'qrCode': base64.b64encode(b'\x89PNG\r\n\x1a\n').decode(),
                'eGovMobileLaunchLink': 'https://m.egov.kz/?link=https%3A%2F%2Fsigex.kz%2Fapi%2Ftest',
                'dataURL': '/api/test-data', 'signURL': '/api/test-sign', 'expireAt': 9999999999999}
        if path == '/api/test-data':
            assert kwargs['json']['signMethod'] == 'XML'
            assert '<authentication>' in kwargs['json']['documentsToSign'][0]['documentXml']
            return {'signURL': '/api/test-sign'}
        if path == '/api/test-sign':
            return {'signMethod': 'XML', 'documentsToSign': [
                {'id': 1, 'documentXml': 'test-signature-value-123'}]}
        raise AssertionError(path)
    monkeypatch.setattr('app.identity.sigex_request', request)


def signed_attempt(client, method='eds', **body):
    r = client.post(f'/api/v1/auth/identity/{method}/start', json=body)
    assert r.status_code == 200, r.text
    attempt = r.json()['id']
    if method == 'eds':
        r = client.post(f'/api/v1/auth/identity/{attempt}/signature', json={'signature': 'test-signature-value-123'})
        assert r.status_code == 200, r.text
    return attempt


@pytest.mark.parametrize('method', ['eds', 'qr'])
def test_signature_logs_into_same_password_account_by_iin(client, doctor, monkeypatch, method):
    mock_sigex(monkeypatch)
    client.post('/api/v1/auth/logout')
    attempt = signed_attempt(client, method, purpose='login')
    result = client.post(f'/api/v1/auth/identity/{attempt}/finish')
    assert result.status_code == 200, result.text
    assert result.json()['id'] == doctor['id']
    assert result.json()['email'] == 'test@example.test'
    assert result.json()['iin'] == '000000000001'
    assert result.json()['iin_verified'] is True
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Doctor)) == 1
        raw = db.execute(text('SELECT profile, identity_hash FROM doctors')).one()
        assert '000000000001' not in raw.profile
        assert raw.identity_hash == digest('IIN000000000001')


@pytest.mark.parametrize('method', ['eds', 'qr'])
def test_unknown_certificate_cannot_access_existing_account(client, doctor, monkeypatch, method):
    mock_sigex(monkeypatch, '000000000002')
    client.post('/api/v1/auth/logout')
    attempt = signed_attempt(client, method, purpose='login')
    assert client.post(f'/api/v1/auth/identity/{attempt}/finish').status_code == 403
    assert client.get('/api/v1/auth/me').status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Doctor)) == 1


@pytest.mark.parametrize('method', ['eds', 'qr'])
def test_registration_iin_must_match_verified_certificate(client, monkeypatch, method):
    mock_sigex(monkeypatch, '000000000002')
    attempt = client.post(f'/api/v1/auth/identity/{method}/start', json={
        'purpose': 'register', 'iin': '000000000001'}).json()['id']
    if method == 'eds':
        assert client.post(f'/api/v1/auth/identity/{attempt}/signature', json={'signature': 'test-signature-value-123'}).status_code == 401
    assert client.post(f'/api/v1/auth/identity/{attempt}/finish').status_code == 401
    assert client.get('/api/v1/auth/me').status_code == 401
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Doctor)) == 0


def test_iin_unique_across_password_and_sigex_registration(client, doctor, monkeypatch):
    mock_sigex(monkeypatch)
    client.post('/api/v1/auth/logout')
    assert client.post('/api/v1/auth/register', json={'name': 'Duplicate Doctor',
        'email': 'duplicate@example.test', 'password': 'Long-test-password-123', 'iin': '000000000001'}).status_code == 409
    attempt = signed_attempt(client, purpose='register', iin='000000000001')
    assert client.post(f'/api/v1/auth/identity/{attempt}/finish').status_code == 409
    assert client.get('/api/v1/auth/me').status_code == 401


@pytest.mark.parametrize('iin', ['', '123', '00000000000x', '０００００００００００１'])
def test_registration_rejects_invalid_iin(client, iin):
    assert client.post('/api/v1/auth/register', json={'name': 'Test Doctor',
        'email': 'test@example.test', 'password': 'Long-test-password-123', 'iin': iin}).status_code == 422
    assert client.post('/api/v1/auth/identity/eds/start', json={'purpose': 'register', 'iin': iin}).status_code == 422


def test_legacy_account_links_only_verified_iin_without_losing_account(client, doctor, monkeypatch):
    mock_sigex(monkeypatch)
    with SessionLocal() as db:
        old = db.get(Doctor, doctor['id'])
        old.identity_hash = None
        old.profile = {'name': 'Legacy Doctor', 'email': 'test@example.test'}
        db.commit()
    attempt = signed_attempt(client, purpose='link')
    result = client.post(f'/api/v1/auth/identity/{attempt}/finish')
    assert result.status_code == 200
    assert result.json()['id'] == doctor['id'] and result.json()['iin'] == '000000000001'
    client.post('/api/v1/auth/logout')
    attempt = signed_attempt(client, purpose='login')
    assert client.post(f'/api/v1/auth/identity/{attempt}/finish').json()['id'] == doctor['id']


def test_link_cannot_replace_iin_or_be_completed_after_logout(client, doctor, monkeypatch):
    mock_sigex(monkeypatch, '000000000002')
    attempt = signed_attempt(client, purpose='link')
    assert client.post(f'/api/v1/auth/identity/{attempt}/finish').status_code == 409
    with SessionLocal() as db:
        assert db.get(Doctor, doctor['id']).identity_hash == digest('IIN000000000001')
    mock_sigex(monkeypatch)
    attempt = signed_attempt(client, purpose='link')
    client.post('/api/v1/auth/logout')
    assert client.post(f'/api/v1/auth/identity/{attempt}/finish').status_code == 401
    assert client.post('/api/v1/auth/identity/eds/start', json={'purpose': 'link'}).status_code == 401


def test_registration_invite_still_required(client, monkeypatch):
    mock_sigex(monkeypatch)
    monkeypatch.setattr(settings(), 'demo_mode', False)
    monkeypatch.setattr(settings(), 'registration_code', 'private-invite')
    assert client.post('/api/v1/auth/identity/eds/start', json={'purpose': 'register', 'iin': '000000000001'}).status_code == 403
    assert client.post('/api/v1/auth/register', json={'name': 'Test Doctor', 'iin': '000000000001',
        'email': 'test@example.test', 'password': 'Long-test-password-123'}).status_code == 403


def test_qr_registration_uses_only_verified_iin(client, monkeypatch):
    mock_sigex(monkeypatch)
    attempt = signed_attempt(client, 'qr', purpose='register', iin='000000000001')
    result = client.post(f'/api/v1/auth/identity/{attempt}/finish')
    assert result.status_code == 200
    assert result.json()['iin'] == '000000000001' and result.json()['iin_verified'] is True


def test_link_cannot_steal_identity_or_survive_account_switch(client, doctor, monkeypatch):
    mock_sigex(monkeypatch)
    other = client.post('/api/v1/auth/register', json={'name': 'Other Doctor', 'iin': '000000000002',
        'email': 'other@example.test', 'password': 'Long-test-password-123'}).json()
    with SessionLocal() as db:
        row = db.get(Doctor, other['id'])
        row.identity_hash = None
        row.profile = {'name': 'Other Doctor', 'email': 'other@example.test'}
        db.commit()
    attempt = signed_attempt(client, purpose='link')
    assert client.post(f'/api/v1/auth/identity/{attempt}/finish').status_code == 409
    mock_sigex(monkeypatch, '000000000002')
    attempt = signed_attempt(client, purpose='link')
    assert client.post('/api/v1/auth/login', json={'email': 'test@example.test', 'password': 'Very-safe-test-123'}).status_code == 200
    assert client.post(f'/api/v1/auth/identity/{attempt}/finish').status_code == 403
    with SessionLocal() as db:
        assert db.get(Doctor, other['id']).identity_hash is None
        assert db.get(Doctor, doctor['id']).identity_hash == digest('IIN000000000001')


def test_xml_verification_resumes_registered_document_after_network_failure(client, monkeypatch):
    from app.db import IdentityAttempt, now
    from app.identity import run_qr
    from app.consent_sigex import ConsentVerificationError
    settings().sigex_enabled = True
    calls = []
    def verified(signature, expected, expected_iin, *, checkpoint=None, resume=None):
        calls.append(resume)
        if not resume:
            checkpoint({'documentId': 'synthetic-private-document', 'signId': 1})
            raise ConsentVerificationError('Временная ошибка проверки', retryable=True)
        assert resume['documentId'] == 'synthetic-private-document'
        return {'identity': 'IIN000000000001', 'name': 'Тестовый Врач'}
    monkeypatch.setattr('app.identity.verify_xml', verified)
    attempt = client.post('/api/v1/auth/identity/eds/start', json={'purpose': 'register', 'iin': '000000000001'}).json()
    path = '/api/v1/auth/identity/' + attempt['id']
    body = {'signature': '<signed>Синтетическая подпись</signed>'}
    result = client.post(path + '/signature', json=body)
    assert result.status_code == 200 and result.json()['state'] == 'verifying'
    assert client.post(path + '/signature', json=body).json()['state'] == 'verifying'
    assert len(calls) == 1
    assert client.get('/api/v1/auth/me').status_code == 401
    status = client.get(path).json()
    assert status['state'] == 'verifying' and status['expires_at'] > attempt['expires_at']
    assert 'Временная ошибка' in status['error']
    with SessionLocal() as db:
        saved = db.get(IdentityAttempt, attempt['id'])
        assert saved.data['signature'] == body['signature']
        saved.data = {**saved.data, 'retry_after': 0}
        db.commit()
    run_qr(attempt['id'])
    assert len(calls) == 2 and calls[1]['signId'] == 1
    assert client.get(path).json()['state'] == 'signed'
    assert client.post(path + '/finish').status_code == 200


def test_failed_signature_reports_rejection_instead_of_false_expiration(client, monkeypatch):
    from app.consent_sigex import ConsentVerificationError
    settings().sigex_enabled = True
    def rejected(*args, **kwargs):
        raise ConsentVerificationError('SIGEX: подпись отклонена')
    monkeypatch.setattr('app.identity.verify_xml', rejected)
    attempt = client.post('/api/v1/auth/identity/eds/start', json={'purpose': 'login'}).json()
    path = '/api/v1/auth/identity/' + attempt['id']
    assert client.post(path + '/signature', json={'signature': 'invalid-signature-value-123'}).status_code == 401
    result = client.get(path)
    assert result.status_code == 401
    assert result.json()['detail'] == 'SIGEX: подпись отклонена'
    assert client.get('/api/v1/auth/me').status_code == 401
