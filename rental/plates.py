# -*- coding: utf-8 -*-
"""Госномер — ключ автопарка. Чистая логика.

Один и тот же номер источники пишут по-разному:

    Yume             «100AB02», бывает с кириллическими буквами-двойниками
    Wialon           «010XYZ08», «Hongqi EQM5 020XY02», «030XYZ08 (маяк)»,
                     «Taxi Technol 040XY02 Elantra <VIN>», «P1234 02»
    ГЛОНАССсофт      «050 XY | 02»
    Efir             «GoPartners  070XY02»

Номер ищется по ТОКЕНАМ имени и сверяется с образцом целиком. Поиск по склеенной строке
находит «номер» внутри VIN (K874025): первая сверка 27.10.2026 так дала 324 машины вместо
314. Соседние токены склеиваются по два-три — так находятся «P1234 02» и «050 XY | 02».
"""

import re

# Кириллические буквы, которые на номере выглядят как латинские.
_CYRILLIC_TWINS = str.maketrans('АВЕКМНОРСТУХ', 'ABEKMHOPCTYX')

# 123ABC02 / 123AB02 — номера Казахстана; A123456 — транзитные и старые.
PLATE_RE = re.compile(r'^(?:[0-9]{3}[A-Z]{2,3}[0-9]{2}|[A-Z][0-9]{6})$')


def normalize(raw):
    """«501 abe 08» → «501ABE08». Пусто — пустая строка."""
    return re.sub(r'[^A-Z0-9]', '', str(raw or '').upper().translate(_CYRILLIC_TWINS))


def is_plate(text):
    return bool(PLATE_RE.match(text or ''))


def find_in_name(name):
    """Все госномера в имени объекта GPS (обычно один)."""
    upper = str(name or '').upper().translate(_CYRILLIC_TWINS)
    tokens = [token for token in re.split(r'[^A-Z0-9]+', upper) if token]
    found = {token for token in tokens if is_plate(token)}
    for index in range(len(tokens) - 1):
        for size in (2, 3):
            joined = ''.join(tokens[index:index + size])
            if is_plate(joined):
                found.add(joined)
    return found
