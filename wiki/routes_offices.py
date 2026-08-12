"""Эндпоинты офисов.

Отдельным модулем, а не внутри routes_parks: у офисов своя форма с картой,
графиком и переопределениями по паркам, и вместе с акциями файл стал бы
третьим по величине в разделе.
"""

from flask import Response, jsonify, request

from . import offices as wiki_offices
from . import queries
from .routes_structure import _clean, _int_or_none, _slugify


def _body():
    return request.get_json(silent=True) or {}


def _coord_or_none(value, limit):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 6) if -limit <= number <= limit else None


def _links(data):
    """Привязки к паркам из тела запроса.

    Принимаем и голый список id (park_ids), и список объектов с
    переопределениями — форма шлёт второе, скрипт переноса и внешние вызовы
    удобнее пишутся первым.
    """
    if isinstance(data.get('parks'), list):
        result = []
        for item in data['parks']:
            if isinstance(item, dict):
                result.append({
                    'park_id': _int_or_none(item.get('park_id')),
                    'phone': _clean(item.get('phone'), 64),
                    'schedule': item.get('schedule'),
                    'note': _clean(item.get('note'), 500),
                })
            else:
                result.append({'park_id': _int_or_none(item)})
        return [item for item in result if item['park_id']]

    if isinstance(data.get('park_ids'), list):
        return [{'park_id': _int_or_none(p)} for p in data['park_ids'] if _int_or_none(p)]
    return None


def _fields(data, *, partial):
    """Поля офиса из тела запроса.

    partial=True (PATCH) кладёт в результат только присланные ключи, иначе
    правка одного телефона обнулила бы адрес и график.
    """
    fields = {}

    def take(key, cleaner):
        if not partial or key in data:
            fields[key] = cleaner(data.get(key))

    take('city', lambda v: _clean(v, 120))
    take('address', lambda v: _clean(v, 1000))
    take('address_note', lambda v: _clean(v, 2000))
    take('phone', lambda v: _clean(v, 64))
    take('partner_label', lambda v: _clean(v, 120))
    take('schedule', wiki_offices.normalize_schedule)
    take('is_online', bool)
    take('all_parks', bool)

    if not partial or 'kind' in data:
        fields['kind'] = 'partner' if data.get('kind') == 'partner' else 'park'

    # Координаты берутся только вместе со ссылкой: карта обязана показывать ту
    # же точку, что откроется по клику. Разошедшиеся ссылка и точка — худший
    # исход, он выглядит как рабочая карта и врёт молча.
    if not partial or 'map_url' in data:
        url = _clean(data.get('map_url'), 1000)
        fields['map_url'] = url
        if not url:
            fields.update({'map_resolved_url': None, 'lat': None, 'lon': None})
        else:
            fields['map_resolved_url'] = _clean(data.get('map_resolved_url'), 1000) or url
            fields['lat'] = _coord_or_none(data.get('lat'), 90)
            fields['lon'] = _coord_or_none(data.get('lon'), 180)
            if fields['lat'] is None or fields['lon'] is None:
                fields['lat'] = fields['lon'] = None

    if data.get('status') in ('active', 'archived'):
        fields['status'] = data['status']
    if 'position' in data:
        fields['position'] = _int_or_none(data['position']) or 0
    return fields


def register(bp, wiki_route, db, log_ip):

    @wiki_route('/offices', methods=('GET', 'POST'))
    def wiki_offices_list(cursor, ctx):
        if request.method == 'GET':
            can_manage = bool(ctx['capabilities'].get('can_manage_structure')
                              or ctx['capabilities'].get('can_manage_access'))
            return jsonify({
                'items': wiki_offices.list_offices(
                    cursor,
                    include_archived=can_manage,
                    query=request.args.get('q'),
                    park_id=_int_or_none(request.args.get('park_id')),
                    city=request.args.get('city'),
                ),
                'cities': wiki_offices.cities(cursor),
                'can_manage': can_manage,
            })

        if not ctx['capabilities'].get('can_manage_structure'):
            return jsonify({"error": "Нет права управлять справочником",
                            "code": "WIKI_FORBIDDEN"}), 403

        data = _body()
        name = _clean(data.get('name'))
        if not name:
            return jsonify({"error": "Укажите название офиса"}), 400

        slug = _clean(data.get('slug'), 120) or _slugify(name)
        base, suffix = slug, 2
        while not wiki_offices.slug_is_free(cursor, slug):
            slug = '%s-%d' % (base, suffix)
            suffix += 1

        office_id = wiki_offices.create_office(
            cursor, slug=slug, name=name, created_by=ctx['user_id'],
            fields=_fields(data, partial=False))
        wiki_offices.set_office_parks(cursor, office_id, _links(data) or [])

        queries.log_action(cursor, actor_id=ctx['user_id'], action='office.create',
                           entity_type='office', entity_id=office_id,
                           details={'name': name}, ip_address=log_ip())
        return jsonify({"id": office_id, "slug": slug}), 201

    @wiki_route('/offices/<int:office_id>', methods=('PATCH', 'DELETE'),
                capability='can_manage_structure')
    def wiki_office_item(cursor, ctx, office_id):
        if request.method == 'DELETE':
            # Архивируем, как парки и акции: за офисом стоят связи с парками,
            # физическое удаление снесло бы их каскадом без следа в журнале.
            if not wiki_offices.update_office(cursor, office_id, {'status': 'archived'}):
                return jsonify({"error": "Офис не найден"}), 404
            queries.log_action(cursor, actor_id=ctx['user_id'], action='office.archive',
                               entity_type='office', entity_id=office_id, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        fields = _fields(data, partial=True)
        if 'name' in data:
            name = _clean(data['name'])
            if not name:
                return jsonify({"error": "Название офиса не может быть пустым"}), 400
            fields['name'] = name

        changed = wiki_offices.update_office(cursor, office_id, fields) if fields else False
        links = _links(data)
        if links is not None:
            wiki_offices.set_office_parks(cursor, office_id, links)
            changed = True

        if not changed:
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='office.update',
                           entity_type='office', entity_id=office_id,
                           details={'fields': sorted(fields.keys())}, ip_address=log_ip())
        return jsonify({"status": "ok"})

    @wiki_route('/offices/resolve-map', methods=('POST',),
                capability='can_manage_structure')
    def wiki_office_resolve_map(cursor, ctx):
        """Ссылка 2ГИС → координаты.

        Разворачиваем на сервере: короткие go.2gis.com отдают адрес точки в
        Location, а редирект браузеру виден не будет (ответ без CORS-заголовков
        для чтения заголовков).
        """
        result = wiki_offices.resolve_map_link(_clean(_body().get('url'), 1000))
        if 'error' in result:
            return jsonify(result), 400
        return jsonify(result)

    # ── Тайлы карты ──────────────────────────────────────────────────────
    #
    # ЕДИНСТВЕННЫЙ роут раздела без авторизации, и это осознанно. Картинку
    # нельзя запросить с заголовком Authorization: <img> его не отправляет, а
    # куки до него доходят не всегда — в вебвью SameSite=None понижается до
    # Lax (bot_schedule2.py), и карта молча ломалась бы у части сотрудников.
    # Отдаём мы при этом чужие публичные тайлы, которые и так доступны любому
    # напрямую у 2ГИС, — закрывать тут нечего.
    #
    # Прокси нужен не ради доступа: 2ГИС режет пачку запросов (замер 12.08 —
    # четыре карты из пятнадцати пришли пустыми), а скачанный один раз тайл
    # больше не меняется.
    @bp.route('/map/tile/<int:z>/<int:x>/<int:y>.png', methods=('GET',))
    def wiki_map_tile(z, x, y):
        if not wiki_offices.tile_is_valid(z, x, y):
            return jsonify({"error": "Тайл вне допустимых границ"}), 400

        try:
            with db._get_cursor() as cursor:
                image = wiki_offices.read_tile(cursor, z, x, y)
        except Exception:  # noqa: BLE001 — таблицы может ещё не быть
            image = None

        if image is None:
            # Скачивание вынесено из-под курсора: соединение из общего пула не
            # должно ждать чужую сеть (пул делится с SSE-аукционом).
            image = wiki_offices.fetch_tile(z, x, y)
            if image:
                try:
                    with db._get_cursor() as cursor:
                        wiki_offices.store_tile(cursor, z, x, y, image)
                except Exception:  # noqa: BLE001 — не смогли закэшировать, отдадим как есть
                    pass

        if not image:
            # Пустой ответ, а не 500: клиент прячет такой тайл и оставляет фон.
            return Response(status=204)

        return Response(image, mimetype='image/png', headers={
            # Тайл на конкретном зуме не меняется — кэшируем надолго.
            'Cache-Control': 'public, max-age=2592000, immutable',
        })
