import subprocess
from types import SimpleNamespace
import pytest
from app.eds_local import verify_local
from app.consent_sigex import verify_document_signature, ConsentVerificationError


def test_local_verifier_uses_stdin_and_verified_identity(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert 'synthetic-secret-signature' not in ' '.join(args)
        assert kwargs['input'].startswith(b'cms\n')
        return SimpleNamespace(returncode=0, stdout=b'OK\n000000000001\n' + b'a' * 64)
    monkeypatch.setattr(subprocess, 'run', run)
    proof = verify_local('cms', b'synthetic-secret-signature', b'document', '000000000001')
    assert proof['verified'] and proof['provider'] == 'nca_local'
    assert proof['certificate_verification'] == 'current_time_ocsp'
    assert len(calls) == 1


@pytest.mark.parametrize('code,retry', [('certificate_revoked', False), ('chain_invalid', False),
    ('iin_mismatch', False), ('ocsp_invalid', False), ('signature_invalid', False), ('ocsp_unavailable', True)])
def test_local_rejection_never_becomes_approval(monkeypatch, code, retry):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=2, stdout=('ERROR\n'+code).encode()))
    with pytest.raises(ConsentVerificationError) as error:
        verify_local('cms', b'signature', b'document', '000000000001')
    assert error.value.retryable is retry


def test_local_wrong_iin_and_missing_runtime_fail_closed(monkeypatch):
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b'OK\n000000000002\n'+b'a'*64))
    with pytest.raises(ConsentVerificationError): verify_local('xml', b'signature', b'document', '000000000001')
    def missing(*a, **kw): raise FileNotFoundError()
    monkeypatch.setattr(subprocess, 'run', missing)
    with pytest.raises(ConsentVerificationError) as error: verify_local('cms', b'signature', b'document', '000000000001')
    assert error.value.retryable


def test_consent_uses_local_cms_without_sigex(monkeypatch):
    import base64
    import httpx
    monkeypatch.setattr(httpx, 'Client', lambda *a, **kw: pytest.fail('No SIGEX document registration permitted'))
    def local(kind, signature, content, iin):
        assert (kind, signature, content, iin) == ('cms', b'synthetic-cms-signature-value', b'%PDF-synthetic', '000000000001')
        return {'verified': True, 'provider': 'nca_local', 'signer_iin': iin}
    monkeypatch.setattr('app.eds_local.verify_local', local)
    proof = verify_document_signature(b'%PDF-synthetic', base64.b64encode(b'synthetic-cms-signature-value').decode(), '000000000001')
    assert proof['verified']
