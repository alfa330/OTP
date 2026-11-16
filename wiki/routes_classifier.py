"""Справочник «Классификатора авто» для экрана: калькулятор в статье и бар машины в поиске.

GET /api/wiki/classifier?city=<id> — указатель машин, города с их тарифами и
данные ОДНОГО города: таблицы тарифов, правила «любой марки», условия текстом.
Город один, потому что весь справочник — 400 КБ, а экрану в каждый момент нужен
город, выбранный в списке (Алматы — около 75 КБ вместе с указателем).

Расчёт «подходит ли машина такого года» делает экран
(src/components/classifier/classifierData.js — двойник wiki/classifier.requirement
и verdict): год меняют по цифре, и запрос на каждую цифру не нужен. Данные
приходят ровно те, из которых собрана книга помощника (classifier.current), —
экран и помощник не могут назвать разный год.

ДОСТУП — как у самой статьи-классификатора: справочник отдаётся, только если
статья в периметре чтения человека (perimeter.read_perimeter). Кому статья не
видна (граница пространства, запрет на статью), тому не видна и машина в
поиске — тем же правилом живёт помощник (classifier.readable_article).
"""

from flask import jsonify, make_response, request

from . import classifier as wiki_classifier
from . import perimeter
from . import schema

DEFAULT_CITY = 'almaty'

# Кэш браузера тут не нужен: портал ставит /api/ «no-store» (bot_schedule2,
# after_request), а экран и так держит пришедшие города в памяти до перезагрузки
# вкладки (classifierData.js). ETag отпечатка всё же отдаём — на условный
# запрос (If-None-Match) ответ 304 без тела.


def city_payload(data, city_id):
    """Данные одного города из справочника (форма classifier_source.VERSION).

    {id, name, url, updated, tariffs: {ключ: {kind: 'list'|'any', any?, rows?,
    other?, brands?, count?, notes, next?}}}. Строки — как в справочнике:
    [номер машины в cars, год, номер требования в reqs?]; год 0 — не допускается.
    None — города нет.
    """
    city = next((item for item in data.get('cities') or () if item.get('id') == city_id), None)
    if city is None:
        return None
    notes = data.get('notes') or []
    lists = data.get('lists') or []
    order = [item['key'] for item in data.get('tariffs') or ()]

    def entry_of(raw):
        out = {'notes': [notes[index] for index in raw.get('notes') or ()
                         if 0 <= index < len(notes)]}
        listed = raw.get('list')
        if isinstance(listed, int) and 0 <= listed < len(lists):
            table = lists[listed]
            out.update(kind='list', rows=table.get('rows') or [],
                       other=table.get('other'), brands=table.get('brands') or {},
                       # Как classifier.admitted: год «от N» или требование текстом.
                       count=sum(1 for row in table.get('rows') or () if row[1] != 0))
        elif raw.get('any') is not None:
            out.update(kind='any', any=raw['any'])
        else:
            return None
        return out

    tariffs = {}
    for key in order:
        raw = (city.get('tariffs') or {}).get(key)
        if not raw:
            continue
        entry = entry_of(raw)
        if entry is None:
            continue
        if raw.get('next'):
            upcoming = entry_of(raw['next'])
            if upcoming is not None:
                upcoming['from'] = raw['next'].get('from')
                entry['next'] = upcoming
        tariffs[key] = entry
    return {'id': city['id'], 'name': city['name'], 'url': city.get('url'),
            'updated': city.get('updated'), 'tariffs': tariffs}


def payload(data, city_id, *, digest, origin=None, state=None):
    """Ответ экрану: указатель, города, тарифы и выбранный город."""
    source = dict(data.get('source') or {})
    state = state or {}
    order = [item['key'] for item in data.get('tariffs') or ()]
    return {
        'fingerprint': digest,
        'source': {
            'url': source.get('url'),
            'title': source.get('title'),
            'updated': source.get('updated'),
            'checked_at': state.get('checked_at') or source.get('checked_at'),
            'changed_at': state.get('changed_at'),
            'origin': origin,
            'general': source.get('general') or [],
            'footnote': source.get('footnote'),
        },
        'tariffs': [{'key': item['key'], 'name': item['name']}
                    for item in data.get('tariffs') or ()],
        'cities': [{'id': city['id'], 'name': city['name'],
                    'tariffs': [key for key in order if key in (city.get('tariffs') or {})]}
                   for city in data.get('cities') or ()],
        'cars': data.get('cars') or [],
        'reqs': data.get('reqs') or [],
        'city': city_payload(data, city_id),
    }


def _readable(cursor, ctx):
    """Видна ли человеку статья-классификатор — тем же расчётом, что витрина."""
    cursor.execute('SELECT id FROM wiki_articles WHERE slug = %s', (schema.CLASSIFIER_SLUG,))
    row = cursor.fetchone()
    if not row:
        return False
    _subjects, _sections, visible = perimeter.read_perimeter(cursor, ctx)
    return row[0] in visible


def register(bp, wiki_route, db, log_ip):

    @wiki_route('/classifier')
    def wiki_classifier_data(cursor, ctx):
        if not _readable(cursor, ctx):
            # «Нет доступа» подтверждало бы, что статья есть; экран и так не
            # покажет бар машины тому, кому статья не видна.
            return jsonify({"error": "Классификатор авто недоступен",
                            "code": "WIKI_CLASSIFIER_HIDDEN"}), 404
        _book, data, digest, origin, state = wiki_classifier.current(cursor)
        if not data:
            return jsonify({"error": "Справочник классификатора не загрузился",
                            "code": "WIKI_CLASSIFIER_EMPTY"}), 503
        city_id = str(request.args.get('city') or DEFAULT_CITY).strip()[:40]
        if not any(city.get('id') == city_id for city in data.get('cities') or ()):
            city_id = (data.get('cities') or [{}])[0].get('id') or DEFAULT_CITY
        etag = '"%s:%s"' % ((digest or '')[:16], city_id)
        if request.if_none_match and etag.strip('"') in request.if_none_match:
            response = make_response('', 304)
        else:
            response = jsonify(payload(data, city_id, digest=digest, origin=origin,
                                       state=state))
        response.headers['ETag'] = etag
        return response
