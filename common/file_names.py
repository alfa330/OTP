# -*- coding: utf-8 -*-
"""Имя загруженного файла — таким, каким его назвал человек.

werkzeug.secure_filename для этого не годится: он выбрасывает всё не-ASCII, и
«Отчёт за ноябрь.xlsx» доезжает до базы как «xlsx», а «Скриншот.png» — как
«png» (так вложения задач теряли имена до 27.11.2026). Здесь буквы любого
алфавита остаются; уходят только путь («C:\\…\\», «../»), управляющие,
форматные и «пустые» символы (U+202E на экране переворачивает «акт‮fdp.doc» в
«актcod.pdf») и знаки, которых не терпят имена файлов Windows. Правила — как у
discipline.files.clean_name / safe_name.

Путь в бакете — отдельно (storage_name): только ASCII. Человек его не видит,
а латиница там избавляет от вопросов с кодированием в подписанных ссылках.
"""

import os
import re
import unicodedata

from werkzeug.utils import secure_filename

NAME_MAX = 200

# Буквы, которые рисуются пустотой: заполнители хангыля и пустая ячейка Брайля.
_BLANK_LETTERS = frozenset('ᅟᅠㅤﾠ⠀')
_FORBIDDEN = re.compile(r'["<>|*?:]')


def display_name(filename, fallback='file', max_len=NAME_MAX):
    """Имя для базы и для показа. Пустое после чистки — fallback.

    Длинное режется по основе: расширение остаётся на месте. Имя приводится к
    NFC — macOS присылает «й» двумя символами."""
    base = str(filename or '').replace('\\', '/').split('/')[-1]
    out = []
    for char in unicodedata.normalize('NFC', base):
        if char.isspace():
            out.append(' ')
        elif char not in _BLANK_LETTERS and unicodedata.category(char) not in ('Cc', 'Cf', 'Cs', 'Co', 'Cn'):
            out.append(char)
    name = re.sub(' {2,}', ' ', _FORBIDDEN.sub('', ''.join(out))).strip('. ')
    if not name:
        return fallback
    if len(name) <= max_len:
        return name
    stem, dot, ext = name.rpartition('.')
    if dot and stem and 0 < len(ext) <= 10:
        return stem[:max_len - len(ext) - 1].rstrip('. ') + '.' + ext
    return name[:max_len]


def storage_name(filename, fallback='file'):
    """Хвост пути в бакете: латиница, расширение на месте.

    «Отчёт.xlsx» → «file.xlsx», «IMG_1.jpg» → «IMG_1.jpg»."""
    stem, ext = os.path.splitext(display_name(filename, fallback=''))
    ext = re.sub(r'[^A-Za-z0-9]', '', ext)[:10]
    stem = secure_filename(stem)[:100].strip('._') or fallback
    return f'{stem}.{ext}' if ext else stem
