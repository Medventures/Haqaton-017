import os
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet
import pytest

test_dir = tempfile.TemporaryDirectory(prefix='medhub-tests-')
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(test_dir.name) / 'test.db').replace('\\', '/')
os.environ['ENCRYPTION_KEY'] = Fernet.generate_key().decode()
os.environ['INDEX_KEY'] = 'test-only-blind-index-key'
os.environ['DEMO_MODE'] = 'true'
os.environ['SECURE_COOKIES'] = 'false'
os.environ['SIGEX_ENABLED'] = 'false'
os.environ['AUDIO_DIR'] = str(Path(test_dir.name) / 'audio')

from app.db import Base, engine
from app.main import app, rate_buckets
from app.config import settings
from fastapi.testclient import TestClient


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    test_dir.cleanup()


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    rate_buckets.clear()
    settings().llm_is_cloud = False
    settings().asr_provider = 'disabled'
    settings().sigex_enabled = False
    settings().consent_signature_required = False
    settings().mis_url = ''
    yield


@pytest.fixture
def client():
    with TestClient(app, headers={'X-Medhub-Request': '1', 'Origin': 'http://localhost:5173'}) as c:
        yield c


@pytest.fixture
def doctor(client):
    r = client.post('/api/v1/auth/register', json={'name': 'Тестовый Врач', 'email': 'test@example.test', 'iin': '000000000001', 'password': 'Very-safe-test-123'})
    assert r.status_code == 201, r.text
    return r.json()
