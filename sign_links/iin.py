# -*- coding: utf-8 -*-
"""Проверка ИИН. Чистая логика: ни базы, ни сети.

ИИН Казахстана — 12 цифр:

    ГГММДД  дата рождения
    В       век и пол: 1/2 — XIX век, 3/4 — XX, 5/6 — XXI (нечётная — мужчина,
            чётная — женщина); 0 встречается у старых записей, где век не
            проставлен
    NNNN    порядковый номер
    К       контрольная цифра

Контрольная цифра считается по официальному алгоритму: сумма первых
одиннадцати цифр с весами 1..11 по модулю 11; если остаток 10 — второй проход
с весами 3..11, 1, 2; если и там 10 — такого ИИН не бывает.

Зачем проверять у себя, если у генератора Sapar своя проверка. Затем, что
на опечатку он отвечает тем же «Нет документов на подписание», что и на
чужой ИИН, — и оператор уходит с ответом «документов нет» там, где надо было
переспросить цифру. Проверка контрольной суммы ловит любую одиночную опечатку
и любую перестановку соседних цифр — то есть ровно то, что случается, когда
ИИН диктуют по телефону.

Двойник на фронте — src/components/sign_links/iin.js: те же коды ошибок, те
же сообщения, чтобы поле подсказывало то же, что ответит сервер. Второй
словарь неизбежен (питон не читает js), поэтому набор кодов сторожит тест.
"""

import calendar

IIN_LENGTH = 12

_WEIGHTS_FIRST = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
_WEIGHTS_SECOND = (3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2)

# Символы, которые человек ставит между цифрами, когда переписывает ИИН из
# документа: пробелы, неразрывные пробелы, дефисы, точки. Их снимаем молча;
# всё остальное — ошибка «только цифры», а не тихая чистка.
_SEPARATORS = '  \t-.‑‒–—'

# Коды ошибок — контракт с фронтом (iin.js). Сообщения по-русски, готовые к
# показу под полем.
ERRORS = {
    'empty': 'Введите ИИН',
    'digits': 'ИИН состоит только из цифр',
    'length': 'В ИИН должно быть 12 цифр',
    'date': 'Первые шесть цифр — дата рождения, такой даты не бывает',
    'century': 'Седьмая цифра ИИН — век и пол, она бывает от 0 до 6',
    'checksum': 'ИИН не сходится по контрольной цифре — проверьте цифры',
}


def normalize(raw):
    """Убирает разделители. Не проверяет — только приводит к строке цифр."""
    text = str(raw or '')
    return ''.join(ch for ch in text if ch not in _SEPARATORS).strip()


def _checksum(first_eleven):
    """Контрольная цифра для первых одиннадцати цифр. None — такого ИИН не бывает."""
    digits = [int(ch) for ch in first_eleven]
    total = sum(d * w for d, w in zip(digits, _WEIGHTS_FIRST)) % 11
    if total == 10:
        total = sum(d * w for d, w in zip(digits, _WEIGHTS_SECOND)) % 11
        if total == 10:
            return None
    return total


def _century_of(digit):
    """Век по седьмой цифре. None — век не проставлен (цифра 0)."""
    if digit in ('1', '2'):
        return 1800
    if digit in ('3', '4'):
        return 1900
    if digit in ('5', '6'):
        return 2000
    return None


def _date_is_plausible(iin):
    """Дата рождения из первых шести цифр существует в календаре.

    Век известен — проверяем настоящий год (29 февраля 1900 года не было).
    Век не проставлен — берём високосный год, чтобы не отвергнуть 29 февраля
    у человека, чей год мы не знаем: лучше пропустить, чем отказать зря.
    """
    year_tail = int(iin[0:2])
    month = int(iin[2:4])
    day = int(iin[4:6])
    if not 1 <= month <= 12:
        return False
    century = _century_of(iin[6])
    year = (century + year_tail) if century is not None else 2000
    return 1 <= day <= calendar.monthrange(year, month)[1]


def validate(raw):
    """(ИИН из 12 цифр, None) либо (None, код ошибки из ERRORS)."""
    value = normalize(raw)
    if not value:
        return None, 'empty'
    if not value.isdigit():
        return None, 'digits'
    if len(value) != IIN_LENGTH:
        return None, 'length'
    if value[6] not in '0123456':
        return None, 'century'
    if not _date_is_plausible(value):
        return None, 'date'
    control = _checksum(value[:11])
    if control is None or control != int(value[11]):
        return None, 'checksum'
    return value, None


def is_valid(raw):
    return validate(raw)[0] is not None


def error_message(code):
    return ERRORS.get(code or '', ERRORS['checksum'])
