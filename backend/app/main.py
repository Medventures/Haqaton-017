import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from cryptography.fernet import Fernet
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File, Query, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete, text, or_, inspect
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from .config import settings
from .db import Doctor, Session, ApiKey, Patient, PatientIdentity, Encounter, Job, Audit, db_session, now, uid, audit
from .security import current_doctor, integration_doctor, digest, passwords, search_tokens, registration_allowed, issue_session, owned
from .schemas import Register, Login, PatientInput, Consent, EncounterPatch, EncounterCreate, EncounterStart, LifecycleAction, Consultation, PrivacyReview, Version, Regenerate, CloudAudio, RecordingTranscribe, MuteAudio
from .privacy import redact_segments
from .patient_input import normalize_iin
from .clinical import ai_notice, export_without_ai
from .openai_asr import OPENAI_ASR_MODEL
from .live import router as live_router, require_idle, active_session
from .identity import router as identity_router
from .consent import router as consent_router, consent_summary, revoke_patient_consents, signature_allows_processing
from .diagnoses import catalog, search_diagnoses, by_code
from .history import snapshot
from .lifecycle import shared_patient, meaningful, empty_encounter, draft_token as sign_draft, load_draft, rotate_draft, previous_encounter, capture_allowed, claim_capture, seal, audio_signature_valid

app = FastAPI(title='Smart Consult API', version='0.2.0', docs_url='/api/docs', openapi_url='/api/openapi.json')
app.include_router(identity_router)
app.include_router(consent_router)
app.include_router(live_router)
rate_buckets = defaultdict(deque)
rate_lock = threading.Lock()


@app.middleware('http')
async def security_headers(request: Request, call_next):
    # Секреты сессии только в HttpOnly cookie; browser writes требуют CSRF header.
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and not request.url.path.startswith('/api/v1/integration/'):
        if request.headers.get('x-medhub-request') != '1' or request.headers.get('origin') not in (None, settings().public_origin):
            return JSONResponse({'detail': 'Недопустимый источник запроса'}, 403)
    if request.url.path.startswith('/api/v1/auth/'):
        key = request.client.host if request.client else 'unknown'
        current = time.monotonic()
        with rate_lock:
            for old in list(rate_buckets):
                if not rate_buckets[old] or rate_buckets[old][-1] < current - 60:
                    del rate_buckets[old]
            bucket = rate_buckets[key]
            while bucket and bucket[0] < current - 60:
                bucket.popleft()
            limit = 100 if request.method == 'GET' else 20
            if len(bucket) >= limit:
                return JSONResponse({'detail': 'Слишком много попыток. Подождите минуту'}, 429)
            bucket.append(current)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # FastAPI по умолчанию возвращает input, который может содержать пароль или ПДн.
    return JSONResponse({'detail': [{'loc': e['loc'], 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]}, 422)


@app.exception_handler(IntegrityError)
async def conflict(request, exc):
    return JSONResponse({'detail': 'Запись с таким идентификатором уже существует'}, 409)


def patient_view(p):
    return {'id': p.id, **p.data, 'external_id': p.external_id, 'recording_consent': p.recording_consent, 'cloud_consent': p.cloud_consent,
            'processing_consent': bool(p.recording_consent and p.cloud_consent and p.data.get('cloud_audio_consent') and p.data.get('openai_audio_consent')),
            'consent_signature': consent_summary(p),
            'ai_processing_allowed': bool(p.recording_consent and signature_allows_processing(p)), 'created_at': p.created_at}


def require_patient_signature(db, patient):
    if not signature_allows_processing(patient, db):
        raise HTTPException(403, 'Сначала подпишите согласие личной ЭЦП пациента в его карточке')


def encounter_view(e, db=None, doctor=None):
    result = {k: getattr(e, k) for k in ('id', 'doctor_id', 'patient_id', 'status', 'recording_consent', 'transcript', 'redacted_transcript', 'fields', 'speaker_roles', 'privacy_reviewed', 'version', 'reviewed_at', 'created_at', 'started_at', 'ended_at', 'paused_at', 'paused_seconds', 'recording_deadline', 'sent_at', 'previous_encounter_id')}
    result['fields'] = Consultation.model_validate(e.fields).model_dump()
    result['ai_notice'] = ai_notice()
    result['persisted'] = inspect(e).persistent
    result['read_only'] = bool(doctor and e.doctor_id != doctor.id)
    result['can_edit'] = not result['read_only']
    result['server_time'] = now()
    result['capture_deadline'] = e.recording_deadline
    result['recording_allowed'] = bool(e.recording_consent and not e.ended_at and not e.paused_at and now() < e.recording_deadline and not result['read_only'])
    if not result['persisted']:
        result['draft_token'] = sign_draft(e)
    if db:
        live = active_session(db, e.id) if not result['read_only'] else None
        if not live and not result['read_only']:
            latest = db.scalar(select(Job).where(Job.encounter_id == e.id, Job.kind == 'live_session').order_by(Job.created_at.desc()).limit(1))
            live = latest if latest and latest.state == 'failed' else None
        result['live_session'] = {'id': live.id, 'state': live.state, 'count': len(live.payload['parts'])} if live else None
        physician = db.get(Doctor, e.doctor_id)
        patient = db.get(Patient, e.patient_id)
        result['physician_name'] = result['doctor_name'] = physician.profile.get('name', '') if physician else ''
        result['patient'] = patient_view(patient)
        result['patient_name'], result['patient_iin'] = patient.data.get('name', ''), patient.data.get('iin', '')
        result['recording_allowed'] = result['recording_allowed'] and patient.recording_consent and signature_allows_processing(patient, db)
    if result['read_only']:
        result['transcript'], result['redacted_transcript'], result['speaker_roles'] = [], [], {}
        result['fields']['sources'] = []
    return result


@app.get('/api/v1/diagnoses', tags=['Справочники'])
def diagnoses(q: str = Query('', max_length=150), limit: int = Query(20, ge=1, le=50), doctor=Depends(current_doctor)):
    return {'items': search_diagnoses(q, limit), 'version': catalog()['version'], 'source': catalog()['source'], 'total': len(catalog()['items'])}


def verify_version(e, version):
    if inspect(e).session:
        require_idle(inspect(e).session, e.id)
    if e.version != version:
        raise HTTPException(409, 'Приём изменён. Обновите страницу перед сохранением')
    if e.status == 'processing':
        raise HTTPException(409, 'Дождитесь завершения обработки')


def queue(db, e, kind, payload=None):
    require_idle(db, e.id)
    if e.status == 'processing':
        raise HTTPException(409, 'Обработка уже запущена')
    job = Job(encounter_id=e.id, kind=kind, payload={'version': e.version, 'previous_status': e.status, **(payload or {})})
    e.status = 'processing'
    db.add(job)
    audit(db, e.doctor_id, f'job.{kind}.queued', e.id)
    db.commit()
    return {'job_id': job.id}


@app.get('/api/health', tags=['Система'])
def health(db=Depends(db_session)):
    db.execute(text('SELECT 1'))
    return {'status': 'ok', 'service': 'medhub'}


@app.get('/api/v1/auth/options', tags=['Авторизация'])
def auth_options():
    return {'sigex_enabled': settings().sigex_enabled, 'demo_mode': settings().demo_mode}


@app.post('/api/v1/auth/register', tags=['Авторизация'], status_code=201)
def register(body: Register, response: Response, db=Depends(db_session)):
    registration_allowed(body.code)
    doctor = Doctor(login_hash=digest(body.email), identity_hash=digest('IIN' + body.iin),
        profile={'name': body.name, 'email': body.email, 'phone': body.phone, 'iin': body.iin, 'iin_verified': False},
        password_hash=passwords.hash(body.password))
    db.add(doctor)
    db.flush()
    issue_session(db, doctor, response)
    audit(db, doctor.id, 'doctor.register', doctor.id)
    db.commit()
    return {'id': doctor.id, **doctor.profile}


@app.post('/api/v1/auth/login', tags=['Авторизация'])
def login(body: Login, response: Response, db=Depends(db_session)):
    doctor = db.scalar(select(Doctor).where(Doctor.login_hash == digest(body.email)))
    try:
        if not doctor:
            passwords.hash(body.password)  # Сходная стоимость для неизвестного логина.
            raise VerifyMismatchError()
        passwords.verify(doctor.password_hash, body.password)
    except (VerifyMismatchError, VerificationError):
        raise HTTPException(401, 'Неверный логин или пароль')
    issue_session(db, doctor, response)
    audit(db, doctor.id, 'doctor.login', doctor.id)
    db.commit()
    return {'id': doctor.id, **doctor.profile}


@app.post('/api/v1/auth/logout', tags=['Авторизация'])
def logout(request: Request, response: Response, db=Depends(db_session)):
    db.execute(delete(Session).where(Session.token_hash == digest(request.cookies.get('medhub_session', ''))))
    db.commit()
    response.delete_cookie('medhub_session')
    return {'ok': True}


@app.get('/api/v1/auth/me', tags=['Авторизация'])
def me(doctor=Depends(current_doctor)):
    return {'id': doctor.id, **doctor.profile}


@app.get('/api/v1/settings', tags=['Система'])
def configuration(doctor=Depends(current_doctor)):
    s = settings()
    return {'asr_provider': s.asr_provider, 'asr_model': OPENAI_ASR_MODEL if s.asr_provider == 'openai' else s.asr_model,
            'asr_configured': bool(s.openai_api_key.strip()) if s.asr_provider == 'openai' else s.asr_provider in ('self_hosted', 'faster_whisper'),
            'llm_provider': s.llm_provider, 'llm_model': s.llm_model, 'llm_is_cloud': s.llm_is_cloud,
            'llm_configured': bool(s.openai_api_key.strip()) if s.llm_provider == 'openai' else s.llm_provider != 'disabled',
            'diarization': s.asr_provider == 'openai' or bool(s.diarization_model), 'mis_configured': bool(s.mis_url),
            'cloud_asr_configured': bool(s.cloud_asr_url), 'audio_retention_hours': s.audio_retention_hours,
            'consent_signature_required': s.consent_signature_required, 'sigex_enabled': s.sigex_enabled}


@app.get('/api/v1/patients', tags=['Пациенты'])
def patients(q: str = Query('', max_length=150), offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100), doctor=Depends(current_doctor), db=Depends(db_session)):
    q = q.strip()
    if re.fullmatch(r'[0-9]{12}', normalize_iin(q)):
        q = normalize_iin(q)
    if not q or len(q) < 2:
        return []
    stmt = select(Patient)
    if q:
        if re.fullmatch(r'[0-9]{12}', q):
            stmt = stmt.where(Patient.iin_hash == digest(q))
        elif len(q.strip()) < 2:
            return []
        else:
            words = re.findall(r'\w+', q.casefold())
            if not words:
                return []
            for word in words:
                stmt = stmt.where(Patient.search_tokens.contains(digest(word)))
    return [patient_view(p) for p in db.scalars(stmt.order_by(Patient.created_at.desc(), Patient.id).offset(offset).limit(limit))]


@app.post('/api/v1/patients', tags=['Пациенты'], status_code=201)
def create_patient(body: PatientInput, doctor=Depends(current_doctor), db=Depends(db_session)):
    if settings().consent_signature_required and any((body.recording_consent, body.cloud_consent, body.cloud_audio_consent, body.openai_audio_consent)):
        raise HTTPException(403, 'Создайте карточку пациента, затем подпишите согласие его личной ЭЦП')
    if db.scalar(select(Patient.id).where(Patient.iin_hash == digest(body.iin)).limit(1)):
        raise HTTPException(409, 'Пациент с таким ИИН уже существует. Найдите его в общем поиске')
    p = Patient(doctor_id=doctor.id, iin_hash=digest(body.iin), search_tokens=search_tokens(body.name), external_id=body.external_id,
        recording_consent=body.recording_consent, cloud_consent=body.cloud_consent,
        data=body.model_dump(mode='json', exclude={'external_id', 'recording_consent', 'cloud_consent'}))
    db.add(p)
    db.flush()
    db.add(PatientIdentity(iin_hash=p.iin_hash, patient_id=p.id))
    db.flush()
    audit(db, doctor.id, 'patient.create', p.id)
    audit(db, doctor.id, 'consent.granted' if p.recording_consent else 'consent.refused', p.id)
    if body.openai_audio_consent:
        audit(db, doctor.id, 'consent.openai_audio.granted', p.id)
    db.commit()
    return patient_view(p)


@app.get('/api/v1/patients/{patient_id}', tags=['Пациенты'])
def get_patient(patient_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    p = shared_patient(db, patient_id)
    audit(db, doctor.id, 'patient.read', p.id)
    db.commit()
    return patient_view(p)


@app.patch('/api/v1/patients/{patient_id}/consent', tags=['Пациенты'])
def consent(patient_id: str, body: Consent, doctor=Depends(current_doctor), db=Depends(db_session)):
    p = shared_patient(db, patient_id, True)
    if any((body.recording_consent, body.cloud_consent, body.cloud_audio_consent, body.openai_audio_consent)):
        require_patient_signature(db, p)
    if not all((body.recording_consent, body.cloud_consent, body.cloud_audio_consent, body.openai_audio_consent)):
        revoke_patient_consents(db, p, doctor.id)
    p.recording_consent, p.cloud_consent = body.recording_consent, body.cloud_consent
    p.data = {**p.data, 'cloud_audio_consent': body.cloud_audio_consent, 'openai_audio_consent': body.openai_audio_consent,
              'processing_consent': bool(body.processing_consent)}
    audit(db, doctor.id, 'consent.openai_audio.granted' if body.openai_audio_consent else 'consent.openai_audio.revoked', p.id)
    audit(db, doctor.id, 'consent.granted' if body.recording_consent else 'consent.revoked', p.id)
    # Отзыв действует также на уже созданные приёмы и будущие cloud-задания.
    for e in db.scalars(select(Encounter).where(Encounter.patient_id == p.id)):
        e.privacy_reviewed = False
        if not body.recording_consent or e.ended_at is None:
            e.recording_consent = body.recording_consent
    db.commit()
    return patient_view(p)


@app.get('/api/v1/patients/{patient_id}/encounters', tags=['Приёмы'])
def patient_encounters(patient_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    shared_patient(db, patient_id)
    return [encounter_view(e, db, doctor) for e in db.scalars(select(Encounter).where(Encounter.patient_id == patient_id,
        or_(Encounter.doctor_id == doctor.id, Encounter.status.in_(['approved', 'exported']))).order_by(Encounter.created_at.desc()).limit(100))]


@app.post('/api/v1/patients/{patient_id}/encounters', tags=['Приёмы'], status_code=201)
def start_encounter(patient_id: str, body: EncounterStart | None = None, doctor=Depends(current_doctor), db=Depends(db_session)):
    p = shared_patient(db, patient_id)
    body = body or EncounterStart()
    previous_id = previous_encounter(db, patient_id, body.previous_encounter_id, doctor.id)
    e = empty_encounter(doctor.id, p, previous_encounter_id=previous_id)
    e.fields = Consultation(visit_type=body.visit_type).model_dump()
    audit(db, doctor.id, 'encounter.start', e.id)
    db.commit()
    return encounter_view(e, db, doctor)


@app.get('/api/v1/encounters', tags=['Приёмы'])
def my_encounters(status: str = Query('', max_length=30), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), doctor=Depends(current_doctor), db=Depends(db_session)):
    query = select(Encounter).where(Encounter.doctor_id == doctor.id)
    if status:
        if status == 'unreviewed':
            query = query.where(Encounter.reviewed_at.is_(None))
        elif status == 'reviewed':
            query = query.where(Encounter.reviewed_at.is_not(None))
        elif status in ('draft', 'ready', 'approved', 'exported', 'processing'):
            query = query.where(Encounter.status == status)
        else:
            raise HTTPException(422, 'Неизвестный статус')
    return [encounter_view(e, db, doctor) for e in db.scalars(query.order_by(Encounter.created_at.desc(), Encounter.id).offset(offset).limit(limit))]


@app.get('/api/v1/encounters/{encounter_id}', tags=['Приёмы'])
def get_encounter(encounter_id: str, draft_token: str | None = Query(None, max_length=5000), doctor=Depends(current_doctor), db=Depends(db_session)):
    e = db.get(Encounter, encounter_id)
    if not e:
        e = load_draft(db, encounter_id, doctor, draft_token)
        concurrent = db.get(Encounter, encounter_id)
        if concurrent is None:
            return {**encounter_view(e, db, doctor), 'last_job': None}
        e = concurrent
    if e.doctor_id != doctor.id and e.status not in ('approved', 'exported'):
        raise HTTPException(404, 'Приём не найден')
    latest = db.scalar(select(Job).where(Job.encounter_id == e.id, Job.kind != 'revision').order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
    return {**encounter_view(e, db, doctor), 'last_job': {'id': latest.id, 'kind': latest.kind, 'state': latest.state, 'error': latest.error, 'stage': latest.payload.get('stage')} if latest and e.doctor_id == doctor.id else None}


@app.put('/api/v1/encounters/{encounter_id}', tags=['Приёмы'])
def create_encounter(encounter_id: str, body: EncounterCreate, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = load_draft(db, encounter_id, doctor, body.draft_token)
    if db.get(Encounter, encounter_id):
        raise HTTPException(409, 'Приём уже сохранён. Обновите его перед редактированием')
    if body.fields.diagnosis_code and body.fields.diagnosis_code not in by_code():
        raise HTTPException(422, 'Выберите существующий код диагноза из справочника')
    e.fields, e.speaker_roles = body.fields.model_dump(), body.speaker_roles
    e.transcript = [segment.model_dump() for segment in body.transcript]
    e.redacted_transcript = redact_segments(e.transcript, db.get(Patient, e.patient_id).data)
    if 'previous_encounter_id' in body.model_fields_set:
        e.previous_encounter_id = previous_encounter(db, e.patient_id, body.previous_encounter_id, doctor.id, e.id)
    if meaningful(e.fields, e.transcript):
        db.add(e)
        db.flush()
        snapshot(db, e, 'create')
        audit(db, doctor.id, 'encounter.create', e.id)
        db.commit()
    return encounter_view(e, db, doctor)


@app.patch('/api/v1/encounters/{encounter_id}', tags=['Приёмы'])
def edit_encounter(encounter_id: str, body: EncounterPatch, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    verify_version(e, body.version)
    if body.fields.diagnosis_code and body.fields.diagnosis_code not in by_code():
        raise HTTPException(422, 'Выберите существующий код диагноза из справочника')
    snapshot(db, e, 'before_edit')
    if 'previous_encounter_id' in body.model_fields_set:
        e.previous_encounter_id = previous_encounter(db, e.patient_id, body.previous_encounter_id, doctor.id, e.id)
    e.fields = body.fields.model_dump()
    e.speaker_roles = body.speaker_roles
    if body.transcript is not None:
        transcript = [x.model_dump() for x in body.transcript]
        # Повторная отправка неизменённой расшифровки не отменяет ручное маскирование.
        if transcript != e.transcript:
            e.transcript = transcript
            e.redacted_transcript = redact_segments(e.transcript, db.get(Patient, e.patient_id).data)
            e.privacy_reviewed = False
    e.status, e.reviewed_at = 'draft', None
    e.version += 1
    snapshot(db, e, 'edit')
    audit(db, doctor.id, 'encounter.edit', e.id)
    db.commit()
    return encounter_view(e, db, doctor)


@app.post('/api/v1/encounters/{encounter_id}/audio', tags=['Приёмы'], status_code=202)
async def upload_audio(encounter_id: str, file: UploadFile = File(...), analyze: bool = Form(False), draft_token: str | None = Form(None), capture_token: str | None = Form(None), doctor=Depends(current_doctor), db=Depends(db_session)):
    e = db.scalar(select(Encounter).where(Encounter.id == encounter_id).with_for_update())
    if e and e.doctor_id != doctor.id:
        raise HTTPException(404, 'Приём не найден')
    if not e:
        e = load_draft(db, encounter_id, doctor, draft_token)
        if db.get(Encounter, encounter_id):
            raise HTTPException(409, 'Приём уже сохранён. Обновите страницу')
    capture_allowed(e, capture_token)
    require_idle(db, e.id)
    p = db.get(Patient, e.patient_id)
    require_patient_signature(db, p)
    if not e.recording_consent or not p.recording_consent:
        raise HTTPException(403, 'Пациент не согласился на запись')
    if e.status == 'processing':
        raise HTTPException(409, 'Дождитесь завершения обработки')
    if settings().asr_provider in ('disabled', 'cloud'):
        raise HTTPException(503, 'Настройте локальное распознавание или доверенный ASR-сервер. Отправка исходного аудио в облако заблокирована.')
    if settings().asr_provider == 'openai':
        if not p.data.get('openai_audio_consent'):
            raise HTTPException(403, 'В карте пациента нужно согласие на запись и обработку данных, включая передачу исходной записи в OpenAI.')
        if not settings().openai_api_key.strip():
            raise HTTPException(503, 'Добавьте OPENAI_API_KEY на сервере и перезапустите API и worker.')
    if analyze and settings().llm_is_cloud and not p.cloud_consent:
        raise HTTPException(403, 'Для анализа нужно согласие пациента на обработку обезличенного текста облачной LLM')
    mime = (file.content_type or '').split(';')[0]
    if mime not in ('audio/webm', 'audio/wav', 'audio/x-wav', 'audio/ogg', 'audio/mp4', 'video/mp4', 'video/webm', 'audio/mpeg', 'audio/x-m4a', 'audio/flac', 'audio/x-flac'):
        raise HTTPException(415, 'Поддерживается WebM, WAV, OGG, MP4, M4A, FLAC или MP3')
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data.extend(chunk)
        if len(data) > settings().max_audio_mb * 1024 * 1024:
            raise HTTPException(413, 'Запись слишком большая')
    if len(data) < 32:
        raise HTTPException(422, 'Запись пуста')
    if not await run_in_threadpool(audio_signature_valid, data, mime):
        raise HTTPException(422, 'Файл не содержит поддерживаемую аудиозапись')
    capture_allowed(e, capture_token)
    claim_capture(db, e, capture_token)
    # Приём создаётся только после проверки файла и всех согласий.
    if not inspect(e).persistent:
        db.add(e)
        db.flush()
        audit(db, doctor.id, 'encounter.create', e.id)
    folder = Path(settings().audio_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (uid() + '.enc')
    path.write_bytes(Fernet(settings().encryption_key.encode()).encrypt(bytes(data)))
    try:
        return queue(db, e, 'transcribe', {'audio': path.name, 'asr_provider': settings().asr_provider,
            'mime': mime, 'bytes': len(data), 'analyze': analyze, 'stage': 'queued'})
    except Exception:
        path.unlink(missing_ok=True)
        raise


def lifecycle_encounter(db, encounter_id, doctor, body, check_version=True):
    e = db.scalar(select(Encounter).where(Encounter.id == encounter_id).with_for_update())
    if not e:
        draft = load_draft(db, encounter_id, doctor, body.draft_token)
        # Другой запрос мог сохранить приём, пока мы ждали блокировку пациента.
        e = db.scalar(select(Encounter).where(Encounter.id == encounter_id).with_for_update())
        if e is None:
            return draft
    if e.doctor_id != doctor.id:
        raise HTTPException(404, 'Приём не найден')
    if check_version and body.version is not None and e.version != body.version:
        raise HTTPException(409, 'Приём изменился. Обновите страницу')
    return e


def close_encounter(e):
    if e.ended_at is None:
        stamp = now()
        if e.paused_at:
            e.paused_seconds += max(0, stamp - e.paused_at)
        e.paused_at, e.ended_at = None, stamp


@app.post('/api/v1/encounters/{encounter_id}/capture-lease', tags=['Приёмы'])
def capture_lease(encounter_id: str, body: LifecycleAction, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = lifecycle_encounter(db, encounter_id, doctor, body)
    require_idle(db, e.id)
    require_patient_signature(db, db.get(Patient, e.patient_id))
    capture_allowed(e)
    if now() >= e.recording_deadline:
        raise HTTPException(403, 'Окно голосовой записи истекло')
    if not db.get(Patient, e.patient_id).recording_consent:
        raise HTTPException(403, 'Нужно согласие пациента на запись')
    return {'capture_token': seal({'kind': 'capture', 'id': e.id, 'doctor_id': doctor.id, 'deadline': e.recording_deadline, 'issued_at': now(), 'nonce': uid()}),
            'recording_deadline': e.recording_deadline, 'server_time': now()}


@app.post('/api/v1/encounters/{encounter_id}/pause', tags=['Приёмы'])
def pause_encounter(encounter_id: str, body: LifecycleAction, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = lifecycle_encounter(db, encounter_id, doctor, body)
    live = active_session(db, e.id)
    if live and live.state == 'recording':
        raise HTTPException(409, 'Сначала остановите потоковую запись')
    if e.ended_at:
        raise HTTPException(409, 'Приём завершён')
    if not e.paused_at:
        e.paused_at = now()
    if not inspect(e).persistent:
        rotate_draft(db, e)
    audit(db, doctor.id, 'encounter.pause', e.id)
    db.commit()
    return encounter_view(e, db, doctor)


@app.post('/api/v1/encounters/{encounter_id}/resume', tags=['Приёмы'])
def resume_encounter(encounter_id: str, body: LifecycleAction, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = lifecycle_encounter(db, encounter_id, doctor, body)
    if e.ended_at:
        raise HTTPException(409, 'Приём завершён. Создайте новый приём')
    if e.paused_at:
        e.paused_seconds += max(0, now() - e.paused_at)
    e.paused_at = None
    e.recording_consent = db.get(Patient, e.patient_id).recording_consent
    if not inspect(e).persistent:
        rotate_draft(db, e)
    audit(db, doctor.id, 'encounter.resume', e.id)
    db.commit()
    return encounter_view(e, db, doctor)


@app.post('/api/v1/encounters/{encounter_id}/finish', tags=['Приёмы'])
def finish_encounter(encounter_id: str, body: LifecycleAction, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = lifecycle_encounter(db, encounter_id, doctor, body, check_version=False)
    live = active_session(db, e.id)
    if live and live.state == 'recording':
        raise HTTPException(409, 'Сначала остановите запись и отправьте оставшиеся фрагменты')
    close_encounter(e)
    discarded = not inspect(e).persistent
    if discarded:
        audit(db, doctor.id, 'draft.finished', e.id)
    else:
        snapshot(db, e, 'finish')
    audit(db, doctor.id, 'encounter.finish', e.id)
    db.commit()
    return {**encounter_view(e, db, doctor), 'discarded': discarded}


@app.get('/api/v1/encounters/{encounter_id}/pdf', tags=['Приёмы'])
def consultation_pdf(encounter_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    from .consultation_pdf import build_consultation_pdf
    e = db.get(Encounter, encounter_id)
    if not e or (e.doctor_id != doctor.id and e.status not in ('approved', 'exported')):
        raise HTTPException(404, 'Лист консультации не найден')
    patient = db.get(Patient, e.patient_id)
    physician = db.get(Doctor, e.doctor_id)
    document = build_consultation_pdf(encounter_view(e, db, doctor), patient_view(patient), physician.profile)
    audit(db, doctor.id, 'encounter.pdf', e.id)
    db.commit()
    return Response(document, media_type='application/pdf', headers={'Content-Disposition': f'inline; filename="consultation-{e.id}.pdf"'})


@app.post('/api/v1/encounters/{encounter_id}/privacy-review', tags=['Приёмы'])
def privacy_review(encounter_id: str, body: PrivacyReview, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    verify_version(e, body.version)
    e.redacted_transcript = redact_segments([x.model_dump() for x in body.segments], db.get(Patient, e.patient_id).data)
    e.privacy_reviewed = True
    e.version += 1
    audit(db, doctor.id, 'privacy.review', e.id)
    db.commit()
    return encounter_view(e, db, doctor)


def masked_audio_path(db, e):
    job = db.scalar(select(Job).where(Job.encounter_id == e.id, Job.kind == 'transcribe', Job.state == 'done').order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
    name = job.payload.get('masked_audio') if job else None
    path = Path(settings().audio_dir) / Path(name).name if name else None
    if not path or not path.is_file():
        raise HTTPException(410, 'Обезличенная запись недоступна или удалена по сроку хранения')
    return path


@app.get('/api/v1/encounters/{encounter_id}/recordings', tags=['Приёмы'])
def recordings(encounter_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    jobs = list(db.scalars(select(Job).where(Job.encounter_id == e.id, Job.kind == 'transcribe').order_by(Job.created_at.desc(), Job.id.desc())))
    result = []
    for original in jobs:
        if original.payload.get('recording_id'):
            continue
        attempts = [j for j in jobs if j.id == original.id or j.payload.get('recording_id') == original.id]
        # Версия задания различает повторные попытки даже внутри одной секунды.
        latest = max(attempts, key=lambda j: (j.payload.get('attempt', 0), j.created_at, j.id))
        completed = next((j for j in sorted(attempts, key=lambda j: (j.payload.get('attempt', 0), j.created_at), reverse=True)
                          if j.payload.get('result_transcript')), original)
        result.append({'id': original.id, 'created_at': original.created_at, 'state': latest.state, 'error': latest.error,
             'timeline_offset': original.payload.get('timeline_offset', 0), 'duration': original.payload.get('duration'),
             'bytes': original.payload.get('bytes'), 'attempts': len(attempts),
             'available': bool(original.payload.get('audio') and (Path(settings().audio_dir) / Path(original.payload['audio']).name).is_file()),
             'transcript': completed.payload.get('result_transcript', []), 'speaker_roles': completed.payload.get('result_roles', {})})
    return result


@app.post('/api/v1/encounters/{encounter_id}/recordings/{recording_id}/transcribe', tags=['Приёмы'], status_code=202)
def retranscribe_recording(encounter_id: str, recording_id: str, body: RecordingTranscribe, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    verify_version(e, body.version)
    recording = db.get(Job, recording_id)
    if not recording or recording.encounter_id != e.id or recording.kind != 'transcribe':
        raise HTTPException(404, 'Запись не найдена')
    if recording.payload.get('recording_id'):
        recording = db.get(Job, recording.payload['recording_id'])
    name = recording.payload.get('audio')
    if not name or not (Path(settings().audio_dir) / Path(name).name).is_file():
        raise HTTPException(410, 'Исходная запись недоступна')
    p = db.get(Patient, e.patient_id)
    require_patient_signature(db, p)
    if not e.recording_consent or not p.recording_consent:
        raise HTTPException(403, 'Согласие пациента на обработку записи отозвано')
    if settings().asr_provider in ('disabled', 'cloud'):
        raise HTTPException(503, 'Настройте распознавание речи на сервере')
    if settings().asr_provider == 'openai':
        if not p.data.get('openai_audio_consent'):
            raise HTTPException(403, 'Нужно согласие пациента на обработку исходной записи в OpenAI')
        if not settings().openai_api_key.strip():
            raise HTTPException(503, 'Добавьте OPENAI_API_KEY на сервере')
    if body.analyze and settings().llm_is_cloud and not p.cloud_consent:
        raise HTTPException(403, 'Нужно согласие пациента на обработку обезличенного текста облачной LLM')
    previous = list(db.scalars(select(Job).where(Job.encounter_id == e.id, Job.kind == 'transcribe')))
    attempt = max((j.payload.get('attempt', 0) for j in previous if j.id == recording.id or j.payload.get('recording_id') == recording.id), default=0) + 1
    return queue(db, e, 'transcribe', {'audio': Path(name).name, 'asr_provider': settings().asr_provider,
        'mime': recording.payload.get('mime', 'audio/webm'), 'bytes': recording.payload.get('bytes'),
        'analyze': body.analyze, 'stage': 'queued', 'recording_id': recording.id, 'attempt': attempt})


@app.get('/api/v1/encounters/{encounter_id}/recordings/{recording_id}/audio', tags=['Приёмы'])
def recording_audio(encounter_id: str, recording_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    j = db.get(Job, recording_id)
    if not j or j.encounter_id != e.id or j.kind != 'transcribe':
        raise HTTPException(404, 'Запись не найдена')
    name = j.payload.get('audio')
    path = Path(settings().audio_dir) / Path(name).name if name else None
    if not path or not path.is_file():
        raise HTTPException(410, 'Запись недоступна; старые записи могли быть удалены прежней политикой хранения')
    data = Fernet(settings().encryption_key.encode()).decrypt(path.read_bytes())
    audit(db, doctor.id, 'audio.original.read', e.id)
    db.commit()
    return Response(data, media_type=j.payload.get('mime', 'audio/webm'), headers={'Content-Disposition': 'inline; filename="consultation-audio"'})


@app.get('/api/v1/encounters/{encounter_id}/history', tags=['Приёмы'])
def history(encounter_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    return [{'id': j.id, 'created_at': j.created_at, **j.payload} for j in db.scalars(select(Job).where(
        Job.encounter_id == e.id, Job.kind == 'revision').order_by(Job.created_at.desc(), Job.id.desc()).limit(100))]


@app.get('/api/v1/encounters/{encounter_id}/masked-audio', tags=['Приёмы'])
def listen_masked(encounter_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    if not e.recording_consent or not db.get(Patient, e.patient_id).recording_consent:
        raise HTTPException(403, 'Согласие на запись отозвано')
    data = Fernet(settings().encryption_key.encode()).decrypt(masked_audio_path(db, e).read_bytes())
    audit(db, doctor.id, 'audio.masked.read', e.id)
    db.commit()
    return Response(data, media_type='audio/wav', headers={'Content-Disposition': 'inline; filename="redacted.wav"'})


@app.post('/api/v1/encounters/{encounter_id}/mute-audio', tags=['Приёмы'], status_code=202)
def mute(encounter_id: str, body: MuteAudio, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    verify_version(e, body.version)
    require_patient_signature(db, db.get(Patient, e.patient_id))
    if not e.recording_consent or not db.get(Patient, e.patient_id).recording_consent:
        raise HTTPException(403, 'Согласие на запись отозвано')
    if any(i < 0 or i >= len(e.transcript) for i in body.segment_indices):
        raise HTTPException(422, 'Некорректные номера реплик')
    return queue(db, e, 'mute_audio', {'masked_audio': masked_audio_path(db, e).name, 'indices': body.segment_indices})


@app.post('/api/v1/encounters/{encounter_id}/cloud-asr', tags=['Приёмы'], status_code=202)
def cloud_asr(encounter_id: str, body: CloudAudio, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    verify_version(e, body.version)
    p = db.get(Patient, e.patient_id)
    require_patient_signature(db, p)
    if not p.recording_consent or not e.recording_consent or not p.data.get('cloud_audio_consent') or not e.privacy_reviewed or not body.audio_reviewed:
        raise HTTPException(403, 'Нужны согласия пациента, проверка текста и прослушивание обезличенного аудио врачом')
    if not settings().cloud_asr_url:
        raise HTTPException(503, 'CLOUD_ASR_URL не настроен')
    return queue(db, e, 'cloud_asr', {'masked_audio': masked_audio_path(db, e).name})


@app.post('/api/v1/encounters/{encounter_id}/generate', tags=['Приёмы'], status_code=202)
def generate(encounter_id: str, body: Regenerate, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    verify_version(e, body.version)
    from .clinical import DOCUMENT_FIELDS
    if not e.transcript and not any(e.fields.get(key) for key in DOCUMENT_FIELDS - {'visit_type', 'visit_format'}):
        raise HTTPException(422, 'Сначала добавьте расшифровку или сведения о приёме')
    p = db.get(Patient, e.patient_id)
    require_patient_signature(db, p)
    direct_openai = settings().llm_provider == 'openai' and p.data.get('openai_audio_consent')
    if settings().llm_is_cloud and (not p.cloud_consent or (not e.privacy_reviewed and not direct_openai)):
        raise HTTPException(403, 'Для облачной LLM нужны согласие пациента и проверка маскирования врачом')
    return queue(db, e, 'generate', {'target': body.target})


@app.post('/api/v1/encounters/{encounter_id}/approve', tags=['Приёмы'])
def approve(encounter_id: str, body: Version, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    verify_version(e, body.version)
    if not any(isinstance(e.fields.get(k), str) and e.fields[k].strip() for k in ('complaints', 'anamnesis', 'examination', 'diagnosis', 'recommendations')):
        raise HTTPException(422, 'Заполните лист консультации')
    close_encounter(e)
    e.status, e.reviewed_at = 'approved', now()
    e.version += 1
    snapshot(db, e, 'approve')
    audit(db, doctor.id, 'encounter.approve', e.id)
    db.commit()
    return encounter_view(e, db, doctor)


@app.get('/api/v1/jobs/{job_id}', tags=['Приёмы'])
def job_status(job_id: str, doctor=Depends(current_doctor), db=Depends(db_session)):
    j = db.get(Job, job_id)
    if not j:
        raise HTTPException(404, 'Задание не найдено')
    owned(db, Encounter, j.encounter_id, doctor)
    return {'id': j.id, 'state': j.state, 'kind': j.kind, 'error': j.error, 'stage': j.payload.get('stage')}


def export_view(db, e):
    p = db.get(Patient, e.patient_id)
    encounter = encounter_view(e, db)
    # Время интерфейса меняется при каждом GET и нарушает идемпотентность МИС.
    for key in ('server_time', 'recording_allowed', 'persisted', 'read_only', 'can_edit', 'capture_deadline'):
        encounter.pop(key, None)
    return export_without_ai({'schema_version': '1.1', 'encounter': encounter, 'patient': patient_view(p), 'doctor_id': e.doctor_id,
            'recordings_url': f'/api/v1/integration/encounters/{e.id}/recordings'})


@app.get('/api/v1/integration/encounters/{encounter_id}/recordings', tags=['МИС'])
def integration_recordings(encounter_id: str, doctor=Depends(integration_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    if e.status not in ('approved', 'exported'):
        raise HTTPException(404, 'Подтверждённый приём не найден')
    audit(db, doctor.id, 'integration.recordings', e.id)
    db.commit()
    return recordings(encounter_id, doctor, db)


@app.get('/api/v1/integration/encounters/{encounter_id}/recordings/{recording_id}/audio', tags=['МИС'])
def integration_audio(encounter_id: str, recording_id: str, doctor=Depends(integration_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    if e.status not in ('approved', 'exported'):
        raise HTTPException(404, 'Подтверждённый приём не найден')
    return recording_audio(encounter_id, recording_id, doctor, db)


@app.post('/api/v1/encounters/{encounter_id}/send-to-mis', tags=['МИС'], status_code=202)
def send_mis(encounter_id: str, body: Version, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    verify_version(e, body.version)
    if e.status not in ('approved', 'exported') or not e.reviewed_at:
        raise HTTPException(409, 'Сначала подтвердите лист консультации')
    if not settings().mis_url:
        raise HTTPException(503, 'MIS_URL не настроен. Доступна интеграция через API чтения')
    close_encounter(e)
    old_jobs = db.scalars(select(Job).where(Job.encounter_id == e.id, Job.kind == 'export').order_by(Job.created_at.desc()))
    previous_export = next((j.payload.get('export') for j in old_jobs if j.payload.get('version') == e.version and j.payload.get('export')), None)
    return queue(db, e, 'export', {'export': previous_export or export_view(db, e)})


@app.post('/api/v1/integration-key', tags=['МИС'])
def create_key(doctor=Depends(current_doctor), db=Depends(db_session)):
    token = 'mh_' + secrets.token_urlsafe(32)
    db.execute(delete(ApiKey).where(ApiKey.doctor_id == doctor.id))
    db.add(ApiKey(token_hash=digest(token), doctor_id=doctor.id))
    audit(db, doctor.id, 'integration.key.rotate', doctor.id)
    db.commit()
    return {'api_key': token, 'note': 'Сохраните ключ: он показывается один раз. Предыдущий ключ отозван.'}


@app.delete('/api/v1/integration-key', tags=['МИС'])
def revoke_key(doctor=Depends(current_doctor), db=Depends(db_session)):
    db.execute(delete(ApiKey).where(ApiKey.doctor_id == doctor.id))
    audit(db, doctor.id, 'integration.key.revoke', doctor.id)
    db.commit()
    return {'ok': True}


@app.get('/api/v1/integration/encounters', tags=['МИС'])
def integration_list(since: int = Query(0, ge=0), offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), patient_id: str | None = None, doctor=Depends(integration_doctor), db=Depends(db_session)):
    q = select(Encounter).where(Encounter.doctor_id == doctor.id, Encounter.status.in_(['approved', 'exported']), Encounter.reviewed_at >= since)
    if patient_id:
        q = q.where(Encounter.patient_id == patient_id)
    rows = list(db.scalars(q.order_by(Encounter.reviewed_at, Encounter.id).offset(offset).limit(limit + 1)))
    items = [export_view(db, e) for e in rows[:limit]]
    audit(db, doctor.id, 'integration.list', doctor.id)
    db.commit()
    return {'items': items, 'next_offset': offset + limit if len(rows) > limit else None}


@app.get('/api/v1/integration/encounters/{encounter_id}', tags=['МИС'])
def integration_get(encounter_id: str, doctor=Depends(integration_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    if e.status not in ('approved', 'exported'):
        raise HTTPException(404, 'Подтверждённый приём не найден')
    result = export_view(db, e)
    audit(db, doctor.id, 'integration.read', e.id)
    db.commit()
    return result


@app.get('/api/v1/audit', tags=['Система'])
def audit_log(doctor=Depends(current_doctor), db=Depends(db_session)):
    return [{'action': x.action, 'object_id': x.object_id, 'created_at': x.created_at} for x in db.scalars(select(Audit).where(Audit.doctor_id == doctor.id).order_by(Audit.created_at.desc()).limit(100))]
