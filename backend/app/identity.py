"""Одноразовый XML входа, проверяемый SIGEX; challenge создаёт только сервер."""
import base64
import re
import secrets
from urllib.parse import urlparse, parse_qs
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from sqlalchemy import select
from .config import settings
from .db import IdentityAttempt, Doctor, SessionLocal, db_session, now, audit
from .schemas import IdentityStart, Signature
from .identity_xml import challenge, verify_xml
from .consent_sigex import ConsentVerificationError
from .signing_tasks import single_attempt
from .security import digest, passwords, registration_allowed, issue_session, current_doctor

router = APIRouter(prefix='/api/v1/auth', tags=['ЭЦП и SIGEX'])


def sigex_request(method, path, **kwargs):
    base = settings().sigex_url.rstrip('/')
    target = path if path.startswith('https://') else base + path
    url = urlparse(target)
    if url.scheme != 'https' or url.netloc != urlparse(base).netloc or url.username or '..' in url.path or not url.path.startswith('/api/'):
        raise ValueError('invalid_sigex_url')
    with httpx.Client(timeout=kwargs.pop('timeout', 20), follow_redirects=False) as client:
        r = client.request(method, target, **kwargs)
        r.raise_for_status()
        if len(r.content) > 1_000_000:
            raise ValueError('sigex_response_too_large')
        value = r.json()
        if not isinstance(value, dict) or 'message' in value or 'requestID' in value:
            raise ValueError('sigex_rejected')
        return value


def qr_request(method, path, expires, requester=None, **kwargs):
    # SIGEX разрешает повтор long-poll при обрыве TCP до получения HTTP ответа.
    import time
    while now() < expires:
        try:
            return (requester or sigex_request)(method, path, timeout=min(30, max(1, expires - now())), **kwargs)
        except httpx.TransportError:
            time.sleep(1)
    raise ValueError('qr_expired')


def verify(attempt, signature, checkpoint=None):
    if attempt.data.get('xml'):
        return verify_xml(signature, attempt.data['xml'], attempt.data.get('expected_iin', ''),
                          checkpoint=checkpoint, resume=attempt.data.get('registered'))
    data = sigex_request('POST', '/api/auth', json={'nonce': attempt.data['nonce'], 'signature': signature, 'external': True})
    identity = data.get('userId', '')
    if not re.fullmatch(r'IIN[0-9]{12}', identity):
        raise ValueError('missing_iin')
    expected = attempt.data.get('expected_iin')
    if expected and not secrets.compare_digest(identity, 'IIN' + expected):
        raise ValueError('iin_mismatch')
    name = next((a['value'] for rdn in data.get('subjectStructure', []) for a in rdn if a.get('oid') == '2.5.4.3' and not a.get('valueInB64')), 'Врач')
    return {'identity': identity, 'name': name}


def attempt_for_browser(db, request, attempt_id, lock=False):
    q = select(IdentityAttempt).where(IdentityAttempt.id == attempt_id)
    a = db.scalar(q.with_for_update() if lock else q)
    if not a or not secrets.compare_digest(a.browser_hash, digest(request.cookies.get('medhub_identity', ''))) or a.state == 'consumed':
        raise HTTPException(410, 'Попытка входа истекла. Начните заново')
    if a.state == 'failed':
        raise HTTPException(401, a.data.get('error') or 'Подпись не прошла проверку. Начните новую попытку')
    if a.expires_at <= now():
        raise HTTPException(410, 'Попытка входа истекла. Начните заново')
    return a


@single_attempt
def run_qr(attempt_id):
    try:
        with SessionLocal() as db:
            a = db.get(IdentityAttempt, attempt_id)
            if not a or a.state != 'pending' or a.expires_at <= now() or not settings().sigex_enabled:
                return
            data, expires = dict(a.data), a.expires_at
            if data.get('retry_after', 0) > now():
                return
        def persist(**values):
            with SessionLocal() as db:
                a = db.scalar(select(IdentityAttempt).where(IdentityAttempt.id == attempt_id).with_for_update())
                if a.state != 'pending' or a.expires_at <= now():
                    raise ValueError('attempt_expired')
                a.data = {**a.data, **values}
                if values.get('signature'):
                    a.expires_at = now() + 900
                db.commit()
        if not data.get('signature'):
            documents = {'signMethod': 'XML', 'version': 1, 'documentsToSign': [{
                'id': 1, 'nameRu': 'Smart Consult — подтверждение входа', 'nameKz': 'Smart Consult — кіруді растау',
                'nameEn': 'Smart Consult — sign in', 'documentXml': data['xml']}]}
            if not data.get('uploaded'):
                uploaded = qr_request('POST', data['dataURL'], expires, json=documents)
                data['signURL'] = uploaded.get('signURL', data['signURL'])
                persist(uploaded=True, signURL=data['signURL'])
            result = qr_request('GET', data['signURL'], expires)
            docs = result.get('documentsToSign', [])
            if result.get('status') == 'CANCELED' or result.get('signMethod') != 'XML' or len(docs) != 1 or docs[0].get('id') != 1:
                raise ValueError('invalid_signature_response')
            data['signature'] = docs[0]['documentXml']
            persist(signature=data['signature'])
        with SessionLocal() as db:
            a = db.get(IdentityAttempt, attempt_id)
            db.expunge(a)
        profile = verify(a, data['signature'], checkpoint=lambda value: persist(registered=value))
        with SessionLocal() as db:
            a = db.scalar(select(IdentityAttempt).where(IdentityAttempt.id == attempt_id).with_for_update())
            if a.state != 'pending' or a.expires_at <= now() or not settings().sigex_enabled:
                return
            a.data = {**a.data, 'profile': profile, 'error': None}
            a.state = 'signed'
            db.commit()
    except Exception as error:
        with SessionLocal() as db:
            a = db.get(IdentityAttempt, attempt_id)
            if a and a.state == 'pending':
                message = str(error) if isinstance(error, ConsentVerificationError) else 'Подпись не прошла проверку документа или ИИН. Начните новую попытку'
                a.data = {**a.data, 'error': message}
                if isinstance(error, ConsentVerificationError) and error.retryable and a.expires_at > now():
                    a.data = {**a.data, 'retry_after': now() + 10}
                    db.commit()
                    return  # Следующий опрос повторит проверку сохранённой подписи.
                a.state = 'failed'
                db.commit()


@router.post('/identity/{method}/start')
def start(method: str, body: IdentityStart, request: Request, response: Response, background: BackgroundTasks, db=Depends(db_session)):
    if not settings().sigex_enabled or method not in ('eds', 'qr'):
        raise HTTPException(503, 'SIGEX отключён')
    if body.purpose == 'register':
        registration_allowed(body.code)
    linked_doctor = current_doctor(request, db) if body.purpose == 'link' else None
    try:
        expires = now() + 300
        xml = challenge(body.purpose, settings().public_origin, expires)
        data = {'xml': xml, 'method': method, 'expected_iin': body.iin}
        if linked_doctor:
            data.update(doctor_id=linked_doctor.id, session_hash=digest(request.cookies.get('medhub_session', '')))
        presentation = {}
        if method == 'qr':
            qr = sigex_request('POST', '/api/egovQr', json={'description': 'Smart Consult — вход в кабинет врача', 'whenDone': {'backUrl': settings().public_origin}})
            png = base64.b64decode(qr['qrCode'], validate=True)
            if len(png) > 400000 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('invalid_qr')
            link = urlparse(qr['eGovMobileLaunchLink'])
            target = urlparse(parse_qs(link.query).get('link', [''])[0])
            if link.scheme != 'https' or link.hostname not in ('m.egov.kz', 'mgovsign.page.link') or target.scheme != 'https' or target.netloc != urlparse(settings().sigex_url).netloc:
                raise ValueError('invalid_launch_url')
            data.update(dataURL=qr['dataURL'], signURL=qr['signURL'])
            expires = min(expires, int(qr['expireAt'] / 1000))
            presentation = {'qr_image': 'data:image/png;base64,' + qr['qrCode'], 'launch_url': qr['eGovMobileLaunchLink']}
            data.update(presentation)
        token = secrets.token_urlsafe(32)
        a = IdentityAttempt(browser_hash=digest(token), capability_hash=digest(secrets.token_urlsafe(32)), purpose=body.purpose, data=data, expires_at=expires)
        db.add(a)
        db.commit()
        response.set_cookie('medhub_identity', token, httponly=True, secure=settings().secure_cookies, samesite='lax', max_age=1200)
        if method == 'qr':
            background.add_task(run_qr, a.id)
        return {'id': a.id, 'document_xml': xml, 'sign_format': 'xml', 'expires_at': expires, **presentation}
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(502, 'SIGEX временно недоступен. Повторите попытку')


@router.get('/identity/{attempt_id}')
def state(attempt_id: str, request: Request, background: BackgroundTasks, db=Depends(db_session)):
    a = attempt_for_browser(db, request, attempt_id)
    if a.state == 'pending' and (a.data.get('method') == 'qr' or a.data.get('signature')):
        background.add_task(run_qr, a.id)
    return {'id': a.id, 'purpose': a.purpose, 'state': 'verifying' if a.state == 'pending' and a.data.get('signature') else a.state,
            'error': a.data.get('error'), 'expires_at': a.expires_at,
            **{k: a.data[k] for k in ('qr_image', 'launch_url') if k in a.data}}


@router.post('/identity/{attempt_id}/signature')
def signature(attempt_id: str, body: Signature, request: Request, db=Depends(db_session)):
    a = attempt_for_browser(db, request, attempt_id, True)
    if a.state != 'pending' or a.data['method'] != 'eds':
        raise HTTPException(409, 'Подпись уже обработана')
    if a.data.get('signature'):
        if not secrets.compare_digest(a.data['signature'].encode(), body.signature.encode()):
            raise HTTPException(409, 'Другая подпись уже принята для проверки')
        return {'state': 'verifying'}
    a.data = {**a.data, 'signature': body.signature}
    a.expires_at = now() + 900
    db.commit()
    # Единый обработчик и блокировка для NCALayer, QR и повторного опроса.
    run_qr(attempt_id)
    db.expire_all()
    a = attempt_for_browser(db, request, attempt_id)
    return {'state': 'signed' if a.state == 'signed' else 'verifying'}


@router.post('/identity/{attempt_id}/finish')
def finish(attempt_id: str, request: Request, response: Response, db=Depends(db_session)):
    a = attempt_for_browser(db, request, attempt_id, True)
    if a.state != 'signed':
        raise HTTPException(409, 'Подписание ещё не завершено')
    p = a.data['profile']
    identity_hash = digest(p['identity'])
    doctor = db.scalar(select(Doctor).where(Doctor.identity_hash == identity_hash))
    if a.purpose == 'link':
        current = current_doctor(request, db)
        if current.id != a.data['doctor_id'] or not secrets.compare_digest(a.data['session_hash'], digest(request.cookies.get('medhub_session', ''))):
            raise HTTPException(403, 'Сессия изменилась. Повторите привязку ИИН')
        current = db.scalar(select(Doctor).where(Doctor.id == current.id).with_for_update())
        if (doctor is not None and doctor.id != current.id) or (current.identity_hash and current.identity_hash != identity_hash):
            a.state, a.data = 'failed', {}
            db.commit()
            raise HTTPException(409, 'ИИН уже связан с другой учётной записью или не совпадает с ИИН вашего кабинета')
        doctor = current
        doctor.identity_hash = identity_hash
    elif doctor is None:
        if a.purpose != 'register':
            a.state, a.data = 'failed', {}
            db.commit()
            raise HTTPException(403, 'Учётная запись с ИИН из подписи не найдена. Зарегистрируйтесь или привяжите ИИН в настройках существующего кабинета')
        doctor = Doctor(login_hash=digest('eds:' + p['identity']), identity_hash=identity_hash, profile={'name': p['name'], 'email': '', 'method': 'sigex'}, password_hash=passwords.hash(secrets.token_urlsafe(64)))
        db.add(doctor)
        db.flush()
    elif a.purpose == 'register':
        a.state, a.data = 'failed', {}
        db.commit()
        raise HTTPException(409, 'Учётная запись с этим ИИН уже существует. Выберите вход')
    doctor.profile = {**doctor.profile, 'iin': p['identity'][3:], 'iin_verified': True}
    a.state = 'consumed'
    a.data = {}
    issue_session(db, doctor, response)
    response.delete_cookie('medhub_identity')
    audit(db, doctor.id, 'identity.link' if a.purpose == 'link' else 'identity.login', doctor.id)
    db.commit()
    return {'id': doctor.id, **doctor.profile}
