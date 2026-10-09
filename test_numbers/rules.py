"""Ввод номера в реестр: проверка и вид на экране. Ни базы, ни сети.

Номер вводят как удобно — «8 701 123 45 67», «+7 (701) 123-45-67», «7011234567»;
в реестр ложатся ключ (последние десять цифр, keys.phone_key) и вид для экрана.
Казахстанский номер показывается одинаково, как бы его ни ввели; чужой — плюсом
и цифрами, как введён: у него по ключу код страны уже не восстановить.
"""

import re

from common.kz_phone import normalize_kz_phone

from .keys import phone_key

# E.164: не длиннее пятнадцати цифр. Длиннее — это не номер, а склейка двух или
# идентификатор мессенджера: ключ от такого «номера» совпал бы с чужим хвостом.
MAX_DIGITS = 15

_NON_DIGITS = re.compile(r'[^0-9]')
# Что человек может набрать в поле номера, кроме цифр.
_ALLOWED = re.compile(r'^[0-9+()\-.\s]*$')


class PhoneError(ValueError):
    """Номер не годится: текст — для человека."""


def parse_phone(raw):
    """Строка из поля ввода → (ключ, вид для экрана). Иначе PhoneError."""
    text = str(raw or '').strip()
    if not text:
        raise PhoneError('Введите номер')
    if not _ALLOWED.match(text):
        raise PhoneError('В номере могут быть только цифры, «+», скобки, пробелы и дефисы')
    digits = _NON_DIGITS.sub('', text)
    if len(digits) < 10:
        raise PhoneError('В номере меньше 10 цифр — введите номер целиком, с кодом оператора')
    if len(digits) > MAX_DIGITS:
        raise PhoneError('В номере больше 15 цифр — проверьте, нет ли лишних')
    return phone_key(digits), display_phone(digits)


def display_phone(raw):
    """Вид номера для экрана: «+7 701 123 45 67» у казахстанского."""
    digits = _NON_DIGITS.sub('', str(raw or ''))
    kz = normalize_kz_phone(digits)
    if kz:
        return f'+7 {kz[1:4]} {kz[4:7]} {kz[7:9]} {kz[9:11]}'
    if len(digits) == 11 and digits.startswith('8'):
        # «8» вместо «+7» — так набирают и российские номера.
        digits = '7' + digits[1:]
    return '+' + digits
