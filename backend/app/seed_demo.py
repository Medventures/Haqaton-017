"""Явно запускаемая демонстрация; запрещена при DEMO_MODE=false."""
import os
from sqlalchemy import select
from .config import settings
from .db import SessionLocal, Doctor, Patient
from .security import digest, passwords, search_tokens

if not settings().demo_mode:
    raise SystemExit('Демонстрационные данные разрешены только в DEMO_MODE')
with SessionLocal() as db:
    doctor = db.scalar(select(Doctor).where(Doctor.login_hash == digest('doctor@medhub.local')))
    if doctor is None:
        demo_password = os.environ.get('DEMO_PASSWORD', '')
        if len(demo_password) < 12:
            raise SystemExit('Задайте DEMO_PASSWORD длиной не менее 12 символов в закрытом .env')
        doctor = Doctor(login_hash=digest('doctor@medhub.local'), profile={'name': 'Демо Врач', 'email': 'doctor@medhub.local'}, password_hash=passwords.hash(demo_password))
        db.add(doctor)
        db.flush()
    for index, (name, birth, consent) in enumerate([
        ('Тестовая Алия Сериковна', '1990-04-12', True),
        ('Демонстрационный Арман', '1985-07-23', False),
        ('Макетова Дана', '2001-02-18', True),
    ], 1):
        iin = f'{index:012d}'
        if db.scalar(select(Patient).where(Patient.doctor_id == doctor.id, Patient.iin_hash == digest(iin))):
            continue
        db.add(Patient(doctor_id=doctor.id, iin_hash=digest(iin), search_tokens=search_tokens(name), data={'name': name, 'iin': iin, 'birth_date': birth, 'sex': 'unknown', 'phone': '', 'cloud_audio_consent': False}, recording_consent=consent, cloud_consent=False))
    db.commit()
print('Создан кабинет демонстрации с тремя вымышленными пациентами.')
