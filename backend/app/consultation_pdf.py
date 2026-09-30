"""Лист консультации для печати. Все данные передаются явно, без запросов к БД/API."""

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
import re
from threading import Lock
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle



ASSETS = Path(__file__).resolve().parent.parent / 'assets'
FONT = 'SmartConsultInter'
BOLD = 'SmartConsultInterBold'
_font_lock = Lock()
LOCAL_TIME = timezone(timedelta(hours=5))
BROWN = colors.HexColor('#8A5C43')
INK = colors.HexColor('#3E2E20')
MUTED = colors.HexColor('#796758')
BORDER = colors.HexColor('#E9CFBA')
SAND = colors.HexColor('#F9F5F1')
WIDTH = A4[0] - 32 * mm


def _register_fonts():
    with _font_lock:
        if FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(FONT, str(ASSETS / 'fonts' / 'Inter-Regular.ttf')))
            pdfmetrics.registerFont(TTFont(BOLD, str(ASSETS / 'fonts' / 'Inter-Bold.ttf')))
            pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=BOLD, italic=FONT, boldItalic=BOLD)


def _text(value, empty='Не указано'):
    if value is None or not str(value).strip():
        return empty
    # ReportLab paragraphs understand XML; clinical text must always remain text.
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))
    return escape(value).replace('\r\n', '\n').replace('\r', '\n').replace('\n', '<br/>')


def _date(value, *, time=False):
    if not value:
        return 'Не указано'
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        else:
            parsed = value if isinstance(value, (date, datetime)) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if isinstance(parsed, datetime) and time:
            parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
            return parsed.astimezone(LOCAL_TIME).strftime('%d.%m.%Y %H:%M')
        return parsed.strftime('%d.%m.%Y')
    except (ValueError, TypeError, OverflowError, OSError):
        return 'Не указано'


def _bmi(fields):
    if fields.get('bmi'):
        return str(fields['bmi'])
    height = re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(см|cm|м|m)?\s*', str(fields.get('height', '')), re.I)
    weight = re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(кг|kg)?\s*', str(fields.get('weight', '')), re.I)
    if not height or not weight:
        return ''
    h, w = float(height[1].replace(',', '.')), float(weight[1].replace(',', '.'))
    if (height[2] or '').lower() in ('см', 'cm') or h > 3:
        h /= 100
    if not 0.5 <= h <= 2.5 or not 5 <= w <= 500:
        return ''
    return f'{w / h ** 2:.1f}'.replace('.', ',') + ' кг/м²'


def build_consultation_pdf(encounter: dict, patient: dict, doctor: dict) -> bytes:
    """Build a paginated A4 document from the existing public encounter/patient views.

    Optional metadata: started_at, ended_at, reviewed_at, previous_encounter_id;
    doctor.specialty / doctor.department. Missing data is explicitly labelled.
    No transcript, audio, credentials or hidden patient fields are embedded.
    """
    _register_fonts()
    fields = encounter.get('fields') or {}
    reviewed = encounter.get('status') in ('approved', 'exported') and bool(encounter.get('reviewed_at'))
    status = 'Проверено врачом' if reviewed else 'Черновик - требует проверки врача'
    title = 'Повторный приём' if fields.get('visit_type') == 'repeat' else 'Первичный приём'
    visit_format = 'Дистанционная консультация' if fields.get('visit_format') == 'remote' else 'Очная консультация'
    encounter_id = str(encounter.get('id') or '')
    body = ParagraphStyle('ClinicalBody', fontName=FONT, fontSize=9.2, leading=13.6,
                          textColor=INK, spaceAfter=5, alignment=TA_LEFT, splitLongWords=True,
                          allowWidows=0, allowOrphans=0)
    small = ParagraphStyle('ClinicalSmall', parent=body, fontSize=8, leading=11, spaceAfter=0)
    meta = ParagraphStyle('ClinicalMeta', parent=body, fontSize=8.5, leading=12, spaceAfter=0)
    section = ParagraphStyle('ClinicalSection', parent=body, fontName=BOLD, fontSize=11,
                             leading=15, textColor=BROWN, spaceBefore=10, spaceAfter=5,
                             keepWithNext=True)
    title_style = ParagraphStyle('ClinicalTitle', parent=body, fontName=BOLD, fontSize=21,
                                leading=26, textColor=BROWN, spaceAfter=5, keepWithNext=True)
    hint = ParagraphStyle('ClinicalHint', parent=small, textColor=MUTED, spaceAfter=8)
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=34 * mm, bottomMargin=19 * mm, pageCompression=1,
                            title='Smart Consult | Лист консультации', author='Smart Consult',
                            subject='Лист консультации пациента', allowSplitting=1)

    def page_frame(canvas, document):
        canvas.saveState()
        page_width, page_height = A4
        canvas.setFillColor(SAND)
        canvas.rect(0, page_height - 27 * mm, page_width, 27 * mm, stroke=0, fill=1)
        canvas.drawImage(str(ASSETS / 'umc-horizontal.png'), 16 * mm, page_height - 20 * mm,
                         width=61 * mm, height=9 * mm, preserveAspectRatio=True, mask='auto')
        canvas.setFillColor(BROWN)
        canvas.setFont(BOLD, 14)
        canvas.drawRightString(page_width - 16 * mm, page_height - 14 * mm, 'Smart Consult')
        canvas.setFont(FONT, 7.3)
        canvas.drawRightString(page_width - 16 * mm, page_height - 20 * mm, 'Консультация с ИИ-ассистентом')
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(.5)
        canvas.line(16 * mm, 15 * mm, page_width - 16 * mm, 15 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 7)
        footer_id = f'ID: {encounter_id}' if encounter_id else 'Лист консультации'
        canvas.drawString(16 * mm, 10 * mm, footer_id)
        canvas.drawRightString(page_width - 16 * mm, 10 * mm, f'Страница {document.page}')
        canvas.restoreState()

    story = [Paragraph(title, title_style), Paragraph(f'{visit_format} · {status}', hint)]

    def cell(label, value):
        return Paragraph(f'<font color="#796758">{_text(label)}</font><br/><b>{_text(value)}</b>', meta)

    doctor_details = ' · '.join(str(doctor.get(k) or '').strip() for k in ('specialty', 'department') if doctor.get(k))
    identity = [
        [cell('Пациент', patient.get('name')), cell('ИИН', patient.get('iin'))],
        [cell('Дата рождения', _date(patient.get('birth_date'))), cell('Врач', doctor.get('name'))],
        [cell('Начало приёма · UTC+05:00', _date(encounter.get('started_at'), time=True)),
         cell('Завершение приёма · UTC+05:00', _date(encounter.get('ended_at'), time=True))],
    ]
    if doctor_details:
        identity.append([cell('Специальность / отделение', doctor_details), cell('Дата проверки', _date(encounter.get('reviewed_at'), time=True))])
    table = Table(identity, colWidths=[WIDTH * .52, WIDTH * .48], hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), SAND), ('BOX', (0, 0), (-1, -1), .5, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), .3, BORDER), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.extend([table, Spacer(1, 6)])
    previous = encounter.get('previous_encounter_id') or fields.get('previous_encounter_id')
    if previous:
        story.append(Paragraph(f'<b>Предыдущий приём:</b> {_text(previous)}', small))
    elif fields.get('visit_type') == 'repeat':
        story.append(Paragraph('Предыдущий приём: не связан с этим листом', small))

    def heading(number, label):
        story.append(Paragraph(f'{number:02d} &nbsp; {label}', section))
        rule = HRFlowable(width='100%', thickness=.5, color=BORDER, spaceAfter=6)
        rule.keepWithNext = True
        story.append(rule)

    def value(key, label=None):
        prefix = f'<b>{label}:</b> ' if label else ''
        story.append(Paragraph(prefix + _text(fields.get(key)), body))

    heading(1, 'Жалобы')
    value('complaints')
    heading(2, 'Анамнез заболевания · Anamnesis morbi')
    value('anamnesis')
    heading(3, 'Анамнез жизни · Anamnesis vitae')
    value('life_history')
    for key, label in (
        ('allergies', 'Аллергии'), ('medications', 'Принимаемые препараты'),
        ('chronic_conditions', 'Хронические заболевания'), ('family_history', 'Наследственность'),
        ('operations', 'Операции и перенесённые заболевания'), ('habits', 'Привычки'),
    ):
        value(key, label)
    heading(4, 'Объективные данные · Status praesens')
    value('examination')
    vital_values = [
        ('Температура', fields.get('temperature')), ('Рост', fields.get('height')),
        ('Вес', fields.get('weight')), ('ИМТ (расчёт)', _bmi(fields)),
        ('ЧДД', fields.get('respiratory_rate')), ('ЧСС / пульс', fields.get('pulse')),
        ('Артериальное давление', fields.get('blood_pressure')), ('SpO2', fields.get('spo2')),
    ]
    vitals = Table([[cell(*v) for v in vital_values[n:n + 4]] for n in (0, 4)], colWidths=[WIDTH / 4] * 4, hAlign='LEFT')
    vitals.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), SAND), ('BOX', (0, 0), (-1, -1), .5, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), .3, BORDER), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    story.append(vitals)
    heading(5, 'Исследования и результаты')
    value('investigations')
    heading(6, 'Диагноз врача')
    value('diagnosis_code', 'Код МКБ-10')
    value('diagnosis')
    heading(7, 'Назначения и рекомендации')
    value('recommendations')
    value('follow_up', 'Дальнейшее наблюдение')
    story.append(Spacer(1, 8))
    rule = HRFlowable(width='100%', thickness=.7, color=BORDER, spaceAfter=8)
    rule.keepWithNext = True
    story.append(rule)
    if reviewed:
        story.append(Paragraph(f'<b>Проверено врачом:</b> {_text(doctor.get("name"))}<br/>'
                               f'{_date(encounter.get("reviewed_at"), time=True)} · UTC+05:00', body))
    else:
        story.append(Paragraph('<b>Черновик.</b> Лист сохранён для доработки и проверки врачом.', body))
    story.append(Paragraph('Сформировано в Smart Consult. Статус проверки отражает подтверждение в системе; '
                           'этот PDF не содержит электронной цифровой подписи.', hint))
    doc.build(story, onFirstPage=page_frame, onLaterPages=page_frame)
    return output.getvalue()
