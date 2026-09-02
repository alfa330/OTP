# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Лиды OLX» (Flask Blueprint).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль, и обратный импорт был
бы циклом (ровно как в parcels/routes.py, wiki/routes.py и crm/routes.py).

Что раздел показывает
---------------------
Раздел 7 ТЗ требует журнал по каждому обращению с выгрузкой за произвольный
период, ежедневную сводку и уведомления о простое. Отсюда четыре ручки чтения:

    GET /api/olx_amo/ping        — жив ли раздел, что смотрящему можно
    GET /api/olx_amo/health      — состояние девяти кабинетов и простой
    GET /api/olx_amo/awaiting    — чаты, где ждут ответа живого человека
    GET /api/olx_amo/journal     — лента обращений с фильтрами и пагинацией
    GET /api/olx_amo/journal/export — тот же журнал файлом, за произвольный период
    GET /api/olx_amo/summary     — сводка за день по кабинетам

и подключение кабинета:

    GET  /api/olx_amo/oauth/callback             — куда OLX вернёт браузер, БЕЗ авторизации
    GET  /api/olx_amo/cabinets/<code>/authorize  — ссылка на согласие владельца
    POST /api/olx_amo/cabinets/<code>/callback   — обмен кода на токены (только админу)

Почему подключение разведено на «показать код» и «применить код». Страница
возврата не может быть защищённой: на неё приходит редирект браузера от OLX, а
заголовка с токеном портала в нём нет. Обменивай она код сразу — любой, кто
открыл ссылку, подключал бы кабинеты к нашей CRM без единой проверки прав.
Поэтому страница только показывает код (без `client_secret` он бесполезен и живёт
секунды), а обмен делает вторая ручка — уже из раздела, под админом.

Заодно это снимает зависимость от того, что именно OLX согласился прописать в
заявке: если там стоит голый адрес сервиса, браузер уедет на «Bot is alive!», и
человек скопирует адрес целиком — код из него достанет `_extract_code`.
"""

import logging
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import quote

from flask import Blueprint, jsonify, make_response, request

from . import access, cabinets, queries, schema

log = logging.getLogger(__name__)

# Порог «тихого» простоя из раздела 7 ТЗ.
DEFAULT_IDLE_MINUTES = 15

_MAX_PAGE = 500


def build_olx_amo_blueprint(*, db, require_api_key, build_cors_preflight_response,
                            resolve_requester):
    """Собирает Blueprint раздела.

    Все зависимости — обязательные keyword-only без значений по умолчанию:
    забытая зависимость должна уронить сборку блюпринта на старте (раздел тогда
    просто не поднимется), а не тихо открыть журнал всем подряд.
    """
    bp = Blueprint('olx_amo', __name__, url_prefix='/api/olx_amo')

    def section_route(rule, methods=('GET',), manage=False):
        """Общий каркас роута: preflight, авторизация, контекст, ошибки.

        manage=True — роут выдаёт или отзывает доступ к переписке кабинетов;
        такие открыты только глобальному админу. Проверка здесь, чтобы не
        повторять её в каждом обработчике и не забыть в новом.
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
                    # пункт меню доступом не является.
                    if not access.can_view(ctx):
                        return jsonify({"error": "Раздел недоступен"}), 403
                    if manage and not access.can_manage_cabinets(ctx):
                        return jsonify({
                            "error": "Подключать кабинеты OLX может только администратор"
                        }), 403

                    return handler(ctx, *args, **kwargs)
                except Exception:
                    log.exception('Лиды OLX: обработчик %s упал', handler.__name__)
                    return jsonify({"error": "Внутренняя ошибка раздела"}), 500

            return wrapper

        return decorator

    # ── состояние раздела ────────────────────────────────────────────────

    @section_route('/ping')
    def olx_amo_ping(ctx):
        with db._get_cursor() as cursor:
            ready = schema.schema_is_ready(cursor)
        return jsonify({
            "ok": True,
            "schema_ready": ready,
            "capabilities": access.capabilities(ctx),
            "cabinets_total": len(cabinets.CABINETS),
            "cabinets_configured": len(cabinets.configured()),
        })

    @section_route('/health')
    def olx_amo_health(ctx):
        """Состояние девяти кабинетов: кто когда опрашивался и кто молчит.

        Это и есть противоядие от «тихого» простоя: пустой опрос тоже
        отмечается, поэтому «обращений нет» видно отдельно от «робот умер».
        """
        idle = _int_arg('idle_minutes', DEFAULT_IDLE_MINUTES, 1, 1440)
        with db._get_cursor() as cursor:
            queries.ensure_accounts(cursor)
            rows = queries.health(cursor, idle_minutes=idle)

        known = {c.code: c for c in cabinets.CABINETS}
        items = []
        for row in rows:
            cab = known.get(row.get('code'))
            items.append({
                "code": row.get('code'),
                "title": cab.title if cab else row.get('code'),
                "olx_id": cab.olx_id if cab else None,
                "tag": cab.tag_form if cab else None,
                "line_phone": cab.line_phone if cab else None,
                "state": row.get('state'),
                "is_enabled": bool(row.get('is_enabled')),
                "is_configured": bool(cab.is_configured()) if cab else False,
                "last_poll_at": _iso(row.get('last_poll_at')),
                "last_message_at": _iso(row.get('last_message_at')),
                "last_lead_at": _iso(row.get('last_lead_at')),
                "last_error": row.get('last_error'),
                "consecutive_failures": row.get('consecutive_failures') or 0,
                "is_stale": bool(row.get('is_stale')),
            })
        stale = [i['code'] for i in items if i['is_stale'] and i['is_enabled']]
        return jsonify({
            "idle_minutes": idle,
            "cabinets": items,
            "stale": stale,
            "is_healthy": not stale,
        })

    @section_route('/awaiting')
    def olx_amo_awaiting(ctx):
        """Чаты, где кандидат написал ещё раз и ждёт живого ответа.

        Робот на повторное обращение молчит намеренно (решение владельца
        02.09.2026): второе автоматическое сообщение раздражает и читается как
        поломка. Вместо него — вот этот список. Он рабочая очередь маркетолога,
        а не отчёт, поэтому самые давние сверху: им хуже всех.
        """
        with db._get_cursor() as cursor:
            rows = queries.awaiting_human(cursor)

        now = queries.now_almaty()
        items = []
        for row in rows:
            cab = cabinets.BY_CODE.get(row.get('cabinet_code'))
            since = row.get('awaiting_human_since')
            items.append({
                "cabinet": row.get('cabinet_code'),
                "cabinet_title": cab.title if cab else row.get('cabinet_code'),
                "thread_id": row.get('thread_id'),
                "since": _iso(since),
                "waiting_minutes": int((now - since).total_seconds() // 60) if since else None,
                "last_message_at": _iso(row.get('last_message_at')),
                # Ссылка на сам чат в кабинете OLX — чтобы отвечать было куда
                # нажать, а не искать переписку глазами.
                "url": "https://www.olx.kz/mojolx/wiadomosci/#!thread=%s" % row.get('thread_id'),
            })
        return jsonify({"total": len(items), "items": items})

    # ── журнал и сводка ──────────────────────────────────────────────────

    @section_route('/journal')
    def olx_amo_journal(ctx):
        """Лента обращений. Фильтры: период, кабинет, результат, номер."""
        limit = _int_arg('limit', 100, 1, _MAX_PAGE)
        offset = _int_arg('offset', 0, 0, 10 ** 7)
        date_from = _date_arg('date_from')
        date_to = _date_arg('date_to')
        # Верхняя граница включительна для человека: «по 31.08» значит вместе с
        # 31 августа. В SQL сравнение строгое, поэтому добавляем сутки.
        if date_to:
            date_to = date_to + timedelta(days=1)

        with db._get_cursor() as cursor:
            page = queries.journal_page(
                cursor, date_from=date_from, date_to=date_to,
                cabinet_code=(request.args.get('cabinet') or '').strip() or None,
                result=(request.args.get('result') or '').strip() or None,
                phone=(request.args.get('phone') or '').strip() or None,
                limit=limit, offset=offset)

        return jsonify({
            "total": page['total'],
            "limit": limit,
            "offset": offset,
            "items": [_journal_item(row) for row in page['items']],
        })

    @section_route('/summary')
    def olx_amo_summary(ctx):
        """Сводка за день по кабинетам — раздел 7 ТЗ."""
        day = _date_arg('day') or queries.today_almaty()
        with db._get_cursor() as cursor:
            rows = queries.daily_summary(cursor, day=day)

        known = {c.code: c for c in cabinets.CABINETS}
        items = []
        for row in rows:
            cab = known.get(row.get('cabinet_code'))
            avg = row.get('avg_latency_ms')
            items.append({
                "code": row.get('cabinet_code'),
                "title": cab.title if cab else row.get('cabinet_code'),
                "total": row.get('total') or 0,
                "leads": row.get('leads') or 0,
                "duplicates": row.get('duplicates') or 0,
                "manual": row.get('manual') or 0,
                "replies": row.get('replies') or 0,
                "errors": row.get('errors') or 0,
                "sla_missed": row.get('sla_missed') or 0,
                "avg_latency_ms": int(avg) if avg is not None else None,
                "max_latency_ms": row.get('max_latency_ms'),
            })
        totals = {
            key: sum(int(i[key] or 0) for i in items)
            for key in ('total', 'leads', 'duplicates', 'manual', 'replies',
                        'errors', 'sla_missed')
        }
        return jsonify({"day": day.isoformat(), "cabinets": items, "totals": totals})

    @section_route('/journal/export')
    def olx_amo_journal_export(ctx):
        """Журнал за произвольный период файлом — прямое требование раздела 7 ТЗ.

        Отдельная ручка, а не флаг у ленты: у выгрузки нет пагинации и другой
        ответ (файл вместо JSON), а смешивать их в одном обработчике значит
        каждый раз выяснять, какой из двух режимов сейчас.
        """
        from io import BytesIO

        import xlsxwriter

        date_from = _date_arg('date_from')
        date_to = _date_arg('date_to')
        if date_to:
            date_to = date_to + timedelta(days=1)
        cabinet_code = (request.args.get('cabinet') or '').strip() or None

        with db._get_cursor() as cursor:
            rows = queries.journal_for_export(
                cursor, date_from=date_from, date_to=date_to,
                cabinet_code=cabinet_code)

        stream = BytesIO()
        book = xlsxwriter.Workbook(stream, {'in_memory': True,
                                            'default_date_format': 'dd.mm.yyyy hh:mm:ss'})
        sheet = book.add_worksheet('Обращения OLX')
        head = book.add_format({'bold': True, 'bg_color': '#F1F5F9', 'border': 1})
        when = book.add_format({'num_format': 'dd.mm.yyyy hh:mm:ss'})
        # Номер телефона — ТЕКСТОМ. Иначе Excel считает его числом, теряет
        # ведущие цифры формата и ставит на ячейку зелёный уголок «число
        # сохранено как текст» при обратном чтении.
        as_text = book.add_format({'num_format': '@'})

        columns = [
            ('Время отклика', 24), ('Время сделки', 24), ('Доставка, с', 12),
            ('Кабинет', 18), ('Как написан номер', 22), ('Номер в CRM', 16),
            ('Тег', 20), ('Исход', 18), ('Сделка', 12), ('Контакт', 12),
            ('Ошибка', 40), ('Текст обращения', 60),
        ]
        for index, (title, width) in enumerate(columns):
            sheet.write(0, index, title, head)
            sheet.set_column(index, index, width)
        sheet.freeze_panes(1, 0)

        labels = {
            'lead_created': 'Сделка создана', 'duplicate': 'Повтор за день',
            'manual_review': 'Нужна проверка', 'canned_reply': 'Отправлен ответ',
            'skipped': 'Пропущено', 'error': 'Ошибка',
        }
        for line, row in enumerate(rows, start=1):
            cab = cabinets.BY_CODE.get(row.get('cabinet_code'))
            latency = row.get('latency_ms')
            sheet.write_datetime(line, 0, row['message_at'], when) \
                if row.get('message_at') else sheet.write_blank(line, 0, None)
            sheet.write_datetime(line, 1, row['lead_created_at'], when) \
                if row.get('lead_created_at') else sheet.write_blank(line, 1, None)
            sheet.write(line, 2, round(latency / 1000.0, 1) if latency is not None else '')
            sheet.write(line, 3, cab.title if cab else row.get('cabinet_code') or '')
            sheet.write_string(line, 4, str(row.get('phone_raw') or ''), as_text)
            sheet.write_string(line, 5, str(row.get('phone_normalized') or ''), as_text)
            sheet.write(line, 6, row.get('tag') or '')
            sheet.write(line, 7, labels.get(row.get('result'), row.get('result') or ''))
            sheet.write(line, 8, row.get('amo_lead_id') or '')
            sheet.write(line, 9, row.get('amo_contact_id') or '')
            sheet.write(line, 10, row.get('error_text') or '')
            sheet.write(line, 11, row.get('message_excerpt') or '')

        book.close()
        stream.seek(0)

        name = 'Лиды OLX %s.xlsx' % (
            (date_from.isoformat() if date_from else 'всё') if not date_to
            else '%s — %s' % (date_from.isoformat() if date_from else 'начало',
                              (date_to - timedelta(days=1)).isoformat()))
        response = make_response(stream.read())
        response.headers['Content-Type'] = (
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        # filename* с UTF-8: имя файла по-русски, а латинский запасной вариант
        # нужен старым клиентам, которые расширенную форму не понимают.
        response.headers['Content-Disposition'] = (
            "attachment; filename=olx_leads.xlsx; filename*=UTF-8''%s"
            % quote(name))
        return response

    # ── куда слать отбивку ───────────────────────────────────────────────

    @section_route('/chats', manage=True)
    def olx_amo_chats(ctx):
        """Группы, в которых есть бот, и какие из них выбраны для отбивки.

        Свой реестр групп раздел не заводит: те, куда бота добавили, уже копятся
        в общей таблице `it_ticket_channels` — её наполняет обработчик
        `my_chat_member`, и из неё же берут списки «Обращения» и «Бот опозданий».
        Второй справочник тех же групп немедленно разошёлся бы с первым.
        """
        try:
            available = db.list_it_ticket_channels(active_only=True) or []
        except Exception:
            log.exception('Лиды OLX: не удалось прочитать список чатов бота')
            available = []

        with db._get_cursor() as cursor:
            selected = queries.list_alert_chats(cursor)

        chosen = {int(row['chat_id']) for row in selected}
        return jsonify({
            "available": [{
                "chat_id": channel.get('chat_id'),
                "title": channel.get('title') or str(channel.get('chat_id')),
                "chat_type": channel.get('chat_type'),
                "username": channel.get('username'),
            } for channel in available],
            "selected": [{
                "chat_id": row.get('chat_id'),
                "title": row.get('title'),
                "chat_type": row.get('chat_type'),
                "last_sent_at": _iso(row.get('last_sent_at')),
                # Бота могли убрать из группы уже после того, как её выбрали.
                # Показываем это прямо, а не роняем отправку молча.
                "is_available": int(row['chat_id']) in {
                    int(c.get('chat_id')) for c in available
                    if c.get('chat_id') is not None},
            } for row in selected],
            "chosen_ids": sorted(chosen),
        })

    @section_route('/chats', methods=('PUT',), manage=True)
    def olx_amo_chats_save(ctx):
        """Сохранить выбор чатов. Тело: {"chat_ids": [-100123, ...]}."""
        body = request.get_json(silent=True) or {}
        raw_ids = body.get('chat_ids')
        if not isinstance(raw_ids, list):
            return jsonify({"error": "Нужен список chat_ids"}), 400

        wanted = set()
        for value in raw_ids:
            try:
                wanted.add(int(value))
            except (TypeError, ValueError):
                return jsonify({"error": "Некорректный идентификатор чата"}), 400

        # Название берём из реестра бота, а не из тела запроса: клиент мог
        # прислать что угодно, а в отбивке и в разделе должно стоять то же имя,
        # что видит человек в Telegram.
        try:
            known = {int(c['chat_id']): c for c in (db.list_it_ticket_channels(True) or [])
                     if c.get('chat_id') is not None}
        except Exception:
            log.exception('Лиды OLX: не удалось прочитать список чатов бота')
            known = {}

        unknown = wanted - set(known)
        if unknown:
            return jsonify({
                "error": "В этих чатах бота нет: %s"
                         % ', '.join(str(x) for x in sorted(unknown)),
            }), 400

        payload = [{
            'chat_id': chat_id,
            'title': known[chat_id].get('title'),
            'chat_type': known[chat_id].get('chat_type'),
        } for chat_id in sorted(wanted)]

        with db._get_cursor() as cursor:
            saved = queries.set_alert_chats(cursor, payload, actor_id=ctx.get('user_id'))
        log.info('Лиды OLX: отбивка настроена на %d чатов пользователем %s',
                 len(saved), ctx.get('user_id'))
        return jsonify({"ok": True, "chosen_ids": [int(r['chat_id']) for r in saved]})

    # ── страница возврата из OLX ─────────────────────────────────────────

    @bp.route('/oauth/callback', methods=['GET'])
    def olx_amo_oauth_landing():
        """Куда OLX возвращает браузер после согласия владельца кабинета.

        БЕЗ авторизации — и иначе быть не может: сюда приходит не наш запрос, а
        редирект браузера от OLX, и заголовка с токеном портала в нём нет.

        Опасности в этом нет. Страница ничего не делает: не ходит в OLX, не
        трогает базу, не знает секрета приложения. Она лишь показывает код,
        который без `client_secret` бесполезен и живёт считанные секунды. Сам
        обмен делает следующая ручка — уже из раздела, под админом.

        Почему не обменять код прямо здесь. Тогда любой, кто открыл ссылку,
        подключал бы кабинеты к нашей CRM без единой проверки прав. Разделение
        «показать код» и «применить код» и есть та проверка.
        """
        from html import escape

        code = (request.args.get('code') or '').strip()
        state = (request.args.get('state') or '').strip()
        error = (request.args.get('error_description')
                 or request.args.get('error') or '').strip()

        cabinet = cabinets.get(state)
        title = cabinet.title if cabinet else (state or 'кабинет не определён')

        if error or not code:
            body = ('<h1>OLX не выдал доступ</h1>'
                    '<p class="muted">%s</p>'
                    % escape(error or 'В ответе нет кода согласия.'))
        else:
            body = (
                '<h1>Доступ подтверждён</h1>'
                '<p class="muted">Кабинет: <b>%s</b></p>'
                '<p class="muted">Скопируйте код и вставьте его в разделе '
                '«Лиды OLX». Код живёт считанные секунды — не откладывайте.</p>'
                '<div class="code" id="code">%s</div>'
                '<button onclick="navigator.clipboard.writeText('
                'document.getElementById(\'code\').textContent.trim())">'
                'Скопировать код</button>'
                % (escape(title), escape(code)))

        page = (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Подключение кабинета OLX</title><style>'
            'body{font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;'
            'background:#f8fafc;color:#0f172a;margin:0;display:grid;place-items:center;'
            'min-height:100vh;padding:24px}'
            'main{background:#fff;border-radius:18px;padding:28px;max-width:520px;'
            'box-shadow:0 1px 3px rgba(15,23,42,.08);border:1px solid #e2e8f0}'
            'h1{font-size:19px;margin:0 0 8px}.muted{color:#64748b;font-size:13.5px;margin:6px 0}'
            '.code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:14px;'
            'background:#f1f5f9;border-radius:12px;padding:12px;margin:14px 0;word-break:break-all}'
            'button{font:inherit;background:#0f172a;color:#fff;border:0;border-radius:12px;'
            'padding:10px 16px;cursor:pointer}button:active{transform:scale(.98)}'
            '</style></head><body><main>%s</main></body></html>' % body)

        response = make_response(page)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        # Код одноразовый и короткоживущий — в кеше ему делать нечего.
        response.headers['Cache-Control'] = 'no-store'
        return response

    # ── подключение кабинета ─────────────────────────────────────────────

    @section_route('/cabinets/<code>/authorize', manage=True)
    def olx_amo_authorize(ctx, code):
        """Ссылка, по которой владелец кабинета подтверждает доступ.

        Ссылку надо открывать в браузере, где выполнен вход ИМЕННО в этот
        кабинет OLX: согласие выдаёт тот, кто вошёл, а не тот, чей client_id в
        ссылке. Перепутать вход — самая частая ошибка подключения: токен
        приедет, но чужой, и робот начнёт читать чужие чаты.
        """
        from .olx_client import REDIRECT_URI, authorize_url

        cabinet = cabinets.get(code)
        if not cabinet:
            return jsonify({"error": "Неизвестный кабинет OLX"}), 404
        if not cabinet.env_client_id:
            return jsonify({
                "error": "Для кабинета не задан OLX_CLIENT_ID_%d" % cabinet.env_index,
                "hint": "client_id выдаёт OLX после одобрения заявки на developer.olx.kz",
            }), 409

        # Порядок: явный параметр запроса → адрес этого кабинета
        # (OLX_REDIRECT_URI_<N>) → общий OLX_REDIRECT_URI. Свой адрес у кабинета
        # нужен потому, что заявки заводились в разное время: там, где уже вписан
        # какой-то адрес, менять его опасно — тем же приложением может
        # пользоваться что-то ещё, и подмена сломает ЕГО экран согласия.
        redirect_uri = (request.args.get('redirect_uri')
                        or cabinet.env_redirect_uri
                        or REDIRECT_URI).strip()
        if not redirect_uri:
            return jsonify({
                "error": "Не задан адрес возврата",
                "hint": "Задайте OLX_REDIRECT_URI (или OLX_REDIRECT_URI_%d для этого "
                        "кабинета) тем же адресом, что вписан в заявку OLX"
                        % cabinet.env_index,
            }), 400

        return jsonify({
            "cabinet": cabinet.code,
            "title": cabinet.title,
            "url": authorize_url(cabinet.env_client_id, redirect_uri, cabinet.code),
        })

    @section_route('/cabinets/<code>/callback', methods=('POST',), manage=True)
    def olx_amo_callback(ctx, code):
        """Обменять код согласия на токены и сохранить их.

        Код живёт считанные секунды: между копированием из адресной строки и
        этим запросом медлить нельзя, иначе OLX ответит `invalid_grant` и
        согласие придётся выдавать заново.
        """
        from .olx_client import REDIRECT_URI, OlxError, exchange_code

        cabinet = cabinets.get(code)
        if not cabinet:
            return jsonify({"error": "Неизвестный кабинет OLX"}), 404

        body = request.get_json(silent=True) or {}
        auth_code = _extract_code(body.get('code'))
        # Тот же адрес, что уехал в экран согласия.
        redirect_uri = str(body.get('redirect_uri')
                           or cabinet.env_redirect_uri or REDIRECT_URI).strip()
        if not auth_code:
            return jsonify({"error": "Нужен код согласия из адресной строки"}), 400
        if not cabinet.is_configured():
            return jsonify({
                "error": "Для кабинета не заданы OLX_CLIENT_ID_%d / OLX_CLIENT_SECRET_%d"
                         % (cabinet.env_index, cabinet.env_index),
            }), 409

        try:
            tokens = exchange_code(cabinet.env_client_id, cabinet.env_client_secret,
                                   auth_code, redirect_uri)
        except OlxError as exc:
            return jsonify({"error": str(exc)}), 502

        with db._get_cursor() as cursor:
            queries.ensure_accounts(cursor)
            queries.save_tokens(cursor, cabinet.code, tokens['access_token'],
                                tokens['expires_at'], tokens.get('refresh_token'),
                                tokens.get('scope'))
        log.info('Лиды OLX: кабинет %s подключён пользователем %s',
                 cabinet.code, ctx.get('user_id'))
        return jsonify({"ok": True, "cabinet": cabinet.code,
                        "scope": tokens.get('scope'),
                        "expires_at": _iso(tokens.get('expires_at'))})

    return bp


# ─────────────────────────────────────────────────────────────────────────────
# Разбор запроса и формат ответа
# ─────────────────────────────────────────────────────────────────────────────

def _extract_code(value):
    """Код согласия из того, что вставил человек.

    Принимаем и голый код, и целиком скопированный адрес из строки браузера.
    Это не удобство ради удобства: у части приложений в заявке OLX прописан
    голый адрес сервиса, и тогда после согласия браузер уезжает на страницу
    «Bot is alive!», а код остаётся висеть в адресной строке. Требовать в этом
    случае «выкусите подстроку между code= и &» — верный способ получить в поле
    половину адреса.
    """
    text = str(value or '').strip()
    if not text:
        return ''
    if 'code=' not in text:
        return text
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(text)
    query = parse_qs(parsed.query or '')
    if not query and parsed.path:
        # Человек вставил не адрес, а только хвост «?code=...&state=...».
        query = parse_qs(text.lstrip('?'))
    found = (query.get('code') or [''])[0].strip()
    return found or text


def _int_arg(name, default, low, high):
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return default
    try:
        return max(low, min(int(raw), high))
    except ValueError:
        return default


def _date_arg(name):
    raw = (request.args.get(name) or '').strip()
    if not raw:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _journal_item(row):
    """Строка журнала для клиента.

    Кабинет отдаём и кодом, и человеческим названием: код нужен фильтру, а
    название — глазам. Считать название на фронте значило бы держать там вторую
    копию справочника кабинетов.
    """
    cab = cabinets.BY_CODE.get(row.get('cabinet_code'))
    return {
        "id": row.get('id'),
        "cabinet": row.get('cabinet_code'),
        "cabinet_title": cab.title if cab else row.get('cabinet_code'),
        "thread_id": row.get('thread_id'),
        "message_id": row.get('message_id'),
        "message_at": _iso(row.get('message_at')),
        "lead_created_at": _iso(row.get('lead_created_at')),
        "latency_ms": row.get('latency_ms'),
        "phone_raw": row.get('phone_raw'),
        "phone": row.get('phone_normalized'),
        "tag": row.get('tag'),
        "result": row.get('result'),
        "amo_lead_id": row.get('amo_lead_id'),
        "amo_contact_id": row.get('amo_contact_id'),
        "error_text": row.get('error_text'),
        "excerpt": row.get('message_excerpt'),
        "attempts": row.get('attempts'),
        "created_at": _iso(row.get('created_at')),
    }
