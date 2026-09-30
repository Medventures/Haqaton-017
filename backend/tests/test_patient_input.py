from datetime import date
import pytest
from pydantic import ValidationError
from app.schemas import PatientInput, Register, IdentityStart
from app.patient_input import birth_date_from_iin


def test_masks_are_normalized_and_birth_date_is_inferred():
    p = PatientInput(name='Синтетический пациент', iin='900101 300001', phone='+7 (701) 123-45-67')
    assert p.iin == '900101300001'
    assert p.birth_date == date(1990, 1, 1)
    assert p.phone == '+77011234567'
    assert PatientInput(name=p.name, iin=p.iin, phone='87011234567').phone == p.phone
    assert IdentityStart(purpose='register', iin='900101 300001').iin == p.iin
    assert Register(name='Врач', iin='900101 300001', email='synthetic@example.test', password='synthetic-password').iin == p.iin


def test_document_birth_date_has_priority_and_non_date_iin_is_allowed():
    assert PatientInput(name='Тест', iin='900101300001', birth_date='1991-02-03').birth_date == date(1991, 2, 3)
    assert PatientInput(name='Тест', iin='000000009900', birth_date='1991-02-03').birth_date == date(1991, 2, 3)
    assert birth_date_from_iin('000229600001') == date(2000, 2, 29)
    for value in ['010229500001', '990101500001', '900101900001', '900132300001', '900101']:
        assert birth_date_from_iin(value) is None
    with pytest.raises(ValidationError):
        PatientInput(name='Тест', iin='000000009900')


@pytest.mark.parametrize('phone', ['+7 (701) 12', 'abc7011234567', '+17011234567'])
def test_incomplete_or_invalid_phones_are_rejected(phone):
    with pytest.raises(ValidationError):
        PatientInput(name='Тест', iin='900101300001', phone=phone)


def test_patient_api_accepts_masks_and_searches_formatted_iin(client, doctor):
    created = client.post('/api/v1/patients', json={'name': 'Синтетический пациент',
        'iin': '900101 300001', 'phone': '+7 (701) 123-45-67'})
    assert created.status_code == 201
    patient = created.json()
    assert patient['iin'] == '900101300001' and patient['birth_date'] == '1990-01-01'
    assert patient['phone'] == '+77011234567'
    assert client.get('/api/v1/patients', params={'q': '900101 300001'}).json()[0]['id'] == patient['id']


def test_optional_phone_is_empty_or_valid_for_patient_and_doctor():
    from app.schemas import Register
    for phone in ['', '   ', None]:
        assert PatientInput(name='Тест', iin='900101300001', phone=phone).phone == ''
        assert Register(name='Тест', iin='900101300001', email='test@example.test', password='Synthetic-password-123', phone=phone).phone == ''
    for phone in ['abc7011234567', '+7 (701) 12', '+17011234567', '+770112345670']:
        for schema, data in [(PatientInput, {}), (Register, {'email': 'test@example.test', 'password': 'Synthetic-password-123'})]:
            with pytest.raises(ValidationError, match='Невалидный телефон'):
                schema(name='Тест', iin='900101300001', phone=phone, **data)


@pytest.mark.parametrize('phone,canonical', [('', ''), ('   ', ''), ('+7 (701) 123-45-67', '+77011234567')])
def test_registration_and_patient_creation_accept_optional_phone(client, phone, canonical):
    registration = client.post('/api/v1/auth/register', json={'name': 'Синтетический Врач', 'iin': '900101300001',
        'email': 'phone-test@example.test', 'password': 'Synthetic-password-123', 'phone': phone})
    assert registration.status_code == 201, registration.text
    assert registration.json()['phone'] == canonical
    assert client.get('/api/v1/auth/me').json()['phone'] == canonical
    created = client.post('/api/v1/patients', json={'name': 'Синтетический пациент', 'iin': '900101300002', 'phone': phone})
    assert created.status_code == 201 and created.json()['phone'] == canonical


def test_invalid_phone_blocks_registration_and_patient_api(client, doctor):
    response = client.post('/api/v1/auth/register', json={'name': 'Синтетический Врач', 'iin': '900101300001',
        'email': 'phone-invalid@example.test', 'password': 'Synthetic-password-123', 'phone': '+7 (701) 12'})
    assert response.status_code == 422 and 'Невалидный телефон' in response.text
    response = client.post('/api/v1/patients', json={'name': 'Синтетический пациент', 'iin': '900101300002', 'phone': '+7 (701) 12'})
    assert response.status_code == 422 and 'Невалидный телефон' in response.text
