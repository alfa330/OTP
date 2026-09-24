"""Эндпоинты городов (задача #322).

Каждый роут начинается с request_space: справочник принадлежит пространству,
и без проверки чужой id в строке запроса открыл бы города соседней вики —
гард во фронте отсекает вкладку, но не запрос. Чужой город отвечает «Город не
найден»: «нет доступа» подтверждало бы, что он есть.

Правит тот же круг, что и офисы с парками (routes_offices._may_edit): у
справочника один хозяин по смыслу — те, кто ведёт содержимое вики.
"""

from flask import jsonify, request

from . import access as wiki_access
from . import cities as wiki_cities
from . import queries
from . import yandex_tariffs
from .routes_structure import _clean, _int_or_none, request_space

_BAD_URL = ('Нужна ссылка на страницу тарифов Яндекс Go — например '
            'https://taxi.yandex.kz/ru_kz/almaty/tariff')


def _body():
    return request.get_json(silent=True) or {}


def _fields(cursor, data, *, partial, space_id):
    """Поля города из тела запроса. Бросает CityFieldError с текстом для 400.

    partial=True (PATCH) берёт только присланные ключи: правка комиссии не
    должна стирать услуги парка.
    """
    fields = {}

    def sent(key):
        return not partial or key in data

    if sent('name'):
        fields['name'] = _clean(data.get('name'), 120)
    if sent('yandex_url'):
        raw = _clean(data.get('yandex_url'), 1000)
        if raw:
            url = yandex_tariffs.canonical_url(raw)
            if not url:
                raise wiki_cities.CityFieldError(_BAD_URL)
            fields['yandex_url'] = url
        else:
            fields['yandex_url'] = None
    if sent('serving_office_id'):
        # Чужой или архивный офис сохраняется как «не выбран», без ошибки —
        # см. cities.own_office.
        fields['serving_office_id'] = wiki_cities.own_office(
            cursor, _int_or_none(data.get('serving_office_id')), space_id=space_id)
    if sent('park_commission'):
        fields['park_commission'] = wiki_cities.parse_percent(
            data.get('park_commission'), 'Комиссия парка')
    if sent('tariff_meta'):
        fields['tariff_meta'] = wiki_cities.clean_tariff_meta(data.get('tariff_meta'))
    if sent('extra_tariffs'):
        fields['extra_tariffs'] = wiki_cities.clean_extra_tariffs(data.get('extra_tariffs'))
    if sent('services'):
        fields['services'] = wiki_cities.clean_services(data.get('services'))
    if sent('note'):
        fields['note'] = _clean(data.get('note'), 2000)
    if data.get('status') in ('active', 'archived'):
        fields['status'] = data['status']
    if 'position' in data:
        fields['position'] = _int_or_none(data['position']) or 0
    return fields


def register(bp, wiki_route, db, log_ip):

    def _may_edit(ctx):
        # Порог тот же, что у офисов и парков: хоть что-то сверх чтения.
        # Подробное обоснование — routes_offices._may_edit.
        return wiki_access.has_write_capability(ctx['capabilities'])

    def _forbidden():
        return jsonify({"error": "Справочник правит тот, у кого есть права сверх чтения",
                        "code": "WIKI_FORBIDDEN"}), 403

    def _not_found():
        return jsonify({"error": "Город не найден"}), 404

    def _bad(error):
        return jsonify({"error": str(error), "code": "WIKI_CITY_FIELD"}), 400

    def _taken(status):
        if status == 'archived':
            # Только для переименования: добавление архивного города
            # возвращает его карточку (см. POST /cities).
            return jsonify({"error": "Этот город в архиве — добавьте его через «+ Город», "
                                     "и вернётся его прежняя карточка",
                            "code": "WIKI_CITY_ARCHIVED"}), 409
        return jsonify({"error": "Такой город уже есть в списке",
                        "code": "WIKI_CITY_EXISTS"}), 409

    def _sync(cursor, ctx, city_id, space_id):
        """Сверка сразу после сохранения ссылки — одна страница, под курсором.

        Ошибку источника не превращаем в ошибку сохранения: город записан, а
        Яндекс мог просто не ответить. Человек увидит причину рядом с кнопкой
        «Обновить с Яндекса» и повторит.
        """
        result = wiki_cities.sync_city(cursor, city_id, space_id=space_id)
        if result and result['status'] != 'error':
            queries.log_action(cursor, actor_id=ctx['user_id'], action='city.sync',
                               entity_type='city', entity_id=city_id,
                               details={'status': result['status']}, ip_address=log_ip())
        return result

    @wiki_route('/cities', methods=('GET', 'POST'))
    def wiki_cities_list(cursor, ctx):
        space_id, error = request_space(cursor, ctx)
        if error:
            return error

        if request.method == 'GET':
            return jsonify({
                'items': wiki_cities.list_cities(cursor, space_id=space_id),
                'can_manage': _may_edit(ctx),
            })

        if not _may_edit(ctx):
            return _forbidden()
        data = _body()
        try:
            fields = _fields(cursor, data, partial=False, space_id=space_id)
        except wiki_cities.CityFieldError as field_error:
            return _bad(field_error)
        if not fields.get('name'):
            return jsonify({"error": "Укажите город"}), 400
        try:
            office_ids = wiki_cities.clean_office_ids(data.get('driver_office_ids'))
        except wiki_cities.CityFieldError as field_error:
            return _bad(field_error)
        found = wiki_cities.find_by_name(cursor, fields['name'], space_id=space_id)
        if found and found[1] == 'active':
            return _taken(found[1])
        if found:
            # Город уже был и лежит в архиве. Своего переключателя архива у
            # вкладки нет, поэтому «добавить снова» и есть возврат: та же
            # карточка со всем, что в неё вписывали, а не вторая пустая рядом.
            # Поля формы не применяем — они пустые у нового города и стёрли бы
            # прежние комиссии и услуги; править — уже в открытой карточке.
            city_id = found[0]
            wiki_cities.update_city(cursor, city_id, {'status': 'active'},
                                    space_id=space_id, updated_by=ctx['user_id'])
            queries.log_action(cursor, actor_id=ctx['user_id'], action='city.restore',
                               entity_type='city', entity_id=city_id,
                               details={'name': fields['name']}, ip_address=log_ip())
            return jsonify({
                "id": city_id,
                "restored": True,
                "sync": None,
                "city": wiki_cities.get_city(cursor, city_id, space_id=space_id),
            })

        city_id = wiki_cities.create_city(cursor, fields=fields,
                                          created_by=ctx['user_id'], space_id=space_id)
        if office_ids:
            wiki_cities.set_city_offices(cursor, city_id, office_ids, space_id=space_id)
        queries.log_action(cursor, actor_id=ctx['user_id'], action='city.create',
                           entity_type='city', entity_id=city_id,
                           details={'name': fields['name'], 'space_id': space_id},
                           ip_address=log_ip())
        sync = _sync(cursor, ctx, city_id, space_id) if fields.get('yandex_url') else None
        return jsonify({
            "id": city_id,
            "sync": sync,
            "city": wiki_cities.get_city(cursor, city_id, space_id=space_id),
        }), 201

    @wiki_route('/cities/<int:city_id>', methods=('GET', 'PATCH', 'DELETE'))
    def wiki_city_item(cursor, ctx, city_id):
        space_id, error = request_space(cursor, ctx)
        if error:
            return error
        city = wiki_cities.get_city(cursor, city_id, space_id=space_id)
        can_manage = _may_edit(ctx)

        if request.method == 'GET':
            # Архивный город читателю «не найден» — так же, как в списке его нет.
            if not city or (city['status'] != 'active' and not can_manage):
                return _not_found()
            return jsonify({'city': city, 'can_manage': can_manage})

        if not can_manage:
            return _forbidden()
        if not city:
            return _not_found()

        if request.method == 'DELETE':
            # Архивируем, как офисы и парки: ручные поля города — чья-то
            # работа, и удаление стёрло бы её без следа в журнале.
            wiki_cities.update_city(cursor, city_id, {'status': 'archived'},
                                    space_id=space_id, updated_by=ctx['user_id'])
            queries.log_action(cursor, actor_id=ctx['user_id'], action='city.archive',
                               entity_type='city', entity_id=city_id, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        try:
            fields = _fields(cursor, data, partial=True, space_id=space_id)
            # Офисы «куда направлять» — связью, а не полем города: приходят
            # только если их прислали, и тогда заменяют список целиком.
            office_ids = (wiki_cities.clean_office_ids(data.get('driver_office_ids'))
                          if 'driver_office_ids' in data else None)
        except wiki_cities.CityFieldError as field_error:
            return _bad(field_error)
        if 'name' in fields:
            if not fields['name']:
                return jsonify({"error": "Название города не может быть пустым"}), 400
            found = wiki_cities.find_by_name(cursor, fields['name'], exclude_id=city_id,
                                             space_id=space_id)
            if found:
                return _taken(found[1])
        if not fields and office_ids is None:
            return jsonify({"error": "Нечего обновлять"}), 400

        if fields:
            wiki_cities.update_city(cursor, city_id, fields, space_id=space_id,
                                    updated_by=ctx['user_id'])
        changed = sorted(fields.keys())
        if office_ids is not None:
            wiki_cities.set_city_offices(cursor, city_id, office_ids, space_id=space_id)
            if not fields:
                # Дата «Обновлено» обязана сдвинуться и от правки одного списка.
                wiki_cities.touch_city(cursor, city_id, space_id=space_id,
                                       updated_by=ctx['user_id'])
            changed.append('driver_office_ids')
        queries.log_action(cursor, actor_id=ctx['user_id'], action='city.update',
                           entity_type='city', entity_id=city_id,
                           details={'fields': changed}, ip_address=log_ip())
        # Сверяем, только если ссылку правда сменили: иначе каждое сохранение
        # комиссии ходило бы к Яндексу.
        url_changed = 'yandex_url' in fields and fields['yandex_url'] \
            and fields['yandex_url'] != city.get('yandex_url')
        sync = _sync(cursor, ctx, city_id, space_id) if url_changed else None
        return jsonify({
            "status": "ok",
            "sync": sync,
            "city": wiki_cities.get_city(cursor, city_id, space_id=space_id),
        })

    @wiki_route('/cities/<int:city_id>/sync', methods=('POST',))
    def wiki_city_sync(cursor, ctx, city_id):
        """«Обновить с Яндекса» — не дожидаясь ночной сверки."""
        if not _may_edit(ctx):
            return _forbidden()
        space_id, error = request_space(cursor, ctx)
        if error:
            return error
        result = _sync(cursor, ctx, city_id, space_id)
        if result is None:
            return _not_found()
        return jsonify({
            "sync": result,
            "city": wiki_cities.get_city(cursor, city_id, space_id=space_id),
        })
