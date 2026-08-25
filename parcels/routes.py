"""HTTP-эндпоинты раздела «Посылки» (Flask Blueprint).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль, и обратный импорт был
бы циклом (ровно как в wiki/routes.py и crm/routes.py).

Соглашения те же, что у остальных роутов портала: методы всегда включают
OPTIONS и первым делом отдаётся preflight, авторизация — общий require_api_key,
ошибка — {"error": "..."} с осмысленным кодом (глобального errorhandler в
проекте нет).

Разделение обязанностей внутри раздела:
    queries.py  — SQL
    access.py   — кто что может
    drivers.py  — CRM yataxi
    routes.py   — только разбор запроса и коды ответов
"""

import logging
import re
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request

from . import access, drivers, queries, schema

# Пределы полей — те же, что в DDL. Проверяем здесь, чтобы длинная вставка
# отвечала понятным «слишком длинно», а не «внутренняя ошибка» из-под INSERT.
_MAX_LENGTHS = {
    'city': 120, 'driver_account_id': 64, 'driver_name': 200, 'driver_phone': 32,
    'sender': 200, 'recipient': 200, 'order_number': 64, 'order_url': 2000,
    'description': 4000, 'comment': 4000,
}

# Насколько назад можно поставить дату приёма. Год — с запасом на «разгребли
# накопившееся за прошлый сезон»; всё, что дальше, почти наверняка опечатка в
# годе (2025 вместо 2026), а такая посылка потеряется в конце реестра.
_MAX_BACKDATE_DAYS = 366


def build_parcels_blueprint(*, db, require_api_key, build_cors_preflight_response,
                            resolve_requester, sensitive_access_granted):
    """Собирает Blueprint раздела.

    sensitive_access_granted — (user_id) -> bool: подтверждена ли ТЕКУЩАЯ сессия
    QR-кодом. Приходит аргументом, а не импортом: сам ключ живёт в
    bot_schedule2 (там сессии, токены и подтверждение админом), а обратный импорт
    оттуда был бы циклом. Аргумент обязательный, без значения по умолчанию:
    забытая зависимость должна уронить сборку блюпринта на старте (раздел тогда
    просто не поднимется), а не тихо открыть реестр всем.
    """
    bp = Blueprint('parcels', __name__, url_prefix='/api/parcels')

    def parcels_route(rule, methods=('GET',), write=False):
        """Общий каркас роута: preflight, авторизация, контекст, ошибки.

        write=True — роут меняет реестр: доступен только фронт-офисам и
        глобальному админу. Проверка здесь, чтобы не повторять её в каждом
        обработчике и не забыть в новом.
        """
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status

                    with db._get_cursor() as cursor:
                        ctx = queries.load_access_context(cursor, requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404

                    # Гейт раздела — здесь, до любого обработчика: спрятанный
                    # пункт меню доступом не является, раздел открывается и
                    # прямым адресом ?view=parcels.
                    if not access.can_open_section(ctx):
                        return jsonify({
                            "error": "Раздел «Посылки» вам не открыт",
                            "code": "PARCELS_SECTION_CLOSED",
                        }), 403

                    # Второй гейт — QR-подтверждение сессии. Стоит ПОСЛЕ первого:
                    # предлагать подтвердить доступ к тому, чего человеку не
                    # выдавали, — тупик, из которого он не выйдет.
                    if (access.requires_sensitive_qr(ctx)
                            and not sensitive_access_granted(ctx['user_id'])):
                        return jsonify({
                            "error": "Раздел «Посылки» откроется после "
                                     "QR-подтверждения доступа",
                            "code": "SENSITIVE_ACCESS_REQUIRED",
                        }), 403

                    if write and not access.can_edit(ctx):
                        return jsonify({
                            "error": "Реестр посылок ведут сотрудники фронт-офисов",
                            "code": "PARCELS_READ_ONLY",
                        }), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('parcels: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Посылки»",
                        "detail": str(exc)[:200],
                    }), 500

            return wrapper

        return decorator

    def _payload():
        return request.get_json(silent=True) or {}

    def _actor(ctx):
        return {'user_id': ctx['user_id'], 'name': ctx.get('name')}

    # ── Диагностика и сводка ─────────────────────────────────────────────
    @parcels_route('/ping')
    def parcels_ping(ctx):
        """Живость раздела + права текущего пользователя.

        schema_ready отличает «раздел ещё разворачивается» от «раздел сломан»:
        без этого первый запуск на чистой базе выглядит как отказ.
        """
        with db._get_cursor() as cursor:
            ready = schema.schema_is_ready(cursor)
            payload = {
                "ok": True,
                "schema_ready": ready,
                "capabilities": access.capabilities(ctx),
                "user_id": ctx['user_id'],
            }
            if ready:
                payload['counters'] = queries.status_counters(cursor)
        return jsonify(payload)

    # ── Справочники ──────────────────────────────────────────────────────
    @parcels_route('/offices')
    def parcels_offices(ctx):
        """Города и офисы из вики — источник для выпадающих списков формы.

        Города отдаём отдельным списком с числом офисов: по нему форма решает,
        спрашивать офис или подставить единственный сама, и решает ОДИНАКОВО с
        сервером — правило одно, данные одни.
        """
        with db._get_cursor() as cursor:
            offices = queries.list_offices(cursor)
        cities = {}
        for office in offices:
            cities.setdefault(office['city'], []).append(office)
        return jsonify({
            "offices": offices,
            "cities": [
                {"city": city, "offices": len(items),
                 "only_office_id": items[0]['id'] if len(items) == 1 else None}
                for city, items in sorted(cities.items())
            ],
        })

    @parcels_route('/filters')
    def parcels_filters(ctx):
        """Значения фильтров, которые реально встречаются в реестре."""
        with db._get_cursor() as cursor:
            return jsonify({
                "cities": queries.cities_in_use(cursor),
                "managers": queries.list_managers(cursor),
            })

    # ── Водитель из CRM ──────────────────────────────────────────────────
    @parcels_route('/driver-lookup', methods=('POST',), write=True)
    def parcels_driver_lookup(ctx):
        """Ссылка на аккаунт водителя (или сам ID) → его данные из CRM.

        Отдельный роут, а не «подтянем при сохранении»: сотрудник должен УВИДЕТЬ
        ФИО до того, как сохранит карточку. Вставил не ту ссылку — заметит сразу,
        а не через месяц, когда посылку будут искать.
        """
        data = _payload()
        raw = data.get('link') or data.get('account_id') or data.get('value')
        try:
            summary = drivers.lookup(raw)
        except drivers.DriverLookupError as exc:
            return jsonify({"error": exc.message, "code": exc.code}), exc.status
        return jsonify({"driver": summary})

    # ── Реестр ───────────────────────────────────────────────────────────
    @parcels_route('')
    def parcels_list(ctx):
        args = request.args
        statuses = [code for code in (args.get('status') or '').split(',') if code]
        unknown = [code for code in statuses if code not in schema.PARCEL_STATUSES]
        if unknown:
            return jsonify({"error": "Неизвестный статус: %s" % unknown[0]}), 400

        filters = {
            'query': args.get('q'),
            'status': statuses or None,
            'city': args.get('city') or None,
            'office_id': _int_or_none(args.get('office_id')),
            'manager_id': _int_or_none(args.get('manager_id')),
            'date_from': _date_or_none(args.get('date_from')),
            'date_to': _date_or_none(args.get('date_to')),
        }
        with db._get_cursor() as cursor:
            items, total = queries.list_parcels(
                cursor,
                limit=_int_or_none(args.get('limit')) or 50,
                offset=_int_or_none(args.get('offset')) or 0,
                **filters,
            )
            # Счётчики считаем теми же фильтрами, но без статуса: сегмент
            # «В офисе» обязан показывать, сколько посылок в ОСТАЛЬНЫХ статусах.
            counter_filters = dict(filters)
            counter_filters.pop('status', None)
            counters = queries.status_counters(cursor, **counter_filters)
        return jsonify({"items": items, "total": total, "counters": counters})

    @parcels_route('/<int:parcel_id>')
    def parcels_read(parcel_id, ctx):
        with db._get_cursor() as cursor:
            item = queries.read_parcel(cursor, parcel_id)
            if not item:
                return jsonify({"error": "Посылка не найдена"}), 404
            events = queries.list_events(cursor, parcel_id)
        return jsonify({"item": item, "events": events})

    @parcels_route('', methods=('POST',), write=True)
    def parcels_create(ctx):
        data = _payload()
        with db._get_cursor() as cursor:
            fields, error = _validate(cursor, data, existing=None)
            if error:
                message, code = error
                return jsonify({"error": message, "code": code}), 400
            item = queries.create_parcel(cursor, fields=fields, actor=_actor(ctx))
        return jsonify({"item": item}), 201

    @parcels_route('/<int:parcel_id>', methods=('PATCH', 'DELETE'), write=True)
    def parcels_modify(parcel_id, ctx):
        if request.method == 'DELETE':
            if not access.can_delete(ctx):
                return jsonify({
                    "error": "Удалять записи может только администратор",
                    "code": "PARCELS_FORBIDDEN",
                }), 403
            with db._get_cursor() as cursor:
                if not queries.delete_parcel(cursor, parcel_id):
                    return jsonify({"error": "Посылка не найдена"}), 404
            return jsonify({"status": "deleted"})

        data = _payload()
        with db._get_cursor() as cursor:
            existing = queries.read_parcel(cursor, parcel_id)
            if not existing:
                return jsonify({"error": "Посылка не найдена"}), 404
            fields, error = _validate(cursor, data, existing=existing)
            if error:
                message, code = error
                return jsonify({"error": message, "code": code}), 400
            item = queries.update_parcel(cursor, parcel_id, fields=fields, actor=_actor(ctx))
            events = queries.list_events(cursor, parcel_id)
        return jsonify({"item": item, "events": events})

    @parcels_route('/<int:parcel_id>/status', methods=('POST',), write=True)
    def parcels_status(parcel_id, ctx):
        data = _payload()
        status = str(data.get('status') or '').strip()
        if status not in schema.PARCEL_STATUSES:
            return jsonify({"error": "Неизвестный статус"}), 400
        comment = _clean(data.get('comment'), _MAX_LENGTHS['comment'])
        with db._get_cursor() as cursor:
            item = queries.set_status(cursor, parcel_id, status=status,
                                      actor=_actor(ctx), comment=comment)
            if not item:
                return jsonify({"error": "Посылка не найдена"}), 404
            events = queries.list_events(cursor, parcel_id)
        return jsonify({"item": item, "events": events})

    return bp


# ─────────────────────────────────────────────────────────────────────────────
# Разбор и проверка полей формы
# ─────────────────────────────────────────────────────────────────────────────

def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_or_none(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# Схемы, которые вообще могут быть у ссылки. Всё остальное — включая
# `javascript:` и `data:` — не ссылка, а способ выполнить код в браузере того,
# кто её откроет. Проверка ЗДЕСЬ, а не только во фронте: в базу ссылка попадает
# через API, а показывать её будут как <a href>.
_LINK_SCHEMES = ('http', 'https')


def _clean_link(value, limit):
    """Ссылка на заказ: приводим к единому виду или отвечаем отказом.

    Возвращает (url, error). `('', None)` — поля нет, это законно.

    Схему достраиваем сами: сотрудник копирует адресную строку, и
    «fleet.yandex.kz/orders/…» без http:// — обычная копипаста, отказывать на
    ней было бы придиркой. Хост обязателен: строка без него ссылкой не станет
    ни в каком браузере.
    """
    text = str(value or '').strip()
    if not text:
        return '', None
    if len(text) > limit:
        return None, 'Ссылка слишком длинная'

    # Схему проверяем ДО достройки. Иначе `javascript:alert(1)` уходит в ветку
    # «схемы нет», получает приставку https:// и отсеивается уже правилом про
    # хост — то есть по случайной причине, которую легко потерять при правке.
    scheme = re.match(r'^([a-zA-Z][a-zA-Z0-9+.\-]*):', text)
    if scheme and scheme.group(1).lower() not in _LINK_SCHEMES:
        return None, 'Ссылка должна начинаться с http:// или https://'

    candidate = text if '://' in text else 'https://' + text.lstrip('/')
    try:
        from urllib.parse import urlparse

        parsed = urlparse(candidate)
    except Exception:  # noqa: BLE001 — мусор во входе не должен ронять запрос
        return None, 'Не удалось разобрать ссылку'
    if parsed.scheme.lower() not in _LINK_SCHEMES:
        return None, 'Ссылка должна начинаться с http:// или https://'
    if not parsed.netloc or '.' not in parsed.netloc:
        return None, 'В ссылке нет адреса сайта'
    return candidate, None


def _clean(value, limit):
    text = str(value or '').strip()
    if not text:
        return None
    return text[:limit]


def _validate(cursor, data, existing=None):
    """Собирает поля карточки из тела запроса. Возвращает (fields, error).

    При PATCH берём за основу существующую запись: форма присылает всё поле
    целиком, но частичная правка (например, только статус комментария) не должна
    обнулять то, чего в теле нет.

    Порядок проверок — от того, что человек вводит первым: дата, город, офис,
    водитель, содержимое. Так первая же ошибка указывает на верхнее незаполненное
    поле, а не гоняет глаз по форме снизу вверх.
    """
    base = existing or {}
    fields = {}

    def given(name):
        return name in data

    # ── Дата приёма ──────────────────────────────────────────────────────
    if given('received_on') or not existing:
        received = _date_or_none(data.get('received_on'))
        if not received:
            return None, ('Укажите дату приёма посылки', 'RECEIVED_ON_REQUIRED')
        today = queries.today_almaty()
        if received > today:
            return None, ('Дата приёма не может быть в будущем', 'RECEIVED_ON_FUTURE')
        if (today - received).days > _MAX_BACKDATE_DAYS:
            return None, ('Дата приёма старше года — проверьте год', 'RECEIVED_ON_TOO_OLD')
        fields['received_on'] = received

    # ── Город и офис ─────────────────────────────────────────────────────
    # Офис привязывается из справочника вики, а не вводится текстом: свободный
    # адрес немедленно разошёлся бы со справочником, как это уже было со статьёй
    # «Адреса офисов».
    touches_place = given('city') or given('office_id') or not existing
    if touches_place:
        city = _clean(data.get('city') if given('city') else base.get('city'),
                      _MAX_LENGTHS['city'])
        if not city:
            return None, ('Выберите город', 'CITY_REQUIRED')

        available = queries.offices_in_city(cursor, city)
        if not available:
            return None, (
                'В городе «%s» нет офисов в справочнике — заведите офис в разделе «Вики»' % city,
                'CITY_WITHOUT_OFFICES',
            )

        # Смена города обнуляет офис: старый офис остался в другом городе, и
        # тащить его в проверку значило бы отвечать «офис не относится к
        # городу» человеку, который города как раз и не менял руками.
        city_changed = _same_city(city, base.get('city')) is False
        if given('office_id'):
            office_id = _int_or_none(data.get('office_id'))
        elif city_changed:
            office_id = None
        else:
            office_id = _int_or_none(base.get('office_id'))
        if office_id is None and len(available) == 1:
            # Единственный офис города подставляем САМИ, а не только в форме:
            # правило должно держаться и когда запрос пришёл мимо интерфейса.
            office_id = available[0]['id']
        if office_id is None:
            return None, ('В городе несколько офисов — выберите нужный', 'OFFICE_REQUIRED')

        office = next((item for item in available if item['id'] == office_id), None)
        if not office:
            return None, ('Этот офис не относится к выбранному городу', 'OFFICE_MISMATCH')

        # Снимок имени и адреса — карточка описывает, где вещь лежала В ТОТ день.
        fields['city'] = office['city']
        fields['office_id'] = office['id']
        fields['office_name'] = office['name']
        fields['office_address'] = office['address']

    # ── Водитель ─────────────────────────────────────────────────────────
    if given('driver_link') or given('driver_account_id') or not existing:
        raw = data.get('driver_link') or data.get('driver_account_id') \
            or base.get('driver_account_id')
        account_id = drivers.extract_account_id(raw)
        if not account_id:
            return None, (
                'Вставьте ссылку на аккаунт водителя или его ID из 32 символов',
                'DRIVER_ID_REQUIRED',
            )
        fields['driver_account_id'] = account_id

        # Снимок из CRM снимаем САМИ, а не верим телу запроса: иначе в реестре
        # оказался бы тот водитель, чьё имя прислал клиент, а не тот, чей ID
        # записан, — и расхождение никто бы не заметил.
        #
        # Ходим в CRM только когда есть зачем: сменился водитель или снимка ещё
        # нет. Правка описания у существующей карточки чужой сервис не дёргает —
        # она не должна ни ждать его, ни зависеть от него.
        supplied = _clean(data.get('driver_name'), _MAX_LENGTHS['driver_name'])
        needs_snapshot = (
            account_id != (base.get('driver_account_id') or None)
            or not base.get('driver_synced_at')
        )
        if needs_snapshot:
            fields.update(_driver_snapshot(account_id))
        # Имя, введённое человеком, главнее снимка: его правят как раз тогда,
        # когда в CRM оно записано латиницей или с опечаткой.
        if supplied:
            fields['driver_name'] = supplied

    # ── Содержимое ───────────────────────────────────────────────────────
    if given('kind') or not existing:
        kind = str(data.get('kind') or '').strip()
        if kind not in schema.PARCEL_KINDS:
            return None, ('Выберите тип посылки', 'KIND_REQUIRED')
        fields['kind'] = kind

    if given('description') or not existing:
        description = _clean(data.get('description'), _MAX_LENGTHS['description'])
        if not description:
            return None, ('Опишите посылку — «коробка с одеждой», «документы»',
                          'DESCRIPTION_REQUIRED')
        fields['description'] = description

    for name in ('sender', 'recipient', 'order_number', 'comment'):
        if given(name):
            fields[name] = _clean(data.get(name), _MAX_LENGTHS.get(name, 200))

    # ── Ссылка на заказ ──────────────────────────────────────────────────
    # Заказ прикрепляется ссылкой (решение владельца 25.08.2026): API отдаёт по
    # водителю последние три заказа, но без адресов — одни id, цены и пробеги,
    # и показывать их в карточке владелец счёл лишними данными. Сотрудник
    # вставляет адрес карточки заказа, и по нему же заказ потом ищется.
    if given('order_url'):
        order_url, error = _clean_link(data.get('order_url'), _MAX_LENGTHS['order_url'])
        if error:
            return None, (error, 'ORDER_URL_INVALID')
        fields['order_url'] = order_url or None

    # ── Статус ───────────────────────────────────────────────────────────
    # Только при заведении: у существующей карточки статус меняет свой роут,
    # чтобы «кто изменил статус» не смешивалось с правкой опечатки.
    if not existing:
        status = str(data.get('status') or 'in_office').strip()
        if status not in schema.PARCEL_STATUSES:
            return None, ('Неизвестный статус', 'STATUS_UNKNOWN')
        fields['status'] = status

    return fields, None


def _same_city(left, right):
    """Один ли это город. Сравнение то же, что в SQL: без регистра и пробелов."""
    return str(left or '').strip().lower() == str(right or '').strip().lower()


def _driver_snapshot(account_id):
    """Данные водителя из CRM. Молчаливо деградирует до пустого снимка.

    CRM — чужой сервис, и её недоступность не должна мешать записать посылку.
    Поэтому здесь нет ни отказа, ни исключения наружу: чего не приехало, того в
    карточке просто нет, а сотрудник видит это на экране и дозаполняет ФИО сам.
    """
    snapshot = {
        'driver_name': None,
        'driver_phone': None,
        'driver_park': None,
        'driver_park_id': None,
        'driver_license': None,
        'driver_callsign': None,
        'driver_car': None,
        'driver_info': None,
        'driver_synced_at': None,
    }
    try:
        summary = drivers.summarize(drivers.fetch_driver(account_id))
    except drivers.DriverLookupError as exc:
        logging.info('parcels: данные водителя %s не приехали (%s)', account_id, exc.code)
        return snapshot
    snapshot.update({
        'driver_name': summary.get('name'),
        'driver_phone': summary.get('phone'),
        'driver_park': summary.get('park'),
        'driver_park_id': summary.get('park_id'),
        'driver_license': summary.get('license'),
        'driver_callsign': summary.get('callsign'),
        'driver_car': summary.get('car'),
        'driver_info': summary.get('info'),
        'driver_synced_at': queries.now_almaty(),
    })
    return snapshot
