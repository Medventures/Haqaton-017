"""Нормализация реквизитов; дата из ИИН — подсказка, а не проверка документа."""
import re
from datetime import date


def normalize_iin(value):
    return re.sub(r'[\s-]', '', value) if isinstance(value, str) else value


def birth_date_from_iin(value):
    value = normalize_iin(value)
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{12}', value) or value[6] not in '123456':
        return None
    year = 1800 + ((int(value[6]) - 1) // 2) * 100 + int(value[:2])
    try:
        result = date(year, int(value[2:4]), int(value[4:6]))
    except ValueError:
        return None
    return result if result.year >= 1900 and result <= date.today() else None


def normalize_phone(value):
    if not value:
        return ''
    if not isinstance(value, str):
        raise ValueError('Невалидный телефон. Введите номер в формате +7 (XXX) XXX-XX-XX или оставьте поле пустым')
    value = value.strip()
    if not value:
        return ''
    digits = re.sub(r'[\s()+-]', '', value)
    if len(digits) == 10:
        digits = '7' + digits
    elif len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    if not re.fullmatch(r'7[0-9]{10}', digits):
        raise ValueError('Невалидный телефон. Введите номер в формате +7 (XXX) XXX-XX-XX или оставьте поле пустым')
    return '+' + digits
