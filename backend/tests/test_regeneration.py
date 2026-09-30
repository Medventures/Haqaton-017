import json
import pytest
from app.clinical import merge_generated_fields
from app.privacy import redact_clinical_context
from app.schemas import Consultation
from app.worker import process_one
from app.config import settings
from test_consultation_pipeline import prepare


def test_targeted_regeneration_preserves_other_fields_and_manual_diagnosis():
    existing = Consultation(complaints='Правка врача', diagnosis='Диагноз врача', diagnosis_code='I10',
        ai_test_recommendations='Правка обследований', ai_diagnosis_variants='Первый вариант',
        diagnosis_suggestions=[{'code': 'I10', 'name': 'Первый'}]).model_dump()
    generated = Consultation(complaints='Новый факт', diagnosis='Чужой диагноз', diagnosis_code='J02.9',
        ai_test_recommendations='Обновлённые обследования', ai_diagnosis_variants='Второй вариант',
        diagnosis_suggestions=[{'code': 'J02.9', 'name': 'Второй'}]).model_dump()
    result = merge_generated_fields(existing, generated, target='ai_diagnosis_variants')
    assert result['ai_diagnosis_variants'] == 'Первый вариант\n\nВторой вариант'
    assert {item['code'] for item in result['diagnosis_suggestions']} == {'I10', 'J02.9'}
    for key in ['complaints', 'diagnosis', 'diagnosis_code', 'ai_test_recommendations']:
        assert result[key] == existing[key]
    assert merge_generated_fields(result, generated, target='ai_diagnosis_variants') == result
    all_fields = merge_generated_fields(existing, generated)
    assert all_fields['diagnosis'] == existing['diagnosis'] and all_fields['diagnosis_code'] == 'I10'
    assert all_fields['complaints'] == 'Правка врача\n\nНовый факт'


def test_all_current_context_is_masked_including_ai_edits():
    patient = {'name': 'Синтетический Тестов', 'iin': '900101300001', 'phone': '+77011234567'}
    fields = {'anamnesis': 'Тестов, 900101 300001', 'ai_diagnosis_variants': 'Правка: +7 (701) 123-45-67',
        'diagnosis_suggestions': [{'code': 'I10', 'reason': 'Тестов'}], 'unexpected_identifier': 'private'}
    result = redact_clinical_context(fields, patient)
    text = json.dumps(result, ensure_ascii=False)
    assert all(value not in text for value in ['Тестов', '900101', '701', 'private'])
    assert 'Правка:' in result['ai_diagnosis_variants'] and result['diagnosis_suggestions'][0]['code'] == 'I10'


def test_worker_passes_latest_edits_and_only_updates_requested_answer(client, doctor, monkeypatch):
    _, e = prepare(client, monkeypatch)
    fields = Consultation(anamnesis='Правка анамнеза', diagnosis='Ручной диагноз', diagnosis_code='I10',
        ai_test_recommendations='Уточнение обследований', ai_diagnosis_variants='Уточнение гипотез').model_dump()
    e = client.patch(f'/api/v1/encounters/{e["id"]}', json={'version': e['version'], 'fields': fields}).json()
    calls = []
    def generated(segments, context, target):
        calls.append((segments, context, target))
        return {'fields': Consultation(anamnesis='Не должно попасть', diagnosis='Не должно попасть',
            ai_test_recommendations='Ответ с учётом уточнений', ai_diagnosis_variants='Не должно попасть').model_dump(),
            'speaker_roles': {}}
    monkeypatch.setattr('app.worker.generate', generated)
    response = client.post(f'/api/v1/encounters/{e["id"]}/generate', json={'version': e['version'], 'target': 'ai_test_recommendations'})
    assert response.status_code == 202
    assert process_one()
    updated = client.get(f'/api/v1/encounters/{e["id"]}').json()
    assert updated['last_job']['state'] == 'done'
    assert len(calls) == 1 and calls[0][2] == 'ai_test_recommendations'
    assert calls[0][1]['anamnesis'] == fields['anamnesis']
    assert calls[0][1]['ai_test_recommendations'] == fields['ai_test_recommendations']
    assert calls[0][1]['ai_diagnosis_variants'] == fields['ai_diagnosis_variants']
    assert updated['fields']['ai_test_recommendations'] == 'Ответ с учётом уточнений'
    for key in ['anamnesis', 'diagnosis', 'diagnosis_code', 'ai_diagnosis_variants']:
        assert updated['fields'][key] == fields[key]
    assert client.post(f'/api/v1/encounters/{e["id"]}/generate', json={'version': updated['version'], 'target': 'diagnosis'}).status_code == 422


def test_provider_sends_current_fields_and_target(monkeypatch):
    from app.providers import generate
    monkeypatch.setattr(settings(), 'llm_provider', 'openai')
    captured = []
    def extract(messages, schema):
        captured.append(messages)
        return json.dumps({'fields': {'ai_test_recommendations': 'Тест', 'ai_diagnosis_variants': 'Тест'}, 'speaker_roles': []})
    monkeypatch.setattr('app.providers.extract_openai', extract)
    generate([], {'anamnesis': 'Исправление', 'ai_test_recommendations': 'Уточнение'}, 'ai_test_recommendations')
    payload = json.loads(captured[0][1]['content'])
    assert payload['current_fields']['ai_test_recommendations'] == 'Уточнение'
    assert payload['current_fields']['anamnesis'] == 'Исправление'
    assert payload['target'] == 'ai_test_recommendations'
