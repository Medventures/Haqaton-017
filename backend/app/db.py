import json
import uuid
from datetime import datetime, timezone
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, String, Text, ForeignKey, Integer, Boolean, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator
from .config import settings


def now():
    return int(datetime.now(timezone.utc).timestamp())


def uid():
    return str(uuid.uuid4())


class Encrypted(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return Fernet(settings().encryption_key.encode()).encrypt(json.dumps(value, ensure_ascii=False).encode()).decode()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return json.loads(Fernet(settings().encryption_key.encode()).decrypt(value.encode()))


class Base(DeclarativeBase):
    pass


class Doctor(Base):
    __tablename__ = 'doctors'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    login_hash: Mapped[str] = mapped_column(String(64), unique=True)
    identity_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    profile: Mapped[dict] = mapped_column(Encrypted)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(default=now)


class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey('doctors.id'))
    expires_at: Mapped[int] = mapped_column(Integer)


class ApiKey(Base):
    __tablename__ = 'api_keys'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey('doctors.id'))
    created_at: Mapped[int] = mapped_column(default=now)


class Patient(Base):
    __tablename__ = 'patients'
    __table_args__ = (UniqueConstraint('doctor_id', 'iin_hash'), UniqueConstraint('doctor_id', 'external_id'))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    doctor_id: Mapped[str] = mapped_column(ForeignKey('doctors.id'), index=True)
    iin_hash: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str | None] = mapped_column(String(100))
    search_tokens: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(Encrypted)
    recording_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    cloud_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(default=now)


class PatientIdentity(Base):
    """Общий реестр ИИН, сохраняющий старые карточки без слияния данных."""
    __tablename__ = 'patient_identities'
    iin_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey('patients.id'), index=True)


class PatientConsent(Base):
    __tablename__ = 'patient_consents'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    patient_id: Mapped[str] = mapped_column(ForeignKey('patients.id'), index=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey('doctors.id'), index=True)
    state: Mapped[str] = mapped_column(String(20), default='draft')
    document_version: Mapped[str] = mapped_column(String(30))
    document_sha256: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(Encrypted)
    verification: Mapped[dict] = mapped_column(Encrypted, default=dict)
    created_at: Mapped[int] = mapped_column(default=now)
    signed_at: Mapped[int | None] = mapped_column(Integer)
    revoked_at: Mapped[int | None] = mapped_column(Integer)


class PatientConsentAttempt(Base):
    __tablename__ = 'patient_consent_attempts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    consent_id: Mapped[str] = mapped_column(ForeignKey('patient_consents.id'), index=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey('doctors.id'), index=True)
    session_hash: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(20), default='pending')
    data: Mapped[dict] = mapped_column(Encrypted, default=dict)
    created_at: Mapped[int] = mapped_column(default=now)
    expires_at: Mapped[int] = mapped_column(Integer)


class Encounter(Base):
    __tablename__ = 'encounters'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    doctor_id: Mapped[str] = mapped_column(ForeignKey('doctors.id'), index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey('patients.id'), index=True)
    status: Mapped[str] = mapped_column(String(30), default='draft')
    recording_consent: Mapped[bool] = mapped_column(Boolean)
    transcript: Mapped[list] = mapped_column(Encrypted, default=list)
    redacted_transcript: Mapped[list] = mapped_column(Encrypted, default=list)
    fields: Mapped[dict] = mapped_column(Encrypted, default=dict)
    speaker_roles: Mapped[dict] = mapped_column(Encrypted, default=dict)
    privacy_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(default=1)
    reviewed_at: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(default=now)
    started_at: Mapped[int] = mapped_column(Integer, default=now)
    ended_at: Mapped[int | None] = mapped_column(Integer)
    paused_at: Mapped[int | None] = mapped_column(Integer)
    paused_seconds: Mapped[int] = mapped_column(Integer, default=0)
    recording_deadline: Mapped[int] = mapped_column(Integer, default=lambda: now() + 900)
    sent_at: Mapped[int | None] = mapped_column(Integer)
    previous_encounter_id: Mapped[str | None] = mapped_column(ForeignKey('encounters.id'), index=True)


class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    encounter_id: Mapped[str] = mapped_column(ForeignKey('encounters.id'), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default='queued')
    error: Mapped[str | None] = mapped_column(String(250))
    payload: Mapped[dict] = mapped_column(Encrypted, default=dict)
    created_at: Mapped[int] = mapped_column(default=now)
    updated_at: Mapped[int] = mapped_column(default=now)


class IdentityAttempt(Base):
    __tablename__ = 'identity_attempts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    browser_hash: Mapped[str] = mapped_column(String(64))
    capability_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default='pending')
    data: Mapped[dict] = mapped_column(Encrypted)
    expires_at: Mapped[int] = mapped_column(Integer)


class Audit(Base):
    __tablename__ = 'audit'
    __table_args__ = (Index('ix_audit_object_action', 'object_id', 'action'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    doctor_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(50))
    object_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[int] = mapped_column(default=now)


# При запуске API/worker ключи обязательны; standalone ASR не использует БД.
Fernet(settings().encryption_key.encode())
if len(settings().index_key) < 24:
    raise RuntimeError('INDEX_KEY должен содержать не менее 24 символов')
engine = create_engine(settings().database_url, pool_pre_ping=True,
    pool_size=settings().database_pool_size, max_overflow=settings().database_max_overflow)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def db_session():
    with SessionLocal() as db:
        yield db


def audit(db, doctor_id, action, object_id):
    db.add(Audit(doctor_id=doctor_id, action=action, object_id=object_id))
