# -*- coding: utf-8 -*-
"""Синтетический справочник «Классификатора авто» формы 2 — для тестов без базы и сети.

Зачем. Тесты помощника (test_wiki_classifier*.py) писались на справочник из
Wiki 2.0: у машины базовые годы по тарифам, у города «поправка» и набор
тарифов. Книга (wiki/classifier.build_book) этой формы больше не принимает: у
каждого города теперь своя таблица (wiki/classifier_source.py), поправок нет.
А сотни ожиданий в тестах — числа той синтетики, и переписать их руками значило
бы перепроверять каждое заново.

v2_from_legacy переводит прежнюю форму в форму 2 так, как выглядели бы
страницы городов Яндекса с этими годами: у каждого города и тарифа — СВОЯ
таблица с готовым годом max(1980, база + поправка). Поправка живёт только
здесь, книга видит явные годы по городам — как в настоящем справочнике. Поэтому
числа ожиданий остаются верными, а «поправку» в самой книге проверять нечем и
незачем.

Правила перевода — зеркало classifier_source.build и _stage_block:
  * указатель cars — одна машина на имя; двойник с русской буквой в коде
    («Jetour Х70» при «Jetour X70») сворачивается тем же ключом
    (classifier_source.model_key), и в таблице тарифа побеждает первая строка;
  * машина без года в тарифе в таблицу не попадает — requirement вернёт 'absent';
  * warnings тарифа → reqs {'text'}, номер требования — третьим элементом строки;
  * «Другие модели» → other таблиц тех тарифов, где у строки есть год;
  * остаток марки «X (кроме указанных моделей)» → brands: с годом там, где он
    есть, и «не допускается» (0) в таблицах со строкой «Другие модели». Так в
    настоящем Межгороде: LADA, ГАЗ, Москвич и УАЗ «не допускается» стоят рядом
    с «Другие модели — от 1999», иначе остаток марки ушёл бы под чужое правило;
  * таблица без строк, остатков и «Других моделей» не пишется вовсе: тарифа
    без таблицы и без правила у города нет (classifier_source.build);
  * одинаковые таблицы лежат один раз — как настоящий Межгород на 11 городов.
"""

import json
import re

from wiki import classifier_source

# Пол года калькулятора Wiki 2.0: max(1980, база + поправка). Нужен только
# синтетике — у настоящего справочника годы уже готовые.
FLOOR = 1980

_CATCH_ALL = re.compile(r'кроме указанных моделей|все модели')


def car(brand, model, warnings=None, **years):
    """Строка прежней формы: car('Toyota', 'Camry', comfort=2014)."""
    row = {'brand': brand, 'model': model, 'years': years}
    if warnings:
        row['warnings'] = warnings
    return row


def _brand_key(value):
    # Тот же ключ марки, что у classifier_source._Index: регистр и знаки не важны.
    return re.sub(r'[^0-9a-zа-яё]', '', str(value or '').lower())


def _year(base, offset):
    return max(FLOOR, base + offset)


def v2_from_legacy(data, *, source=None):
    """Прежняя форма {'cities' (с offset), 'tariffs', 'cars' (с years)} → форма 2.

    source — шапка справочника (updated, general, footnote), если тесту нужна.
    """
    tariffs = [{'key': item['key'], 'name': item['name']} for item in data['tariffs']]
    order = [item['key'] for item in tariffs]

    cars, numbers, plain, catch, other = [], {}, [], {}, None
    for raw in data['cars']:
        brand = str(raw.get('brand') or '').strip()
        model = str(raw.get('model') or '').strip()
        years = {key: int(year) for key, year in (raw.get('years') or {}).items()}
        warnings = {key: str(text).strip() for key, text in (raw.get('warnings') or {}).items()
                    if str(text).strip()}
        if brand == classifier_source.OTHER_MODELS:
            other = (years, warnings)
            continue
        if _CATCH_ALL.search(model):
            catch[brand] = (years, warnings)
            continue
        if not brand or not model:
            continue
        name = (_brand_key(brand), classifier_source.model_key(model))
        if name not in numbers:
            numbers[name] = len(cars)
            cars.append([brand, model])
        plain.append((numbers[name], years, warnings))

    reqs, req_ids, lists, list_ids = [], {}, [], {}

    def encode(year, text):
        if not text:
            return [year]
        if text not in req_ids:
            req_ids[text] = len(reqs)
            reqs.append({'text': text})
        return [year, req_ids[text]]

    def list_id(table):
        token = json.dumps(table, ensure_ascii=False, sort_keys=True)
        if token not in list_ids:
            list_ids[token] = len(lists)
            lists.append(table)
        return list_ids[token]

    cities = []
    for city in data['cities']:
        offset = int(city.get('offset') or 0)
        entries = {}
        for key in order:
            if key not in (city.get('tariffs') or ()):
                continue
            rows = {}
            for number, years, warnings in plain:
                if key in years and number not in rows:
                    rows[number] = [number] + encode(_year(years[key], offset), warnings.get(key))
            table = {'rows': [rows[number] for number in sorted(rows)]}
            if other and key in other[0]:
                table['other'] = encode(_year(other[0][key], offset), other[1].get(key))
            brands = {}
            for brand, (years, warnings) in catch.items():
                if key in years:
                    brands[brand] = encode(_year(years[key], offset), warnings.get(key))
                elif 'other' in table:
                    brands[brand] = [0]
            if brands:
                table['brands'] = brands
            if not table['rows'] and 'other' not in table and not brands:
                continue
            entries[key] = {'list': list_id(table)}
        cities.append({'id': city['id'], 'name': city['name'], 'code': city['id'],
                       'url': classifier_source.city_url(city['id']), 'updated': '',
                       'tariffs': entries})

    return {
        'version': classifier_source.VERSION,
        'source': dict(source or {}),
        'tariffs': tariffs,
        'cities': cities,
        'lists': lists,
        'reqs': reqs,
        'notes': [],
        'cars': cars,
    }
