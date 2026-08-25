# -*- coding: utf-8 -*-
"""Справочник «внутренний номер → сотрудник».

Два источника, и ни одного захардкоженного ФИО (репозиторий публичный):

1. **Наша база** — `users` + `operator_profiles`: ФИО кириллицей, направление,
   дата найма.
2. **Станция, `GET /agents/map`** — кто владеет номером СЕЙЧАС (транслит, без
   отчеств). Единственный источник правды о текущем владельце: у нас номер
   уволившегося остаётся висеть на нём же, и 15 номеров закреплены сразу за
   двумя людьми.

**Зачем периоды владения.** Номер уволившегося отдают новому сотруднику. Звонок
двухмесячной давности принадлежит предыдущему владельцу, и подписать его именем
нынешнего — соврать в отчёте. Поэтому у переиспользованного номера два периода:
предыдущий владелец с открытой левой границей и нынешний — с даты своего найма.

**Как определяется нынешний владелец без ручного списка.** Имя из `/agents/map`
сверяется с кандидатами из базы по словам сразу в двух видах — кириллицей и в
транслите: станция пишет «zhupan_aruzhan», база — «Жупан Аружан», и казахские
буквы в них разные («Құрман» против «Kurman»). Совпало ≥2 слова (или одно, если
кандидат такой один) — это он. Не совпало ничего — берём того, кого наняли
позже: у прежнего владельца номер уже отобрали.

Модуль чистый: ни сети, ни базы. На вход — строки из базы и словарь станции.
"""

import re

# Казахские буквы к русским: сравниваем «Құрман» и «Курман» как одно слово.
_KZ = str.maketrans({'ә': 'а', 'ө': 'о', 'ұ': 'у', 'ү': 'у', 'қ': 'к', 'ғ': 'г',
                     'ң': 'н', 'һ': 'х', 'і': 'и', 'э': 'е', 'ё': 'е',
                     'ь': '', 'ъ': ''})

_TR = {'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ж': 'zh',
       'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n',
       'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f',
       'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ы': 'y',
       'ю': 'yu', 'я': 'ya'}

# Очередь, а не человек: четыре цифры, первая — тройка.
QUEUE_RE = re.compile(r'^3\d{3}$')

# Как выглядит внутренний номер. Всё остальное в справочник не берём: ключ там
# VARCHAR(8), а в `users.sip_number` лежит VARCHAR(64) без единой проверки.
EXT_RE = re.compile(r'\d{2,8}')

UNKNOWN_DIRECTION = 'направление неизвестно'


def name_words(value):
    """ФИО → (слова кириллицей, те же слова в транслите).

    Скобочные пометки вроде «(только на станции)» частью имени не считаются, а
    чисто цифровые слова выбрасываются: станция пишет и «Nurmakhan 6323».
    """
    base = str(value or '').lower().translate(_KZ)
    base = re.sub(r'\(.*?\)', ' ', base)
    base = base.replace('_', ' ').replace('.', ' ')
    words = [w for w in re.split(r'[^0-9a-zа-я]+', base)
             if len(w) > 2 and not w.isdigit()]
    cyrillic = set(words)
    latin = {''.join(_TR.get(char, char) for char in word) for word in words}
    return cyrillic, latin


def names_match(left, right):
    """Одно ли это ФИО. Порядок слов не важен — «Мараткызы Молдир» и «Молдир
    Мараткызы» один человек; требуем два общих слова, чтобы совпадение по
    распространённому имени не выдавалось за совпадение по человеку."""
    left_ru, left_tr = name_words(left)
    right_ru, right_tr = name_words(right)
    if not left_ru or not right_ru:
        return False
    return len(left_ru & right_ru) >= 2 or len(left_tr & right_tr) >= 2


def _shares_word(left, right):
    left_ru, left_tr = name_words(left)
    right_ru, right_tr = name_words(right)
    return bool((left_ru & right_ru) or (left_tr & right_tr))


def _station_name(raw):
    """«zhupan_aruzhan» → «Zhupan Aruzhan». Станция пишет как придётся: где-то
    через подчёркивание, где-то через пробел, где-то уже с большой буквы."""
    cleaned = re.sub(r'[_.]+', ' ', str(raw or '')).strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if not cleaned:
        return ''
    if cleaned.islower():
        cleaned = ' '.join(part.capitalize() for part in cleaned.split(' '))
    return cleaned


def build_directory(db_rows, agents_map=None, overrides=None):
    """Строит {ext: {'periods': [...], 'station': ..., 'source': ...}}.

    db_rows — словари {'ext', 'name', 'hire_date' (ISO или None), 'direction',
    'department'}; один ext может встретиться несколько раз, это и есть
    переиспользованный номер.
    """
    agents_map = agents_map or {}
    overrides = overrides or {}

    candidates = {}
    for row in db_rows:
        ext = str(row.get('ext') or '').strip()
        # Внутренний номер — 3-4 цифры, но в базе это VARCHAR(64) без проверок, и
        # туда попадает всякое («6650 (личный)», пробелы, заметки). Ключ
        # справочника VARCHAR(8), поэтому мусор надо отсеять здесь, а не ловить
        # ошибку вставки, которая унесёт с собой весь справочник.
        if not EXT_RE.fullmatch(ext):
            continue
        candidates.setdefault(ext, []).append({
            'name': (row.get('name') or '').strip(),
            'hire_date': (str(row['hire_date'])[:10] if row.get('hire_date') else None),
            'direction': (row.get('direction') or '').strip() or UNKNOWN_DIRECTION,
            'department': (row.get('department') or '').strip(),
        })

    directory = {}
    for ext in set(list(candidates) + list(agents_map) + list(overrides)):
        ext = str(ext).strip()
        if not EXT_RE.fullmatch(ext):
            continue  # не внутренний номер: справочник хранит ключ как VARCHAR(8)
        if QUEUE_RE.match(ext):
            continue  # очередь, а не рабочее место
        station = _station_name(agents_map.get(ext))

        if ext in overrides:
            override = overrides[ext]
            directory[ext] = {
                'periods': [{'since': None,
                             'name': override.get('name') or station or ext,
                             'direction': override.get('direction') or UNKNOWN_DIRECTION}],
                'station': station,
                'source': 'правка вручную',
            }
            continue

        people = list(candidates.get(ext) or [])
        if not people:
            if not station:
                continue
            directory[ext] = {
                'periods': [{'since': None, 'name': station + ' (только на станции)',
                             'direction': UNKNOWN_DIRECTION}],
                'station': station,
                'source': 'только станция',
            }
            continue

        if len(people) == 1:
            person = people[0]
            directory[ext] = {
                'periods': [{'since': None, 'name': person['name'],
                             'direction': person['direction']}],
                'station': station,
                'source': 'база',
            }
            continue

        # Номер закреплён за несколькими: разводим по дате найма.
        current = None
        if station:
            current = next((p for p in people if names_match(station, p['name'])), None)
            if current is None:
                current = next((p for p in people if _shares_word(station, p['name'])), None)
        if current is None:
            people.sort(key=lambda p: p['hire_date'] or '')
            current = people[-1]
        previous = [p for p in people if p is not current]
        previous.sort(key=lambda p: p['hire_date'] or '')

        periods = []
        if previous:
            earlier = previous[-1]
            periods.append({'since': None, 'name': earlier['name'],
                            'direction': earlier['direction']})
        periods.append({'since': current['hire_date'], 'name': current['name'],
                        'direction': current['direction']})
        directory[ext] = {
            'periods': periods, 'station': station,
            'source': 'база, номер переиспользован — разведён по дате найма',
        }

    return directory


def resolver(directory):
    """(ext, время) → (ФИО, направление). Незнакомый номер называется честно:
    «Неизвестный номер 6715» лучше пустой ячейки — по нему видно, что звонок был
    и чей он, просто человека мы не знаем."""
    def resolve(ext, when):
        ext = str(ext or '')
        record = directory.get(ext)
        if not record:
            return ('Неизвестный номер ' + ext if ext else '', 'нет в справочнике')
        periods = record['periods']
        best = periods[0]
        moment = str(when or '')[:10]
        for period in periods:
            if period.get('since') and moment >= period['since']:
                best = period
        return (best['name'], best['direction'])
    return resolve
