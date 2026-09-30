import json
import httpx
import pytest
from app.clinical import AI_FIELDS, merge_generated_fields, export_without_ai
from app.schemas import Consultation
from app.db import SessionLocal, Encounter, Job
from app.config import settings
from app.worker import process_one
from audio_fixture import audio_bytes
from test_consultation_pipeline import prepare


def test_merge_preserves_all_existing_fields_and_does_not_duplicate():
    old = Consultation(complaints='Запись врача', anamnesis='Два дня', allergies='Не уточнено',
        pulse='70', visit_type='repeat', diagnosis='Запись диагноза', diagnosis_code='I10',
        reviewed_fields=['complaints', 'anamnesis']).model_dump()
    new = Consultation(complaints='Дополнение пациента', anamnesis='Два дня', pulse='90',
        diagnosis_code='J02.9', ai_test_recommendations='Обсудить обследования',
        ai_diagnosis_variants='Нужны уточнения').model_dump()
    result = merge_generated_fields(old, new)
    assert result['complaints'] == 'Запись врача\n\nДополнение пациента'
    assert result['anamnesis'] == 'Два дня' and result['allergies'] == 'Не уточнено'
    assert result['pulse'] == '70' and any('90' in text for text in result['warnings'])
    assert result['diagnosis_code'] == 'I10' and result['visit_type'] == 'repeat'
    assert result['reviewed_fields'] == ['anamnesis']
    assert merge_generated_fields(result, new)['complaints'] == result['complaints']


def test_merge_keeps_original_at_field_limit_and_discards_obsolete_sources():
    old = Consultation(complaints='А' * 15000, sources=[{'field': 'anamnesis', 'segments': [9]}]).model_dump()
    result = merge_generated_fields(old, {'complaints': 'Дополнение'}, new_transcript=True)
    assert result['complaints'] == old['complaints']
    assert result['warnings'] and result['sources'] == []


@pytest.mark.parametrize('automatic', [False, True])
def test_worker_adds_to_prefilled_fields_on_both_generation_paths(client, doctor, monkeypatch, automatic):
    p, e = prepare(client, monkeypatch)
    fields = Consultation(complaints='Уже внесено врачом', anamnesis='Сохранённый анамнез',
        allergies='Пенициллин', recommendations='Врачебная рекомендация', diagnosis='Ручной диагноз', diagnosis_code='I10',
        ai_diagnosis_variants='Прежний вариант').model_dump()
    e = client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': e['version'], 'fields': fields}).json()
    generated = Consultation(complaints='Боль в горле', anamnesis='Со вчерашнего дня',
        diagnosis='Диагноз от модели', diagnosis_code='J02.9',
        ai_test_recommendations='AI_TESTS_SENTINEL', ai_diagnosis_variants='AI_VARIANTS_SENTINEL',
        diagnosis_suggestions=[{'code': 'J02.9', 'name': 'AI_CODE_SENTINEL'}]).model_dump()
    monkeypatch.setattr('app.worker.generate', lambda *args: {'fields': generated, 'speaker_roles': {'SPEAKER_00': 'patient'}})
    response = client.post(f'/api/v1/encounters/{e["id"]}/audio', data={'analyze': str(automatic).lower()},
        files={'file': ('synthetic.wav', audio_bytes(), 'audio/wav')})
    assert response.status_code == 202
    assert process_one()
    saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    if not automatic:
        assert client.post(f'/api/v1/encounters/{e["id"]}/generate', json={'version': saved['version']}).status_code == 202
        assert process_one()
        saved = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert saved['fields']['complaints'] == 'Уже внесено врачом\n\nБоль в горле'
    assert saved['fields']['anamnesis'] == 'Сохранённый анамнез\n\nСо вчерашнего дня'
    assert saved['fields']['allergies'] == 'Пенициллин'
    assert saved['fields']['recommendations'] == 'Врачебная рекомендация'
    assert saved['fields']['diagnosis'] == 'Ручной диагноз' and saved['fields']['diagnosis_code'] == 'I10'
    assert saved['fields']['ai_diagnosis_variants'] == 'Прежний вариант\n\nAI_VARIANTS_SENTINEL'
    assert saved['fields']['ai_test_recommendations'] == 'AI_TESTS_SENTINEL'
    approved = client.post(f'/api/v1/encounters/{e["id"]}/approve', json={'version': saved['version']})
    assert approved.status_code == 200
    headers = {'Authorization': 'Bearer ' + client.post('/api/v1/integration-key').json()['api_key']}
    for url in ['/api/v1/integration/encounters', f'/api/v1/integration/encounters/{e["id"]}']:
        exported = client.get(url, headers=headers)
        assert exported.status_code == 200
        assert 'SENTINEL' not in exported.text and 'ai_notice' not in exported.text
        assert 'Врачебная рекомендация' in exported.text


def test_queued_legacy_export_is_cleaned_before_outbound_request(client, doctor, monkeypatch):
    p, e = prepare(client, monkeypatch)
    with SessionLocal() as db:
        row = db.get(Encounter, e['id'])
        row.reviewed_at = 123
        row.status = 'processing'
        db.add(Job(encounter_id=row.id, kind='export', state='queued', payload={'version': row.version,
            'export': {'encounter': {'id': row.id, 'ai_notice': {'legacy': True}, 'fields': {
                'complaints': 'Проверено врачом', 'ai_conclusion': 'LEGACY_SENTINEL',
                'ai_test_recommendations': 'TESTS_SENTINEL', 'ai_diagnosis_variants': 'DIAGNOSIS_SENTINEL',
                'diagnosis_suggestions': [{'code': 'TEST_SENTINEL'}],
                'sources': [{'field': 'ai_conclusion', 'segments': [0]}]}}}}))
        db.commit()
    monkeypatch.setattr(settings(), 'mis_url', 'https://mis.example.test/consultations')
    deliveries = []
    def post(self, url, **kwargs):
        deliveries.append(kwargs['json'])
        return httpx.Response(201, request=httpx.Request('POST', url))
    monkeypatch.setattr(httpx.Client, 'post', post)
    assert process_one()
    assert len(deliveries) == 1
    assert 'SENTINEL' not in json.dumps(deliveries) and 'ai_notice' not in deliveries[0]['encounter']
    assert deliveries[0]['encounter']['fields']['complaints'] == 'Проверено врачом'


def test_export_allowlist_cannot_leak_new_ai_fields():
    data = {'encounter': {'fields': {'complaints': 'Оставить', 'future_ai_field': 'Убрать',
        **{key: 'Убрать' for key in AI_FIELDS}, 'sources': [], 'reviewed_fields': ['ai_conclusion']}}}
    result = export_without_ai(data)
    assert result['encounter']['fields'] == {'complaints': 'Оставить', 'sources': [], 'reviewed_fields': []}
    assert 'future_ai_field' in data['encounter']['fields']
