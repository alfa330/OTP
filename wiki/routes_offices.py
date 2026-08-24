"""Эндпоинты офисов.

Отдельным модулем, а не внутри routes_parks: у офисов своя форма с картой,
графиком и переопределениями по паркам, и вместе с акциями файл стал бы
третьим по величине в разделе.

Каждый роут начинается с request_space: справочник принадлежит пространству, и
без него офисы одной вики были бы видны в другой (см. шапку wiki/offices.py).
Единственное исключение — прокси тайлов: он и так без авторизации, потому что
<img> не отправляет заголовков, и отдаёт чужие публичные картинки 2ГИС, в
которых нет ни адреса, ни того, чей это офис.
"""

from flask import Response, g, jsonify, request

from . import access as wiki_access
from . import offices as wiki_offices
from . import queries
from .routes_structure import _clean, _int_or_none, _slugify, request_space


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
                link = {
                    'park_id': _int_or_none(item.get('park_id')),
                    'schedule': item.get('schedule'),
                    'note': _clean(item.get('note'), 500),
                }
                # Номера правят из карточки парка; здесь их принимают, только
                # если прислали, — иначе правка графика стирала бы телефоны.
                phones = wiki_offices.link_phones(item)
                if phones is not None:
                    link['phones'] = phones
                result.append(link)
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
    take('all_parks', bool)
    take('no_office', bool)

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

    # «Офиса в городе нет» — это отсутствие офиса, а не офис с пустыми полями.
    # Карту, график и телефон гасим сразу: оставленные «на всякий случай», они
    # рано или поздно разойдутся с надписью «Офиса в городе нет», и город будет
    # одновременно и без офиса, и с телефоном офиса.
    if fields.get('no_office'):
        fields.update({'address': None, 'phone': None, 'schedule': None,
                       'map_url': None, 'map_resolved_url': None,
                       'lat': None, 'lon': None})

    if data.get('status') in ('active', 'archived'):
        fields['status'] = data['status']
    if 'position' in data:
        fields['position'] = _int_or_none(data['position']) or 0
    return fields


def register(bp, wiki_route, db, log_ip):

    def _may_edit(ctx):
        """Справочник правит всякий, у кого есть что-то сверх чтения.

        То же правило, что у парков (routes_parks._may_edit): офисы и парки —
        один справочник, разнесённый по двум экранам, и разные пороги на них
        читались бы как случайность.
        """
        # Способности ИТОГОВЫЕ, а не только должностные: право, выписанное
        # правилом раздела, тоже открывает справочник — решение владельца
        # подтверждено 21.08.2026, когда способности стали суммой должности и
        # выписанного (queries.load_capabilities). Порог остался прежним —
        # «хоть что-то сверх чтения»; изменилось лишь то, что теперь это
        # «что-то» может прийти не только от должности.
        #
        # Да, справочник шире одного раздела, а правило выписано на раздел.
        # Границей остаётся ПРОСТРАНСТВО: способность отвечает «вправе ли
        # править», request_space — «что именно правит».
        # Владелец выбрал это осознанно: следить за телефонами парков некому,
        # кроме тех, кто вообще ведёт содержимое, а поимённая выдача прав в
        # разделе — такое же решение руководителя, как и назначение на должность.
        return wiki_access.has_write_capability(ctx['capabilities'])

    def _forbidden():
        return jsonify({"error": "Справочник правит тот, у кого есть права сверх чтения",
                        "code": "WIKI_FORBIDDEN"}), 403

    @wiki_route('/offices', methods=('GET', 'POST'))
    def wiki_offices_list(cursor, ctx):
        space_id, error = request_space(cursor, ctx)
        if error:
            return error

        if request.method == 'GET':
            can_manage = _may_edit(ctx)
            # Дата — «на какой день показать статус». По умолчанию сегодня по
            # Алматы: у сервера в Render время UTC, и до трёх ночи его «сегодня»
            # ещё вчерашнее.
            day = wiki_offices.parse_day(request.args.get('date')) or wiki_offices.office_today()
            # Архивные — по запросу, а не всегда. Управляющему они приезжали в
            # общем списке и мешались среди живых офисов; теперь это тумблер в
            # фильтре, и по умолчанию он выключен.
            show_archived = can_manage and request.args.get('archived') in ('1', 'true')
            return jsonify({
                'items': wiki_offices.list_offices(
                    cursor,
                    include_archived=show_archived,
                    query=request.args.get('q'),
                    park_id=_int_or_none(request.args.get('park_id')),
                    city=request.args.get('city'),
                    day=day,
                    space_id=space_id,
                ),
                'cities': wiki_offices.cities(cursor, space_id=space_id),
                'date': day.isoformat(),
                'can_manage': can_manage,
            })

        if not _may_edit(ctx):
            return _forbidden()

        data = _body()
        name = _clean(data.get('name'))
        if not name:
            return jsonify({"error": "Укажите название офиса"}), 400

        slug = _clean(data.get('slug'), 120) or _slugify(name)
        base, suffix = slug, 2
        while not wiki_offices.slug_is_free(cursor, slug, space_id=space_id):
            slug = '%s-%d' % (base, suffix)
            suffix += 1

        office_id = wiki_offices.create_office(
            cursor, slug=slug, name=name, created_by=ctx['user_id'],
            fields=_fields(data, partial=False), space_id=space_id)
        wiki_offices.set_office_parks(cursor, office_id, _links(data) or [],
                                      space_id=space_id)

        queries.log_action(cursor, actor_id=ctx['user_id'], action='office.create',
                           entity_type='office', entity_id=office_id,
                           details={'name': name, 'space_id': space_id},
                           ip_address=log_ip())
        return jsonify({"id": office_id, "slug": slug}), 201

    @wiki_route('/offices/<int:office_id>', methods=('PATCH', 'DELETE'))
    def wiki_office_item(cursor, ctx, office_id):
        if not _may_edit(ctx):
            return _forbidden()
        space_id, error = request_space(cursor, ctx)
        if error:
            return error

        if request.method == 'DELETE':
            # Архивируем, как парки и акции: за офисом стоят связи с парками,
            # физическое удаление снесло бы их каскадом без следа в журнале.
            # «Не найден» отвечаем и на чужой офис: он для этого пространства и
            # правда не существует, а «нет прав» подтвердило бы, что он есть.
            if not wiki_offices.update_office(cursor, office_id, {'status': 'archived'},
                                              space_id=space_id):
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

        # Проверяем офис ДО правки связей: без этого чужой офис ответил бы
        # «Нечего обновлять» на пустых полях, а привязку парков успел бы
        # переписать — set_office_parks работает от office_id.
        if not wiki_offices.get_office(cursor, office_id, space_id=space_id):
            return jsonify({"error": "Офис не найден"}), 404

        changed = (wiki_offices.update_office(cursor, office_id, fields,
                                              space_id=space_id)
                   if fields else False)
        links = _links(data)
        if links is not None:
            wiki_offices.set_office_parks(cursor, office_id, links, space_id=space_id)
            changed = True

        if not changed:
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='office.update',
                           entity_type='office', entity_id=office_id,
                           details={'fields': sorted(fields.keys())}, ip_address=log_ip())
        return jsonify({"status": "ok"})

    @wiki_route('/offices/<int:office_id>/closure', methods=('PUT', 'DELETE'))
    def wiki_office_closure(cursor, ctx, office_id):
        """Закрытие офиса на срок: «закрыт до 29.08» и «срок не известен».

        Отдельно от отметки дня, потому что отвечает на другой вопрос. Отметка
        дня — это факт за один прошедший день, её и наперёд ставить нельзя.
        Закрытие — заявление о будущем: офис не работает с такого-то числа и до
        такого-то, и все дни между ними считаются закрытыми сами.

        До этого срок закрытия писать было некуда, и дежурные писали его словами
        в причину («с 17.08 по 03.09 по тех.причинам»), а отметка действовала
        один день — назавтра офис «открывался» сам по графику.

        until — день ОТКРЫТИЯ (не включается в закрытие): «закрыт до 29.08»
        значит «28-го ещё закрыт, 29-го работает». Пустой until — срок не
        известен, закрытие держится, пока его не снимут.
        """
        if not _may_edit(ctx):
            return _forbidden()
        space_id, error = request_space(cursor, ctx)
        if error:
            return error
        if not wiki_offices.get_office(cursor, office_id, space_id=space_id):
            return jsonify({"error": "Офис не найден"}), 404

        if request.method == 'DELETE':
            # Идемпотентно: закрытия не было — офис и так по графику.
            wiki_offices.clear_office_closure(cursor, office_id, space_id=space_id)
            queries.log_action(cursor, actor_id=ctx['user_id'], action='office.closure.clear',
                               entity_type='office', entity_id=office_id,
                               details={}, ip_address=log_ip())
            return jsonify({"status": "cleared", "closure": None})

        data = _body()
        today = wiki_offices.office_today()
        # Начало по умолчанию — сегодня: дежурный отмечает то, что происходит.
        # Задним числом разрешено (ремонт начался в понедельник, отметили в
        # среду), наперёд — нет: это уже не «закрыт», а план, а планы живут в
        # графике.
        start = wiki_offices.parse_day(data.get('from')) if data.get('from') else today
        if start is None:
            return jsonify({"error": "Неверная дата начала"}), 400
        if start > today:
            return jsonify({"error": "Закрыть можно с сегодняшнего или прошедшего дня"}), 400

        until = wiki_offices.parse_day(data.get('until')) if data.get('until') else None
        if data.get('until') and until is None:
            return jsonify({"error": "Неверная дата открытия"}), 400
        # Открытие в день начала или раньше — закрытие нулевой длины, то есть
        # его нет. Молча записать такое значило бы показать офис работающим
        # там, где дежурный только что отметил закрытие.
        if until is not None and until <= start:
            return jsonify({"error": "Офис должен открыться позже дня закрытия"}), 400

        wiki_offices.set_office_closure(cursor, office_id, start, until,
                                        note=_clean(data.get('note'), 500),
                                        space_id=space_id)
        queries.log_action(cursor, actor_id=ctx['user_id'], action='office.closure.set',
                           entity_type='office', entity_id=office_id,
                           details={'from': start.isoformat(),
                                    'until': until.isoformat() if until else None},
                           ip_address=log_ip())
        return jsonify({"status": "ok", "closure": {
            "from": start.isoformat(),
            "until": until.isoformat() if until else None,
        }})

    @wiki_route('/offices/<int:office_id>/day/<day>', methods=('PUT', 'DELETE'))
    def wiki_office_day(cursor, ctx, office_id, day):
        """Отметка «в этот день офис был открыт / закрыт» и снятие отметки.

        Отдельный роут, а не поле формы офиса: закрытие на день — ежедневное
        действие дежурного, а форма офиса открывается раз в полгода. Наперёд
        отмечать нельзя — история фиксирует то, что было, а не то, что
        планируется.
        """
        if not _may_edit(ctx):
            return _forbidden()
        space_id, error = request_space(cursor, ctx)
        if error:
            return error

        target = wiki_offices.parse_day(day)
        if target is None:
            return jsonify({"error": "Неверная дата"}), 400
        if target > wiki_offices.office_today():
            return jsonify({"error": "Отметить можно сегодняшний или прошедший день"}), 400
        if not wiki_offices.get_office(cursor, office_id, space_id=space_id):
            return jsonify({"error": "Офис не найден"}), 404

        if request.method == 'DELETE':
            # Идемпотентно: не было отметки — значит день и так считается по
            # графику, и это ровно тот результат, которого просили.
            wiki_offices.clear_office_day(cursor, office_id, target, space_id=space_id)
            queries.log_action(cursor, actor_id=ctx['user_id'], action='office.day.clear',
                               entity_type='office', entity_id=office_id,
                               details={'day': target.isoformat()}, ip_address=log_ip())
            return jsonify({"status": "cleared", "day": None})

        data = _body()
        state = data.get('state')
        if state not in ('open', 'closed'):
            return jsonify({"error": "Статус дня — «Открыт» или «Закрыт»"}), 400

        wiki_offices.set_office_day(cursor, office_id, target, state,
                                   note=_clean(data.get('note'), 500),
                                   recorded_by=ctx['user_id'], space_id=space_id)
        queries.log_action(cursor, actor_id=ctx['user_id'], action='office.day.set',
                           entity_type='office', entity_id=office_id,
                           details={'day': target.isoformat(), 'state': state},
                           ip_address=log_ip())
        return jsonify({"status": "ok",
                        "day": wiki_offices.read_office_day(cursor, office_id, target,
                                                            space_id=space_id)})

    @wiki_route('/offices/resolve-map', methods=('POST',))
    def wiki_office_resolve_map(cursor, ctx):
        """Ссылка 2ГИС → координаты.

        Разворачиваем на сервере: короткие go.2gis.com отдают адрес точки в
        Location, а редирект браузеру виден не будет (ответ без CORS-заголовков
        для чтения заголовков).
        """
        if not _may_edit(ctx):
            return _forbidden()
        result = wiki_offices.resolve_map_link(_clean(_body().get('url'), 1000))
        if 'error' in result:
            # Сбой на стороне 2ГИС — не 400: ссылка может быть верной, и
            # «проверьте ссылку» отправило бы правившего искать несуществующую
            # ошибку в ней.
            return jsonify(result), 502 if result.pop('upstream', False) else 400
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

        # Флаг снимает общий no-store, который after_request вешает на всё
        # под /api/ (bot_schedule2.py). Без него браузер перекачивал бы по
        # четыре картинки на карточку при каждом открытии раздела.
        g.allow_public_cache = True
        return Response(image, mimetype='image/png', headers={
            # Тайл на конкретном зуме не меняется — кэшируем надолго.
            'Cache-Control': 'public, max-age=2592000, immutable',
        })
