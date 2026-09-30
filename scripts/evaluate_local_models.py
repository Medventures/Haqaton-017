"""Run synthetic compatibility checks through the actual medhub LLM adapter.

Outputs are evaluation artifacts, not clinical validation. No patient data or
cloud API is used. Full responses are saved for human review of assertions.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def segments(lines):
    return [{'speaker': speaker, 'start': i * 5, 'end': i * 5 + 4, 'text': text}
            for i, (speaker, text) in enumerate(lines)]


RU = segments([
    ('SPEAKER_00', 'Какие у вас жалобы? Сколько дней болеете?'),
    ('SPEAKER_01', 'Горло болит два дня. Температуры и кашля нет.'),
    ('SPEAKER_00', 'Есть аллергия на лекарства? Что принимаете сейчас?'),
    ('SPEAKER_01', 'На амоксициллин была сыпь. Сейчас никаких лекарств не принимаю.'),
    ('SPEAKER_00', 'Осмотр ещё не выполнен. Диагноз пока не установлен. Ничего не назначаю до осмотра.'),
])
MIXED = segments([
    ('SPEAKER_00', 'Что беспокоит и как давно?'),
    ('SPEAKER_01', 'Басым үш күннен бері ауырады. Жүрегім айнымайды.'),
    ('SPEAKER_02', 'Я медсестра. Измерила давление: сто двадцать на восемьдесят, пульс семьдесят два.'),
    ('SPEAKER_00', 'После осмотра уточним причину. Обезболивающие пока не назначаю.'),
])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Trusted local/VPN LM Studio URL ending in /v1')
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--long', action='store_true', help='Also test a synthetic 15-minute transcript')
    parser.add_argument('--reasoning-effort', choices=('none', 'minimal', 'low', 'medium', 'high', 'xhigh'))
    parser.add_argument('--max-tokens', type=int)
    parser.add_argument('--temperature', type=float)
    args = parser.parse_args()
    os.environ.update(LLM_PROVIDER='openai_compatible', LLM_URL=args.url,
                      LLM_MODEL=args.model, LLM_RESPONSE_FORMAT='json_schema', LLM_IS_CLOUD='false')
    for key, value in [('LLM_REASONING_EFFORT', args.reasoning_effort),
                       ('LLM_MAX_TOKENS', args.max_tokens), ('LLM_TEMPERATURE', args.temperature)]:
        if value is not None:
            os.environ[key] = str(value)
    from app.providers import generate
    from app.clinical import merge_generated_fields
    import httpx

    cases = [('russian_facts', RU, {}, 'all'),
             ('mixed_language_correction', MIXED,
              {'anamnesis': 'Уточнение врача: головная боль пять дней, не три.',
               'diagnosis': 'Сохранённый врачом диагноз', 'diagnosis_code': 'R51'}, 'all'),
             ('targeted_regeneration', RU,
              {'allergies': 'Сыпь на амоксициллин', 'diagnosis': 'Диагноз врача',
               'ai_test_recommendations': 'Правка врача: анализы пока не нужны, сначала осмотр.'},
              'ai_test_recommendations')]
    if args.long:
        lines = RU + segments([('SPEAKER_00' if i % 2 == 0 else 'SPEAKER_01',
                               'Продолжаем уточнение ранее описанных жалоб. Новых симптомов и измерений нет.')
                              for i in range(140)]) + segments([
                                  ('SPEAKER_01', 'Уточнение в конце: боль в горле уже прошла.'),
                                  ('SPEAKER_00', 'Зафиксировал: боль прошла. Диагноз пока не установлен.')])
        for i, item in enumerate(lines):
            item.update(start=i * 6, end=i * 6 + 5)
        cases.append(('long_context', lines, {}, 'all'))
    report = {'model': args.model, 'url': args.url, 'synthetic_only': True,
              'reasoning_effort': args.reasoning_effort, 'max_tokens': args.max_tokens,
              'temperature': args.temperature, 'cases': []}
    original_post = httpx.Client.post
    capture = {}

    def capture_post(self, *pos, **kw):
        response = original_post(self, *pos, **kw)
        data = response.json()
        capture.update(status=response.status_code, usage=data.get('usage'))
        if response.is_error:
            capture['provider_error'] = data
        return response

    httpx.Client.post = capture_post
    try:
        for name, transcript, context, target in cases:
            capture.clear()
            start = time.monotonic()
            entry = {'name': name, 'target': target}
            try:
                result = generate(transcript, context, target)
                merged = merge_generated_fields(context, result['fields'], target=target)
                checks = {'schema_valid': True,
                          'examination_left_empty': not result['fields']['examination'],
                          'manual_diagnosis_preserved': not context.get('diagnosis') or merged['diagnosis'] == context['diagnosis'],
                          'sources_returned': bool(result['fields']['sources'])}
                if name == 'russian_facts':
                    checks.update(allergy_retained='амоксициллин' in merged['allergies'].lower(),
                                  no_new_diagnosis=not merged['diagnosis'],
                                  roles_correct=result['speaker_roles'] == {'SPEAKER_00': 'doctor', 'SPEAKER_01': 'patient'})
                if name == 'mixed_language_correction':
                    checks.update(blood_pressure_retained='120' in merged['blood_pressure'] and '80' in merged['blood_pressure'],
                                  pulse_retained='72' in merged['pulse'],
                                  nurse_identified=result['speaker_roles'].get('SPEAKER_02') == 'nurse')
                entry.update(result=result, merged_fields=merged, checks=checks,
                             note='Automated checks cover structure and selected strings only; review all facts manually.')
            except Exception as error:
                entry.update(error_type=type(error).__name__, error=str(error)[:1000])
            entry.update(elapsed_seconds=round(time.monotonic() - start, 2), **capture)
            report['cases'].append(entry)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({key: entry[key] for key in ('name', 'elapsed_seconds', 'status', 'checks', 'error_type', 'provider_error') if key in entry}, ensure_ascii=False), flush=True)
    finally:
        httpx.Client.post = original_post


if __name__ == '__main__':
    main()
