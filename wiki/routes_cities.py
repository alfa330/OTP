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
from . import dispatchers as wiki_dispatchers
from . import parks as wiki_parks
from . import queries
from . import registration as wiki_registration
from . import yandex_tariffs
from .routes_structure import _clean, _int_or_none, request_space

_BAD_URL = ('Нужна ссылка на страницу тарифов Яндекс Go — например '
            'https://taxi.yandex.kz/ru_kz/almaty/tariff')


def _body():
    return request.get_json(silent=True) or {}


def registration_block(cursor, city, *, space_id):
    """Блок «Регистрация водителей» карточки — тем же правилом, что у помощника.

    Первое открытие после выкладки снимает диспетчерские сразу (до ночной
    сверки карточка была бы пустой). Архивному городу блок не нужен.
    """
    wiki_dispatchers.ensure_synced(cursor, space_id=space_id)
    catalog = wiki_registration.load(cursor, space_id=space_id)
    own = next((item for item in catalog.cities if item['id'] == city['id']), None)
    block = catalog.block(own) if own else None
    if block is not None:
        block['source'] = wiki_dispatchers.sync_state(cursor, space_id=space_id)
    return block


def city_card(cursor, city_id, *, space_id):
    """Карточка города для ответа роута: сводка, слепок и регистрация водителей."""
    city = wiki_cities.get_city(cursor, city_id, space_id=space_id)
    if city:
        city['registration'] = registration_block(cursor, city, space_id=space_id)
    return city


def _choice(value, label):
    """Выбор в форме диспетчерской: 'auto' — как подставит сверка, None —
    «без привязки», число — id. Остальное — отказ, а не молчаливое «нет»."""
    if value == 'auto' or value is None:
        return value
    number = _int_or_none(value)
    if number is None or number <= 0:
        raise wiki_cities.CityFieldError('%s пришёл в неверном виде' % label)
    return number


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
    if sent('option_commissions'):
        fields['option_commissions'] = wiki_cities.clean_option_commissions(
            data.get('option_commissions'))
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
            registration_ids = wiki_cities.clean_city_ids(data.get('registration_city_ids'))
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
                "city": city_card(cursor, city_id, space_id=space_id),
            })

        city_id = wiki_cities.create_city(cursor, fields=fields,
                                          created_by=ctx['user_id'], space_id=space_id)
        if office_ids:
            wiki_cities.set_city_offices(cursor, city_id, office_ids, space_id=space_id)
        if registration_ids:
            wiki_cities.set_registration_cities(cursor, city_id, registration_ids,
                                                space_id=space_id)
        queries.log_action(cursor, actor_id=ctx['user_id'], action='city.create',
                           entity_type='city', entity_id=city_id,
                           details={'name': fields['name'], 'space_id': space_id},
                           ip_address=log_ip())
        sync = _sync(cursor, ctx, city_id, space_id) if fields.get('yandex_url') else None
        return jsonify({
            "id": city_id,
            "sync": sync,
            "city": city_card(cursor, city_id, space_id=space_id),
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
            city['registration'] = registration_block(cursor, city, space_id=space_id)
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
            # «Куда регистрировать водителей» — тоже связью и тоже целиком.
            registration_ids = (wiki_cities.clean_city_ids(data.get('registration_city_ids'))
                                if 'registration_city_ids' in data else None)
        except wiki_cities.CityFieldError as field_error:
            return _bad(field_error)
        if 'name' in fields:
            if not fields['name']:
                return jsonify({"error": "Название города не может быть пустым"}), 400
            found = wiki_cities.find_by_name(cursor, fields['name'], exclude_id=city_id,
                                             space_id=space_id)
            if found:
                return _taken(found[1])
        if not fields and office_ids is None and registration_ids is None:
            return jsonify({"error": "Нечего обновлять"}), 400

        if fields:
            wiki_cities.update_city(cursor, city_id, fields, space_id=space_id,
                                    updated_by=ctx['user_id'])
        changed = sorted(fields.keys())
        if office_ids is not None:
            wiki_cities.set_city_offices(cursor, city_id, office_ids, space_id=space_id)
            changed.append('driver_office_ids')
        if registration_ids is not None:
            wiki_cities.set_registration_cities(cursor, city_id, registration_ids,
                                                space_id=space_id)
            changed.append('registration_city_ids')
        if not fields:
            # Дата «Обновлено» обязана сдвинуться и от правки одних списков.
            wiki_cities.touch_city(cursor, city_id, space_id=space_id,
                                   updated_by=ctx['user_id'])
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
            "city": city_card(cursor, city_id, space_id=space_id),
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
            "city": city_card(cursor, city_id, space_id=space_id),
        })

    # ── Диспетчерские кабинета Fleet ─────────────────────────────────────────
    #
    # Список читают все, у кого есть вкладка: это тот же справочник, что в
    # карточке города, только целиком. Привязку к парку и городу правит тот
    # же круг, что и сам справочник (_may_edit).

    def _dispatchers_payload(cursor, space_id):
        wiki_dispatchers.ensure_synced(cursor, space_id=space_id)
        return {
            'items': wiki_dispatchers.list_dispatchers(cursor, space_id=space_id),
            'source': wiki_dispatchers.sync_state(cursor, space_id=space_id),
            'has_source': space_id == wiki_dispatchers.fleet_space_id(cursor),
            # Варианты выбора — тем же ответом: окну привязки не нужно ходить
            # за парками и городами отдельно.
            'parks': [{'id': park['id'], 'name': park['name']}
                      for park in wiki_parks.list_parks(cursor, space_id=space_id)],
            'cities': [{'id': city['id'], 'name': city['name']}
                       for city in wiki_cities.registration_cities(cursor, space_id=space_id)],
        }

    @wiki_route('/dispatchers', methods=('GET',))
    def wiki_dispatchers_list(cursor, ctx):
        space_id, error = request_space(cursor, ctx)
        if error:
            return error
        payload = _dispatchers_payload(cursor, space_id)
        payload['can_manage'] = _may_edit(ctx)
        return jsonify(payload)

    @wiki_route('/dispatchers/sync', methods=('POST',))
    def wiki_dispatchers_sync(cursor, ctx):
        """«Обновить из кабинета» — снять кэш «Рассылок» сейчас, не дожидаясь ночи."""
        if not _may_edit(ctx):
            return _forbidden()
        space_id, error = request_space(cursor, ctx)
        if error:
            return error
        result = wiki_dispatchers.sync_from_fleet(cursor, space_id=space_id)
        if result.get('status') == 'ok':
            queries.log_action(cursor, actor_id=ctx['user_id'], action='dispatcher.sync',
                               entity_type='space', entity_id=space_id,
                               details={key: result.get(key) for key in
                                        ('total', 'linked', 'added', 'gone')},
                               ip_address=log_ip())
        payload = _dispatchers_payload(cursor, space_id)
        payload.update(sync=result, can_manage=True)
        return jsonify(payload)

    @wiki_route('/dispatchers/<int:dispatcher_id>', methods=('PATCH',))
    def wiki_dispatcher_item(cursor, ctx, dispatcher_id):
        """Привязка диспетчерской: {park_id, city_id} — id, null («нет») или 'auto'."""
        if not _may_edit(ctx):
            return _forbidden()
        space_id, error = request_space(cursor, ctx)
        if error:
            return error
        dispatcher = wiki_dispatchers.get_dispatcher(cursor, dispatcher_id, space_id=space_id)
        if not dispatcher:
            return jsonify({"error": "Диспетчерская не найдена"}), 404
        data = _body()
        try:
            park = _choice(data['park_id'], 'Парк') if 'park_id' in data else False
            city = _choice(data['city_id'], 'Город') if 'city_id' in data else False
        except wiki_cities.CityFieldError as field_error:
            return _bad(field_error)
        if park is False and city is False:
            return jsonify({"error": "Нечего обновлять"}), 400
        auto_park, auto_city = (wiki_dispatchers.auto_values(cursor, dispatcher, space_id=space_id)
                                if 'auto' in (park, city) else (None, None))
        wiki_dispatchers.update_dispatcher(
            cursor, dispatcher_id, space_id=space_id,
            park=None if park is False else ((auto_park, False) if park == 'auto' else (park, True)),
            city=None if city is False else ((auto_city, False) if city == 'auto' else (city, True)))
        updated = wiki_dispatchers.get_dispatcher(cursor, dispatcher_id, space_id=space_id)
        # Журнал читают люди: названия словами, «как в кабинете» вместо auto, и
        # только то поле, которое правили (разбор 29.10.2026: было «парк: auto»,
        # «парк: №3» и ни слова о том, какая диспетчерская).
        details = {'dispatcher': '%s (%s)' % (dispatcher['name'],
                                              dispatcher['fleet_city'] or 'город не указан')}
        if park is not False:
            details['park'] = ((updated or {}).get('park_name') or 'без парка') + (
                ' — как в кабинете' if park == 'auto' else '')
        if city is not False:
            details['city'] = ((updated or {}).get('city_name') or 'не выбран') + (
                ' — как в кабинете' if city == 'auto' else '')
        queries.log_action(cursor, actor_id=ctx['user_id'], action='dispatcher.update',
                           entity_type='dispatcher', entity_id=dispatcher_id,
                           details=details, ip_address=log_ip())
        return jsonify({'dispatcher': updated})
