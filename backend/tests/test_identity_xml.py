import base64
from datetime import datetime, timedelta, timezone
import pytest
from lxml import etree
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from app.identity_xml import challenge, verify_xml, DS, C14N
from app.consent_sigex import ConsentVerificationError


@pytest.fixture(scope='module')
def certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.SERIAL_NUMBER, 'IIN000000000001'),
                      x509.NameAttribute(NameOID.COMMON_NAME, 'Синтетический Врач')])
    stamp = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(stamp).not_valid_after(stamp + timedelta(days=1)).sign(key, hashes.SHA256()))
    return base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()


def envelope(certificate):
    xml = challenge('login', 'https://example.test', 1900000000)
    root = etree.fromstring(xml.encode())
    sig = etree.SubElement(root, '{' + DS + '}Signature', nsmap={'ds': DS})
    info = etree.SubElement(sig, '{' + DS + '}SignedInfo')
    etree.SubElement(info, '{' + DS + '}CanonicalizationMethod', Algorithm=C14N)
    ref = etree.SubElement(info, '{' + DS + '}Reference', URI='')
    transforms = etree.SubElement(ref, '{' + DS + '}Transforms')
    etree.SubElement(transforms, '{' + DS + '}Transform', Algorithm=DS + 'enveloped-signature')
    etree.SubElement(transforms, '{' + DS + '}Transform', Algorithm=C14N)
    data = etree.SubElement(etree.SubElement(sig, '{' + DS + '}KeyInfo'), '{' + DS + '}X509Data')
    etree.SubElement(data, '{' + DS + '}X509Certificate').text = certificate
    return xml, root


def test_xml_requires_remote_crypto_and_exact_canonical_content(certificate, monkeypatch):
    xml, root = envelope(certificate)
    calls = []
    def verify(kind, signature, content, iin):
        calls.append(iin)
        assert content == etree.tostring(etree.fromstring(xml.encode()), method='c14n')
        assert etree.fromstring(signature).tag == 'authentication'
        assert kind == 'xml'
        return {'verified': True, 'signer_iin': iin}
    monkeypatch.setattr('app.identity_xml.verify_local', verify)
    assert verify_xml(etree.tostring(root).decode(), xml)['identity'] == 'IIN000000000001'
    assert calls == ['000000000001']
    def rejected(*args, **kwargs): raise ConsentVerificationError('invalid_signature')
    monkeypatch.setattr('app.identity_xml.verify_local', rejected)
    with pytest.raises(ConsentVerificationError): verify_xml(etree.tostring(root).decode(), xml)


@pytest.mark.parametrize('fault', ['nonce', 'purpose', 'origin', 'expiresAt', 'missing', 'duplicate', 'external', 'nested', 'xxe', 'iin'])
def test_xml_tamper_and_wrapping_rejected_before_network(certificate, monkeypatch, fault):
    xml, root = envelope(certificate)
    sig = root[-1]
    if fault in ('nonce', 'purpose', 'origin', 'expiresAt'): root.find(fault).text = 'tampered'
    if fault == 'missing': root.remove(sig)
    if fault == 'duplicate': root.append(etree.fromstring(etree.tostring(sig)))
    if fault == 'external': sig.find('.//{' + DS + '}Reference').set('URI', 'https://example.test')
    if fault == 'nested': root.remove(sig); etree.SubElement(root, 'wrapper').append(sig)
    signed = etree.tostring(root).decode()
    if fault == 'xxe': signed = '<!DOCTYPE authentication [<!ENTITY x SYSTEM "file:///secret">]>' + signed
    monkeypatch.setattr('app.identity_xml.verify_local', lambda *a, **k: pytest.fail('Must reject before network'))
    with pytest.raises(ValueError): verify_xml(signed, xml, '000000000002' if fault == 'iin' else '')
