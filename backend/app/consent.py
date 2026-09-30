"""Согласие пациента: неизменяемый PDF и проверяемая сервером подпись SIGEX."""
import base64
import hashlib
import secrets
from urllib.parse import urlparse, parse_qs
from typing import Literal
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import select
from .config import settings
from .db import Patient, PatientConsent, PatientConsentAttempt, Encounter, Session, SessionLocal, db_session, now, uid, audit
from .security import current_doctor, digest
from .schemas import Strict
from .lifecycle import shared_patient
from .identity import sigex_request, qr_request
from .signing_tasks import single_attempt
from .consent_sigex import verify_document_signature, ConsentVerificationError

router = APIRouter(prefix='/api/v1', tags=['Согласия пациентов'])
DOCUMENT_VERSION = '2026-09-30.1'
ATTEMPT_SECONDS = 300


class PrepareConsent(Strict):
    pass


class StartConsent(Strict):
    method: Literal['eds', 'qr']
    acknowledged: bool


class ConsentSignature(Strict):
    signature: str = Field(min_length=20, max_length=2_000_000)


class RevokeConsent(Strict):
    reason: str = Field(default='', max_length=1000)


def consent_summary(patient):
    cache = patient.data.get('consent_signature')
    return dict(cache) if isinstance(cache, dict) else {'state': 'not_signed', 'verified_at': None}


def signature_allows_processing(patient, db=None):
    if not getattr(settings(), 'consent_signature_required', False):
        return True
    if db is None:
        cache = consent_summary(patient)
        return cache.get('state') == 'signed' and cache.get('version') == DOCUMENT_VERSION and bool(cache.get('verified_at'))
    for consent in db.scalars(select(PatientConsent).where(PatientConsent.patient_id == patient.id,
            PatientConsent.state == 'signed', PatientConsent.document_version == DOCUMENT_VERSION,
            PatientConsent.signed_at.is_not(None))):
        expected = consent.document.get('patient', {}).get('iin', '')
        if (expected and secrets.compare_digest(expected, patient.data.get('iin', ''))
                and consent.verification.get('verified') is True
                and secrets.compare_digest(consent.verification.get('signer_iin', ''), expected)):
            return True
    return False


def consent_view(consent):
    return {'id': consent.id, 'patient_id': consent.patient_id, 'state': consent.state,
            'version': consent.document_version, 'document_sha256': consent.document_sha256,
            'created_at': consent.created_at, 'verified_at': consent.signed_at, 'revoked_at': consent.revoked_at,
            'signer_iin_masked': consent.verification.get('signer_iin_masked'), 'method': consent.verification.get('method'),
            'pdf_url': f'/api/v1/patient-consents/{consent.id}/pdf',
            'signature_url': f'/api/v1/patient-consents/{consent.id}/signature' if consent.signed_at else None}


def patient_consent_view(patient):
    return {'id': patient.id, **patient.data, 'external_id': patient.external_id,
            'recording_consent': patient.recording_consent, 'cloud_consent': patient.cloud_consent,
            'processing_consent': bool(patient.recording_consent and patient.cloud_consent and
                patient.data.get('cloud_audio_consent') and patient.data.get('openai_audio_consent')),
            'ai_processing_allowed': bool(patient.recording_consent and signature_allows_processing(patient)),
            'consent_signature': consent_summary(patient), 'created_at': patient.created_at}


def _permission(db, patient, doctor_id, granted):
    patient.recording_consent = patient.cloud_consent = granted
    patient.data = {**patient.data, 'processing_consent': granted, 'cloud_audio_consent': granted, 'openai_audio_consent': granted}
    for encounter in db.scalars(select(Encounter).where(Encounter.patient_id == patient.id)):
        encounter.privacy_reviewed = False
        if not granted or encounter.ended_at is None:
            encounter.recording_consent = granted
    audit(db, doctor_id, 'consent.granted' if granted else 'consent.revoked', patient.id)


def revoke_patient_consents(db, patient, doctor_id, reason=''):
    """Caller holds patient lock and owns commit; also cancels late QR completion."""
    stamp = now()
    consents = list(db.scalars(select(PatientConsent).where(PatientConsent.patient_id == patient.id).with_for_update()))
    for consent in consents:
        if consent.state != 'revoked':
            consent.state, consent.revoked_at = 'revoked', stamp
            consent.verification = {**consent.verification, 'revocation_reason': reason}
            audit(db, doctor_id, 'consent.document.revoked', consent.id)
        for attempt in db.scalars(select(PatientConsentAttempt).where(PatientConsentAttempt.consent_id == consent.id,
                PatientConsentAttempt.state == 'pending').with_for_update()):
            attempt.state, attempt.data = 'canceled', {'error': 'Согласие отозвано'}
    patient.data = {**patient.data, 'consent_signature': {**consent_summary(patient), 'state': 'revoked', 'revoked_at': stamp}}
    _permission(db, patient, doctor_id, False)


def _consent(db, consent_id):
    consent = db.get(PatientConsent, consent_id)
    if not consent:
        raise HTTPException(404, 'Документ согласия не найден')
    return consent


def _attempt(db, consent_id, attempt_id, doctor_id, session_hash):
    attempt = db.get(PatientConsentAttempt, attempt_id)
    if not attempt or attempt.consent_id != consent_id or attempt.doctor_id != doctor_id or not secrets.compare_digest(attempt.session_hash, session_hash):
        raise HTTPException(404, 'Попытка подписания не найдена в этой сессии')
    return attempt


def _locked(db, consent_id, attempt_id=None):
    consent = _consent(db, consent_id)
    patient = db.scalar(select(Patient).where(Patient.id == consent.patient_id).with_for_update().execution_options(populate_existing=True))
    consent = db.scalar(select(PatientConsent).where(PatientConsent.id == consent_id).with_for_update().execution_options(populate_existing=True))
    attempt = db.scalar(select(PatientConsentAttempt).where(PatientConsentAttempt.id == attempt_id).with_for_update().execution_options(populate_existing=True)) if attempt_id else None
    return patient, consent, attempt


def _expire(db, consent, attempt):
    if attempt.state == 'pending' and attempt.expires_at <= now():
        _, consent, attempt = _locked(db, consent.id, attempt.id)
        if attempt.state == 'pending' and attempt.expires_at <= now():
            attempt.state, attempt.data = 'expired', {'error': 'Срок подписания истёк'}
            if consent.state == 'pending':
                consent.state = 'draft'
            db.commit()


def _reply(consent, attempt, patient=None):
    result = {'id': attempt.id, 'method': attempt.method, 'state': 'verifying' if attempt.state == 'pending' and attempt.data.get('cms') else attempt.state, 'expires_at': attempt.expires_at, 'server_time': now(),
              'consent': consent_view(consent), 'error': attempt.data.get('error')}
    if attempt.state == 'pending':
        result.update({key: attempt.data[key] for key in ('qr_image', 'launch_url') if key in attempt.data})
    if patient:
        result['patient'] = patient_consent_view(patient)
    return result


def _document_bytes(consent):
    document = base64.b64decode(consent.document['pdf_base64'], validate=True)
    if not secrets.compare_digest(hashlib.sha256(document).hexdigest(), consent.document_sha256):
        raise ConsentVerificationError('Документ повреждён. Подготовьте новое согласие')
    return document


def _complete(db, consent_id, attempt_id, cms, verification):
    patient, consent, attempt = _locked(db, consent_id, attempt_id)
    if not settings().sigex_enabled:
        raise HTTPException(503, 'SIGEX отключён')
    if (not attempt or attempt.state != 'pending' or attempt.expires_at <= now()
            or consent.state != 'pending'):
        raise HTTPException(409, 'Попытка завершена, отменена или истекла')
    session = db.get(Session, attempt.session_hash)
    if not session or session.doctor_id != attempt.doctor_id or session.expires_at <= now():
        raise HTTPException(409, 'Сессия врача завершена. Начните новую попытку подписания')
    expected_iin = consent.document['patient']['iin']
    if (verification.get('verified') is not True or not secrets.compare_digest(verification.get('signer_iin', ''), expected_iin)
            or not secrets.compare_digest(patient.data['iin'], expected_iin)
            or not secrets.compare_digest(verification.get('document_sha256', ''), consent.document_sha256)):
        raise ConsentVerificationError('Подпись не принадлежит пациенту из документа')
    stamp = now()
    consent.state, consent.signed_at = 'signed', stamp
    consent.verification = {**verification, 'cms_base64': cms, 'method': attempt.method,
                            'signer_iin_masked': '••••••••' + expected_iin[-4:], 'verified_at': stamp}
    attempt.state, attempt.data = 'signed', {}
    patient.data = {**patient.data, 'consent_signature': {
        'state': 'signed', 'consent_id': consent.id, 'verified_at': stamp,
        'signer_iin_masked': consent.verification['signer_iin_masked'], 'method': attempt.method,
        'document_sha256': consent.document_sha256, 'version': consent.document_version}}
    _permission(db, patient, attempt.doctor_id, True)
    audit(db, attempt.doctor_id, 'consent.document.signed', consent.id)
    db.commit()
    return _reply(consent, attempt, patient)


def _fail(db, consent_id, attempt_id, error):
    patient, consent, attempt = _locked(db, consent_id, attempt_id)
    if attempt and attempt.state == 'pending':
        attempt.state, attempt.data = ('expired' if attempt.expires_at <= now() else 'failed'), {'error': error}
        if consent.state == 'pending':
            consent.state = 'draft'
        audit(db, attempt.doctor_id, 'consent.signature.failed', consent.id)
        db.commit()


@single_attempt
def run_consent_qr(consent_id, attempt_id):
    try:
        with SessionLocal() as db:
            consent = _consent(db, consent_id)
            attempt = db.get(PatientConsentAttempt, attempt_id)
            if not attempt or attempt.state != 'pending' or consent.state != 'pending' or not settings().sigex_enabled:
                return
            if attempt.expires_at <= now():
                _expire(db, consent, attempt)
                return
            data, pdf, expected_iin, expires = dict(attempt.data), _document_bytes(consent), consent.document['patient']['iin'], attempt.expires_at
            if data.get('retry_after', 0) > now():
                return
        documents = {'signMethod': 'CMS_WITH_DATA', 'version': 1, 'documentsToSign': [{
            'id': 1, 'nameRu': 'Smart Consult — согласие пациента на запись и обработку данных',
            'nameKz': 'Smart Consult — пациенттің жазба мен деректерді өңдеуге келісімі',
            'nameEn': 'Smart Consult — patient recording and data processing consent',
            'document': {'file': {'mime': '@file/pdf', 'data': base64.b64encode(pdf).decode()}}}]}
        def persist(**values):
            with SessionLocal() as db:
                _, consent, attempt = _locked(db, consent_id, attempt_id)
                if attempt.state != 'pending' or consent.state != 'pending' or attempt.expires_at <= now():
                    raise ConsentVerificationError('Попытка отменена или истекла')
                attempt.data = {**attempt.data, **values}
                if values.get('cms'):
                    attempt.expires_at = now() + 900  # Подпись получена вовремя; проверка может занять дольше QR.
                db.commit()
        if not data.get('cms'):
            if not data.get('uploaded'):
                uploaded = qr_request('POST', data['dataURL'], expires, json=documents, requester=sigex_request)
                data['signURL'] = uploaded.get('signURL', data['signURL'])
                persist(uploaded=True, signURL=data['signURL'])
            result = qr_request('GET', data['signURL'], expires, requester=sigex_request)
            docs = result.get('documentsToSign', [])
            if result.get('status') == 'CANCELED' or result.get('signMethod') != 'CMS_WITH_DATA' or len(docs) != 1 or docs[0].get('id') != 1:
                raise ConsentVerificationError('Подписание отменено или SIGEX вернул другой документ')
            data['cms'] = docs[0]['document']['file']['data']
            persist(cms=data['cms'])
        cms = data['cms']
        verification = verify_document_signature(pdf, cms, expected_iin,
            resume=data.get('registered'), checkpoint=lambda value: persist(registered=value))
        with SessionLocal() as db:
            _complete(db, consent_id, attempt_id, cms, verification)
    except Exception as error:
        message = str(error) if isinstance(error, ConsentVerificationError) else 'Не удалось завершить подписание. Начните новую попытку'
        with SessionLocal() as db:
            if isinstance(error, ConsentVerificationError) and error.retryable:
                _, consent, attempt = _locked(db, consent_id, attempt_id)
                if attempt.state == 'pending' and attempt.expires_at > now() and attempt.data.get('cms'):
                    attempt.data = {**attempt.data, 'error': message, 'retry_after': now() + 10}
                    db.commit()
                    return
            _fail(db, consent_id, attempt_id, message)


@router.get('/patients/{patient_id}/consents')
def list_consents(patient_id: str, request: Request, doctor=Depends(current_doctor), db=Depends(db_session)):
    patient = shared_patient(db, patient_id)
    rows = list(db.scalars(select(PatientConsent).where(PatientConsent.patient_id == patient_id).order_by(PatientConsent.created_at.desc(), PatientConsent.id.desc()).limit(50)))
    views = []
    for consent in rows:
        if consent.state == 'pending':
            pending = db.scalar(select(PatientConsentAttempt).where(PatientConsentAttempt.consent_id == consent.id,
                PatientConsentAttempt.state == 'pending').limit(1))
            if pending:
                _expire(db, consent, pending)
        view = consent_view(consent)
        attempt = db.scalar(select(PatientConsentAttempt).where(PatientConsentAttempt.consent_id == consent.id,
            PatientConsentAttempt.doctor_id == doctor.id, PatientConsentAttempt.state == 'pending',
            PatientConsentAttempt.expires_at > now(), PatientConsentAttempt.session_hash == digest(request.cookies.get('medhub_session', ''))).limit(1))
        if attempt:
            view['active_attempt'] = {'id': attempt.id, 'state': attempt.state, 'method': attempt.method}
        views.append(view)
    current = next((view for view in views if view['state'] == 'signed'), views[0] if views else None)
    return {'current': current, 'history': views,
            'policy': {'signature_required': getattr(settings(), 'consent_signature_required', False), 'sigex_enabled': settings().sigex_enabled},
            'patient': patient_consent_view(patient)}


@router.post('/patients/{patient_id}/consents', status_code=201)
def prepare_consent(patient_id: str, body: PrepareConsent, doctor=Depends(current_doctor), db=Depends(db_session)):
    from .consent_document import build_consent_pdf
    patient = shared_patient(db, patient_id, True)
    existing = db.scalar(select(PatientConsent).where(PatientConsent.patient_id == patient_id,
        PatientConsent.state.in_(['draft', 'pending']), PatientConsent.document_version == DOCUMENT_VERSION).order_by(PatientConsent.created_at.desc()).limit(1))
    if existing and existing.document['patient']['iin'] == patient.data['iin']:
        return consent_view(existing)
    consent_id, stamp = uid(), now()
    identity = {key: patient.data.get(key, '') for key in ('name', 'iin', 'birth_date')}
    pdf = build_consent_pdf(identity, consent_id, stamp, DOCUMENT_VERSION)
    consent = PatientConsent(id=consent_id, patient_id=patient.id, doctor_id=doctor.id, state='draft',
        document_version=DOCUMENT_VERSION, document_sha256=hashlib.sha256(pdf).hexdigest(),
        document={'pdf_base64': base64.b64encode(pdf).decode(), 'patient': identity, 'nonce': consent_id},
        verification={}, created_at=stamp)
    db.add(consent)
    audit(db, doctor.id, 'consent.document.prepared', consent.id)
    db.commit()
    return consent_view(consent)


@router.get('/patient-consents/{consent_id}/pdf')
def consent_pdf(consent_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    consent = _consent(db, consent_id)
    pdf = _document_bytes(consent)
    audit(db, doctor.id, 'consent.document.read', consent.id)
    db.commit()
    return Response(pdf, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename="patient-consent-{consent.id}.pdf"'})


@router.get('/patient-consents/{consent_id}/signature')
def consent_signature_file(consent_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    consent = _consent(db, consent_id)
    if not consent.signed_at or not consent.verification.get('cms_base64'):
        raise HTTPException(404, 'Проверенная подпись отсутствует')
    cms = base64.b64decode(consent.verification['cms_base64'], validate=True)
    audit(db, doctor.id, 'consent.signature.read', consent.id)
    db.commit()
    return Response(cms, media_type='application/pkcs7-mime', headers={'Content-Disposition': f'attachment; filename="patient-consent-{consent.id}.p7m"'})


@router.post('/patient-consents/{consent_id}/start')
def start_consent(consent_id: str, body: StartConsent, request: Request, background: BackgroundTasks, doctor=Depends(current_doctor), db=Depends(db_session)):
    if not body.acknowledged:
        raise HTTPException(422, 'Подтвердите, что пациент ознакомился с документом')
    if not settings().sigex_enabled:
        raise HTTPException(503, 'SIGEX отключён')
    patient, consent, _ = _locked(db, consent_id)
    if consent.state in ('signed', 'revoked'):
        raise HTTPException(409, 'Документ уже подписан или отозван. Подготовьте новое согласие')
    for previous in db.scalars(select(PatientConsentAttempt).where(PatientConsentAttempt.consent_id == consent_id, PatientConsentAttempt.state == 'pending').with_for_update()):
        if previous.expires_at > now() and (previous.doctor_id != doctor.id or not secrets.compare_digest(previous.session_hash, digest(request.cookies.get('medhub_session', '')))):
            raise HTTPException(409, 'Подписание уже начато в другой сессии. Дождитесь завершения или истечения попытки')
        previous.state, previous.data = 'canceled', {'error': 'Начата новая попытка подписания'}
    data, presentation = {}, {}
    expires = now() + ATTEMPT_SECONDS
    if body.method == 'qr':
        try:
            qr = sigex_request('POST', '/api/egovQr', json={'description': 'Smart Consult — согласие пациента', 'whenDone': {'backUrl': settings().public_origin}})
            png = base64.b64decode(qr['qrCode'], validate=True)
            if len(png) > 400000 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('invalid_qr')
            link = urlparse(qr['eGovMobileLaunchLink'])
            target = urlparse(parse_qs(link.query).get('link', [''])[0])
            if link.scheme != 'https' or link.hostname not in ('m.egov.kz', 'mgovsign.page.link') or target.scheme != 'https' or target.netloc != urlparse(settings().sigex_url).netloc:
                raise ValueError('invalid_launch_url')
            data = {'dataURL': qr['dataURL'], 'signURL': qr['signURL']}
            expires = min(expires, int(qr['expireAt'] / 1000))
            if expires <= now():
                raise ValueError('expired_qr')
            presentation = {'qr_image': 'data:image/png;base64,' + qr['qrCode'], 'launch_url': qr['eGovMobileLaunchLink']}
            data.update(presentation)
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(502, 'SIGEX временно недоступен. Повторите попытку') from None
    else:
        presentation = {'data_base64': consent.document['pdf_base64']}
    attempt = PatientConsentAttempt(consent_id=consent.id, doctor_id=doctor.id,
        session_hash=digest(request.cookies.get('medhub_session', '')), method=body.method, state='pending', data=data, expires_at=expires)
    consent.state = 'pending'
    db.add(attempt)
    audit(db, doctor.id, 'consent.signature.started', consent.id)
    db.commit()
    if body.method == 'qr':
        background.add_task(run_consent_qr, consent.id, attempt.id)
    return {**_reply(consent, attempt), **presentation}


@router.get('/patient-consents/{consent_id}/attempts/{attempt_id}')
def consent_attempt_state(consent_id: str, attempt_id: str, request: Request, background: BackgroundTasks, doctor=Depends(current_doctor), db=Depends(db_session)):
    attempt = _attempt(db, consent_id, attempt_id, doctor.id, digest(request.cookies.get('medhub_session', '')))
    consent = _consent(db, consent_id)
    _expire(db, consent, attempt)
    if attempt.state == 'pending' and (attempt.method == 'qr' or attempt.data.get('cms')):
        background.add_task(run_consent_qr, consent_id, attempt_id)
    return _reply(consent, attempt, db.get(Patient, consent.patient_id))


@router.post('/patient-consents/{consent_id}/attempts/{attempt_id}/signature')
def submit_signature(consent_id: str, attempt_id: str, body: ConsentSignature, request: Request, doctor=Depends(current_doctor), db=Depends(db_session)):
    if not settings().sigex_enabled:
        raise HTTPException(503, 'SIGEX отключён')
    attempt = _attempt(db, consent_id, attempt_id, doctor.id, digest(request.cookies.get('medhub_session', '')))
    consent = _consent(db, consent_id)
    _expire(db, consent, attempt)
    if attempt.state != 'pending' or consent.state != 'pending' or attempt.method != 'eds':
        raise HTTPException(409, 'Попытка завершена, отменена или истекла')
    try:
        pdf, expected_iin = _document_bytes(consent), consent.document['patient']['iin']
        db.rollback()  # Внешняя проверка не удерживает блокировки карточки пациента.
        # Храним результат NCALayer до обращения к SIGEX, как и результат QR.
        _, consent, attempt = _locked(db, consent_id, attempt_id)
        if attempt.state != 'pending' or consent.state != 'pending' or attempt.expires_at <= now() or attempt.data.get('cms'):
            raise HTTPException(409, 'Попытка завершена или подпись уже проверяется')
        attempt.data = {**attempt.data, 'cms': body.signature}
        attempt.expires_at = now() + 900
        db.commit()
        run_consent_qr(consent_id, attempt_id)
        db.expire_all()
        consent, attempt = _consent(db, consent_id), db.get(PatientConsentAttempt, attempt_id)
        if attempt.state in ('canceled', 'expired') or consent.state == 'revoked':
            raise HTTPException(409, 'Попытка завершена, отменена или истекла')
        if attempt.state == 'failed':
            raise HTTPException(401, attempt.data.get('error', 'Подпись отклонена'))
        return _reply(consent, attempt, db.get(Patient, consent.patient_id))
    except ConsentVerificationError as error:
        _fail(db, consent_id, attempt_id, str(error))
        raise HTTPException(401, str(error)) from None
    except (httpx.HTTPError, ValueError, KeyError):
        _fail(db, consent_id, attempt_id, 'Не удалось проверить подпись через SIGEX')
        raise HTTPException(502, 'Не удалось проверить подпись через SIGEX') from None


@router.post('/patient-consents/{consent_id}/attempts/{attempt_id}/cancel')
def cancel_signature(consent_id: str, attempt_id: str, request: Request, doctor=Depends(current_doctor), db=Depends(db_session)):
    _attempt(db, consent_id, attempt_id, doctor.id, digest(request.cookies.get('medhub_session', '')))
    patient, consent, attempt = _locked(db, consent_id, attempt_id)
    if attempt.state == 'pending':
        attempt.state, attempt.data = 'canceled', {'error': 'Подписание отменено'}
        if consent.state == 'pending':
            consent.state = 'draft'
        audit(db, doctor.id, 'consent.signature.canceled', consent.id)
        db.commit()
    return _reply(consent, attempt, patient)


@router.post('/patient-consents/{consent_id}/revoke')
def revoke_consent(consent_id: str, body: RevokeConsent, doctor=Depends(current_doctor), db=Depends(db_session)):
    patient, consent, _ = _locked(db, consent_id)
    revoke_patient_consents(db, patient, doctor.id, body.reason)
    db.commit()
    return {'state': 'revoked', 'consent': consent_view(consent), 'patient': patient_consent_view(patient)}
