import re


def redact(text, patient):
    """Детерминированный первый проход. Перед облаком обязателен просмотр врачом."""
    values = [patient.get('iin', ''), patient.get('phone', ''), patient.get('birth_date', '')]
    values += re.findall(r'[\w-]{3,}', patient.get('name', ''))
    for value in sorted(filter(None, values), key=len, reverse=True):
        text = re.sub(re.escape(value), '[ПЕРСОНАЛЬНЫЕ ДАННЫЕ]', text, flags=re.I)
    for pattern, label in [
        (r'(?<!\d)(?:\d[\s-]?){12}(?!\d)', '[ИИН]'),
        (r'(?<!\d)(?:\+?7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)', '[ТЕЛЕФОН]'),
        (r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', '[EMAIL]'),
        (r'\b\d{2}[./]\d{2}[./]\d{4}\b', '[ДАТА]'),
        (r'(?i)(?:адрес|проживаю|живу по адресу)\s*[:—-]?[^.!?\n]+', '[АДРЕС]'),
        (r'(?i)(?:меня зовут|мое имя|моё имя|фамилия)\s+[^.!?,\n]+', '[ИМЯ]'),
    ]:
        text = re.sub(pattern, label, text)
    return text


def redact_segments(segments, patient):
    return [{**s, 'text': redact(s['text'], patient)} for s in segments]


def redact_clinical_context(fields, patient):
    """Маскирует и ручные правки до передачи их модели, включая ИИ-подсказки."""
    from .clinical import DOCUMENT_FIELDS, AI_FIELDS

    def mask(value):
        if isinstance(value, str):
            return redact(value, patient)
        if isinstance(value, list):
            return [mask(item) for item in value]
        if isinstance(value, dict):
            return {key: mask(item) for key, item in value.items()}
        return value

    return {key: mask(value) for key, value in fields.items() if key in DOCUMENT_FIELDS | AI_FIELDS}
