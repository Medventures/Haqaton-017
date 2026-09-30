"""Неизменяемый документ согласия: сохранённые PDF-байты подписываются целиком."""

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .consultation_pdf import ASSETS, FONT, BOLD, BROWN, INK, MUTED, BORDER, SAND, _register_fonts, _text, _date


def build_consent_pdf(patient: dict, consent_id: str, created_at: int, version: str) -> bytes:
    """Формировать один раз до подписания; после проверки PDF не переписывается."""
    _register_fonts()
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=33 * mm, bottomMargin=20 * mm, pageCompression=1,
                            title='Smart Consult | Согласие пациента', author='Smart Consult')
    body = ParagraphStyle('ConsentBody', fontName=FONT, fontSize=9.3, leading=13.7,
                          textColor=INK, spaceAfter=7, allowWidows=0, allowOrphans=0)
    heading = ParagraphStyle('ConsentHeading', parent=body, fontName=BOLD, textColor=BROWN,
                             fontSize=10.4, leading=14, spaceBefore=6, keepWithNext=True)
    title = ParagraphStyle('ConsentTitle', parent=heading, fontSize=18, leading=22, spaceAfter=10)
    small = ParagraphStyle('ConsentSmall', parent=body, fontSize=8, leading=11, textColor=MUTED)

    def frame(canvas, document):
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(SAND)
        canvas.rect(0, height - 27 * mm, width, 27 * mm, stroke=0, fill=1)
        canvas.drawImage(str(ASSETS / 'umc-horizontal.png'), 18 * mm, height - 20 * mm,
                         width=61 * mm, height=9 * mm, preserveAspectRatio=True, mask='auto')
        canvas.setFillColor(BROWN)
        canvas.setFont(BOLD, 14)
        canvas.drawRightString(width - 18 * mm, height - 15 * mm, 'Smart Consult')
        canvas.setFont(FONT, 8)
        canvas.drawRightString(width - 18 * mm, height - 21 * mm, 'Согласие на запись и обработку данных')
        canvas.setStrokeColor(BORDER)
        canvas.line(18 * mm, 16 * mm, width - 18 * mm, 16 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont(FONT, 7)
        canvas.drawString(18 * mm, 11 * mm, f'ID: {consent_id}')
        canvas.drawRightString(width - 18 * mm, 11 * mm, f'Страница {document.page}')
        canvas.restoreState()

    story = [Paragraph('Согласие пациента', title),
             Paragraph('На запись консультации и обработку данных с помощью ИИ-ассистента', body)]
    identity = Table([
        [Paragraph('<b>Пациент</b><br/>' + _text(patient.get('name')), body),
         Paragraph('<b>ИИН</b><br/>' + _text(patient.get('iin')), body)],
        [Paragraph('<b>Документ подготовлен</b><br/>' + _date(created_at, time=True) + ' (UTC+05:00)', body),
         Paragraph('<b>Версия согласия</b><br/>' + _text(version), body)],
    ], colWidths=[102 * mm, 72 * mm])
    identity.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), SAND), ('BOX', (0, 0), (-1, -1), .5, BORDER),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 9),
        ('RIGHTPADDING', (0, 0), (-1, -1), 9), ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.extend([identity, Spacer(1, 10)])
    sections = [
        ('1. Цель и состав данных',
         'Я разрешаю записывать разговор во время моих консультаций в Smart Consult и обрабатывать '
         'аудиозапись, расшифровку, сведения о говорящих, персональные и медицинские данные, которые '
         'могут прозвучать в разговоре. Цель: подготовка листа консультации, его проверка врачом '
         'и передача утверждённого результата в медицинскую информационную систему (МИС).'),
        ('2. Распознавание и генерация текста',
         'Я согласен на локальную обработку и передачу данных настроенным облачным сервисам: '
         'обезличенного текста - языковой модели (LLM), проверенного обезличенного аудио - сервису '
         'распознавания речи (ASR). Также разрешаю передачу исходной записи в OpenAI для распознавания, '
         'включая возможные персональные и медицинские данные. Маскирование не гарантирует полного '
         'удаления всех идентифицирующих сведений; облачная обработка выполняется вне Smart Consult.'),
        ('3. Хранение и доступ',
         'Записи, расшифровки, распределение реплик по ролям, черновики и проверенные заключения '
         'сохраняются в системе. Доступ к ним получают уполномоченные пользователи в пределах своих '
         'прав. Документ согласия, подпись и сведения о её проверке сохраняются отдельно для '
         'подтверждения согласия. Согласие действует для консультаций этого пациента до его отзыва.'),
        ('4. Добровольность и отзыв',
         'Подписание добровольно. Я могу отказаться от записи и обработки с помощью ИИ или отозвать '
         'согласие через врача. Консультацию можно оформить вручную. Отзыв прекращает новые записи '
         'и дальнейшую обработку, на которую распространяется согласие; он не отменяет уже '
         'выполненную передачу и не удаляет автоматически ранее сохранённые документы и записи.'),
        ('5. Проверка врачом и подписание',
         'Результат ИИ является предварительным заключением, не является диагнозом и требует '
         'проверки врача. Окончательную ответственность за клинические решения несёт врач. '
         'Подписывая этот документ личной ЭЦП через NCALayer или QR/eGov Mobile, я подтверждаю, '
         'что ознакомился с текстом и согласен с перечисленными условиями. Документ и подпись '
         'передаются в SIGEX для проверки ЭЦП и соответствия ИИН пациента.'),
    ]
    for label, text in sections:
        story.extend([Paragraph(label, heading), Paragraph(text, body)])
    story.extend([Spacer(1, 6), Paragraph(
        'Этот PDF содержит текст для подписания. Сам по себе открытый или скачанный PDF не подтверждает '
        'наличие ЭЦП. Проверенный статус, время проверки и файл подписи отображаются в карточке '
        'пациента. После подписания содержимое этого документа не изменяется.', small)])
    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    return output.getvalue()
