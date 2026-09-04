"""HTTP-эндпоинты раздела «Чаты водителей» (Flask Blueprint).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль, и обратный импорт был
бы циклом (ровно как в wiki/routes.py, crm/routes.py и parcels/routes.py).

Соглашения те же, что у остальных роутов портала: методы всегда включают
OPTIONS и первым делом отдаётся preflight, авторизация — общий require_api_key,
ошибка — {"error": "..."} с осмысленным кодом.

Разделение обязанностей внутри раздела:
    queries.py    SQL
    access.py     кто что может
    chat2desk.py  разговор с вендором
    report.py     сборка книги xlsx
    routes.py     только разбор запроса и коды ответов

ПОЧЕМУ ПОИСК ОТДАЁТ СРАЗУ И ПЕРЕПИСКУ. Окно раздела — двое суток, и за него на
один телефон приходится в медиане 2 обращения (p90 — 4). Тянуть список, а потом
по клику догружать каждый чат отдельным вызовом вендора значило бы платить
квотой за то, что уже приехало: сообщения окна приходят ОДНИМ запросом и сами
несут request_id и dialog_id. Поэтому /search возвращает готовые чаты с
сообщениями, а /open — это только запись в журнал.
"""

import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, send_file

from . import access, chat2desk, queries, report

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Сколько держим переписку в кеше, прежде чем сходить к вендору заново. Пять
# минут: переписка живая (водитель на линии прямо сейчас), но смена открывает
# один и тот же чат по нескольку раз подряд, и платить за это квотой незачем.
CACHE_TTL_SECONDS = 300

# Потолок поисков на человека в сутки. Не про деньги, а про то, что исчерпание
# месячной квоты Chat2Desk роняет НЕ этот раздел, а ежедневный синк метрик — то
# есть табло СЗоВ, зарплатные метрики чат-менеджеров и учёт часов. В августе
# бесплатный пул уже выбирали досуха (27.08: left_free_requests = 0), поэтому
# потолок — условие запуска, а не украшение. 150 поисков в смену — заведомо
# больше живой потребности (за сутки во всём отделе 872 уникальных телефона).
DAILY_SEARCH_LIMIT = 150

# Потолок строк выгрузки: книга собирается в памяти инстанса.
EXPORT_ROW_CAP = 20000

JOURNAL_PAGE_SIZE = 50
JOURNAL_MAX_PAGE_SIZE = 200


def build_driver_chats_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                 resolve_requester, sensitive_access_granted,
                                 client_ip=None, excel_text_warning=None):
    """Собирает Blueprint раздела.

    sensitive_access_granted — (user_id) -> bool: подтверждена ли ТЕКУЩАЯ сессия
    QR-кодом. Приходит аргументом, а не импортом: сам ключ живёт в
    bot_schedule2 (там сессии, токены и подтверждение супервайзером). Аргумент
    обязательный, без значения по умолчанию: забытая зависимость должна уронить
    сборку блюпринта на старте (раздел тогда просто не поднимется), а не тихо
    открыть переписку водителей всем подряд.

    client_ip — () -> str для журнала. Значение по умолчанию есть: без адреса
    журнал остаётся журналом, а ронять из-за него раздел незачем.

    excel_text_warning — хелпер `<ignoredErrors>` для выгрузки. Без него книга
    соберётся, просто с зелёным уголком «Число сохранено как текст» на каждом
    телефоне.
    """
    bp = Blueprint('driver_chats', __name__, url_prefix='/api/driver_chats')

    def _ip():
        try:
            return client_ip() if client_ip else None
        except Exception:  # noqa: BLE001
            return None

    def _ua():
        return (request.headers.get('User-Agent') or '')[:500] or None

    def dch_route(rule, methods=('GET',), journal=False):
        """Общий каркас роута: preflight, авторизация, контекст, гейты, ошибки.

        journal=True — роут показывает журнал: доступен супервайзерам СЗоВ, главе
        отдела и суперадминам. Проверка здесь, чтобы не повторять её в каждом
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
                    # прямым адресом ?view=driver_chats.
                    if not access.can_open_section(ctx):
                        return jsonify({
                            "error": "Раздел «Чаты водителей» вам не открыт",
                            "code": "DRIVER_CHATS_SECTION_CLOSED",
                        }), 403

                    # Второй гейт — QR-подтверждение сессии. Стоит ПОСЛЕ первого:
                    # предлагать подтвердить доступ к тому, чего человеку не
                    # выдавали, — тупик, из которого он не выйдет.
                    if (access.requires_sensitive_qr(ctx)
                            and not sensitive_access_granted(ctx['user_id'])):
                        return jsonify({
                            "error": "Раздел «Чаты водителей» откроется после "
                                     "QR-подтверждения доступа",
                            "code": "SENSITIVE_ACCESS_REQUIRED",
                        }), 403

                    if journal and not access.can_view_journal(ctx):
                        return jsonify({
                            "error": "Журнал доступен супервайзерам СЗоВ",
                            "code": "DRIVER_CHATS_JOURNAL_CLOSED",
                        }), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except chat2desk.Chat2DeskError as exc:
                    # Ошибку вендора показываем как есть: «превышен лимит
                    # запросов» человеку понятнее, чем «внутренняя ошибка».
                    logging.warning('driver_chats: Chat2Desk отказал на %s: %s', rule, exc)
                    return jsonify({"error": str(exc), "code": "CHAT2DESK_ERROR"}), 502
                except Exception as exc:  # noqa: BLE001
                    logging.exception('driver_chats: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Чаты водителей»",
                        "detail": str(exc)[:200],
                    }), 500

            return wrapper

        return decorator

    def _payload():
        return request.get_json(silent=True) or {}

    def _int_or_none(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # ── Контекст раздела ─────────────────────────────────────────────────────

    @dch_route('/context')
    def driver_chats_context(ctx):
        window_from, window_to = chat2desk.window_bounds()
        with db._get_cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM dch_events "
                "WHERE user_id = %(user_id)s AND kind = 'search' "
                "  AND created_at >= date_trunc('day', "
                "      (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))",
                {'user_id': ctx['user_id']})
            used_today = int((cursor.fetchone() or [0])[0] or 0)
        return jsonify({
            'capabilities': access.capabilities(ctx),
            'window': {'from': window_from.isoformat(), 'to': window_to.isoformat(),
                       'days': chat2desk.WINDOW_DAYS},
            'limits': {'searches_per_day': DAILY_SEARCH_LIMIT, 'used_today': used_today},
            'comment_max_length': chat2desk.MAX_COMMENT_LENGTH,
            'me': {'user_id': ctx['user_id'], 'name': ctx.get('name')},
        }), 200

    # ── Поиск чатов водителя ─────────────────────────────────────────────────

    @dch_route('/search')
    def driver_chats_search(ctx):
        raw_phone = request.args.get('phone') or ''
        phone = chat2desk.normalize_phone(raw_phone)
        if not phone:
            return jsonify({
                "error": "Непохоже на номер телефона. Введите казахстанский "
                         "номер — например, 87071234567",
                "code": "BAD_PHONE",
            }), 400

        window_from, window_to = chat2desk.window_bounds()

        with db._get_cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM dch_events "
                "WHERE user_id = %(user_id)s AND kind = 'search' "
                "  AND created_at >= date_trunc('day', "
                "      (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))",
                {'user_id': ctx['user_id']})
            used_today = int((cursor.fetchone() or [0])[0] or 0)
        if used_today >= DAILY_SEARCH_LIMIT:
            return jsonify({
                "error": "На сегодня исчерпан лимит поисков (%d). Он защищает "
                         "общий лимит запросов к Chat2Desk, от которого зависят "
                         "табло и метрики отдела. Обратитесь к супервайзеру."
                         % DAILY_SEARCH_LIMIT,
                "code": "DAILY_LIMIT_REACHED",
            }), 429

        # Клиент вендора: сначала бесплатно из своей базы, и только потом — API.
        with db._get_cursor() as cursor:
            client_id = queries.local_client_id(cursor, chat2desk.phone_variants(phone))
        client_name = None
        if client_id is None:
            found = chat2desk.find_client(phone)
            if not found or not found.get('id'):
                with db._get_cursor() as cursor:
                    queries.log_event(cursor, ctx, 'search', phone=phone,
                                      ip_address=_ip(), user_agent=_ua())
                return jsonify({
                    'phone': phone, 'client_id': None, 'chats': [],
                    'window': {'from': window_from.isoformat(), 'to': window_to.isoformat()},
                    'not_found': True,
                }), 200
            client_id = int(found['id'])
            client_name = found.get('name')

        # Переписка: кеш -> вендор.
        truncated = False
        with db._get_cursor() as cursor:
            messages = queries.cached_messages(cursor, client_id, window_from,
                                               window_to, CACHE_TTL_SECONDS)
        from_cache = messages is not None
        if not from_cache:
            raw, total = chat2desk.fetch_window_messages(client_id, window_from, window_to)
            names = chat2desk.operator_names()
            messages = [chat2desk.normalize_message(msg, names) for msg in raw]
            truncated = total > len(raw)
            with db._get_cursor() as cursor:
                queries.store_messages(cursor, client_id, phone, messages,
                                       window_from, window_to)

        chats = chat2desk.group_chats(messages)

        # Обогащение метаданными заявок — бесплатное и НЕОБЯЗАТЕЛЬНОЕ: сегодняшних
        # заявок в c2d_requests ещё нет (синк идёт в 04:10 за вчера), и чат
        # прекрасно показывается без оценки водителя.
        #
        # А вот ТАКСОПАРК обязателен: по одному телефону приходят чаты разных
        # парков, и без названия оператор не понимает, что перед ним. Поэтому
        # парк берётся из самого сообщения (channel_id) через справочник, а
        # ночной срез заявок остаётся лишь третьим запасным вариантом.
        with db._get_cursor() as cursor:
            # Обогащаемся по ВСЕМ обращениям чата, а не по одному: после склейки
            # по парку их внутри несколько, и любое выбранное было бы
            # произвольным.
            meta = queries.request_meta(
                cursor, [rid for c in chats for rid in c.get('request_ids') or []])
            channels = queries.channel_names(cursor)
        unknown = [c['channel_id'] for c in chats
                   if c.get('channel_id') and int(c['channel_id']) not in channels]
        if unknown:
            # Парк подключили сегодня — в нашей базе его ещё нет. Один вызов на
            # весь справочник, дальше он живёт в кеше процесса 6 часов.
            channels = {**chat2desk.channel_names(), **channels}
        for chat in chats:
            rows = [meta[rid] for rid in (chat.get('request_ids') or []) if rid in meta]

            def _first(field, source=rows):
                return next((r.get(field) for r in source if r.get(field)), None)

            channel_id = chat.get('channel_id')
            chat['channel_name'] = (channels.get(int(channel_id)) if channel_id else None) \
                or _first('channel_name')
            # Кто отвечал — из САМИХ сообщений, а имя из ночного среза заявок
            # только как запасное. Срез хранит одного оператора на заявку, а
            # лента подписывает каждое сообщение своим автором: разойдись они,
            # человек унесёт на скриншоте одно имя, а в тексте увидит другое.
            chat['operator_name'] = (', '.join(chat['authors']) if chat['authors']
                                     else _first('operator_name'))
            # Оценка водителя лежит только в обычных обращениях: у 40 987 заявок
            # типа 'rating' поле rating_score не заполнено НИ РАЗУ, хотя именно
            # они называются «оценкой». Берём первую непустую среди обычных.
            chat['rating_score'] = _first(
                'rating_score', [r for r in rows if r.get('request_type') != 'rating'])
            if not client_name:
                client_name = chat2desk.clean_client_name(_first('client_name'))

        with db._get_cursor() as cursor:
            queries.log_event(cursor, ctx, 'search', phone=phone, client_id=client_id,
                              messages_count=len(messages), ip_address=_ip(),
                              user_agent=_ua())

        return jsonify({
            'phone': phone,
            'client_id': client_id,
            'client_name': client_name,
            'chats': chats,
            'window': {'from': window_from.isoformat(), 'to': window_to.isoformat()},
            'truncated': truncated,
            'from_cache': from_cache,
            'searches_left': max(0, DAILY_SEARCH_LIMIT - used_today - 1),
        }), 200

    # ── Открытие чата (только журнал) ────────────────────────────────────────

    @dch_route('/open', methods=('POST',))
    def driver_chats_open(ctx):
        data = _payload()
        phone = chat2desk.normalize_phone(data.get('phone'))
        if not phone:
            return jsonify({"error": "Не указан телефон водителя", "code": "BAD_PHONE"}), 400
        with db._get_cursor() as cursor:
            event = queries.log_event(
                cursor, ctx, 'open',
                phone=phone,
                client_id=_int_or_none(data.get('client_id')),
                channel_id=_int_or_none(data.get('channel_id')),
                dialog_id=_int_or_none(data.get('dialog_id')),
                # request_id у открытия НЕ пишем: после склейки по парку у чата
                # нет одного обращения — их внутри несколько, и любое
                # записанное было бы неправдой о том, что именно смотрел
                # человек. Адрес просмотра — клиент и парк.
                channel_name=(data.get('channel_name') or None),
                messages_count=_int_or_none(data.get('messages_count')),
                ip_address=_ip(), user_agent=_ua())
        return jsonify({'status': 'ok', 'event': event}), 200

    # ── «Передан»: заметка в чат + журнал ────────────────────────────────────

    @dch_route('/handoff', methods=('POST',))
    def driver_chats_handoff(ctx):
        data = _payload()
        phone = chat2desk.normalize_phone(data.get('phone'))
        client_id = _int_or_none(data.get('client_id'))
        if not phone or not client_id:
            return jsonify({"error": "Не указан чат водителя", "code": "BAD_TARGET"}), 400

        note = str(data.get('note') or '').strip()
        if len(note) > chat2desk.MAX_COMMENT_LENGTH:
            return jsonify({
                "error": "Комментарий длиннее %d символов" % chat2desk.MAX_COMMENT_LENGTH,
                "code": "COMMENT_TOO_LONG",
            }), 400

        text = chat2desk.build_handoff_text(ctx.get('name'), note)
        sent = chat2desk.send_internal_comment(client_id, text)

        # Запись в журнал — ПОСЛЕ успешной отправки и обязательно: заметку
        # отозвать нельзя, и «передал, но в журнале нет» — ровно та дыра, ради
        # закрытия которой журнал и просили.
        #
        # Здесь же сбрасываем кеш переписки: заметка уже в чате у вендора, но
        # наш снимок ей на пять минут старше, и повторный поиск показал бы ленту
        # БЕЗ только что отправленного комментария.
        with db._get_cursor() as cursor:
            queries.drop_cached_messages(cursor, client_id)
            event = queries.log_event(
                cursor, ctx, 'handoff',
                phone=phone, client_id=client_id,
                channel_id=_int_or_none(data.get('channel_id')),
                dialog_id=sent.get('dialog_id') or _int_or_none(data.get('dialog_id')),
                # Обращение берём ТОЛЬКО у вендора: это то, куда заметка реально
                # легла. Значение с фронта было бы догадкой — у склеенного чата
                # обращений несколько.
                request_id=sent.get('request_id'),
                channel_name=(data.get('channel_name') or None),
                comment_text=text,
                c2d_message_id=_int_or_none(sent.get('message_id')),
                ip_address=_ip(), user_agent=_ua())

        return jsonify({'status': 'ok', 'sent': sent, 'text': text, 'event': event}), 200

    # ── Журнал ───────────────────────────────────────────────────────────────

    def _journal_filters():
        """Разбор фильтров журнала. Общий для страницы и выгрузки — иначе файл
        и экран показывали бы разное при одних и тех же настройках."""
        def _day(name):
            raw = (request.args.get(name) or '').strip()
            if not raw:
                return None
            try:
                return datetime.strptime(raw[:10], '%Y-%m-%d')
            except ValueError:
                return None

        date_from = _day('date_from')
        date_to = _day('date_to')
        kinds = [k for k in (request.args.get('kinds') or '').split(',') if k.strip()]
        return {
            'date_from': date_from,
            # Верхняя граница — начало СЛЕДУЮЩИХ суток: иначе «по 3 сентября»
            # молча теряло бы всё, что было в этот день после полуночи.
            'date_to': (date_to + timedelta(days=1)) if date_to else None,
            'kinds': [k for k in kinds if k in ('search', 'open', 'handoff')] or None,
            'user_id': _int_or_none(request.args.get('user_id')),
            'phone': chat2desk.normalize_phone(request.args.get('phone')),
            'raw_from': date_from.date() if date_from else None,
            'raw_to': date_to.date() if date_to else None,
        }

    @dch_route('/journal', journal=True)
    def driver_chats_journal(ctx):
        filters = _journal_filters()
        page = max(1, _int_or_none(request.args.get('page')) or 1)
        size = min(JOURNAL_MAX_PAGE_SIZE,
                   max(1, _int_or_none(request.args.get('page_size')) or JOURNAL_PAGE_SIZE))
        filters['limit'] = size
        filters['offset'] = (page - 1) * size
        with db._get_cursor() as cursor:
            result = queries.journal_page(cursor, filters)
            people = queries.journal_people(cursor)
        return jsonify({**result, 'page': page, 'page_size': size, 'people': people}), 200

    @dch_route('/journal/export', journal=True)
    def driver_chats_journal_export(ctx):
        filters = _journal_filters()
        with db._get_cursor() as cursor:
            rows = queries.journal_all(cursor, filters, cap=EXPORT_ROW_CAP)

        notes = []
        if filters.get('user_id'):
            name = next((r['user_name'] for r in rows if r['user_id'] == filters['user_id']), None)
            notes.append('сотрудник: %s' % (name or filters['user_id']))
        if filters.get('phone'):
            notes.append('телефон водителя: %s' % filters['phone'])
        if filters.get('kinds'):
            notes.append('действия: %s' % ', '.join(
                report.KIND_LABELS.get(k, k) for k in filters['kinds']))

        stream, count = report.build_workbook(
            rows,
            period_from=filters.get('raw_from'),
            period_to=filters.get('raw_to'),
            generated_at=queries.now_almaty(),
            generated_by=ctx.get('name') or '',
            filters_note='; '.join(notes),
            truncated=len(rows) >= EXPORT_ROW_CAP,
            text_warning_patch=excel_text_warning)

        logging.info('driver_chats: журнал выгрузил %s (%s строк)', ctx.get('name'), count)
        return send_file(
            stream, mimetype=XLSX_MIME, as_attachment=True,
            download_name=report.export_file_name(filters.get('raw_from'),
                                                  filters.get('raw_to')))

    @dch_route('/ping')
    def driver_chats_ping(ctx):
        """Проверка, что блюпринт собрался и подключён.

        Стоит ЗА теми же гейтами, что и остальной раздел, хотя данных не отдаёт:
        неаутентифицированная ручка в разделе про персональные данные — это
        приглашение искать в нём дыры, а «здоровье» раздела и так видно из логов
        подключения блюпринта.
        """
        return jsonify({'status': 'ok', 'section': 'driver_chats'}), 200

    return bp
