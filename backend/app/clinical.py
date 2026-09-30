"""Обязательная пометка добавляется сервером, независимо от ответа модели."""

AI_CONCLUSION_NOTICE = (
    'Рекомендации по обследованиям и предварительные варианты диагноза от ИИ '
    'предназначены только для ознакомления и не являются диагнозом или назначением. '
    'Они не включаются в PDF и не передаются в МИС. Врач несёт окончательную ответственность '
    'за диагноз, назначения и медицинские решения.'
)


def ai_notice():
    return {
        'type': 'ai_advisory',
        'is_diagnosis': False,
        'requires_doctor_review': True,
        'disclaimer': AI_CONCLUSION_NOTICE,
    }


# Явный список: новые служебные поля и подсказки не должны попадать в МИС.
DOCUMENT_FIELDS = frozenset(('visit_type', 'visit_format', 'complaints', 'anamnesis',
    'life_history', 'allergies', 'medications', 'chronic_conditions', 'family_history',
    'operations', 'habits', 'examination', 'temperature', 'height', 'weight', 'pulse',
    'respiratory_rate', 'blood_pressure', 'spo2', 'investigations', 'diagnosis',
    'diagnosis_code', 'recommendations', 'follow_up'))
AI_FIELDS = frozenset(('ai_conclusion', 'ai_test_recommendations', 'ai_diagnosis_variants',
                       'diagnosis_suggestions', 'warnings'))
GENERATION_TARGETS = (DOCUMENT_FIELDS - {'visit_type', 'visit_format', 'diagnosis', 'diagnosis_code'}) | {
    'all', 'ai_test_recommendations', 'ai_diagnosis_variants'}


def document_fields(fields):
    result = {key: value for key, value in fields.items() if key in DOCUMENT_FIELDS}
    result['sources'] = [source for source in fields.get('sources', []) if source.get('field') in DOCUMENT_FIELDS]
    result['reviewed_fields'] = [key for key in fields.get('reviewed_fields', []) if key in DOCUMENT_FIELDS]
    return result


def export_without_ai(export):
    """Применяется и к сохранённым заданиям отправки, созданным старой версией."""
    from copy import deepcopy
    result = deepcopy(export)
    encounter = result['encounter']
    encounter['fields'] = document_fields(encounter.get('fields', {}))
    encounter.pop('ai_notice', None)
    for key in AI_FIELDS:
        encounter.pop(key, None)
    return result


def merge_generated_fields(existing, generated, *, new_transcript=False, target='all'):
    """Дополняет запись врача; повторный анализ не дублирует тот же текст."""
    from .schemas import Consultation
    result = Consultation.model_validate(existing).model_dump()
    incoming = Consultation.model_validate(generated).model_dump()
    if target not in GENERATION_TARGETS:
        raise ValueError('Неизвестное поле для перегенерации')
    warnings = list(incoming['warnings'] if target == 'all' else result['warnings'])
    changed = set()
    scalar_fields = {'temperature', 'height', 'weight', 'pulse', 'respiratory_rate',
                     'blood_pressure', 'spo2', 'diagnosis_code'}
    normalize = lambda value: ' '.join(value.casefold().split())
    for key in DOCUMENT_FIELDS - {'visit_type', 'visit_format'}:
        if target != 'all' and key != target:
            continue
        if key in {'diagnosis', 'diagnosis_code'} and existing.get('diagnosis', '').strip():
            continue  # Внесённый диагноз врача не дополняется предположениями модели.
        old, addition = result[key], incoming[key].strip()
        if not addition or normalize(addition) in normalize(old):
            continue
        if old.strip() and key in scalar_fields:
            warnings.append(f'Поле {key}: сохранено «{old}»; в расшифровке «{addition}». Проверьте различие.')
            continue
        if key == 'diagnosis_code' and existing.get('diagnosis', '').strip():
            continue  # Код модели не привязывается к уже внесённому врачом диагнозу.
        parts = [part.strip() for part in addition.split('\n\n') if part.strip()]
        additions = [part for part in parts if normalize(part) not in normalize(old)]
        value = old + ('\n\n' if old.strip() else '') + '\n\n'.join(additions)
        limit = next(meta.max_length for meta in Consultation.model_fields[key].metadata if hasattr(meta, 'max_length'))
        if len(value) > limit:
            warnings.append(f'Поле {key} достигло лимита длины. Исходный текст сохранён; дополнение доступно в расшифровке.')
            continue
        result[key] = value
        changed.add(key)
    for key in ('ai_test_recommendations', 'ai_diagnosis_variants'):
        if target not in ('all', key):
            continue
        old, addition = result[key], incoming[key].strip()
        if key == 'ai_diagnosis_variants':
            parts = [part.strip() for part in addition.split('\n\n') if part.strip()]
            additions = [part for part in parts if normalize(part) not in normalize(old)]
            value = old + ('\n\n' if old.strip() and additions else '') + '\n\n'.join(additions)
            if len(value) <= 15000:
                result[key] = value
            else:
                warnings.append('Варианты диагноза достигли лимита длины. Сократите текст перед повторным анализом.')
        else:
            result[key] = addition
    if target in ('all', 'ai_diagnosis_variants'):
        suggestions = {item['code']: item for item in result['diagnosis_suggestions']}
        for item in incoming['diagnosis_suggestions']:
            if item['code'] not in suggestions and len(suggestions) < 5:
                suggestions[item['code']] = item
        result['diagnosis_suggestions'] = list(suggestions.values())
    result['warnings'] = list(dict.fromkeys(warnings))[:20]
    result['reviewed_fields'] = [key for key in result['reviewed_fields'] if key not in changed and key in DOCUMENT_FIELDS]
    sources = {} if new_transcript else {s['field']: set(s['segments']) for s in result['sources']}
    for source in incoming['sources']:
        if source['field'] in changed:
            sources.setdefault(source['field'], set()).update(source['segments'])
    result['sources'] = [{'field': key, 'segments': sorted(indices)[:100]} for key, indices in sources.items()][:50]
    return Consultation.model_validate(result).model_dump()
