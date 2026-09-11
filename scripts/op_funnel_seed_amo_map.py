# -*- coding: utf-8 -*-
"""Сопоставление операторов amoCRM для «Воронки ОП» без админского токена.

Зачем
-----
В amoCRM у сделки лежит ЧИСЛОВОЙ id ответственного, а имени нет: справочник
`/api/v4/users` открыт только администраторам, и наша учётка им не является
(403 «Admin access only», проверено 11.09.2026 — в том числе на запросе одного
пользователя). Без имени таб «Основа ОП» показывал бы одиннадцать безымянных
чисел.

Долгоживущий токен тоже не помог: ключ, выданный 11.09.2026, amoCRM не принимает
ни как `Bearer`, ни как `X-Auth-Token`, ни как `X-Api-Key` — на всех вариантах
401 «Неверный логин или пароль» (у токенов amoCRM другая форма: это JWT с
точками, а выданный ключ — 201 символ без них).

Как обходимся
-------------
Соответствие выводится из данных, которые уже есть, в три звена:

1. **id → имя в amoCRM.** В нашей таблице `amo_leads` у сделки числовой
   `responsible`, в выгрузке супервайзера — та же сделка с именем. Считаем по
   каждому дню, сколько сделок у id и сколько у имени, и сравниваем векторы.
   Вектор семимерный со значениями в сотнях: у чужой пары расхождение измеряется
   сотнями, у своей — единицами (наша ночная выгрузка и выгрузка супервайзера
   сняты в разные моменты). На данных 01–07.09.2026 из 29 id так определились 25,
   причём ближайший кандидат ошибался на 0–9 сделок, а следующий — на 149–637.

2. **имя в amoCRM → ФИО сотрудника.** Это уже сведено руками в самом файле
   супервайзера: подпись строки — ФИО, а внутри формулы COUNTIFS стоит имя из
   amoCRM. Оттуда и читаем: «Nurmakhan 6323» → Сагидоллаев Нурмахан,
   «Кенжебай Адильхан» → Кенжебай Әділхан, «Жаксылык Нуралы» → Жақсылық Нұралы.

3. **ФИО → id в портале** по составу направления «Основа ОП», со сведением
   казахских букв к русским аналогам (ә→а, қ→к, ұ→у, і→и): в написании ФИО это
   единственное расхождение между системами.

Пара записывается ТОЛЬКО при однозначном совпадении на всех трёх звеньях.
Остальное остаётся человеку на экране сопоставления: приблизительная догадка
поставила бы чужие цифры в чужую строку, и в отчёте это не видно.

Запуск
------
    python -X utf8 scripts/op_funnel_seed_amo_map.py --xlsx «файл супервайзера.xlsx»
    python -X utf8 scripts/op_funnel_seed_amo_map.py --xlsx ... --apply

Без `--apply` только печатает, что собирается записать. Повторный запуск
безопасен: уже сопоставленные строки не трогаются.
"""

import argparse
import datetime
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DIRECTION_CODE = 'op_osnova'
SOURCE = 'amo'

# Допуск сравнения векторов: 2 % объёма, но не меньше семи сделок за период —
# иначе у оператора с двумя сделками любой сосед оказывался бы «близким».
REL_TOLERANCE = 0.02
MIN_TOLERANCE = 7
# Во столько раз второй кандидат должен быть хуже первого, чтобы пара считалась
# однозначной.
MARGIN = 10

_KZ_FOLD = str.maketrans({
    'ә': 'а', 'қ': 'к', 'ң': 'н', 'ө': 'о', 'ұ': 'у', 'ү': 'у',
    'һ': 'х', 'і': 'и', 'ғ': 'г', 'ё': 'е',
})


def low(value):
    return ' '.join(str(value or '').strip().lower().split())


def fold(value):
    return low(value).translate(_KZ_FOLD).replace('ъ', '').replace('ь', '')


def parse_day(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value or '').strip()
    for pattern in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def read_supervisor_file(path):
    """Из файла супервайзера: объёмы по именам и связка «имя amoCRM → ФИО»."""
    from openpyxl import load_workbook

    values = load_workbook(path, data_only=True, read_only=True)
    by_name = defaultdict(lambda: defaultdict(int))
    days = set()
    for sheet_name in ('Свод данных', 'Свод вход'):
        if sheet_name not in values.sheetnames:
            continue
        for row in values[sheet_name].iter_rows(min_row=2, values_only=True):
            cells = (list(row) + [None] * 3)[:3]
            responsible, _stage, day_raw = cells
            day = parse_day(day_raw)
            if responsible and day:
                by_name[str(responsible).strip()][day] += 1
                days.add(day)

    formulas = load_workbook(path, data_only=False)
    sheet = formulas['Общий']
    amo_to_human = {}
    for row in range(1, sheet.max_row + 1):
        label = sheet.cell(row=row, column=27).value      # AA — ФИО оператора
        formula = sheet.cell(row=row, column=38).value    # AL — COUNTIFS по имени
        if not label or not isinstance(formula, str):
            continue
        found = re.search(r"'Свод данных'!A:A\s*,\s*\"([^\"]+)\"", formula)
        if found:
            amo_to_human[low(found.group(1))] = str(label).strip()
    return by_name, sorted(days), amo_to_human


def read_our_volumes(cursor, day_from, day_to):
    cursor.execute(
        "SELECT responsible, created_date, COUNT(*) FROM amo_leads "
        "WHERE created_date BETWEEN %s AND %s GROUP BY responsible, created_date",
        (day_from, day_to),
    )
    by_id = defaultdict(lambda: defaultdict(int))
    for responsible, day, count in cursor.fetchall():
        if responsible:
            by_id[str(responsible).strip()][day] = int(count)
    return by_id


def match_ids_to_names(by_id, by_name, days):
    """Звено 1: id → имя в amoCRM по векторам суточных объёмов."""
    def vector(bucket):
        return [bucket.get(day, 0) for day in days]

    name_vectors = {name: vector(bucket) for name, bucket in by_name.items()}
    good, left = {}, []
    claimed = defaultdict(list)

    for code, bucket in by_id.items():
        mine = vector(bucket)
        volume = sum(mine)
        if volume == 0:
            continue
        ranked = sorted(
            (sum(abs(a - b) for a, b in zip(mine, other)), name)
            for name, other in name_vectors.items()
        )
        best, name = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else None
        limit = max(volume * REL_TOLERANCE, MIN_TOLERANCE)
        near = best <= limit
        alone = second is None or best == 0 or second >= best * MARGIN
        if near and alone:
            claimed[name].append((code, best, volume))
        else:
            left.append((code, name, best, second, volume))

    for name, group in claimed.items():
        if len(group) == 1:
            code, best, volume = group[0]
            good[code] = {'amo_name': name, 'distance': best, 'volume': volume}
        else:
            # Два id на одно имя — решает человек: это либо дубль учётки, либо
            # совпадение объёмов у двух малозагруженных аккаунтов.
            for code, best, volume in group:
                left.append((code, name, best, None, volume))
    return good, left


def resolve_people(cursor, amo_to_human):
    """Звенья 2 и 3: имя amoCRM → ФИО → сотрудник направления."""
    cursor.execute(
        """
        SELECT DISTINCT u.id, u.name
        FROM users u
        LEFT JOIN group_operator_memberships gom ON gom.operator_id = u.id
        LEFT JOIN groups g ON g.id = gom.group_id
        LEFT JOIN directions d ON d.id = u.direction_id
        WHERE g.calculation_model_code = %s OR d.calculation_model_code = %s
        """,
        (DIRECTION_CODE, DIRECTION_CODE),
    )
    people = {}
    for user_id, name in cursor.fetchall():
        people[low(name)] = (user_id, name)
    folded = {fold(name): value for name, value in people.items()}

    out = {}
    for amo_name, human in amo_to_human.items():
        found = people.get(low(human)) or folded.get(fold(human))
        if not found:
            words = set(fold(human).split())
            for key, value in folded.items():
                if set(key.split()) == words:
                    found = value
                    break
        if found:
            out[amo_name] = found
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--xlsx', required=True, help='файл супервайзера «Основы ОП»')
    parser.add_argument('--apply', action='store_true', help='записать в базу')
    args = parser.parse_args()

    from database import Database  # импорт здесь: поднимает пул к базе

    by_name, days, amo_to_human = read_supervisor_file(args.xlsx)
    if not days:
        raise SystemExit('В файле не нашлось листа «Свод данных» с датами')
    print('период сверки: %s — %s, суток %d' % (days[0], days[-1], len(days)))
    print('имён в выгрузке: %d, связок «имя → ФИО» в формулах: %d'
          % (len(by_name), len(amo_to_human)))

    db = Database()
    from op_funnel import queries

    with db._get_cursor() as cursor:
        by_id = read_our_volumes(cursor, days[0], days[-1])
        matched, left = match_ids_to_names(by_id, by_name, days)
        people = resolve_people(cursor, amo_to_human)

        rows = []
        for code, info in matched.items():
            found = people.get(low(info['amo_name']))
            if found:
                rows.append((code, info['amo_name'], found[0], found[1], info))

        print()
        print('=== СВЯЗЫВАЕТСЯ: %d ===' % len(rows))
        for code, amo_name, user_id, user_name, info in sorted(rows, key=lambda r: -r[4]['volume']):
            print('  %-12s %-26s -> %-26s id %-5s сделок %5d, расхождение %d'
                  % (code, amo_name[:26], user_name[:26], user_id, info['volume'],
                     info['distance']))

        no_person = [item for item in matched.items() if low(item[1]['amo_name']) not in people]
        if no_person:
            print()
            print('=== ИМЯ УЗНАЛИ, СОТРУДНИКА НЕТ: %d ===' % len(no_person))
            for code, info in sorted(no_person, key=lambda kv: -kv[1]['volume']):
                print('  %-12s %-26s сделок %d' % (code, info['amo_name'][:26], info['volume']))

        if left:
            print()
            print('=== ОСТАВЛЕНО ЧЕЛОВЕКУ: %d ===' % len(left))
            for code, name, best, second, volume in sorted(left, key=lambda r: -r[4]):
                print('  %-12s ближайшее «%s», сделок %d, расхождение %s, второй %s'
                      % (code, name[:24], volume, best, second))

        if not args.apply:
            print()
            print('Ничего не записано. Повторите с --apply, чтобы сохранить связки.')
            return

        # Строки заводим, если их ещё нет, и проставляем сотрудника только там,
        # где он пока не указан: ручное решение человека сильнее нашей догадки.
        queries.touch_operator_map(
            cursor, SOURCE, {code: info['amo_name'] for code, info in matched.items()},
            DIRECTION_CODE)
        written = 0
        existing = {
            row['external_key']: row
            for row in queries.read_operator_map(cursor, sources=[SOURCE])
        }
        for code, amo_name, user_id, user_name, _info in rows:
            current = existing.get(code)
            if current and current.get('user_id'):
                continue
            queries.set_operator_map(cursor, SOURCE, code, user_id, None, False)
            written += 1
        print()
        print('Записано связок: %d (уже сопоставленные не трогали)' % written)


if __name__ == '__main__':
    main()
