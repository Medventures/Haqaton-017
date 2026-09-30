import hashlib
import hmac
import re
import secrets
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from .config import settings
from .db import Session, ApiKey, Doctor, db_session, now

passwords = PasswordHasher()


def digest(value):
    return hmac.new(settings().index_key.encode(), value.strip().casefold().encode(), hashlib.sha256).hexdigest()


def search_tokens(name):
    return ' '.join(sorted({digest(word[:i]) for word in re.findall(r'\w+', name.casefold()) for i in range(2, len(word) + 1)}))


def registration_allowed(code):
    s = settings()
    if not s.demo_mode and (not s.registration_code or not secrets.compare_digest(code, s.registration_code)):
        raise HTTPException(403, 'Для регистрации нужен код приглашения клиники')


def current_doctor(request: Request, db=Depends(db_session)):
    token = request.cookies.get('medhub_session', '')
    session = db.get(Session, digest(token)) if token else None
    if not session or session.expires_at <= now():
        raise HTTPException(401, 'Войдите в кабинет')
    return db.get(Doctor, session.doctor_id)


def integration_doctor(request: Request, db=Depends(db_session)):
    authorization = request.headers.get('authorization', '')
    token = authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
    key = db.get(ApiKey, digest(token)) if token else None
    if not key:
        raise HTTPException(401, 'Требуется API-ключ МИС')
    return db.get(Doctor, key.doctor_id)


def issue_session(db, doctor, response):
    token = secrets.token_urlsafe(32)
    db.add(Session(token_hash=digest(token), doctor_id=doctor.id, expires_at=now() + 28800))
    response.set_cookie('medhub_session', token, httponly=True, secure=settings().secure_cookies, samesite='strict', max_age=28800)


def owned(db, model, object_id, doctor, lock=False):
    query = select(model).where(model.id == object_id, model.doctor_id == doctor.id)
    if lock:
        query = query.with_for_update()
    value = db.scalar(query)
    if value is None:
        raise HTTPException(404, 'Запись не найдена')
    return value
