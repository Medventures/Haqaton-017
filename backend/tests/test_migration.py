from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from app.db import Base


def test_initial_migration_matches_orm(monkeypatch):
    engine = create_engine('sqlite://')
    monkeypatch.setattr('app.db.engine', engine)
    config = Config('alembic.ini')
    command.upgrade(config, 'head')
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {'alembic_version'}
    for name, table in Base.metadata.tables.items():
        assert {c['name'] for c in inspector.get_columns(name)} == set(table.columns.keys())
    engine.dispose()


def test_shared_registry_migration_preserves_existing_duplicate_patients(monkeypatch):
    engine = create_engine('sqlite://')
    monkeypatch.setattr('app.db.engine', engine)
    config = Config('alembic.ini')
    command.upgrade(config, '0001')
    with engine.begin() as db:
        for suffix in ('1', '2'):
            db.execute(text('INSERT INTO doctors (id, login_hash, profile, password_hash, created_at) VALUES (:id, :id, :data, :data, 100)'),
                       {'id': 'doctor-' + suffix, 'data': 'encrypted-placeholder'})
            db.execute(text('INSERT INTO patients (id, doctor_id, iin_hash, search_tokens, data, recording_consent, cloud_consent, created_at) '
                            'VALUES (:id, :doctor, :hash, :data, :data, 0, 0, 100)'),
                       {'id': 'patient-' + suffix, 'doctor': 'doctor-' + suffix, 'hash': 'same-blind-index', 'data': 'encrypted-patient-' + suffix})
        db.execute(text("INSERT INTO encounters (id, doctor_id, patient_id, status, recording_consent, transcript, redacted_transcript, fields, speaker_roles, privacy_reviewed, version, reviewed_at, created_at) "
                        "VALUES ('old-visit', 'doctor-1', 'patient-1', 'approved', 0, 'cipher', 'cipher', 'cipher', 'cipher', 0, 4, 800, 100)"))
    command.upgrade(config, 'head')
    with engine.connect() as db:
        assert db.execute(text('SELECT id, data FROM patients ORDER BY id')).all() == [
            ('patient-1', 'encrypted-patient-1'), ('patient-2', 'encrypted-patient-2')]
        assert db.execute(text('SELECT iin_hash FROM patient_identities')).scalars().all() == ['same-blind-index']
        assert db.execute(text('SELECT started_at, ended_at, recording_deadline, version FROM encounters')).one() == (100, 800, 1000, 4)
    engine.dispose()
