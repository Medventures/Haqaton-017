"""Одноразовый читаемый XML; криптографию, OCSP и TSP проверяет SIGEX."""
import base64
import copy
import re
import secrets
from datetime import datetime, timezone
from lxml import etree
from cryptography import x509
from cryptography.x509.oid import NameOID
from .consent_sigex import register_signature

DS = 'http://www.w3.org/2000/09/xmldsig#'
C14N = 'http://www.w3.org/TR/2001/REC-xml-c14n-20010315'
EXCLUSIVE = 'http://www.w3.org/2001/10/xml-exc-c14n#'
CANON = {C14N, C14N + '#WithComments', EXCLUSIVE, EXCLUSIVE + 'WithComments'}


def challenge(purpose, origin, expires):
    root = etree.Element('authentication')
    for key, value in [('service', 'Smart Consult'), ('title', 'Подтверждение входа в кабинет врача'),
                       ('purpose', purpose), ('origin', origin), ('nonce', secrets.token_urlsafe(32)),
                       ('expiresAt', datetime.fromtimestamp(expires, timezone.utc).isoformat()),
                       ('notice', 'Эта подпись подтверждает только вход или привязку ИИН. Она не является согласием пациента.')]:
        etree.SubElement(root, key).text = value
    return etree.tostring(root, encoding='unicode')


def parse(value):
    if not isinstance(value, str) or len(value.encode()) > 200000 or '<!DOCTYPE' in value.upper() or '<!ENTITY' in value.upper():
        raise ValueError('invalid_xml')
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
    try:
        root = etree.fromstring(value.encode(), parser)
    except etree.XMLSyntaxError:
        raise ValueError('invalid_xml') from None
    if root.getprevious() is not None or root.getnext() is not None:
        raise ValueError('invalid_xml')
    return root


def fields(root, signature=None):
    if root.tag != 'authentication' or root.attrib or (root.text or '').strip():
        raise ValueError('invalid_authentication')
    result = []
    for child in root:
        if child is signature:
            if (child.tail or '').strip():
                raise ValueError('invalid_authentication')
            continue
        if not isinstance(child.tag, str) or child.attrib or len(child) or '{' in child.tag or (child.tail or '').strip():
            raise ValueError('invalid_authentication')
        result.append((child.tag, child.text or ''))
    return result


def verify_xml(signed, expected, expected_iin='', *, checkpoint=None, resume=None):
    root = parse(signed)
    signatures = root.findall('.//{' + DS + '}Signature')
    if len(signatures) != 1 or signatures[0].getparent() is not root:
        raise ValueError('one_enveloped_signature_required')
    signature = signatures[0]
    if fields(root, signature) != fields(parse(expected)):
        raise ValueError('challenge_mismatch')
    refs = signature.findall('./{' + DS + '}SignedInfo/{' + DS + '}Reference')
    cm = signature.find('./{' + DS + '}SignedInfo/{' + DS + '}CanonicalizationMethod')
    if len(refs) != 1 or refs[0].get('URI') != '' or cm is None or cm.get('Algorithm') not in CANON or signature.findall('./{' + DS + '}Object'):
        raise ValueError('whole_document_signature_required')
    transforms = refs[0].findall('./{' + DS + '}Transforms/{' + DS + '}Transform')
    algorithms = [t.get('Algorithm') for t in transforms]
    if (len(algorithms) not in (1, 2) or algorithms[0] != DS + 'enveloped-signature'
            or (len(algorithms) == 2 and algorithms[1] not in CANON) or any(len(t) for t in transforms)):
        raise ValueError('unsupported_xml_transform')
    certs = signature.findall('./{' + DS + '}KeyInfo/{' + DS + '}X509Data/{' + DS + '}X509Certificate')
    if len(certs) != 1:
        raise ValueError('one_certificate_required')
    cert = x509.load_der_x509_certificate(base64.b64decode(''.join((certs[0].text or '').split()), validate=True))
    serials = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
    if len(serials) != 1 or not re.fullmatch(r'IIN[0-9]{12}', serials[0].value):
        raise ValueError('missing_iin')
    iin = serials[0].value[3:]
    if expected_iin and not secrets.compare_digest(iin, expected_iin):
        raise ValueError('iin_mismatch')
    # Извлечённый ИИН пока недоверенный. SIGEX обязан проверить его у единственного подписанта.
    unsigned = copy.deepcopy(root)
    sig = unsigned.find('./{' + DS + '}Signature')
    previous, tail = sig.getprevious(), sig.tail or ''
    if previous is None: unsigned.text = (unsigned.text or '') + tail
    else: previous.tail = (previous.tail or '') + tail
    unsigned.remove(sig)
    algorithm = algorithms[-1] if len(algorithms) == 2 else C14N
    content = etree.tostring(unsigned, method='c14n', exclusive=algorithm.startswith(EXCLUSIVE), with_comments=algorithm.endswith('WithComments'))
    register_signature(content, etree.tostring(signature, encoding='unicode'), iin, sign_type='xml',
                       title='Smart Consult — одноразовое подтверждение входа', checkpoint=checkpoint, resume=resume)
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {'identity': 'IIN' + iin, 'name': names[0].value if names else 'Врач'}
