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
    syntony.py    переписка Syntony из своей базы (источник с 25.11.2026)
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

from flask import Blueprint, jsonify, redirect, request, send_file

from syntony_chats import client as syntony_client

from . import access, chat2desk, queries, report, schema, syntony

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Сколько держим переписку в кеше, прежде чем сходить к вендору заново. Пять
# минут: переписка живая (водитель на линии прямо сейчас), но смена открывает
# один и тот же чат по нескольку раз подряд, и платить за это квотой незачем.
CACHE_TTL_SECONDS = 300

# Потолок поисков на человека в сутки. Не про деньги, а про то, что исчерпание
# месячной квоты Chat2Desk роняет НЕ этот раздел, а ежедневный синк метрик — то
# есть табло СЗоВ, зарплатные метрики чат-менеджеров и учёт часов.
#
# СОРОК (решение владельца 27.10.2026, было 30). История числа: 150 ставились,
# когда разделом пользовался один супер-админ; после открытия отделу в периметре
# стало 54 человека, и 150 на каждого — это до 8100 поисков в сутки, втрое
# больше всего месячного пула вендора (замер 08.09: 100 326 вызовов в месяц, из
# них половина съедена ночным синком к восьмому числу), — поэтому 08.09.2026
# владелец опустил до тридцати. Сорок на человека — 2160 поисков в сутки в самом
# худшем случае, когда лимит выберут ВСЕ, а по факту за первые дни весь отдел
# делал 3-63 поиска в сутки.
#
# Один поиск стоит в среднем полтора вызова к вендору: 65 % водителей уже есть в
# нашей базе (тогда вызов один, за сообщениями), 35 % пишут впервые (плюс поиск
# клиента, 1-3 вызова). Повторный поиск того же номера в пределах пяти минут
# бесплатен — отвечает кеш, а пока жив поток вебхуков, вендора не спрашивают вовсе.
DAILY_SEARCH_LIMIT = 40

# Сколько поисков «СВ и выше» могут добавить человеку сверх лимита — на
# сегодня, до полуночи по Алматы (владелец 27.10.2026: «до 5 попыток»). Это
# ИТОГ на день, а не прибавка одного нажатия: добавить пять, а потом ещё пять
# нельзя. Худший случай отдела растёт на 54 × 5 = 270 поисков в сутки.
MAX_EXTRA_SEARCHES = 5

# Сколько минут после отправки лента показывает НАШУ копию заметки «Передан».
# Вендор принимает её мгновенно и сразу возвращает message_id, но в выборке
# /v1/messages показывает примерно через минуту: 07.09.2026 заметка ушла в
# 10:05:23, двадцать обращений за следующие 31 секунду вернули ленту без неё, а
# появилась она в 10:06:19. Десять минут — с запасом на медленный день вендора;
# лишнего не покажет, доехавшая копия вытесняется по совпадению id.
PENDING_NOTE_MINUTES = 10

# Потолок строк выгрузки: книга собирается в памяти инстанса.
EXPORT_ROW_CAP = 20000

JOURNAL_PAGE_SIZE = 50
JOURNAL_MAX_PAGE_SIZE = 200


def plural_searches(count):
    """«1 поиск», «3 поиска», «5 поисков» — тексты про лимит видит оператор."""
    value = abs(int(count))
    tail = value % 100
    last = value % 10
    if 11 <= tail <= 14 or last == 0 or last >= 5:
        word = 'поисков'
    elif last == 1:
        word = 'поиск'
    else:
        word = 'поиска'
    return '%d %s' % (value, word)


def small_int(value):
    """Целое из JSON-тела: число без дроби или строка из цифр.

    bool отвергается (подкласс int: True молча стал бы «одним поиском»), как и
    дробь, бесконечность и NaN: ручка принимает число со степпера, и любое
    другое — это не «округлить», а «не понял».
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def extra_searches_note(person, extra, previous):
    """Строка журнала о добавке: «Ван Ли: +3 поиска на сегодня (было +1)».

    Имя получателя — снимком, как всё в журнале: вопрос «кому добавили»
    задают про тот день, а человек потом может смениться фамилией или уйти.
    Кто добавил — это сама строка журнала (её автор).
    """
    person = person or {}
    name = person.get('name') or '№%s' % person.get('user_id')
    note = ('%s: +%s на сегодня' % (name, plural_searches(extra)) if extra
            else '%s: добавка снята' % name)
    if previous:
        note += ' (было +%d)' % previous
    return note


def build_driver_chats_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                 resolve_requester, sensitive_access_granted,
                                 client_ip=None, excel_text_warning=None, media_secret=None):
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

    media_secret — общий секрет сервера (JWT_SECRET), от которого подписываются
    ссылки на вложения Syntony (syntony.py). Без него фото и файлы в ленте Syntony
    не показываются — переписка показывается.
    """
    bp = Blueprint('driver_chats', __name__, url_prefix='/api/driver_chats')
    media_key = syntony.media_key(media_secret)

    def _ip():
        try:
            return client_ip() if client_ip else None
        except Exception:  # noqa: BLE001
            return None

    def _ua():
        return (request.headers.get('User-Agent') or '')[:500] or None

    def dch_route(rule, methods=('GET',), journal=False, settings=False):
        """Общий каркас роута: preflight, авторизация, контекст, гейты, ошибки.

        journal=True — роут показывает журнал: доступен супервайзерам СЗоВ, главе
        отдела и суперадминам. settings=True — роут «Настроек» (поиски сверх
        лимита): «СВ и выше», тот же круг. Проверки здесь, чтобы не повторять их
        в каждом обработчике и не забыть в новом.
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
                    if settings and not access.can_grant_extra_searches(ctx):
                        return jsonify({
                            "error": "Настройки раздела доступны супервайзерам СЗоВ",
                            "code": "DRIVER_CHATS_SETTINGS_CLOSED",
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

    def _limit_reached_message(ctx, extra):
        """Текст отказа по лимиту — с тем, что человеку делать дальше.

        «Обратитесь к супервайзеру» теперь не пустая фраза: супервайзер может
        добавить поисков на сегодня. Но только тому, кому их вообще добавляют, и
        только пока не добавлено всё, — иначе текст отправлял бы человека за
        тем, чего ему не дадут.
        """
        if extra:
            # «+3 сверх него», а не «3 добавленных»: с числом впереди
            # прилагательное пришлось бы склонять («1 добавленный»).
            reached = 'На сегодня поиски закончились: %d по лимиту и +%d сверх него.' % (
                DAILY_SEARCH_LIMIT, extra)
        else:
            reached = 'На сегодня исчерпан лимит поисков (%d).' % DAILY_SEARCH_LIMIT
        # Про квоту Chat2Desk — только пока переписка оттуда: на Syntony поиск идёт по своей
        # базе и квоты не тратит, и объяснение было бы неправдой.
        why = (' Лимит защищает общую квоту запросов к Chat2Desk, от которой '
               'зависят табло и метрики отдела.'
               if syntony.source() == syntony.SOURCE_CHAT2DESK else '')
        if access.can_receive_extra_searches(ctx) and extra < MAX_EXTRA_SEARCHES:
            left = MAX_EXTRA_SEARCHES - extra
            if left == 1:
                more = 'ещё один поиск'
            else:
                # «до N поисков» — родительный падеж при любом N от двух.
                more = ('ещё до %d поисков' if extra else 'до %d поисков') % left
            return reached + why + (' Если поиски ещё нужны, супервайзер может '
                                    'добавить %s на сегодня.' % more)
        return reached + why + ' Завтра лимит снова %d.' % DAILY_SEARCH_LIMIT

    # ── Контекст раздела ─────────────────────────────────────────────────────

    @dch_route('/context')
    def driver_chats_context(ctx):
        window_from, window_to = chat2desk.window_bounds()
        with db._get_cursor() as cursor:
            used_today, extra_today = queries.search_usage_today(cursor, ctx['user_id'])
        limited = access.is_search_limited(ctx)
        return jsonify({
            'capabilities': access.capabilities(ctx),
            'window': {'from': window_from.isoformat(), 'to': window_to.isoformat(),
                       'days': chat2desk.WINDOW_DAYS},
            # Супер-админу потолка нет, и в limits вместо числа уходит null:
            # экран рисует остаток по числу, а «осталось поисков: —» было бы
            # шумом там, где считать нечего.
            #
            # searches_per_day — потолок СЕГОДНЯ, уже с добавленными поисками:
            # остаток на экране считается от него, и второе число рядом экрану
            # не понадобилось бы ни для чего, кроме ошибки.
            'limits': {
                'searches_per_day': (DAILY_SEARCH_LIMIT + extra_today) if limited else None,
                'used_today': used_today,
                'extra_today': extra_today if limited else 0,
            },
            'comment_max_length': chat2desk.MAX_COMMENT_LENGTH,
            'me': {'user_id': ctx['user_id'], 'name': ctx.get('name')},
            # Откуда переписка и уходит ли «Передан» в сам чат: у Chat2Desk — внутренней
            # заметкой, у Syntony — только отметкой в журнале и ленте раздела (syntony.py).
            'source': syntony.source(),
            'handoff_to_chat': syntony.source() == syntony.SOURCE_CHAT2DESK,
        }), 200

    # ── Поиск чатов водителя ─────────────────────────────────────────────────

    def _search_syntony(ctx, raw_phone, phone, tail):
        """Поиск по переписке Syntony в своей базе (syntony.py). Ни одного вызова API.

        Ответ — той же формы, что у Chat2Desk-пути: экран, журнал и лимит у источников
        общие. Отличия: client_id нет (у Syntony водитель — номер), «обращений» и оценок
        нет, вложения — подписанными адресами ручки /media, а stale_minutes говорит, что
        переписка Syntony в базе давно не пополнялась."""
        window_from, window_to = chat2desk.window_bounds()
        start, end = syntony.window_range(window_from, window_to)
        channels = syntony.channel_ids()
        window = {'from': window_from.isoformat(), 'to': window_to.isoformat()}
        with db._get_cursor() as cursor:
            used_today, extra_today = queries.search_usage_today(cursor, ctx['user_id'])
            limit_today = DAILY_SEARCH_LIMIT + extra_today
            limited = access.is_search_limited(ctx)
            if limited and used_today >= limit_today:
                return jsonify({
                    "error": _limit_reached_message(ctx, extra_today),
                    "code": "DAILY_LIMIT_REACHED",
                }), 429
            found = syntony.find_contact(cursor, phone, tail, channels)
            if found and found[0] == 'ambiguous':
                queries.log_event(cursor, ctx, 'search', phone=phone or tail,
                                  source=syntony.SOURCE_SYNTONY,
                                  ip_address=_ip(), user_agent=_ua())
                return jsonify({
                    "error": "По последним цифрам этого номера нашлось несколько "
                             "водителей. Введите номер целиком, с кодом страны.",
                    "code": "PHONE_AMBIGUOUS",
                }), 400
            if not found:
                queries.log_event(cursor, ctx, 'search', phone=phone or tail,
                                  source=syntony.SOURCE_SYNTONY,
                                  ip_address=_ip(), user_agent=_ua())
                return jsonify({
                    'phone': phone or raw_phone.strip(), 'client_id': None, 'chats': [],
                    'window': window, 'not_found': True, 'source': syntony.SOURCE_SYNTONY,
                    'fetched_at': chat2desk.now_almaty().isoformat(),
                    'searches_left': (max(0, limit_today - used_today - 1)
                                      if limited else None),
                }), 200
            number, client_name = found
            rows, truncated = syntony.window_rows(cursor, number, channels, start, end)
            notes = syntony.handoff_messages(cursor, number, window_from, window_to)
            stale = syntony.stale_minutes(cursor)
            # Журнал — под номером, как он лежит у Syntony: под ним же лягут «открыл» и
            # «Передан», и выгрузка по водителю соберёт их вместе.
            queries.log_event(cursor, ctx, 'search', phone=number,
                              messages_count=len(rows), source=syntony.SOURCE_SYNTONY,
                              ip_address=_ip(), user_agent=_ua())
        build_media = syntony.media_url_builder(media_key, ctx['user_id'])
        messages = [item for item in (syntony.message_from_row(row, build_media) for row in rows)
                    if item]
        known = {item['id'] for item in messages}
        messages += [note for note in notes if note['id'] not in known]
        chats = chat2desk.group_chats(messages)
        names = syntony.channel_names(rows)
        for note in notes:
            names.setdefault(note['channelId'], syntony.channel_name(note['channelId']))
        for chat in chats:
            chat['channel_name'] = names.get(chat.get('channel_id')) or syntony.channel_name(
                chat.get('channel_id'))
            chat['operator_name'] = ', '.join(chat['authors']) if chat['authors'] else None
            chat['rating_score'] = None
        return jsonify({
            'phone': number,
            'client_id': None,
            'client_name': client_name,
            'chats': chats,
            'window': window,
            'truncated': truncated,
            'from_cache': False,
            'source': syntony.SOURCE_SYNTONY,
            'stale_minutes': stale,
            'fetched_at': chat2desk.now_almaty().isoformat(),
            'searches_left': (max(0, limit_today - used_today - 1) if limited else None),
        }), 200

    @dch_route('/search')
    def driver_chats_search(ctx):
        raw_phone = request.args.get('phone') or ''
        phone = chat2desk.normalize_phone(raw_phone)
        # Хвост считаем от ИСХОДНОГО ввода, а не от нормализованного номера:
        # узбекский, киргизский и таджикский национальные номера девятизначные,
        # и номер целиком из них не собирается — а найти водителя по ним можно.
        tail = chat2desk.phone_tail(raw_phone)
        # «Обновить» на экране — тот же поиск, но мимо кеша. Отдельного роута он
        # не заслуживает: ответ, лимит и запись в журнал у него ровно те же, а
        # два почти одинаковых обработчика разъехались бы на первой же правке.
        force_fresh = str(request.args.get('refresh') or '').strip().lower() \
            in ('1', 'true', 'yes')
        if not phone and not tail:
            return jsonify({
                "error": "Непохоже на номер телефона. Введите номер целиком — "
                         "например, 87071234567, 79161234567 или "
                         "+998 90 123 45 67",
                "code": "BAD_PHONE",
            }), 400

        if syntony.source() == syntony.SOURCE_SYNTONY:
            return _search_syntony(ctx, raw_phone, phone, tail)

        window_from, window_to = chat2desk.window_bounds()

        # ОДИН поход в базу на всё, что нужно ДО вендора: счётчик лимита, клиент
        # за номером и, если это не обновление, готовая переписка. Раньше это
        # были три отдельных курсора, а каждый — заявка на место в общем пуле
        # соединений, где четыре места постоянно держит цикл запросов бота.
        # Ожидание слота человек ждёт ровно так же, как сам запрос.
        truncated = False
        client_id, messages, fetched_at = None, None, None
        pending_notes = []
        # Поток вебхуков Chat2Desk: сообщения приходят сами и лежат неделю, то есть
        # покрывают всё окно раздела. Пока он жив, у вендора не спрашиваем ничего —
        # ни клиента по номеру, ни переписку, ни имена операторов.
        webhook_payloads = None
        webhook_names = {}
        # Хвост нашёл нескольких водителей — отвечаем после блока: внутри него
        # держится место в общем пуле соединений, и выходить из него с ответом
        # значит держать его дольше нужного.
        ambiguous_tail = False
        with db._get_cursor() as cursor:
            # Потолок сегодня — лимит плюс добавленное «СВ и выше» на этот день.
            used_today, extra_today = queries.search_usage_today(cursor, ctx['user_id'])
            limit_today = DAILY_SEARCH_LIMIT + extra_today
            limited = access.is_search_limited(ctx)
            if not limited or used_today < limit_today:
                # Клиент вендора: сначала своя память о паре телефон-клиент,
                # потом ночной срез заявок, и только потом — API.
                # Точные записи спрашиваем, только если номер собрался
                # целиком: искать в базе по девяти цифрам без кода страны
                # нечего — у вендора номер лежит с кодом.
                stream_ok = queries.webhook_stream_covers(cursor, window_from)
                variants = chat2desk.phone_variants(phone) if phone else []
                client_id = (queries.cached_client_id(cursor, phone)
                             or queries.local_client_id(cursor, variants)
                             # Ночной срез заявок не знает тех, кто написал впервые
                             # сегодня, — а поток знает.
                             or (queries.webhook_client_id(cursor, variants)
                                 if stream_ok else None)) if phone else None
                if client_id is None:
                    # Точные записи не подошли — ищем по ХВОСТУ номера. Это
                    # единственный способ найти иностранный номер, названный без
                    # кода страны: достроить «531 729 83 61» до турецкого
                    # «90 531 729 83 61» нельзя ничем, кроме гадания по двум
                    # сотням кодов, а последние девять цифр у номера одни и те же
                    # в любой записи.
                    by_tail = queries.local_client_by_tail(cursor, tail)
                    if by_tail is None and stream_ok:
                        by_tail = queries.webhook_client_by_tail(cursor, tail)
                    if by_tail and by_tail[0] == 'ambiguous':
                        ambiguous_tail = True
                        # Пишем журнал ЗДЕСЬ же, уже открытым курсором: искать
                        # человек искал, а отдельный курсор ради одной строки —
                        # ещё одно место в общем пуле, где четыре из них держит
                        # цикл запросов бота.
                        queries.log_event(cursor, ctx, 'search', phone=phone or tail,
                                          ip_address=_ip(), user_agent=_ua())
                    elif by_tail:
                        client_id, stored_phone = by_tail
                        # Дальше живём под ТОЙ записью номера, в какой он лежит
                        # у нас: под ней сохранится кеш и ляжет строка журнала,
                        # а повторный поиск того же водителя попадёт уже в
                        # точный путь, без второго прохода по хвосту.
                        phone = chat2desk.normalize_phone(stored_phone) or phone
                if client_id is not None:
                    # Свои заметки «Передан», которых вендор ещё не показывает.
                    # Читаем здесь, чтобы не открывать под них третий курсор.
                    pending_notes = queries.pending_handoff_notes(
                        cursor, client_id, PENDING_NOTE_MINUTES)
                    if stream_ok:
                        # Кеш переписки тут не нужен вовсе: он и заводился, чтобы не
                        # ходить к вендору чаще раза в пять минут, а из событий лента
                        # собирается на каждый заход и показывает сообщение сразу.
                        webhook_payloads = queries.webhook_messages(
                            cursor, client_id, window_from, window_to)
                        webhook_names = queries.chat_author_names(cursor)
                    elif not force_fresh:
                        messages, fetched_at = queries.cached_messages(
                            cursor, client_id, window_from, window_to,
                            CACHE_TTL_SECONDS)
        if ambiguous_tail:
            # Такого на живой базе нет ни разу (16 421 хвост, ни одного
            # совпадения между разными водителями), но если однажды случится —
            # показать переписку соседа нельзя, а молча ответить «не найдено»
            # значило бы соврать. Строка журнала уже записана выше, тем же
            # курсором.
            return jsonify({
                "error": "По последним цифрам этого номера нашлось несколько "
                         "водителей. Введите номер целиком, с кодом страны.",
                "code": "PHONE_AMBIGUOUS",
            }), 400

        if limited and used_today >= limit_today:
            return jsonify({
                "error": _limit_reached_message(ctx, extra_today),
                "code": "DAILY_LIMIT_REACHED",
            }), 429

        client_name = None
        if client_id is None:
            # У вендора фильтры `phone` и `client_phone` ТОЧНЫЕ, поэтому девять
            # цифр без кода страны спрашивать бесполезно — и незачем: вызов
            # стоит квоты, общей с ночным синком метрик отдела. Такой номер мы
            # либо нашли хвостом по своей базе, либо честно говорим «не нашли».
            found = chat2desk.find_client(phone) if phone else None
            if not found or not found.get('id'):
                with db._get_cursor() as cursor:
                    queries.log_event(cursor, ctx, 'search', phone=phone or tail,
                                      ip_address=_ip(), user_agent=_ua())
                return jsonify({
                    'phone': phone or raw_phone.strip(), 'client_id': None, 'chats': [],
                    'window': {'from': window_from.isoformat(), 'to': window_to.isoformat()},
                    'not_found': True,
                    'fetched_at': chat2desk.now_almaty().isoformat(),
                }), 200
            client_id = int(found['id'])
            client_name = found.get('name')

        # Переписка. Кеш прочитан выше, тем же курсором; обновление его не
        # читает вовсе: кнопку жмут ровно тогда, когда пятиминутный снимок не
        # годится — оператор только что отправил внутренний комментарий или ждёт
        # ответа водителя, который сейчас на линии. Отдать ему в ответ тот же
        # самый снимок — это «кнопка не работает», и он нажмёт «Передан» второй
        # раз.
        if webhook_payloads is not None:
            # Лента из событий. Имена операторов — из своей базы; вендорский
            # справочник остаётся запасным на случай пустого среза (новый чатник
            # ещё не попал в ночной синк).
            messages = chat2desk.messages_from_events(
                webhook_payloads, webhook_names or chat2desk.operator_names())
            fetched_at = chat2desk.now_almaty()
        from_cache = messages is not None and webhook_payloads is None
        if messages is None:
            raw, total = chat2desk.fetch_window_messages(client_id, window_from, window_to)
            names = chat2desk.operator_names()
            messages = [chat2desk.normalize_message(msg, names) for msg in raw]
            truncated = total > len(raw)
            fetched_at = chat2desk.now_almaty()
            # Вендор показывает свежесозданное сообщение в списке примерно через
            # минуту, поэтому собственную заметку оператор не видел, сколько бы
            # раз ни нажал «Обновить». Дописываем её сами — id у нашей копии тот
            # же, что вернул вендор, так что его копия, доехав, не задвоится.
            messages = chat2desk.merge_pending_comments(
                messages,
                [chat2desk.pending_comment_message(
                    message_id=note['message_id'], text=note['text'],
                    created=note['created'], channel_id=note['channel_id'],
                    dialog_id=note['dialog_id'], request_id=note['request_id'])
                 for note in pending_notes])

        chats = chat2desk.group_chats(messages)

        # ВТОРОЙ и последний поход в базу: сохранить снимок, забрать метаданные
        # заявок и справочник парков, записать журнал. Вендора внутри блока нет
        # намеренно — держать место в пуле соединений, пока идёт сетевой запрос,
        # значит занимать его на порядок дольше самой работы с базой.
        #
        # Обогащение метаданными заявок — бесплатное и НЕОБЯЗАТЕЛЬНОЕ: сегодняшних
        # заявок в c2d_requests ещё нет (синк идёт в 04:10 за вчера), и чат
        # прекрасно показывается без оценки водителя.
        #
        # А вот ТАКСОПАРК обязателен: по одному телефону приходят чаты разных
        # парков, и без названия оператор не понимает, что перед ним. Поэтому
        # парк берётся из самого сообщения (channel_id) через справочник, а
        # ночной срез заявок остаётся лишь третьим запасным вариантом.
        with db._get_cursor() as cursor:
            if not from_cache and webhook_payloads is None:
                # Снимок переписки нужен только на вендорском пути: лента из событий
                # и так лежит в базе, второй копией она бы просто устаревала.
                queries.store_messages(cursor, client_id, phone, messages,
                                       window_from, window_to)
            # Обогащаемся по ВСЕМ обращениям чата, а не по одному: после склейки
            # по парку их внутри несколько, и любое выбранное было бы
            # произвольным.
            meta = queries.request_meta(
                cursor, [rid for c in chats for rid in c.get('request_ids') or []])
            channels = queries.channel_names(cursor)
            # Журнал пишем здесь же. От обогащения ниже он не зависит ни одним
            # полем — там доклеиваются только названия парков в ответ, — а
            # отдельный курсор ради этой строки стоил бы ещё одного места в пуле.
            queries.log_event(cursor, ctx, 'search', phone=phone, client_id=client_id,
                              messages_count=len(messages), ip_address=_ip(),
                              user_agent=_ua())
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

        return jsonify({
            'phone': phone,
            'client_id': client_id,
            'client_name': client_name,
            'chats': chats,
            'window': {'from': window_from.isoformat(), 'to': window_to.isoformat()},
            'truncated': truncated,
            'from_cache': from_cache,
            # Возраст ленты — с сервера, а не с часов браузера: на кеше они
            # расходятся на его возраст, и подпись «обновлено» врала бы.
            'fetched_at': fetched_at.isoformat() if fetched_at else None,
            'searches_left': (max(0, limit_today - used_today - 1)
                              if limited else None),
        }), 200

    # ── Открытие чата (только журнал) ────────────────────────────────────────

    @dch_route('/open', methods=('POST',))
    def driver_chats_open(ctx):
        data = _payload()
        phone = chat2desk.normalize_phone(data.get('phone'))
        if not phone:
            return jsonify({"error": "Не указан телефон водителя", "code": "BAD_PHONE"}), 400
        if syntony.source() == syntony.SOURCE_SYNTONY:
            # У Syntony канал и диалог — GUID строкой: их место — свои колонки журнала.
            with db._get_cursor() as cursor:
                event = queries.log_event(
                    cursor, ctx, 'open', phone=phone,
                    channel_name=(data.get('channel_name') or None),
                    messages_count=_int_or_none(data.get('messages_count')),
                    source=syntony.SOURCE_SYNTONY,
                    syntony_channel_id=syntony.clean_id(data.get('channel_id')),
                    syntony_conversation_id=syntony.clean_id(data.get('dialog_id')),
                    ip_address=_ip(), user_agent=_ua())
            return jsonify({'status': 'ok', 'event': event}), 200
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

    def _handoff_syntony(ctx, data):
        """«Передан» на Syntony: отметка в журнале раздела, в ленту своего чата — сразу.

        В сам Syntony отметка не уходит: в API истории внутренних заметок нет (он только
        читает), отправка в чат — партнёрский API. Экран так и говорит (handoff_to_chat=false
        в /context), а ответ несёт delivered_to_chat=false."""
        phone = chat2desk.normalize_phone(data.get('phone'))
        channel = syntony.clean_id(data.get('channel_id'))
        if not phone or not channel or channel not in set(syntony.channel_ids()):
            return jsonify({"error": "Не указан чат водителя", "code": "BAD_TARGET"}), 400
        note = str(data.get('note') or '').strip()
        if len(note) > chat2desk.MAX_COMMENT_LENGTH:
            return jsonify({
                "error": "Комментарий длиннее %d символов" % chat2desk.MAX_COMMENT_LENGTH,
                "code": "COMMENT_TOO_LONG",
            }), 400
        text = chat2desk.build_handoff_text(ctx.get('name'), note)
        conversation = syntony.clean_id(data.get('dialog_id'))
        with db._get_cursor() as cursor:
            # Отметку ставят только на свой чат: номер СЗоВ, где этот водитель писал.
            if not syntony.contact_has_channel(cursor, phone, channel):
                return jsonify({"error": "Этого чата у водителя нет", "code": "BAD_TARGET"}), 400
            event = queries.log_event(
                cursor, ctx, 'handoff', phone=phone,
                channel_name=(data.get('channel_name') or None),
                comment_text=text, source=syntony.SOURCE_SYNTONY,
                syntony_channel_id=channel, syntony_conversation_id=conversation,
                ip_address=_ip(), user_agent=_ua())
        return jsonify({
            'status': 'ok', 'sent': None, 'text': text, 'event': event,
            'delivered_to_chat': False,
            'message': syntony.handoff_message(
                event_id=event['id'], text=text, created=event.get('created_at'),
                channel_id=channel, conversation_id=conversation),
        }), 200

    @dch_route('/handoff', methods=('POST',))
    def driver_chats_handoff(ctx):
        data = _payload()
        if syntony.source() == syntony.SOURCE_SYNTONY:
            return _handoff_syntony(ctx, data)
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

        # Готовое сообщение для ленты — чтобы экран показал заметку СРАЗУ, не
        # дожидаясь, пока вендор начнёт отдавать её в выборке (это около
        # минуты). Собирает его сервер, а не фронт: форму сообщения знает
        # chat2desk.normalize_message, и второе место, знающее её же, разъехалось
        # бы с ним на первой правке.
        return jsonify({
            'status': 'ok', 'sent': sent, 'text': text, 'event': event,
            'message': chat2desk.pending_comment_message(
                message_id=sent.get('message_id'),
                text=text,
                created=chat2desk.now_almaty(),
                channel_id=_int_or_none(data.get('channel_id')),
                dialog_id=sent.get('dialog_id') or _int_or_none(data.get('dialog_id')),
                request_id=sent.get('request_id')),
        }), 200

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
            'kinds': [k for k in kinds if k in schema.EVENT_KINDS] or None,
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

    def _export_period(args):
        """Период выгрузки из запроса. Возвращает ((начало, конец), ответ-ошибка).

        Правила ровно те, что показывает пикер, — и здесь они настоящие, а не
        подсказка: кнопку в интерфейсе можно обойти, ручку зовут напрямую.
        Экран журнала при этом периодом не ограничен: смотреть за квартал можно,
        а вот собрать его одним файлом — нет.

        Перевёрнутый период не отвергаем, а разворачиваем: «с 30-го по 1-е» —
        это описка, а не попытка сломать выгрузку (так же поступают выгрузки
        «Посылок» и табло СЗоВ).
        """
        def _day(name):
            raw = (args.get(name) or '').strip()
            if not raw:
                return None
            try:
                return datetime.strptime(raw[:10], '%Y-%m-%d').date()
            except ValueError:
                return None

        date_from = _day('date_from')
        date_to = _day('date_to')
        if not date_from or not date_to:
            return None, (jsonify({
                "error": "Выберите период выгрузки — не длиннее %d суток"
                         % report.EXPORT_MAX_DAYS,
                "code": "DRIVER_CHATS_PERIOD_REQUIRED",
            }), 400)
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        # Обе границы включительно: «с 1 по 1» — это одни сутки, а не ноль.
        days = (date_to - date_from).days + 1
        if days > report.EXPORT_MAX_DAYS:
            return None, (jsonify({
                # Называем ЗАПРОШЕННУЮ длину, а не только потолок: «слишком
                # длинно» без числа заставляет человека считать самому.
                # Запрошенную длину склоняем («31 день», «32 дня»), а потолок —
                # число фиксированное, и «суток» после него стоит правильно.
                "error": "Период %s — это слишком много за раз. Максимум %d суток: "
                         "выберите период короче."
                         % (report.plural_days(days), report.EXPORT_MAX_DAYS),
                "code": "DRIVER_CHATS_PERIOD_TOO_LONG",
            }), 400)
        return (date_from, date_to), None

    @dch_route('/journal/export', journal=True)
    def driver_chats_journal_export(ctx):
        # Период у файла ОБЯЗАТЕЛЕН и не длиннее месяца, в отличие от экрана.
        # Считается он до всего остального: незачем ходить в базу за выборкой,
        # которую всё равно нельзя отдать.
        period, error = _export_period(request.args)
        if error:
            return error
        period_from, period_to = period

        filters = _journal_filters()
        # Рамку файла задаёт пикер, а не фильтр экрана: на экране период мог
        # остаться каким угодно, а книга собирается по выбранному в пикере.
        # Верхняя граница — начало следующих суток, как и в фильтре: иначе «по
        # 3 сентября» молча теряло бы всё, что было в этот день после полуночи.
        filters['date_from'] = datetime.combine(period_from, datetime.min.time())
        filters['date_to'] = datetime.combine(period_to, datetime.min.time()) + timedelta(days=1)
        filters['raw_from'] = period_from
        filters['raw_to'] = period_to

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

    # ── «Настройки»: поиски сверх лимита на сегодня ──────────────────────────
    #
    # Владелец 27.10.2026: «св и выше добавь в настройках возможность добавлять
    # дополнительные попытки операторам до 5 попыток», и на уточнение — только
    # на сегодня. Оператор упёрся в лимит, пишет супервайзеру, тот добавляет
    # +1…+5 до полуночи по Алматы; завтра лимит снова обычный сам собой.

    @dch_route('/settings', settings=True)
    def driver_chats_settings(ctx):
        with db._get_cursor() as cursor:
            people = queries.extra_search_people(cursor)
        return jsonify({
            'daily_limit': DAILY_SEARCH_LIMIT,
            'max_extra': MAX_EXTRA_SEARCHES,
            'people': people,
        }), 200

    @dch_route('/settings/extra/<int:user_id>', methods=('PUT',), settings=True)
    def driver_chats_set_extra(ctx, user_id):
        body = request.get_json(silent=True)
        body = body if isinstance(body, dict) else {}
        extra = small_int(body.get('extra'))
        # expected — сколько было на экране у нажавшего. Необязателен (ручку
        # можно позвать и руками), но если пришёл — обязан быть числом: молча
        # пропустить сломанный значило бы снять защиту от устаревшего списка.
        expected = small_int(body['expected']) if 'expected' in body else None
        if (extra is None or not 0 <= extra <= MAX_EXTRA_SEARCHES
                or ('expected' in body and expected is None)):
            return jsonify({
                "error": "Добавить можно от 0 до %d поисков" % MAX_EXTRA_SEARCHES,
                "code": "EXTRA_OUT_OF_RANGE",
            }), 400
        with db._get_cursor() as cursor:
            # Право получателя проверяем на сервере, а не списком на экране:
            # ручку зовут и напрямую, а список мог устареть, пока окно было
            # открыто (человека перевели, уволили, назначили супервайзером).
            person = queries.load_access_context(cursor, user_id)
            if not person:
                return jsonify({"error": "Сотрудник не найден",
                                "code": "USER_NOT_FOUND"}), 404
            if not access.can_receive_extra_searches(person):
                return jsonify({
                    "error": "Добавить поиски можно только операторам, стажёрам "
                             "и тренерам раздела",
                    "code": "EXTRA_NOT_ALLOWED",
                }), 403
            saved, previous = queries.set_extra_searches(
                cursor, user_id, extra, ctx, expected=expected)
            if saved and saved['extra_today'] != previous:
                # Журнал — той же транзакцией: добавка без строки в журнале —
                # это «43 поиска при лимите 40» без объяснения, а у самой
                # добавки за день хранится только последний, кто её менял.
                queries.log_event(cursor, ctx, 'extra_searches',
                                  comment_text=extra_searches_note(person, extra, previous),
                                  ip_address=_ip(), user_agent=_ua())
            used_today, _ = queries.search_usage_today(cursor, user_id)
        if saved is None:
            current = previous
            shown = ('+%d' % current['extra_today']) if current['extra_today'] else '0'
            who = (' (%s)' % current['granted_by_name']
                   if current['extra_today'] and current['granted_by_name'] else '')
            return jsonify({
                "error": "Пока список был открыт, добавку уже поменяли: сейчас %s%s. "
                         "Строка обновлена." % (shown, who),
                "code": "EXTRA_CHANGED",
                'user_id': user_id, 'used_today': used_today, **current,
            }), 409
        logging.info('driver_chats: %s поставил(а) %s доп. поисков на сегодня — %s',
                     ctx.get('name'), extra, person.get('name'))
        return jsonify({'user_id': user_id, 'used_today': used_today, **saved}), 200

    # ── Вложения Syntony ─────────────────────────────────────────────────────
    #
    # Ручка БЕЗ общего require_api_key: её зовут <img>, <audio> и ссылка в новой вкладке, а
    # они не несут заголовок с токеном портала (сайт и API на разных адресах, вход — токеном
    # в заголовке). Пропуск — подпись в адресе (syntony.check_media_link): её выдаёт только
    # поиск, прошедший все гейты раздела, она привязана к сообщению и человеку и живёт 2–3
    # часа. На каждом открытии заново проверяем человека (работает, в периметре раздела) и
    # сообщение (номер СЗоВ, не удалено, вложение есть) — и только потом берём у Syntony
    # ссылку на 5 минут и переадресуем на неё. Ссылку не храним: так велит Syntony (§3.5).

    def _media_refusal(message, code, status, retry_after=None):
        response = jsonify({"error": message, "code": code})
        response.status_code = status
        response.headers['Cache-Control'] = 'no-store'
        if retry_after:
            response.headers['Retry-After'] = str(int(retry_after))
        return response

    @bp.route('/media/<message_id>', methods=['GET'])
    def driver_chats_media(message_id):
        user_id = syntony.check_media_link(media_key, message_id, request.args)
        if user_id is None:
            return _media_refusal("Ссылка на файл устарела — обновите переписку",
                                  'MEDIA_LINK_INVALID', 403)
        try:
            with db._get_cursor() as cursor:
                person = queries.load_access_context(cursor, user_id)
                person_ok = bool(person) and str(person.get('status') or 'working').strip().lower() \
                    not in ('fired', 'dismissal') and access.can_open_section(person)
                file_ok = person_ok and syntony.media_allowed(cursor, message_id,
                                                              syntony.channel_ids())
        except Exception:  # noqa: BLE001
            logging.exception('driver_chats: вложение Syntony — ошибка базы')
            return _media_refusal("Файл сейчас недоступен", 'MEDIA_ERROR', 500)
        if not person_ok:
            return _media_refusal("Раздел «Чаты водителей» вам не открыт",
                                  'DRIVER_CHATS_SECTION_CLOSED', 403)
        if not file_ok:
            return _media_refusal("Файл не найден", 'MEDIA_NOT_FOUND', 404)
        config = syntony_client.load_config()
        if config is None:
            return _media_refusal("Syntony не подключена: на сервере нет ключа",
                                  'SYNTONY_NOT_CONFIGURED', 503)
        try:
            link = syntony_client.file_link(config, message_id)
        except syntony_client.SyntonyHistoryError as exc:
            # В журнал — код и статус, без адреса: в нём бывает номер и подпись.
            logging.warning('driver_chats: вложение Syntony — отказ %s', exc.code)
            if exc.status in (409, 429, 503):
                return _media_refusal("Файл ещё загружается — откройте через минуту",
                                      'MEDIA_NOT_READY', 503, exc.retry_after or 30)
            if exc.status in (404, 410):
                return _media_refusal("Файл недоступен: у Syntony его больше нет",
                                      'MEDIA_GONE', 410)
            return _media_refusal("Syntony не отдала файл", 'MEDIA_UPSTREAM', 502)
        response = redirect(link['url'], code=302)
        # Ссылку Syntony хранить нельзя (живёт 5 минут), значит и переадресацию на неё — тоже.
        # Открытая картинка браузером второй раз в той же странице не запрашивается.
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

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
