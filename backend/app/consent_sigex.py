"""Совместимый интерфейс проверки согласий; криптография выполняется локально НУЦ."""
import base64
import re


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
    from .eds_local import verify_local
    return verify_local('cms', cms, pdf_bytes, expected_iin)
