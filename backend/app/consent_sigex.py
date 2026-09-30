"""Проверка именно CMS вложенного PDF через приватную регистрацию SIGEX.

SIGEX подтверждает сертификат на момент регистрации. HTTP 200 сам по себе
не означает успех: провайдер также возвращает ошибки в JSON с message.
"""
import base64
import hashlib
import re
import secrets
from urllib.parse import urlparse
import httpx
from .config import settings


class ConsentVerificationError(Exception):
    def __init__(self, message, *, retryable=False):
        super().__init__(message)
        self.retryable = retryable


def verify_document_signature(pdf_bytes, cms_base64, expected_iin, **kwargs):
    if not re.fullmatch(r'[0-9]{12}', expected_iin):
        raise ConsentVerificationError('ИИН пациента в документе некорректен')
    try:
        cms = base64.b64decode(cms_base64, validate=True)
        if len(cms) < 20 or len(cms) > 1_500_000:
            raise ValueError()
    except (ValueError, TypeError):
        raise ConsentVerificationError('Некорректный формат CMS подписи') from None
    return register_signature(pdf_bytes, cms_base64, expected_iin, **kwargs)


def register_signature(pdf_bytes, signature, expected_iin, sign_type='cms', title='Smart Consult — согласие пациента на запись и обработку данных', checkpoint=None, resume=None):
    base = settings().sigex_url.rstrip('/')
    target = urlparse(base)
    if target.scheme != 'https' or target.username or target.password or target.path not in ('', '/'):
        raise ConsentVerificationError('На сервере указан некорректный адрес SIGEX')
    def checked(client, path, stage, **kwargs):
        try:
            response = client.post(base + path, **kwargs)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError()
            if 'message' in value or 'requestID' in value:
                # Только техническая категория: ответы могут содержать персональные данные.
                raw = str(value.get('message', '')).lower()
                category = 'доступ запрещён' if any(x in raw for x in ('access', 'permission', 'authentication', 'unauthorized')) else 'подпись отклонена'
                if raw == 'this signature has already been submitted':
                    category = 'подпись уже зарегистрирована; начните новую попытку подписания'
                temporary = any(x in raw for x in ('timeout', 'temporar', 'ocsp', 'tsp', 'timestamp', 'connection', 'unavailable'))
                request_id = str(value.get('requestID', ''))
                reference = f' (запрос {request_id})' if re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id) else ''
                raise ConsentVerificationError(f'SIGEX: {stage} — {category}. Подпись не подтверждена{reference}', retryable=temporary)
            return value
        except httpx.HTTPError:
            raise ConsentVerificationError(f'SIGEX: {stage} — временная ошибка связи. Подпись сохранена для повторной проверки', retryable=True) from None
        except (ValueError, TypeError):
            raise ConsentVerificationError(f'SIGEX: {stage} — некорректный ответ. Подпись не подтверждена') from None
    with httpx.Client(timeout=120, follow_redirects=False) as client:
        registered = resume or checked(client, '/api', 'регистрация подписи', json={
            'signType': sign_type, 'signature': signature,
            'title': title,
            'description': 'Электронная подпись документа Smart Consult',
            'settings': {'private': True, 'signaturesLimit': 1, 'strictSignersRequirements': True,
                         'signersRequirements': [{'iin': 'IIN' + expected_iin, 'ca': 'nca'}],
                         'publicDuringPreregistration': False, 'publicWhileLessThanSignatures': 0,
                         'forceArchive': False, 'tempStorageAfterRegistration': 0}})
        try:
            document_id, sign_id = registered['documentId'], registered['signId']
            attached = base64.b64decode(registered['data'], validate=True) if sign_type == 'cms' else pdf_bytes
            if (not isinstance(document_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,200}', document_id)
                    or type(sign_id) is not int or sign_id <= 0 or not secrets.compare_digest(attached, pdf_bytes)):
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ConsentVerificationError('Подпись не содержит точный PDF согласия, подготовленный сервером') from None
        if checkpoint: checkpoint(registered)
        associated = checked(client, f'/api/{document_id}/data', 'сопоставление документа', content=pdf_bytes,
                             headers={'Content-Type': 'application/octet-stream'})
        # SIGEX документирует 0 как неизвестный размер. Проверка /verify ниже
        # обязательна и проверяет подпись именно переданных байтов.
        if (associated.get('documentId') != document_id or type(associated.get('signedDataSize')) is not int
                or associated['signedDataSize'] not in (0, len(pdf_bytes))
                or not isinstance(associated.get('digests'), dict) or not associated['digests']):
            raise ConsentVerificationError('SIGEX не подтвердил содержимое PDF согласия')
        verified = checked(client, f'/api/{document_id}/verify', 'проверка документа', content=pdf_bytes,
                           headers={'Content-Type': 'application/octet-stream'})
        if (verified.get('documentId') != document_id or not isinstance(verified.get('dataArchived'), bool)
                or not isinstance(verified.get('tempStorage'), bool)):
            raise ConsentVerificationError('SIGEX не подтвердил проверку именно этого документа')
    # ИИН подтверждён strictSignersRequirements для первой и единственной подписи.
    return {'verified': True, 'signer_iin': expected_iin, 'provider': 'sigex',
            'provider_document_id': document_id, 'provider_signature_id': sign_id,
            'certificate_verification': 'registration_time', 'strict_signer_requirements': True,
            'document_sha256': hashlib.sha256(pdf_bytes).hexdigest()}
