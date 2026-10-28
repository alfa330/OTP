# -*- coding: utf-8 -*-
"""Тарифы Яндекса у карточек «Наличия авто» — из «Классификатора авто» вики.

Владелец 28.10.2026: отбор по тарифам — тарифами самого Яндекса, которые обновляются в
вики в разделе «Классификатор авто», с мультивыбором. Карточка тариф не хранит: её
модель и год выпуска сверяются с книгой классификатора (wiki/classifier.py — тот же
classifier-data.json, что и экран «Классификатора»), и тариф приходит оттуда. Обновили
классификатор — обновилось и наличие; второй копии справочника нет.

Город — Алматы: офис выдачи один (catalog.OFFICE). Машина подходит под тариф, если её
год выпуска не младше требуемого в городе (базовый год модели + поправка города — ровно
как считают калькулятор ClassifierView.jsx и помощник вики).

Модель находится по названию карточки: самое длинное имя классификатора, с которого
название начинается («Toyota Camry 70» → «Toyota Camry»), либо без первого слова
(«FAW HONGQI H5» → «Hongqi H5»). Не нашлось само («Kia Soluto») — сотрудник аренды
пишет имя из классификатора в поле карточки classifier_key.

На карточке — ВЕРХНИЙ тариф лестницы (Эконом < Комфорт < Комфорт+ < Бизнес < Ultima) и
отдельно «Электро»: машина бизнес-класса проходит и в «Комфорт», но перечислять пять
тарифов на карточке — шум. Отбор же смотрит на все подходящие: оператору, которому
нужна машина под «Комфорт+», бизнес-машина тоже годится.

Чистая логика: книга приходит параметром, базы и Flask здесь нет.
"""

import logging
import re

from wiki import text as wiki_text

CITY = 'almaty'
LADDER = ('econom', 'comfort', 'comfort_plus', 'business', 'ultima')
FLAGS = ('electro',)


def book():
    """Книга классификатора или None — тогда у карточек тарифов нет и отбор не показывается."""
    try:
        from wiki import classifier
        return classifier.book()
    except Exception:  # noqa: BLE001 — модуль вики не поднялся: наличие работает без тарифов
        logging.exception('rental: книга классификатора не прочиталась')
        return None


def _key(value):
    """Имя для сравнения — то же правило, что у классификатора: только буквы и цифры."""
    return re.sub(r'[^a-zа-я0-9]', '', wiki_text.fold_kazakh(str(value or '').lower()))


def _words(value):
    return [word for word in re.split(r'[^a-zа-я0-9]+', str(value or '').lower()) if word]


_INDEX = {}


def _index(book):
    """[(ключ имени, машина)] от длинных ключей к коротким — на книгу считается один раз."""
    cached = _INDEX.get(id(book))
    if cached is None:
        cached = sorted(((_key(car['name']), car) for car in book.get('cars') or ()),
                        key=lambda pair: -len(pair[0]))
        _INDEX.clear()
        _INDEX[id(book)] = cached
    return cached


def city_of(book, city_id=CITY):
    for city in (book or {}).get('cities') or ():
        if city.get('id') == city_id:
            return city
    return None


def city_tariffs(book, city_id=CITY):
    """Тарифы города для отбора: [{key, name}] в порядке классификатора."""
    city = city_of(book, city_id)
    if not city:
        return []
    names = book.get('tariff_names') or {}
    return [{'key': key, 'name': names.get(key, key)} for key in city.get('tariffs') or ()]


def find_car(book, title, classifier_key=None):
    """Машина классификатора по названию карточки (или по явному имени) либо None."""
    if not book:
        return None
    index = _index(book)
    if classifier_key:
        wanted = _key(classifier_key)
        for key, car in index:
            if key == wanted:
                return car
    words = _words(title)
    for variant in (_key(title), _key(' '.join(words[1:]))):
        if not variant:
            continue
        for key, car in index:        # первый подходящий — самый длинный
            if key and variant.startswith(key):
                return car
    return None


def card_year(card):
    digits = re.sub(r'\D', '', str(card.get('year') or ''))[:4]
    return int(digits) if len(digits) == 4 else 0


def fits(car, year, city):
    """Тарифы города, под которые машина этого года проходит, в порядке города."""
    if not car or not year or not city:
        return []
    offset = int(city.get('offset') or 0)
    years = car.get('years') or {}
    return [key for key in city.get('tariffs') or () if key in years and year >= int(years[key]) + offset]


def shown(keys):
    """Что писать на карточке: верхний тариф лестницы и отдельные флаги («Электро»)."""
    top = [key for key in LADDER if key in keys]
    return ([top[-1]] if top else []) + [key for key in FLAGS if key in keys]


def with_names(book, keys):
    names = (book or {}).get('tariff_names') or {}
    return [{'key': key, 'name': names.get(key, key)} for key in keys]


def card_tariffs(book, card, city_id=CITY):
    """{'fits': [...], 'shown': [...], 'model': имя в классификаторе или None} для карточки."""
    car = find_car(book, card.get('title'), card.get('classifier_key'))
    keys = fits(car, card_year(card), city_of(book, city_id)) if car else []
    return {'fits': with_names(book, keys), 'shown': with_names(book, shown(keys)),
            'model': car['name'] if car else None}
