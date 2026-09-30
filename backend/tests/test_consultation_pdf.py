from io import BytesIO
from datetime import datetime

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

from app.clinical import AI_CONCLUSION_NOTICE
from app.consultation_pdf import FONT, BOLD, build_consultation_pdf


def sample():
    # Synthetic data only, including the example identifier.
    return (
        {'id': 'synthetic-encounter', 'status': 'draft', 'started_at': '2026-09-30T06:15:00Z',
         'ended_at': '2026-09-30T06:29:00Z', 'fields': {
             'visit_type': 'primary', 'complaints': 'Синтетический пример: боль в горле.',
             'life_history': 'Әә Ғғ Ққ Ңң Өө Ұұ Үү Һһ Іі', 'height': '158 см', 'weight': '78 кг',
             'ai_conclusion': 'Предварительный синтетический текст для проверки врачом.',
             'ai_test_recommendations': 'AI_TESTS_SENTINEL', 'ai_diagnosis_variants': 'AI_VARIANTS_SENTINEL',
         }},
        {'name': 'Тестовый Пациент', 'iin': '000000000000', 'birth_date': '1990-01-02'},
        {'name': 'Тестовый Врач', 'specialty': 'Терапевт', 'department': 'Тестовое отделение'},
    )


def pdf_text(data):
    reader = PdfReader(BytesIO(data))
    return reader, '\n'.join(p.extract_text() for p in reader.pages)


def test_pdf_includes_fields_kazakh_font_and_explicit_draft():
    encounter, patient, doctor = sample()
    encounter['started_at'] = int(datetime.fromisoformat(encounter['started_at']).timestamp())
    data = build_consultation_pdf(encounter, patient, doctor)
    reader, text = pdf_text(data)
    assert data.startswith(b'%PDF-')
    assert 'Первичный приём' in text and 'Черновик - требует проверки врача' in text
    assert '000000000000' in text and '02.01.1990' in text
    assert '30.09.2026 11:15' in text and '30.09.2026 11:29' in text
    assert '31,2 кг/м²' in text
    assert 'Әә Ғғ Ққ Ңң Өө Ұұ Үү Һһ Іі' in text
    assert AI_CONCLUSION_NOTICE not in text
    assert 'Предварительное заключение ИИ' not in text
    assert encounter['fields']['ai_conclusion'] not in text
    assert 'AI_TESTS_SENTINEL' not in text and 'AI_VARIANTS_SENTINEL' not in text
    assert all(ord(c) in pdfmetrics.getFont(font).face.charToGlyph
               for font in (FONT, BOLD) for c in 'ӘәҒғҚқҢңӨөҰұҮүҺһІі')
    embedded_fonts = [str(font.get_object()['/BaseFont'])
                      for font in reader.pages[0]['/Resources']['/Font'].get_object().values()]
    assert any('Inter-Bold' in font for font in embedded_fonts)
    assert any('Inter-Regular' in font for font in embedded_fonts)
    assert reader.pages[0]['/Resources'].get('/XObject')  # Real UMC logo is embedded.


def test_pdf_repeat_overflow_and_escaped_markup():
    encounter, patient, doctor = sample()
    encounter.update(status='approved', reviewed_at='2026-09-30T07:00:00Z', previous_encounter_id='previous-test-id')
    fields = encounter['fields']
    fields.update(visit_type='repeat', complaints='<img src="https://invalid.test/private"> & клинический текст')
    fields['anamnesis'] = ('Длительная запись анамнеза с проверкой переноса без потери информации. ' * 180) + ' КОНЕЦ_АНАМНЕЗА'
    fields['recommendations'] = ('Рекомендации и наблюдение.\n' * 200) + ' КОНЕЦ_РЕКОМЕНДАЦИЙ'
    reader, text = pdf_text(build_consultation_pdf(encounter, patient, doctor))
    assert len(reader.pages) >= 5
    assert 'Повторный приём' in text and 'previous-test-id' in text
    assert 'Проверено врачом' in text and '30.09.2026 12:00' in text
    assert 'КОНЕЦ_АНАМНЕЗА' in text and 'КОНЕЦ_РЕКОМЕНДАЦИЙ' in text
    assert '<img src="https://invalid.test/private"> & клинический текст' in text
    assert all('Smart Consult' in page.extract_text() and f'Страница {i}' in page.extract_text()
               for i, page in enumerate(reader.pages, start=1))
    assert not any(page.get('/Annots') for page in reader.pages)
    assert 'Черновик - требует проверки врача' not in text


def test_pdf_does_not_invent_vitals_or_review_for_incomplete_metadata():
    encounter, patient, doctor = sample()
    encounter.update(status='approved', reviewed_at=None, started_at=None, ended_at=None)
    encounter['fields'] = {'height': 'рост не измерен', 'weight': '78'}
    _, text = pdf_text(build_consultation_pdf(encounter, patient, doctor))
    assert 'Черновик - требует проверки врача' in text
    assert '31,2 кг/м²' not in text and 'Не указано' in text
    assert 'не содержит электронной цифровой подписи' in ' '.join(text.split())
