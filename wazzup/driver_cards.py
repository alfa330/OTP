"""Карточка водителя в чатах Верификаторов: ручки раздела «Чаты ОП».

Постановка владельца 16.10.2026: на каждый чат у верификаторов есть сделка в
amoCRM, и для каждого чата они заполняют карточку водителя — ID водителя,
таксопарк и город привлечения. Карточка показывается сверху у каждого чата, у
неё есть история, и отдельно смотрятся только карточки. Данные сразу уходят в
сделку amoCRM (очередь — wazzup/driver_cards_sync.py, сделка чата —
wazzup/driver_cards_amo.py).

Кто что может. Видят карточку и реестр все, кому открыта переписка (гард ленты:
прежняя аудитория раздела и верификатор с подтверждённым доступом, только
аккаунт «op»). Заполняют — те, кто обрабатывает чаты (верификатор, супер-админ):
право то же, что у ответа клиенту и внутренней заметки.

Global, как и у заметок, вне карточек: его чаты обрабатываются отдельно.
"""
import re
import uuid
from datetime import date, datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from flask import jsonify, request

from . import driver_cards_amo as amo

TZ = ZoneInfo('Asia/Almaty')
HISTORY_LIMIT = 50
LIST_LIMIT = 100
# Ссылка из Fleet с параметрами отчёта и GPS доходит до 320 знаков.
MAX_RAW_DRIVER_ID = 2000
MAX_PERIOD_DAYS = 366

CARD_COLUMNS = ('c.id,c.channel_id,c.chat_id,c.created_at,c.author_id,c.author_name,'
                'c.contact_name,c.contact_phone,c.driver_id,c.park_enum_id,c.park_name,'
                'c.city_enum_id,c.city_name,c.amo_state,c.amo_lead_id,c.amo_error,c.amo_synced_at,'
                'c.amo_queued_at')
CARD_FIELDS = len(CARD_COLUMNS.split(','))

DRIVER_ID_HINT = ('ID водителя — 32 символа: цифры и латинские буквы a–f. '
                  'Можно вставить ссылку на профиль водителя во Fleet.')

_HEX32 = re.compile(r'(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])')
_PROFILE = re.compile(r'(?:contractors|drivers)/([0-9a-f]{32})(?![0-9a-f])')
# Ссылка, а не голый ID: в ней одинокий 32-значный код — это park_id или чужой
# идентификатор, а не водитель (обрезанный ID профиля, ссылка на список водителей).
_LINK = re.compile(r'https?:|www\.|fleet\.|/|park_id|contractors|drivers')
_PHONE_QUERY = re.compile(r'[\d\s()+\-]+')


def normalize_driver_id(raw):
    """ID профиля водителя в Яндекс Fleet или None для пустого поля.

    Принимается сам ID и ссылка на профиль: из 717 значений, вписанных
    верификаторами в amoCRM, 10 были ссылками вида
    fleet.yandex.kz/contractors/<ID водителя>/details?park_id=<ID парка> — в них
    два 32-значных кода, и водитель — тот, что после contractors/. Ссылка без
    такого пути (ID профиля обрезан, ссылка на список водителей парка) — отказ:
    одинокий код в ней — park_id, и он ушёл бы в сделку как ID водителя у всех
    водителей парка. Не ссылка — годится текст ровно с одним кодом. Номер
    телефона (11 цифр, тоже бывал по ошибке) и любой другой текст — ValueError."""
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > MAX_RAW_DRIVER_ID:
        raise ValueError(DRIVER_ID_HINT)
    value = raw.strip().lower()
    if not value:
        return None
    profile = set(_PROFILE.findall(value))
    if profile:
        if len(profile) != 1:
            raise ValueError(DRIVER_ID_HINT)
        return profile.pop()
    if _LINK.search(value):
        raise ValueError(DRIVER_ID_HINT)
    found = set(_HEX32.findall(value))
    if len(found) != 1:
        raise ValueError(DRIVER_ID_HINT)
    return found.pop()


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def card_item(row):
    (card_id, channel_id, chat_id, created_at, author_id, author_name, contact_name, contact_phone,
     driver_id, park_id, park_name, city_id, city_name, state, lead_id, error, synced_at,
     queued_at) = row[:CARD_FIELDS]
    return {
        'id': str(card_id), 'channelId': channel_id, 'chatId': chat_id, 'createdAt': _iso(created_at),
        'authorId': author_id, 'authorName': author_name,
        'contactName': contact_name, 'contactPhone': contact_phone,
        'driverId': driver_id,
        'park': {'id': int(park_id), 'name': park_name} if park_id is not None else None,
        'city': {'id': int(city_id), 'name': city_name} if city_id is not None else None,
        # queuedAt — когда версия встала в очередь (сохранение, «Повторить»): по
        # нему экран отличает свежий итог от позднего снимка той же версии.
        'amo': {'state': state, 'leadId': int(lead_id) if lead_id is not None else None,
                'leadUrl': amo.lead_url(lead_id), 'error': error, 'syncedAt': _iso(synced_at),
                'queuedAt': _iso(queued_at)},
    }


def _json(status=200, **values):
    response = jsonify(**values)
    response.status_code = status
    response.headers['Cache-Control'] = 'private, no-store'
    return response


def load_options(cursor):
    """Последний удачный снимок справочников amoCRM или None."""
    cursor.execute("SELECT payload, fetched_at FROM wazzup_driver_card_options WHERE key='amo'")
    row = cursor.fetchone()
    if not row or not isinstance(row[0], dict):
        return None
    return dict(row[0], fetchedAt=_iso(row[1]))


def store_options(cursor, payload):
    from psycopg2.extras import Json
    cursor.execute("""
        INSERT INTO wazzup_driver_card_options(key, payload, fetched_at) VALUES ('amo', %s, now())
        ON CONFLICT (key) DO UPDATE SET payload = EXCLUDED.payload, fetched_at = EXCLUDED.fetched_at
    """, (Json(payload),))


def _enum(options, kind, value):
    """(id, name) значения справочника или ValueError; None — поле не заполнено."""
    if value is None or value == '':
        return None
    try:
        enum_id = int(value)
    except (TypeError, ValueError):
        raise ValueError('Некорректное значение списка')
    if isinstance(value, bool) or enum_id <= 0:
        raise ValueError('Некорректное значение списка')
    for item in (options or {}).get(kind) or []:
        if int(item.get('id') or 0) == enum_id:
            return enum_id, str(item.get('name') or '')
    label = 'таксопарка' if kind == 'parks' else 'города'
    raise ValueError('Этого %s больше нет в списке amoCRM — выберите другой' % label)


def _period(values):
    """Границы периода в UTC по датам Алматы (обе включены). По умолчанию — 30 дней."""
    today = datetime.now(TZ).date()

    def parse(name, fallback):
        raw = values.get(name)
        if not raw:
            return fallback
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            raise ValueError('Некорректная дата периода')

    start = parse('from', today - timedelta(days=29))
    end = parse('to', today)
    if end < start:
        start, end = end, start
    if (end - start).days > MAX_PERIOD_DAYS:
        raise ValueError('Период — не больше года')
    return (datetime.combine(start, dt_time.min, TZ),
            datetime.combine(end + timedelta(days=1), dt_time.min, TZ))


def register_driver_card_routes(bp, *, guard, actor, may_edit, require_api_key, preflight, db,
                                excluded_channels=(), on_saved=None, options_provider=None):
    """guard() — читатель переписки; actor() — тот, кто обрабатывает чаты.

    on_saved() будит очередь записи в amoCRM после коммита. options_provider()
    отдаёт справочники (снимок из базы, а без него — из amoCRM, своими
    транзакциями и с паузой после неудачи: wazzup/driver_cards_sync.py) или None."""
    if options_provider is None:
        def options_provider():
            with db._get_cursor() as cursor:
                return load_options(cursor)

    def scope(values):
        if values.get('account') != 'op':
            return None, _json(403, error='Карточка водителя есть только в чатах Верификаторов')
        channel, chat = values.get('channelId'), values.get('chatId')
        if (not isinstance(channel, str) or not isinstance(chat, str)
                or not chat.strip() or len(chat) > 100):
            return None, _json(400, error='Укажите канал и чат')
        try:
            channel = str(uuid.UUID(channel))
        except (ValueError, TypeError, AttributeError):
            return None, _json(400, error='Некорректный канал')
        if channel in excluded_channels:
            return None, _json(403, error='Global обрабатывается отдельно')
        return (channel, chat), None

    def chat_row(cursor, channel, chat):
        cursor.execute("SELECT contact_name, contact_phone FROM wazzup_chats WHERE account='op' "
                       "AND channel_id=%s AND chat_id=%s", (channel, chat))
        return cursor.fetchone()

    def history(cursor, channel, chat, limit=HISTORY_LIMIT):
        cursor.execute("SELECT " + CARD_COLUMNS + " FROM wazzup_driver_cards c WHERE c.account='op' "
                       "AND c.channel_id=%s AND c.chat_id=%s ORDER BY c.created_at DESC, c.id DESC LIMIT %s",
                       (channel, chat, limit + 1))
        rows = cursor.fetchall()
        return [card_item(row) for row in rows[:limit]], len(rows) > limit

    def options_now():
        try:
            return options_provider()
        except Exception:
            return None

    @bp.route('/driver-card', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def driver_card():
        if request.method == 'OPTIONS':
            return preflight()
        if request.method == 'GET':
            user_id, error = guard()
            if error:
                return error
            context, error = scope(request.args)
            if error:
                return error
            channel, chat = context
            with db._get_cursor() as cursor:
                if chat_row(cursor, channel, chat) is None:
                    return _json(404, error='Чат не найден')
                items, has_more = history(cursor, channel, chat)
            return _json(items=items, hasMore=has_more, canEdit=bool(may_edit(user_id)))

        user, error = actor()
        if error:
            return error
        if request.content_length and request.content_length > 16 * 1024:
            return _json(413, error='Слишком большой запрос')
        values = request.get_json(silent=True)
        if not isinstance(values, dict):
            return _json(400, error='Некорректный запрос')
        context, error = scope(values)
        if error:
            return error
        channel, chat = context
        try:
            client_id = str(uuid.UUID(str(values.get('clientCardId', ''))))
        except (ValueError, TypeError, AttributeError):
            return _json(400, error='Некорректный идентификатор сохранения')
        try:
            driver_id = normalize_driver_id(values.get('driverId'))
        except ValueError as exc:
            return _json(400, error=str(exc), field='driverId')
        # Справочники — до транзакции: без снимка в базе за ними идут в amoCRM.
        options = options_now()

        # Всё проверяется до первой записи: ответ 4xx транзакцию всё равно коммитит.
        with db._get_cursor() as cursor:
            chat_info = chat_row(cursor, channel, chat)
            if chat_info is None:
                return _json(404, error='Чат не найден')
            # Повтор того же сохранения (таймаут, двойной щелчок) — та же строка.
            cursor.execute("SELECT " + CARD_COLUMNS + ",c.account FROM wazzup_driver_cards c "
                           "WHERE c.author_id=%s AND c.client_card_id=%s", (user[0], client_id))
            same = cursor.fetchone()
            if same is not None:
                if (same[CARD_FIELDS], same[1], same[2]) != ('op', channel, chat):
                    return _json(409, error='Этот идентификатор уже использован для другой карточки',
                                 code='REQUEST_CONFLICT')
                return _json(200, item=card_item(same))
            if options is None:
                return _json(503, error='Списки таксопарков и городов amoCRM пока недоступны — '
                                        'попробуйте через минуту', code='OPTIONS_UNAVAILABLE')
            try:
                park = _enum(options, 'parks', values.get('parkId'))
                city = _enum(options, 'cities', values.get('cityId'))
            except ValueError as exc:
                return _json(400, error=str(exc))
            if driver_id is None and park is None and city is None:
                return _json(400, error='Заполните хотя бы одно поле карточки')
            latest, _ = history(cursor, channel, chat, limit=1)
            latest = latest[0] if latest else None
            if latest:
                cleared = [label for label, before, after in (
                    ('ID водителя', latest['driverId'], driver_id),
                    ('Таксопарк привлечения', latest['park'], park),
                    ('Город привлечения', latest['city'], city)) if before and not after]
                if cleared:
                    return _json(400, error='Поле «%s» уже заполнено — его можно изменить, но не очистить'
                                 % cleared[0])
                unchanged = (latest['driverId'] == driver_id
                             and (latest['park'] or {}).get('id') == (park or (None,))[0]
                             and (latest['city'] or {}).get('id') == (city or (None,))[0])
                if unchanged and latest['amo']['state'] in ('pending', 'synced'):
                    return _json(200, item=latest, unchanged=True)
            cursor.execute(
                "INSERT INTO wazzup_driver_cards (id,account,channel_id,chat_id,client_card_id,author_id,"
                "author_name,contact_name,contact_phone,driver_id,park_enum_id,park_name,city_enum_id,city_name) "
                "VALUES (%s,'op',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (author_id, client_card_id) DO NOTHING RETURNING " + CARD_COLUMNS.replace('c.', ''),
                (str(uuid.uuid4()), channel, chat, client_id, user[0], str(user[2] or user[7] or 'Оператор'),
                 chat_info[0], chat_info[1], driver_id,
                 park[0] if park else None, park[1] if park else None,
                 city[0] if city else None, city[1] if city else None))
            row = cursor.fetchone()
            if row is None:
                # Параллельный повтор того же запроса успел раньше — отдать его строку.
                cursor.execute("SELECT " + CARD_COLUMNS + " FROM wazzup_driver_cards c "
                               "WHERE c.author_id=%s AND c.client_card_id=%s", (user[0], client_id))
                row = cursor.fetchone()
                if row is None or (row[1], row[2]) != (channel, chat):
                    return _json(409, error='Этот идентификатор уже использован для другой карточки',
                                 code='REQUEST_CONFLICT')
                return _json(200, item=card_item(row))
            item = card_item(row)
        if on_saved is not None:
            try:
                on_saved()
            except Exception:
                pass
        return _json(201, item=item)

    @bp.route('/driver-card/retry', methods=['POST', 'OPTIONS'])
    @require_api_key
    def driver_card_retry():
        """Повторить запись в amoCRM последней карточки чата — после отказа или
        «сделка не найдена». Ничего не отправляет само: ставит в очередь."""
        if request.method == 'OPTIONS':
            return preflight()
        _user, error = actor()
        if error:
            return error
        values = request.get_json(silent=True)
        if not isinstance(values, dict):
            return _json(400, error='Некорректный запрос')
        context, error = scope(values)
        if error:
            return error
        channel, chat = context
        try:
            card_id = str(uuid.UUID(str(values.get('cardId', ''))))
        except (ValueError, TypeError, AttributeError):
            return _json(400, error='Некорректная карточка')
        with db._get_cursor() as cursor:
            latest, _ = history(cursor, channel, chat, limit=1)
            if not latest or latest[0]['id'] != card_id:
                return _json(409, error='Карточку уже изменили — откройте её заново', code='STALE_CARD')
            if latest[0]['amo']['state'] not in ('failed', 'no_deal'):
                return _json(200, item=latest[0])
            cursor.execute(
                "UPDATE wazzup_driver_cards c SET amo_state='pending', amo_attempts=0, amo_no_deal_attempts=0, "
                "amo_error=NULL, amo_next_attempt_at=now(), amo_queued_at=now(), "
                "amo_lease_token=NULL, amo_lease_until=NULL "
                "WHERE c.id=%s AND c.amo_state IN ('failed','no_deal') RETURNING " + CARD_COLUMNS,
                (card_id,))
            row = cursor.fetchone()
            item = card_item(row) if row else latest[0]
        if on_saved is not None and row:
            try:
                on_saved()
            except Exception:
                pass
        return _json(200, item=item)

    @bp.route('/driver-card/options', methods=['GET', 'OPTIONS'])
    @require_api_key
    def driver_card_options():
        if request.method == 'OPTIONS':
            return preflight()
        _user_id, error = guard()
        if error:
            return error
        if request.args.get('account', 'op') != 'op':
            return _json(403, error='Карточка водителя есть только в чатах Верификаторов')
        options = options_now()
        if options is None:
            return _json(503, error='Списки таксопарков и городов amoCRM пока недоступны',
                         code='OPTIONS_UNAVAILABLE')
        return _json(parks=options.get('parks') or [], cities=options.get('cities') or [],
                     fetchedAt=options.get('fetchedAt'))

    @bp.route('/driver-cards', methods=['GET', 'OPTIONS'])
    @require_api_key
    def driver_cards_list():
        """Только карточки: по одной (текущей) на чат, свежие сверху."""
        if request.method == 'OPTIONS':
            return preflight()
        _user_id, error = guard()
        if error:
            return error
        values = request.args
        if values.get('account', 'op') != 'op':
            return _json(403, error='Карточка водителя есть только в чатах Верификаторов')
        try:
            start, end = _period(values)
            limit = min(LIST_LIMIT, max(1, int(values.get('limit') or 50)))
            offset = max(0, int(values.get('offset') or 0))
        except (TypeError, ValueError) as exc:
            message = str(exc) if 'период' in str(exc).lower() else 'Некорректный запрос'
            return _json(400, error=message)
        query = str(values.get('q') or '').strip().lower()[:100]
        # По номеру — только запрос, похожий на номер: цифры из куска ID водителя
        # («fe1112») нашли бы чужие чаты, где эти цифры стоят в телефоне.
        digits = ''.join(ch for ch in query if ch.isdigit()) if _PHONE_QUERY.fullmatch(query) else ''
        params = {'start': start, 'end': end, 'q': query,
                  'q_like': '%' + query.replace('\\', '').replace('%', '').replace('_', '') + '%',
                  'q_digits': '%' + digits + '%' if len(digits) >= 3 else None,
                  'limit': limit, 'offset': offset}
        # Без знака процента в тексте запроса: psycopg2 принял бы его за параметр.
        base = """
            WITH latest AS (
                SELECT DISTINCT ON (c.channel_id, c.chat_id) """ + CARD_COLUMNS + """
                  FROM wazzup_driver_cards c
                 WHERE c.account = 'op'
                 ORDER BY c.channel_id, c.chat_id, c.created_at DESC, c.id DESC
            )
            SELECT {columns}
              FROM latest l
              LEFT JOIN wazzup_chats w
                ON w.account = 'op' AND w.channel_id = l.channel_id AND w.chat_id = l.chat_id
             WHERE l.created_at >= %(start)s AND l.created_at < %(end)s
               AND (%(q)s = ''
                    OR l.driver_id LIKE %(q_like)s
                    OR lower(COALESCE(w.contact_name, l.contact_name, '')) LIKE %(q_like)s
                    OR lower(l.author_name) LIKE %(q_like)s
                    OR lower(COALESCE(l.park_name, '')) LIKE %(q_like)s
                    OR lower(COALESCE(l.city_name, '')) LIKE %(q_like)s
                    OR (%(q_digits)s IS NOT NULL AND l.chat_id LIKE %(q_digits)s))
        """
        columns = ('l.id,l.channel_id,l.chat_id,l.created_at,l.author_id,l.author_name,'
                   'COALESCE(w.contact_name, l.contact_name),COALESCE(w.contact_phone, l.contact_phone),'
                   'l.driver_id,l.park_enum_id,l.park_name,l.city_enum_id,l.city_name,'
                   'l.amo_state,l.amo_lead_id,l.amo_error,l.amo_synced_at,l.amo_queued_at,w.chat_type')
        with db._get_cursor() as cursor:
            cursor.execute(base.format(columns=columns)
                           + " ORDER BY l.created_at DESC, l.id DESC LIMIT %(limit)s OFFSET %(offset)s", params)
            rows = cursor.fetchall()
            # Итог — отдельным запросом: COUNT(*) OVER () на странице за хвостом дал бы ноль.
            cursor.execute(base.format(columns='COUNT(*)'), params)
            total = int((cursor.fetchone() or (0,))[0] or 0)
        items = []
        for row in rows:
            item = card_item(row)
            item['chatType'] = row[CARD_FIELDS]
            item['chatAvailable'] = row[CARD_FIELDS] is not None
            items.append(item)
        return _json(items=items, total=total)
