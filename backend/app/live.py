"""Порционная запись: зашифрованные фрагменты и идемпотентные подтверждения в очереди."""
import hashlib
import io
import tempfile
import wave
from pathlib import Path
from uuid import UUID
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Response
from pydantic import Field
from sqlalchemy import select, inspect
from .config import settings
from .db import Encounter, Patient, Job, SessionLocal, db_session, now, uid, audit
from .security import current_doctor, owned
from .schemas import Strict, Consultation
from .lifecycle import load_draft, capture_allowed, claim_capture
from .consent import signature_allows_processing
from .privacy import redact_segments, redact_clinical_context
from .clinical import merge_generated_fields
from .history import snapshot
from .providers import transcribe, generate, ProviderError
from .audio_privacy import mask_audio

router = APIRouter(prefix='/api/v1/encounters', tags=['Потоковая запись'])
ACTIVE = ('recording', 'closing')


def active_session(db, encounter_id):
    return db.scalar(select(Job).where(Job.encounter_id == encounter_id, Job.kind == 'live_session', Job.state.in_(ACTIVE)).limit(1))


def require_idle(db, encounter_id):
    if active_session(db, encounter_id):
        raise HTTPException(409, 'Сначала завершите потоковую запись и дождитесь обработки')


def permitted(db, e, provider=None):
    p = db.get(Patient, e.patient_id)
    if not signature_allows_processing(p, db) or not p.recording_consent or not e.recording_consent:
        raise HTTPException(403, 'Нет действующего согласия пациента на запись и обработку')
    if provider and provider != settings().asr_provider:
        raise HTTPException(409, 'Провайдер ASR изменился во время записи')
    if settings().asr_provider in ('disabled', 'cloud'):
        raise HTTPException(503, 'Настройте доверенный ASR или OpenAI')
    if settings().asr_provider == 'openai' and (not p.data.get('openai_audio_consent') or not settings().openai_api_key):
        raise HTTPException(403, 'Для OpenAI нужны согласие пациента и настроенный API-ключ')
    return p


def pcm(data):
    try:
        with wave.open(io.BytesIO(data)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise ValueError()
            frames = wav.readframes(wav.getnframes())
            if not frames or len(frames) != wav.getnframes() * 2 or len(frames) > 32 * 32000:
                raise ValueError()
            return frames
    except (wave.Error, EOFError, ValueError):
        raise HTTPException(422, 'Нужен полный WAV mono PCM16 16000 Гц, не более 32 секунд') from None


def wav_bytes(frames):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(frames)
    return output.getvalue()


def encrypt_audio(data):
    folder = Path(settings().audio_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / (uid() + '.enc')
    path.write_bytes(Fernet(settings().encryption_key.encode()).encrypt(data))
    return path


def read_audio(name):
    return Fernet(settings().encryption_key.encode()).decrypt((Path(settings().audio_dir) / Path(name).name).read_bytes())


def stream_job(db, e, stream_id, lock=False):
    query = select(Job).where(Job.id == str(stream_id), Job.encounter_id == e.id, Job.kind == 'live_session')
    job = db.scalar(query.with_for_update() if lock else query)
    if not job:
        raise HTTPException(404, 'Сеанс записи не найден')
    return job


def pieces(db, session):
    return [db.get(Job, part) for part in session.payload['parts']]


def combined(db, session):
    result = list(session.payload.get('base_transcript', []))
    for job in pieces(db, session):
        if job.state != 'done':
            break  # Не показываем более поздний фрагмент перед ещё не готовым ранним.
        result.extend(job.payload.get('result_transcript', []))
    return result


def view(db, e, session):
    from .main import encounter_view
    parts = pieces(db, session)
    return {'id': session.id, 'state': session.state, 'received': len(parts),
        'completed': sum(j.state == 'done' for j in parts), 'seconds': session.payload.get('seconds', 0),
        'error': session.error or next((j.error for j in parts if j.error), None),
        'transcript': combined(db, session), 'encounter': encounter_view(e, db),
        'job_id': session.payload.get('final_job')}


@router.post('/{encounter_id}/live/{stream_id}/parts', status_code=202)
async def upload_part(encounter_id: str, stream_id: UUID, file: UploadFile = File(...),
                      sequence: int = Form(..., ge=0, le=119), draft_token: str | None = Form(None),
                      capture_token: str | None = Form(None), doctor=Depends(current_doctor), db=Depends(db_session)):
    e = db.scalar(select(Encounter).where(Encounter.id == encounter_id).with_for_update())
    if e and e.doctor_id != doctor.id:
        raise HTTPException(404, 'Приём не найден')
    if not e:
        e = load_draft(db, encounter_id, doctor, draft_token)
    data = await file.read(1_024_045)
    if len(data) > 1_024_044:
        raise HTTPException(413, 'Фрагмент превышает 32 секунды')
    frames = pcm(data)
    digest = hashlib.sha256(data).hexdigest()
    session = db.scalar(select(Job).where(Job.id == str(stream_id)).with_for_update())
    if session and (session.encounter_id != e.id or session.kind != 'live_session'):
        raise HTTPException(409, 'Идентификатор записи занят')
    if session and sequence < len(session.payload['parts']):
        previous = db.get(Job, session.payload['parts'][sequence])
        if previous.payload['sha256'] != digest:
            raise HTTPException(409, 'Номер фрагмента уже занят другим содержимым')
        return {'sequence': sequence, 'received': len(session.payload['parts']), 'duplicate': True}
    capture_allowed(e, capture_token)
    permitted(db, e, session.payload['provider'] if session else None)
    if e.status == 'processing':
        raise HTTPException(409, 'Приём уже обрабатывается')
    if session and session.state != 'recording':
        raise HTTPException(409, 'Сеанс записи закрыт')
    if sequence != (len(session.payload['parts']) if session else 0):
        raise HTTPException(409, 'Отправьте предыдущий фрагмент')
    if not session:
        require_idle(db, e.id)
        claim_capture(db, e, capture_token)
        if not inspect(e).persistent:
            db.add(e); db.flush()
        speaker_base = max((int(s['speaker'].split('_')[-1]) for s in e.transcript), default=-1) + 1
        session = Job(id=str(stream_id), encounter_id=e.id, kind='live_session', state='recording', payload={
            'parts': [], 'seconds': 0, 'analyzed_seconds': 0, 'provider': settings().asr_provider,
            'base_transcript': e.transcript, 'base_roles': e.speaker_roles, 'speaker_base': speaker_base,
            'offset': max((s['end'] for s in e.transcript), default=0), 'deadline': e.recording_deadline})
        db.add(session)
        audit(db, doctor.id, 'live.started', e.id)
    duration = len(frames) / 32000
    if session.payload['seconds'] + duration > 905:
        raise HTTPException(413, 'Превышено окно записи 15 минут')
    path = encrypt_audio(data)
    try:
        part = Job(id=uid(), encounter_id=e.id, kind='live_chunk', payload={
            'session': session.id, 'sequence': sequence, 'audio': path.name, 'sha256': digest,
            'duration': duration, 'offset': session.payload['seconds'] + session.payload['offset']})
        db.add(part)
        session.payload = {**session.payload, 'parts': session.payload['parts'] + [part.id],
                           'seconds': session.payload['seconds'] + duration}
        session.updated_at = now()
        db.commit()
        return {'sequence': sequence, 'received': sequence + 1, 'duplicate': False}
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.get('/{encounter_id}/live/{stream_id}')
def progress(encounter_id: str, stream_id: UUID, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    return view(db, e, stream_job(db, e, stream_id))


@router.get('/{encounter_id}/live/{stream_id}/audio')
def saved_audio(encounter_id: str, stream_id: UUID, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor)
    session = stream_job(db, e, stream_id)
    audio = wav_bytes(b''.join(pcm(read_audio(j.payload['audio'])) for j in pieces(db, session)))
    audit(db, doctor.id, 'audio.live.read', e.id); db.commit()
    return Response(audio, media_type='audio/wav', headers={'Content-Disposition': 'attachment; filename="saved-fragments.wav"'})


class LiveFinish(Strict):
    count: int = Field(ge=1, le=120)
    interrupted: bool = False


def enqueue_finish(db, e, session, interrupted=False):
    job = Job(id=uid(), encounter_id=e.id, kind='live_finalize', payload={'session': session.id})
    db.add(job)
    session.state = 'closing'
    session.error = None
    session.payload = {**session.payload, 'final_job': job.id, 'interrupted': interrupted}
    e.status = 'processing'
    return job


@router.post('/{encounter_id}/live/{stream_id}/finish', status_code=202)
def finish(encounter_id: str, stream_id: UUID, body: LiveFinish, doctor=Depends(current_doctor), db=Depends(db_session)):
    e = owned(db, Encounter, encounter_id, doctor, True)
    session = stream_job(db, e, stream_id, True)
    if body.count != len(session.payload['parts']):
        raise HTTPException(409, 'Не все фрагменты сохранены. Повторите отправку')
    if session.state in ('closing', 'done'):
        return {'job_id': session.payload['final_job']}
    permitted(db, e, session.payload['provider'])
    for part in pieces(db, session):
        if part.state == 'failed':
            part.state, part.error = 'queued', None
    job = enqueue_finish(db, e, session, body.interrupted)
    db.commit()
    return {'job_id': job.id}


def can_analyze(p):
    s = settings()
    return s.llm_provider != 'disabled' and (not s.llm_is_cloud or (
        p.cloud_consent and s.llm_provider == 'openai' and p.data.get('openai_audio_consent')))


def process(job_id):
    """Один worker; результаты промежуточной LLM защищены версией документа."""
    try:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            e = db.get(Encounter, job.encounter_id)
            session = db.get(Job, job.payload['session'])
            p = permitted(db, e, session.payload['provider'])
            metadata, patient_data, kind = dict(job.payload), p.data, job.kind
            base = session.payload['speaker_base'] + metadata.get('sequence', 0) * 4
        if kind == 'live_chunk':
            with tempfile.TemporaryDirectory(prefix='medhub-live-') as directory:
                source = Path(directory) / 'part.wav'
                source.write_bytes(read_audio(metadata['audio']))
                # Тишина сохраняется, но не отправляется в ASR во избежание галлюцинаций.
                import array
                samples = array.array('h', pcm(source.read_bytes()))
                spoken = any(value != 0 for value in samples)
                result = transcribe(source) if spoken else []
            speakers = list(dict.fromkeys(s['speaker'] for s in result))
            if len(speakers) > 4:
                raise ProviderError('В одном фрагменте определено более четырёх голосов. Нужна повторная проверка')
            mapping = {speaker: f'SPEAKER_{base + index:02d}' for index, speaker in enumerate(speakers)}
            result = [{**s, 'speaker': mapping[s['speaker']],
                       'start': metadata['offset'] + min(s['start'], metadata['duration']),
                       'end': metadata['offset'] + min(s['end'], metadata['duration'])} for s in result]
            with SessionLocal() as db:
                job = db.get(Job, job_id); e = db.get(Encounter, job.encounter_id)
                permitted(db, e)
                job.payload = {**job.payload, 'result_transcript': result}
                job.state, job.error, job.updated_at = 'done', None, now()
                db.commit()
            preview(metadata['session'])
        else:
            finalize(job_id)
    except Exception as error:
        message = error.detail if isinstance(error, HTTPException) else str(error) if isinstance(error, ProviderError) else 'Обработка фрагментов не завершена. Запись сохранена; повторите обработку.'
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            job.state, job.error = 'failed', str(message)[:250]
            if job.kind == 'live_finalize':
                session = db.get(Job, job.payload['session'])
                session.state, session.error = 'failed', job.error
                db.get(Encounter, job.encounter_id).status = 'draft'
            db.commit()


def preview(session_id):
    with SessionLocal() as db:
        session = db.get(Job, session_id); e = db.get(Encounter, session.encounter_id)
        p = permitted(db, e)
        parts = pieces(db, session)
        if session.state != 'recording' or any(j.state != 'done' for j in parts):
            return
        duration = session.payload['seconds']
        if duration - session.payload.get('analyzed_seconds', 0) < 60 or not can_analyze(p):
            return
        transcript = combined(db, session)
        if not any(s['text'].strip() for s in transcript):
            return
        version, context = e.version, redact_clinical_context(e.fields, p.data)
        redacted = redact_segments(transcript, p.data)
        session.payload = {**session.payload, 'analyzed_seconds': duration}
        db.commit()
    try:
        result = generate(redacted, context, 'all')
        with SessionLocal() as db:
            e = db.scalar(select(Encounter).where(Encounter.id == e.id).with_for_update())
            session = db.get(Job, session_id)
            p = permitted(db, e)
            if e.version != version or session.state != 'recording' or not can_analyze(p):
                return
            snapshot(db, e, 'before_live_preview')
            e.fields = merge_generated_fields(e.fields, result['fields'])
            e.speaker_roles = {**e.speaker_roles, **result['speaker_roles']}
            e.transcript, e.redacted_transcript = transcript, redacted
            e.version += 1; e.reviewed_at = None; e.privacy_reviewed = False
            snapshot(db, e, 'live_preview')
            db.commit()
    except Exception:
        # Ошибка LLM не делает успешно распознанный фрагмент ошибочным.
        with SessionLocal() as db:
            session = db.get(Job, session_id)
            session.error = 'Промежуточный анализ не завершён. Итоговая попытка будет после остановки.'
            db.commit()


def finalize(job_id):
    archive_path = masked_path = None
    with SessionLocal() as db:
        job = db.get(Job, job_id); session = db.get(Job, job.payload['session'])
        e = db.get(Encounter, job.encounter_id); p = permitted(db, e, session.payload['provider'])
        parts = pieces(db, session)
        if any(j.state in ('queued', 'running') for j in parts):
            job.state = 'queued'; db.commit(); return
        if any(j.state != 'done' for j in parts):
            raise ProviderError('Не все фрагменты распознаны. Повторите обработку сохранённой записи')
        transcript = combined(db, session)
        redacted = redact_segments(transcript, p.data)
        context = redact_clinical_context(e.fields, p.data)
        allow_llm, version = can_analyze(p), e.version
        audio = wav_bytes(b''.join(pcm(read_audio(j.payload['audio'])) for j in parts))
        offset = session.payload['offset']
        local_transcript = [{**s, 'start': s['start'] - offset, 'end': s['end'] - offset}
                            for j in parts for s in j.payload.get('result_transcript', [])]
        local_redacted = redact_segments(local_transcript, p.data)
    try:
        archive_path = encrypt_audio(audio)
        with tempfile.TemporaryDirectory(prefix='medhub-live-final-') as directory:
            source = Path(directory) / 'full.wav'; source.write_bytes(audio)
            indices = [i for i, (raw, masked) in enumerate(zip(local_transcript, local_redacted)) if raw['text'] != masked['text']]
            masked_path = encrypt_audio(mask_audio(source, local_transcript, indices))
        result, analysis_error = None, None
        if allow_llm and any(s['text'].strip() for s in transcript):
            try:
                result = generate(redacted, context, 'all')
            except Exception:
                analysis_error = 'Расшифровка сохранена. Итоговый анализ не завершён; повторите генерацию.'
        with SessionLocal() as db:
            job = db.get(Job, job_id); session = db.get(Job, job.payload['session'])
            e = db.scalar(select(Encounter).where(Encounter.id == job.encounter_id).with_for_update())
            p = permitted(db, e)
            if e.version != version:
                raise ProviderError('Поля изменились во время анализа. Повторите завершение записи')
            snapshot(db, e, 'before_live_final')
            if result and can_analyze(p):
                e.fields = merge_generated_fields(e.fields, result['fields'])
                e.speaker_roles = result['speaker_roles']
            e.transcript, e.redacted_transcript = transcript, redacted
            e.privacy_reviewed, e.reviewed_at, e.status = False, None, 'ready'
            e.version += 1
            # Обычная архивная запись совместима с прослушиванием и повторным ASR.
            db.add(Job(encounter_id=e.id, kind='transcribe', state='done', error=analysis_error, payload={
                'audio': archive_path.name, 'masked_audio': masked_path.name, 'mime': 'audio/wav',
                'bytes': len(audio), 'result_transcript': local_transcript, 'result_roles': e.speaker_roles,
                'asr_provider': session.payload['provider'], 'live_session': session.id, 'version': e.version,
                'timeline_offset': offset, 'duration': session.payload['seconds']}))
            session.state, session.error = 'done', analysis_error
            job.state, job.error, job.updated_at = 'done', analysis_error, now()
            snapshot(db, e, 'live_final'); audit(db, e.doctor_id, 'live.completed', e.id)
            db.commit()
    except Exception:
        if archive_path: archive_path.unlink(missing_ok=True)
        if masked_path: masked_path.unlink(missing_ok=True)
        raise


def recover_and_expire(restart=False):
    with SessionLocal() as db:
        if restart:
            for job in db.scalars(select(Job).where(Job.kind.in_(('live_chunk', 'live_finalize')), Job.state == 'running')):
                job.state = 'queued'
        for session in db.scalars(select(Job).where(Job.kind == 'live_session', Job.state == 'recording')):
            if now() > session.payload['deadline'] + 35:
                e = db.scalar(select(Encounter).where(Encounter.id == session.encounter_id).with_for_update())
                enqueue_finish(db, e, session, True)
        db.commit()
