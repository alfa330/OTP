"""Общая выборка «машина × город × тариф × год» для двух расчётов классификатора.

    PYTHONPATH=. venv/bin/python tests/gen_classifier_parity.py

Экран считает «подходит ли машина» сам (src/components/classifier/classifierData.js:
requirementOf, verdictOf), помощник — питоном (wiki/classifier.py: requirement,
verdict). Разойдутся — и помощник назовёт год, которого нет на экране. Поэтому
ожидания считает ПИТОН по настоящему справочнику (wiki/data/classifier-data.json)
и пишет в tests/data/classifier_parity.json; node-тест
(tests/classifier_data.test.mjs) сверяет с ними JS, а pytest
(tests/test_classifier_parity_fixture.py) — что файл не протух относительно
питона и справочника.

Выборка детерминированная: в каждом тарифе каждого города — первые строки
таблицы, строки с оговоркой, машина вне таблицы (остаток марки, «Другие модели»
или «нет в списке») и правило «любая модель»; годы — на границах требования и
внутри исключения.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from wiki import classifier  # noqa: E402

PATH = os.path.join(ROOT, 'tests', 'data', 'classifier_parity.json')

# Машины, на которых видны все виды требований: строки с оговорками, LADA вне
# таблицы Межгорода (правило марки), машины вне таблиц («Другие модели»).
ANCHORS = (('Chevrolet', 'Cobalt'), ('BYD', 'Qin Plus'), ('BYD', 'Han'), ('Changan', 'Alsvin'),
           ('Toyota', 'Camry'), ('Toyota', 'Alphard'), ('LADA (ВАЗ)', 'Granta'),
           ('LADA (ВАЗ)', '2101'), ('Chevrolet', 'Onix'), ('Hyundai', 'Solaris'), ('Kia', 'K5'))


def _years(req):
    years = {2026, 1987}
    if req['year']:
        years |= {req['year'] - 1, req['year']}
    if req.get('except'):
        years |= {req['except'][0], req['except'][1], req['except'][0] - 1}
    return sorted(years)


def build_cases(book):
    by_name = {(car['brand'], car['model']): car for car in book['cars']}
    by_index = {car['index']: car for car in book['cars']}
    anchors = [by_name[pair] for pair in ANCHORS if pair in by_name]
    cases = []
    for city in book['cities']:
        for key in city['tariffs']:
            entry = city['entries'][key]
            picked = list(anchors)
            if entry['list'] is not None:
                rows = entry['list']['rows']
                listed = sorted(rows)[:3]
                worded = sorted(index for index, req in rows.items() if req.get('text'))[:3]
                picked += [by_index[index] for index in listed + worded if index in by_index]
            seen = set()
            for car in picked:
                if car['index'] in seen:
                    continue
                seen.add(car['index'])
                req = classifier.requirement(car, city, key)
                cases.append({
                    'city': city['id'], 'tariff': key, 'brand': car['brand'], 'model': car['model'],
                    'kind': req['kind'], 'year': req['year'],
                    'verdicts': {str(year): classifier.verdict(req, year) for year in _years(req)},
                })
    return cases


def dump(cases):
    """Файл выборки: по случаю на строку — дифф после пересборки читается глазами."""
    lines = [json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
             for case in cases]
    return '{"cases": [\n' + ',\n'.join(lines) + '\n]}\n'


def main():
    book = classifier.build_book(classifier.read_data())
    cases = build_cases(book)
    with open(PATH, 'w', encoding='utf-8') as handle:
        handle.write(dump(cases))
    print('случаев: %d → %s' % (len(cases), PATH))


if __name__ == '__main__':
    main()
