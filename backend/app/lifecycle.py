"""Подписанные несохранённые приёмы и неизменяемое окно голосовой записи."""
import json
import subprocess
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from sqlalchemy import select
from .config import settings
from .db import Encounter, Patient, Audit, now, uid, audit
from .schemas import Consultation

RECORDING_SECONDS = 900
UPLOAD_GRACE_SECONDS = 30


def shared_patient(db, patient_id, lock=False):
    stmt = select(Patient).where(Patient.id == patient_id)
    p = db.scalar(stmt.with_for_update() if lock else stmt)
    if p is None:
        raise HTTPException(404, 'Пациент не найден')
    return p


def meaningful(fields, transcript=None):
    ignored = {'visit_type', 'visit_format', 'sources', 'warnings', 'reviewed_fields', 'diagnosis_suggestions'}
    return any(isinstance(value, str) and value.strip() for key, value in fields.items() if key not in ignored) or any(x.get('text', '').strip() for x in (transcript or []))


def seal(payload):
    return Fernet(settings().encryption_key.encode()).encrypt(json.dumps(payload).encode()).decode()


def unseal(token, kind):
    try:
        data = json.loads(Fernet(settings().encryption_key.encode()).decrypt((token or '').encode(), ttl=86400))
        if data['kind'] != kind:
            raise ValueError()
        return data
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise HTTPException(409, 'Черновик недействителен. Начните новый приём')


def draft_token(e):
    return seal({'kind': 'draft', 'id': e.id, 'doctor_id': e.doctor_id, 'patient_id': e.patient_id,
                 'started_at': e.started_at, 'recording_deadline': e.recording_deadline, 'paused_at': e.paused_at, 'paused_seconds': e.paused_seconds,
                 'previous_encounter_id': e.previous_encounter_id, 'visit_type': e.fields.get('visit_type', 'primary'),
                 'visit_format': e.fields.get('visit_format', 'in_person'),
                 'nonce': e._draft_nonce})


def empty_encounter(doctor_id, patient, **values):
    stamp = now()
    e = Encounter(id=uid(), doctor_id=doctor_id, patient_id=patient.id, status='draft', recording_consent=patient.recording_consent,
                  transcript=[], redacted_transcript=[], fields=Consultation().model_dump(), speaker_roles={}, privacy_reviewed=False,
                  version=1, created_at=stamp, started_at=stamp, recording_deadline=stamp + RECORDING_SECONDS, paused_seconds=0)
    for key, value in values.items():
        setattr(e, key, value)
    e._draft_nonce = uid()
    return e


def load_draft(db, encounter_id, doctor, token):
    data = unseal(token, 'draft')
    if data.get('id') != encounter_id or data.get('doctor_id') != doctor.id:
        raise HTTPException(404, 'Приём не найден')
    # Блокировка общей карточки сериализует материализацию, паузу и завершение.
    patient = shared_patient(db, data['patient_id'], True)
    if db.scalar(select(Audit.id).where(Audit.object_id == encounter_id, Audit.action == 'draft.finished')):
        raise HTTPException(409, 'Этот приём уже завершён')
    if db.scalar(select(Audit.id).where(Audit.object_id == data['nonce'], Audit.action == 'draft.superseded')):
        raise HTTPException(409, 'Состояние черновика изменилось. Используйте последнюю версию')
    e = empty_encounter(doctor.id, patient, id=encounter_id, started_at=data['started_at'], created_at=data['started_at'],
                        recording_deadline=data['recording_deadline'], paused_at=data.get('paused_at'), paused_seconds=data.get('paused_seconds', 0),
                        previous_encounter_id=data.get('previous_encounter_id'))
    e.fields = Consultation(visit_type=data.get('visit_type', 'primary'), visit_format=data.get('visit_format', 'in_person')).model_dump()
    e._draft_nonce = data['nonce']
    return e


def rotate_draft(db, e):
    audit(db, e.doctor_id, 'draft.superseded', e._draft_nonce)
    e._draft_nonce = uid()


def previous_encounter(db, patient_id, previous_id, doctor_id, current_id=None):
    if not previous_id:
        return None
    previous = db.get(Encounter, previous_id)
    if not previous or previous.patient_id != patient_id or previous.id == current_id or (
            previous.doctor_id != doctor_id and previous.status not in ('approved', 'exported')):
        raise HTTPException(422, 'Предыдущий приём этого пациента недоступен')
    if current_id:
        seen = {current_id}
        row = previous
        while row:
            if row.id in seen:
                raise HTTPException(422, 'Ссылки на предыдущие приёмы не должны образовывать цикл')
            seen.add(row.id)
            row = db.get(Encounter, row.previous_encounter_id) if row.previous_encounter_id else None
    return previous.id


def capture_allowed(e, token=None):
    if e.ended_at is not None:
        raise HTTPException(409, 'Приём завершён. Добавлять новые записи больше нельзя')
    if e.paused_at is not None:
        raise HTTPException(409, 'Сначала возобновите приём')
    if now() < e.recording_deadline:
        return
    if token:
        payload = unseal(token, 'capture')
        if (payload.get('id') == e.id and payload.get('doctor_id') == e.doctor_id
                and payload.get('deadline') == e.recording_deadline
                and payload.get('issued_at', e.recording_deadline) < e.recording_deadline
                and now() <= e.recording_deadline + UPLOAD_GRACE_SECONDS):
            return
    raise HTTPException(403, '15 минут для голосовой записи истекли. Лист можно заполнить вручную')


def claim_capture(db, e, token):
    if not token:
        return
    payload = unseal(token, 'capture')
    if payload.get('id') != e.id or payload.get('doctor_id') != e.doctor_id or payload.get('deadline') != e.recording_deadline:
        raise HTTPException(403, 'Разрешение относится к другому приёму')
    nonce = payload.get('nonce')
    if not nonce or db.scalar(select(Audit.id).where(Audit.object_id == nonce, Audit.action == 'capture.used')):
        raise HTTPException(409, 'Эта запись уже загружена. Начните новую запись в доступное время')
    audit(db, e.doctor_id, 'capture.used', nonce)


def audio_signature_valid(data, mime):
    """Отклоняем пустой/переименованный файл до создания медицинской записи."""
    if mime in ('audio/wav', 'audio/x-wav'):
        import io
        import wave
        try:
            with wave.open(io.BytesIO(data)) as stream:
                return stream.getnframes() > 0 and bool(stream.readframes(1))
        except (wave.Error, EOFError):
            return False
    if mime in ('audio/webm', 'video/webm'):
        signature = data.startswith(b'\x1a\x45\xdf\xa3')
    elif mime == 'audio/ogg':
        signature = data.startswith(b'OggS')
    elif mime in ('audio/flac', 'audio/x-flac'):
        signature = data.startswith(b'fLaC')
    elif mime == 'audio/mpeg':
        signature = data.startswith(b'ID3') or (data[0] == 255 and data[1] & 224 == 224)
    else:
        signature = data[4:8] == b'ftyp'
    if not signature:
        return False
    with tempfile.TemporaryDirectory(prefix='medhub-audio-probe-') as folder:
        source = Path(folder) / 'recording.audio'
        source.write_bytes(data)
        try:
            # Достаточно одного аудиопакета; полный файл не декодируем.
            probe = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-select_streams', 'a:0', '-read_intervals', '%+#1', '-show_entries', 'packet=codec_type,size',
                '-of', 'json', str(source)], capture_output=True, timeout=15, check=True)
            packets = json.loads(probe.stdout).get('packets', [])
            return any(packet.get('codec_type') == 'audio' and int(packet.get('size', 0)) > 0 for packet in packets)
        except FileNotFoundError:
            raise HTTPException(503, 'На сервере требуется ffprobe для проверки аудиозаписи') from None
        except (subprocess.SubprocessError, ValueError, TypeError, OSError):
            return False
