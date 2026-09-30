"""Локальная проверка НУЦ: изолированный JVM-процесс, подпись только через stdin."""
import base64
import hashlib
import os
from pathlib import Path
import re
import subprocess
import threading
from .config import settings
from .consent_sigex import ConsentVerificationError

_slots = threading.BoundedSemaphore(2)
_messages = {
    'signature_invalid': 'ЭЦП не прошла криптографическую проверку',
    'document_mismatch': 'Подписан другой документ',
    'iin_mismatch': 'ИИН владельца ЭЦП не совпадает с ИИН в документе',
    'certificate_invalid': 'Сертификат не предназначен для подписи физического лица',
    'certificate_expired': 'Срок действия сертификата ЭЦП истёк или ещё не наступил',
    'chain_invalid': 'Сертификат ЭЦП не принадлежит доверенной цепочке НУЦ',
    'certificate_revoked': 'Сертификат ЭЦП отозван',
    'ocsp_unknown': 'НУЦ не подтвердил статус сертификата',
    'ocsp_invalid': 'Ответ о статусе сертификата не прошёл проверку',
    'ocsp_stale': 'Ответ о статусе сертификата устарел',
    'ocsp_unavailable': 'Сервис статуса сертификатов НУЦ временно недоступен',
}


def verify_local(kind, signature, content, expected_iin):
    if kind not in ('xml', 'cms') or not re.fullmatch(r'[0-9]{12}', expected_iin):
        raise ConsentVerificationError('Некорректные параметры проверки ЭЦП')
    if not (0 < len(signature) <= 1_500_000 and 0 < len(content) <= 1_500_000):
        raise ConsentVerificationError('Превышен размер документа или подписи')
    root = Path(__file__).resolve().parents[1] / 'verifier'
    classpath = str(root / 'classes') + os.pathsep + str(root / 'lib' / '*')
    payload = '\n'.join((kind, base64.b64encode(signature).decode(), base64.b64encode(content).decode(), expected_iin))
    if not _slots.acquire(timeout=1):
        raise ConsentVerificationError('Проверка ЭЦП занята. Подпись сохранена для повторной проверки', retryable=True)
    try:
        result = subprocess.run([settings().eds_java_command, '-Xmx128m', '-cp', classpath, 'Verifier', str(root / 'trust')],
            input=payload.encode('ascii'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise ConsentVerificationError('Локальная проверка ЭЦП временно недоступна', retryable=True) from None
    finally:
        _slots.release()
    output = result.stdout.decode('ascii', errors='replace').splitlines()
    if result.returncode != 0:
        code = output[1] if len(output) == 2 and output[0] == 'ERROR' else ''
        raise ConsentVerificationError(_messages.get(code, 'Локальный модуль проверки ЭЦП недоступен'),
                                       retryable=code in ('', 'ocsp_unavailable', 'ocsp_stale'))
    if (len(output) != 3 or output[0] != 'OK' or output[1] != expected_iin
            or not re.fullmatch(r'[0-9a-f]{64}', output[2])):
        raise ConsentVerificationError('Локальный модуль не подтвердил владельца ЭЦП')
    return {'verified': True, 'signer_iin': output[1], 'provider': 'nca_local',
            'certificate_sha256': output[2], 'certificate_verification': 'current_time_ocsp',
            'document_sha256': hashlib.sha256(content).hexdigest()}
