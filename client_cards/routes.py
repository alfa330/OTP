# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Карточка клиента» (Flask Blueprint /api/client_cards).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2 сам
подключает этот модуль, обратный импорт был бы циклом (как у «Аренды авто»).

Соглашения портала: методы включают OPTIONS и первым делом отдаётся preflight, авторизация
— общий require_api_key, ошибка — {"error": "...", "code": "..."}.

Замок QR. В карточке — аккаунты, телефоны и переписка по живым водителям и клиентам.
Рядовому сотруднику (оператор, стажёр) раздел открывается после подтверждения сессии QR-кодом
у руководителя — тем же ключом, что вики, «Обращения» и телефон автообзвона Тез
(владелец 06.11.2026: данные водителей — только подтверждённой сессии). Отказ —
403 SENSITIVE_ACCESS_REQUIRED: страница телефона сама показывает QR (src/embed).

Карточка в телефоне (страница client_card.html):
    GET    /phone/state                      что нужно странице: причины, адресаты, незаполненные
    POST   /phone/calls                      звонок начался → его карточка и привязанные аккаунты
    POST   /cards/<id>/event                 answered | ended
    POST   /cards/<id>/lookup                спросить TEZ APP о номере
    POST   /cards/<id>/account               выбрать аккаунт {account_id} или приложить {link}
    DELETE /cards/<id>/links/<link_id>       снять ошибочную привязку
    PATCH  /cards/<id>                       причины, комментарий, продолжение, город
    POST   /cards/<id>/complete              «Сохранить»
    GET    /cards/<id>                       карточка целиком (история, журнал)
    GET    /history                          обращения человека / отдела с поиском

Запросы в другие отделы:
    GET    /requests                         список: оператору — свои, руководителю — все
    POST   /requests                         создать из карточки (multipart: текст и файлы)
    GET    /requests/<id>                    запрос с перепиской
    POST   /requests/<id>/reply              написать в нить (multipart)
    POST   /requests/<id>/status             resolved | cancelled | reopen
    POST   /requests/<id>/retry              отправить ещё раз
    GET    /requests/<id>/files/<message>    вложение переписки

Раздел портала (руководитель):
    GET    ''                                экран: права, отделы, настройки, справочники
    PUT    /settings                         настройки отдела
    POST   /departments                      выдать раздел отделу (глобальный админ)
    POST   /categories, PATCH|DELETE /categories/<id>, POST /categories/reorder
    POST   /destinations, PATCH|DELETE /destinations/<id>
    POST   /alerts/test                      проверочное сообщение по адресам алертов
    GET    /journal                          журнал обращений
    GET    /report, /report/export           отчёт за период и его выгрузка
"""

import logging
from datetime import date, datetime, timedelta
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import access, queries, report as report_xlsx, rules
from .service import CardError

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
# Отчёт дольше года одним запросом не считаем: это уже не «рост обращений», а архив.
REPORT_MAX_DAYS = 366
TITLE_LIMIT = 160
# Сколько причин можно завести одним вставленным списком.
BULK_TITLES_LIMIT = 100


def _iso(value):
    """Наивное время Алматы → ISO со смещением +05:00: браузер в другом поясе не сдвинет его."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=rules.ALMATY).isoformat(timespec='seconds')
    return value.isoformat()


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bad(message, code, status=400):
    return jsonify({"error": message, "code": code}), status


def _day(value):
    try:
        return date.fromisoformat(str(value or ''))
    except ValueError:
        return None


# ─── Что уходит экрану ───────────────────────────────────────────────────────

def public_account(account, admin_url, *, ctx=None, department_id=None):
    if not account:
        return None
    item = {
        'id': account['id'], 'kind': account['kind'],
        'kind_label': rules.KIND_LABELS.get(account['kind'], 'Аккаунт'),
        'tez_id': account['tez_id'], 'full_name': account.get('full_name'),
        'phone': account.get('phone'), 'phone_text': rules.format_phone(account.get('phone')),
        'city': account.get('city'), 'status': account.get('status'),
        'registered_at': _iso(account.get('registered_at')),
        'last_order_at': _iso(account.get('last_order_at')),
        # Профиль пришёл из TEZ APP; иначе известны только вид и ID из ссылки оператора.
        'known': account.get('fetched_at') is not None,
        'url': rules.account_url(account['kind'], account['tez_id'], admin_url),
    }
    link = account.get('link')
    if link:
        item['link'] = {
            'id': link['id'], 'origin': link['origin'], 'created_by_name': link.get('created_by_name'),
            'created_at': _iso(link.get('created_at')),
            'can_unlink': bool(ctx) and access.can_unlink(ctx, link, department_id),
        }
        item['last_used_at'] = _iso(account.get('last_used_at'))
    return item


def public_card(card, now):
    state = rules.card_state(card, now)
    left = None
    if state == rules.STATE_WRAPUP:
        left = max(0, int((rules.wrapup_deadline(card) - now).total_seconds()))
    late = bool(card.get('completed_at') and card.get('ended_at')
                and card['completed_at'] > rules.wrapup_deadline(card))
    return {
        'id': card['id'], 'department_id': card['department_id'],
        'operator_id': card['operator_id'], 'operator_name': card.get('operator_name'),
        'phone': card['phone'], 'phone_text': rules.format_phone(card['phone']),
        'direction': card['direction'], 'direction_label': rules.DIRECTION_LABELS.get(card['direction']),
        'status': card['status'], 'state': state, 'state_label': rules.STATE_LABELS[state],
        'account_id': card.get('account_id'), 'account_kind': card.get('account_kind'),
        # Оператор отметил «Аккаунта нет»: экран показывает это вместо пустого места.
        'no_account': rules.has_no_account(card),
        'city': card.get('city'), 'comment': card.get('comment') or '',
        'parent_id': card.get('parent_id'), 'topics': card.get('topics') or [],
        'wrapup_sec': int(card.get('wrapup_sec') or 0),
        # Сколько секунд постобработки осталось НА МОМЕНТ ОТВЕТА: страница ведёт отсчёт от
        # получения, а не по часам компьютера оператора.
        'wrapup_left_sec': left, 'late': late,
        'created_at': _iso(card.get('created_at')), 'answered_at': _iso(card.get('answered_at')),
        'ended_at': _iso(card.get('ended_at')), 'completed_at': _iso(card.get('completed_at')),
    }


def public_card_request(item):
    return {'id': item['id'], 'destination_title': item.get('destination_title'), 'status': item['status'],
            'status_label': rules.REQUEST_STATUS_LABELS.get(item['status'], item['status']),
            'delivery_status': item.get('delivery_status'), 'created_at': _iso(item.get('created_at')),
            'last_inbound_at': _iso(item.get('last_inbound_at'))}


def public_request(item, accounts=None, admin_url=None):
    account = (accounts or {}).get(item.get('account_id'))
    return {
        'id': item['id'], 'department_id': item['department_id'], 'card_id': item.get('card_id'),
        'destination_id': item['destination_id'], 'destination_title': item.get('destination_title'),
        'body': item.get('body') or '', 'status': item['status'],
        'status_label': rules.REQUEST_STATUS_LABELS.get(item['status'], item['status']),
        'phone': item.get('phone'), 'phone_text': rules.format_phone(item.get('phone')),
        'topic_title': item.get('topic_title'),
        'account': ({'kind': account['kind'], 'kind_label': rules.KIND_LABELS.get(account['kind']),
                     'tez_id': account['tez_id'], 'full_name': account.get('full_name'),
                     'city': account.get('city'),
                     'url': rules.account_url(account['kind'], account['tez_id'], admin_url)}
                    if account else None),
        'created_by': item.get('created_by'), 'created_by_name': item.get('created_by_name'),
        'chat_title': item.get('tg_chat_title'),
        'delivery_status': item.get('delivery_status'), 'delivery_error': item.get('delivery_error'),
        'unread': int(item.get('author_unread_count') or 0),
        'first_reply_at': _iso(item.get('first_reply_at')), 'last_message_at': _iso(item.get('last_message_at')),
        'last_inbound_at': _iso(item.get('last_inbound_at')),
        'resolved_at': _iso(item.get('resolved_at')), 'resolved_by_name': item.get('resolved_by_name'),
        'created_at': _iso(item.get('created_at')),
    }


def public_message(item):
    return {
        'id': item['id'], 'direction': item['direction'], 'body': item.get('body') or '',
        'author_name': item.get('author_name'), 'author_user_id': item.get('author_user_id'),
        'tg_from_id': item.get('tg_from_id'), 'tg_username': item.get('tg_username'),
        'tg_message_id': item.get('tg_message_id'),
        'reply_to_tg_message_id': item.get('reply_to_tg_message_id'),
        'file': ({'name': item.get('file_name'), 'mime': item.get('file_mime'), 'size': item.get('file_size'),
                  'kind': item.get('file_kind')} if item.get('file_ref') else None),
        # Наш файл, который ещё не дошёл до группы: оператор видит, что его надо дослать.
        'pending': bool(item['direction'] == 'out' and item.get('file_ref') and not item.get('tg_message_id')),
        'created_at': _iso(item.get('created_at')),
    }


def public_destination(item, *, manage):
    result = {'id': item['id'], 'title': item['title'], 'is_active': bool(item['is_active']),
              'is_ready': bool(item['is_ready']), 'chat_title': item.get('chat_title')}
    if manage:
        # chat_id — служебный идентификатор чужого чата: только тем, кто настраивает адресатов.
        result['chat_id'] = item.get('chat_id')
        # Кого бот отмечает в группе при каждом запросе — ники без «@».
        from wiki import offices as wiki_offices

        result['mention_usernames'] = wiki_offices.split_telegram_usernames(item.get('mention_usernames'))
    return result


def public_settings(settings):
    return {
        'department_id': settings['department_id'], 'enabled': bool(settings['enabled']),
        'group_ids': settings['group_ids'], 'wrapup_sec': settings['wrapup_sec'],
        'alerts': settings['alerts'], 'alert_head': bool(settings['alert_head']),
        'alert_chat_id': settings.get('alert_chat_id'), 'alert_chat_title': settings.get('alert_chat_title'),
        'updated_by_name': settings.get('updated_by_name'), 'updated_at': _iso(settings.get('updated_at')),
    }


def build_client_cards_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester,
                                 service, qr_locked=None):
    """qr_locked(user_id) — закрыта ли сессия запроса замком QR (bot_schedule2). Без него
    замка нет: так раздел собирают тесты."""
    bp = Blueprint('client_cards', __name__, url_prefix='/api/client_cards')

    def cards_route(rule, methods=('GET',)):
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
                    ctx = service.context(requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if not access.can_open_section(ctx):
                        return _bad('Раздел «Карточка клиента» вам не открыт', 'CLIENT_CARDS_CLOSED', 403)
                    if qr_locked is not None and qr_locked(requester_id):
                        return _bad('Покажите QR-код руководителю — карточка откроется после подтверждения',
                                    'SENSITIVE_ACCESS_REQUIRED', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except CardError as exc:
                    return _bad(exc.message, exc.code, exc.status)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('client_cards: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Карточка клиента»",
                                    "detail": type(exc).__name__}), 500

            return wrapper

        return decorator

    def _json():
        return request.get_json(silent=True) or {}

    def _uploads():
        """Файлы multipart-запроса: [{'name', 'data', 'mime'}]. Пустые пропускаем."""
        result = []
        for item in request.files.getlist('files'):
            if item is None or not str(item.filename or '').strip():
                continue
            data = item.read()
            if not data:
                continue
            result.append({'name': item.filename, 'data': data, 'mime': item.mimetype})
        return result

    def _bundle(ctx, bundle):
        """Карточка с аккаунтами и запросами — один вид ответа у всех действий над карточкой."""
        now = rules.now_almaty()
        card = bundle['card']
        admin_url = service.admin_url()
        payload = {
            'card': public_card(card, now),
            'accounts': [public_account(item, admin_url, ctx=ctx, department_id=card['department_id'])
                         for item in bundle['accounts']],
            'account': public_account(bundle['account'], admin_url, ctx=ctx,
                                      department_id=card['department_id']),
            'requests': [public_card_request(item) for item in bundle['requests']],
            'completion_error': (rules.completion_error(card)
                                 if card['status'] == rules.STATUS_OPEN else None),
        }
        if 'tez' in bundle:
            tez = bundle['tez']
            hint = tez.get('driver_hint')
            payload['tez'] = {
                'source': tez['source'], 'ok': bool(tez['ok']), 'error': tez.get('error'),
                'driver_hint': ({'exists': bool(hint.get('exists')),
                                 'registered_at': _iso(hint.get('registered_at')),
                                 'last_order_at': _iso(hint.get('last_order_at'))} if hint else None),
            }
        return payload

    def _managed_department(ctx, raw):
        """Отдел, которым человек руководит: названный в запросе или единственный."""
        managed = access.managed_department_ids(ctx)
        department_id = _int(raw)
        if department_id is None and len(managed) == 1:
            department_id = managed[0]
        if department_id is None or department_id not in managed:
            raise CardError('Настройки раздела вам не открыты', 'CLIENT_CARDS_FORBIDDEN', 403)
        return department_id

    def _section_department(ctx, raw):
        """Отдел, данные которого человек смотрит: названный или первый из открытых."""
        ids = access.section_department_ids(ctx)
        department_id = _int(raw)
        if department_id is None and ids:
            department_id = access.card_department_id(ctx) if access.card_department_id(ctx) in ids else ids[0]
        if department_id is None or department_id not in ids:
            raise CardError('Этот отдел вам не открыт', 'CLIENT_CARDS_FORBIDDEN', 403)
        return department_id

    # ─── Экран раздела ───────────────────────────────────────────────────────

    @cards_route('')
    def client_cards_screen(ctx):
        """Экран раздела: права, отделы, настройки и справочники выбранного отдела."""
        department_id = _section_department(ctx, request.args.get('department_id'))
        manage = access.can_manage(ctx, department_id)
        with db._get_cursor() as cursor:
            settings = queries.get_settings(cursor, department_id)
            payload = {
                'capabilities': access.capabilities(ctx),
                'departments': queries.departments(cursor, access.section_department_ids(ctx)),
                'department_id': department_id,
                'can_manage': manage,
                'settings': public_settings(settings) if manage else {'enabled': bool(settings['enabled'])},
                'categories': queries.categories(cursor, department_id, include_inactive=manage),
                'destinations': [public_destination(item, manage=manage) for item in
                                 queries.destinations(cursor, department_id, include_inactive=manage)],
                'unread_requests': queries.unread_total(cursor, ctx['user_id']),
                'tez': {'source': service.tez_source(), 'admin_url': service.admin_url()},
                'server_time': _iso(rules.now_almaty()),
            }
            if manage:
                payload['groups'] = queries.department_groups(cursor, department_id)
                payload['chats'] = queries.bot_chats(cursor)
                payload['head'] = queries.department_head(cursor, department_id)
                payload['alerts_log'] = [dict(item, sent_at=_iso(item['sent_at']))
                                         for item in queries.alerts_log(cursor, department_id)]
                payload['limits'] = {'wrapup_max_sec': rules.WRAPUP_MAX_SEC,
                                     'category_depth': rules.CATEGORY_MAX_DEPTH}
            if access.is_global_admin(ctx):
                payload['all_departments'] = queries.all_departments(cursor)
        return jsonify(payload)

    # ─── Карточка в телефоне ─────────────────────────────────────────────────

    @cards_route('/phone/state')
    def client_cards_phone_state(ctx):
        """Что нужно странице карточки при открытии: причины, адресаты, незаполненные карточки."""
        if not access.phone_card_enabled(ctx):
            return jsonify({'enabled': False})
        department_id = access.card_department_id(ctx)
        cards, names = service.pending(ctx)
        now = rules.now_almaty()
        admin_url = service.admin_url()
        with db._get_cursor() as cursor:
            settings = queries.get_settings(cursor, department_id)
            categories = queries.categories(cursor, department_id)
            destinations = queries.destinations(cursor, department_id)
            unread = queries.unread_total(cursor, ctx['user_id'])
        pending = []
        for card in cards:
            item = public_card(card, now)
            account = names.get(card.get('account_id'))
            item['account'] = ({'kind_label': rules.KIND_LABELS.get(account['kind']), 'tez_id': account['tez_id'],
                                'full_name': account.get('full_name')} if account else None)
            pending.append(item)
        return jsonify({
            'enabled': True,
            'wrapup_sec': settings['wrapup_sec'],
            'categories': categories,
            'destinations': [public_destination(item, manage=False) for item in destinations],
            'pending': pending,
            'unread_requests': unread,
            'user': {'id': ctx['user_id'], 'name': ctx['name']},
            'tez': {'source': service.tez_source(), 'admin_url': admin_url},
            'link_example': rules.LINK_EXAMPLE,
            'limits': {'topics': rules.TOPICS_LIMIT, 'comment': rules.COMMENT_LIMIT,
                       'request_body': rules.REQUEST_BODY_LIMIT, 'request_files': rules.REQUEST_FILES_LIMIT,
                       'request_file_bytes': rules.REQUEST_FILE_MAX_BYTES},
        })

    @cards_route('/phone/calls', methods=('POST',))
    def client_cards_phone_call(ctx):
        data = _json()
        bundle = service.open_call(ctx, phone=data.get('phone'), direction=data.get('direction'),
                                   call_key=data.get('call_key'))
        return jsonify(_bundle(ctx, bundle))

    @cards_route('/cards/<int:card_id>/event', methods=('POST',))
    def client_cards_card_event(card_id, ctx):
        return jsonify(_bundle(ctx, service.call_event(ctx, card_id, _json().get('event'))))

    @cards_route('/cards/<int:card_id>/lookup', methods=('POST',))
    def client_cards_card_lookup(card_id, ctx):
        return jsonify(_bundle(ctx, service.lookup(ctx, card_id)))

    @cards_route('/cards/<int:card_id>/account', methods=('POST',))
    def client_cards_card_account(card_id, ctx):
        data = _json()
        if str(data.get('link') or '').strip():
            return jsonify(_bundle(ctx, service.attach_link(ctx, card_id, data.get('link'))))
        # {"none": true} — «Аккаунта нет»: звонивший не зарегистрирован в Тез.
        if data.get('none') is True:
            return jsonify(_bundle(ctx, service.mark_no_account(ctx, card_id)))
        account_id = _int(data.get('account_id'))
        if account_id is None and data.get('account_id') is not None:
            return _bad('Аккаунт указан неверно', 'CARD_BAD_ACCOUNT')
        return jsonify(_bundle(ctx, service.choose_account(ctx, card_id, account_id)))

    @cards_route('/cards/<int:card_id>/links/<int:link_id>', methods=('DELETE',))
    def client_cards_card_unlink(card_id, link_id, ctx):
        return jsonify(_bundle(ctx, service.unlink(ctx, card_id, link_id)))

    @cards_route('/cards/<int:card_id>', methods=('GET', 'PATCH'))
    def client_cards_card(card_id, ctx):
        if request.method == 'PATCH':
            return jsonify(_bundle(ctx, service.save_card(ctx, card_id, _json())))
        # ?bundle=1 — карточка для работы (с аккаунтами номера): так оператор открывает
        # свою незаполненную карточку. Без него — обращение для чтения из истории и журнала.
        if request.args.get('bundle') in ('1', 'true'):
            return jsonify(_bundle(ctx, service.bundle(ctx, card_id)))
        detail = service.card_detail(ctx, card_id)
        now = rules.now_almaty()
        card = detail['card']
        return jsonify({
            'card': public_card(card, now),
            'account': public_account(detail['account'], service.admin_url()),
            'requests': [public_card_request(item) for item in detail['requests']],
            'parent': public_card(detail['parent'], now) if detail['parent'] else None,
            'can_edit': detail['can_edit'],
        })

    @cards_route('/cards/<int:card_id>/complete', methods=('POST',))
    def client_cards_card_complete(card_id, ctx):
        return jsonify(_bundle(ctx, service.complete_card(ctx, card_id)))

    @cards_route('/history')
    def client_cards_history(ctx):
        cards, has_more, names, requests = service.history(
            ctx, card_id=_int(request.args.get('card_id')), account_id=_int(request.args.get('account_id')),
            phone=request.args.get('phone'), search=request.args.get('q'),
            scope='all' if request.args.get('scope') == 'all' else 'person',
            before_id=_int(request.args.get('before')))
        now = rules.now_almaty()
        items = []
        for card in cards:
            item = public_card(card, now)
            account = names.get(card.get('account_id'))
            item['account'] = ({'kind': account['kind'], 'kind_label': rules.KIND_LABELS.get(account['kind']),
                                'tez_id': account['tez_id'], 'full_name': account.get('full_name')}
                               if account else None)
            item['requests'] = [public_card_request(row) for row in requests.get(card['id'], [])]
            items.append(item)
        return jsonify({'items': items, 'has_more': has_more})

    # ─── Запросы в другие отделы ─────────────────────────────────────────────

    @cards_route('/requests', methods=('GET', 'POST'))
    def client_cards_requests(ctx):
        if request.method == 'POST':
            form = request.form if (request.files or request.form) else _json()
            detail, error = service.create_request(
                ctx, card_id=_int(form.get('card_id')), destination_id=_int(form.get('destination_id')),
                body=form.get('body'), uploads=_uploads())
            return jsonify(_request_detail(detail, error)), 201
        before = None
        before_at, before_id = request.args.get('before_at'), _int(request.args.get('before_id'))
        if before_at and before_id:
            try:
                before = (datetime.fromisoformat(before_at).replace(tzinfo=None), before_id)
            except ValueError:
                before = None
        rows, has_more, names = service.list_requests(
            ctx, department_id=_int(request.args.get('department_id')),
            mine=request.args.get('mine') in ('1', 'true'), status=request.args.get('status') or None,
            search=request.args.get('q'), before=before)
        admin_url = service.admin_url()
        with db._get_cursor() as cursor:
            unread = queries.unread_total(cursor, ctx['user_id'])
        return jsonify({'items': [public_request(row, names, admin_url) for row in rows],
                        'has_more': has_more, 'unread_requests': unread})

    def _request_detail(detail, delivery_error=None):
        admin_url = service.admin_url()
        account = detail.get('account')
        names = {account['id']: account} if account else {}
        return {
            'request': public_request(detail['request'], names, admin_url),
            'messages': [public_message(item) for item in detail['messages']],
            'can_act': bool(detail['can_act']),
            'delivery_error': delivery_error,
        }

    @cards_route('/requests/<int:request_id>')
    def client_cards_request(request_id, ctx):
        return jsonify(_request_detail(service.request_detail(ctx, request_id)))

    @cards_route('/requests/<int:request_id>/reply', methods=('POST',))
    def client_cards_request_reply(request_id, ctx):
        form = request.form if (request.files or request.form) else _json()
        detail, error = service.reply(ctx, request_id, body=form.get('body'), uploads=_uploads(),
                                      reply_to=_int(form.get('reply_to')))
        return jsonify(_request_detail(detail, error))

    @cards_route('/requests/<int:request_id>/status', methods=('POST',))
    def client_cards_request_status(request_id, ctx):
        return jsonify(_request_detail(service.set_request_status(ctx, request_id, _json().get('status'))))

    @cards_route('/requests/<int:request_id>/retry', methods=('POST',))
    def client_cards_request_retry(request_id, ctx):
        detail, error = service.retry_delivery(ctx, request_id)
        return jsonify(_request_detail(detail, error))

    @cards_route('/requests/<int:request_id>/files/<int:message_id>')
    def client_cards_request_file(request_id, message_id, ctx):
        """Вложение переписки. Наш файл — JSON со ссылкой на хранилище (видео с перемоткой);
        файл из Telegram — самим содержимым."""
        found = service.request_file(ctx, request_id, message_id)
        if 'url' in found:
            return jsonify({'url': found['url']})
        mime = found.get('mime') or 'application/octet-stream'
        return send_file(BytesIO(found['data']), mimetype=mime,
                         download_name=found.get('name') or ('file-%s' % message_id),
                         as_attachment=not (mime.startswith('image/') or mime.startswith('video/')))

    # ─── Настройки (руководитель) ────────────────────────────────────────────

    @cards_route('/settings', methods=('PUT',))
    def client_cards_settings(ctx):
        data = _json()
        department_id = _managed_department(ctx, data.get('department_id'))
        fields = {}
        if 'enabled' in data:
            fields['enabled'] = bool(data.get('enabled'))
        if 'wrapup_sec' in data:
            fields['wrapup_sec'] = rules.normalize_wrapup(data.get('wrapup_sec'))
        if 'alerts' in data:
            fields['alerts'] = rules.normalize_alerts(data.get('alerts'))
        if 'alert_head' in data:
            fields['alert_head'] = bool(data.get('alert_head'))
        now = rules.now_almaty()
        with db._get_cursor() as cursor:
            if 'group_ids' in data:
                allowed = {group['id'] for group in queries.department_groups(cursor, department_id)}
                fields['group_ids'] = sorted({_int(x) for x in (data.get('group_ids') or [])} & allowed)
            if 'alert_chat_id' in data:
                chat_id = _int(data.get('alert_chat_id'))
                title = queries.chat_title(cursor, chat_id) if chat_id is not None else None
                if chat_id is not None and title is None:
                    return _bad('Бот не состоит в этой группе — добавьте его и выберите группу заново',
                                'CLIENT_CARDS_BAD_CHAT')
                fields['alert_chat_id'], fields['alert_chat_title'] = chat_id, title
            settings = queries.save_settings(cursor, department_id, fields, by=ctx['user_id'],
                                             by_name=ctx['name'], now=now)
        return jsonify({'settings': public_settings(settings)})

    @cards_route('/departments', methods=('POST',))
    def client_cards_provide(ctx):
        """Выдать раздел отделу — только администратору компании: это решение не отдела."""
        if not access.is_global_admin(ctx):
            return _bad('Выдать раздел отделу может администратор компании', 'CLIENT_CARDS_FORBIDDEN', 403)
        department_id = _int(_json().get('department_id'))
        with db._get_cursor() as cursor:
            known = {item['id'] for item in queries.all_departments(cursor)}
            if department_id not in known:
                return _bad('Отдел не найден', 'CLIENT_CARDS_NOT_FOUND', 404)
            queries.provide_department(cursor, department_id)
        return jsonify({'department_id': department_id}), 201

    # ─── Причины обращения ───────────────────────────────────────────────────

    def _category_for(cursor, ctx, category_id):
        category = queries.get_category(cursor, category_id)
        if not category or not access.can_manage(ctx, category['department_id']):
            raise CardError('Причина не найдена', 'CLIENT_CARDS_NOT_FOUND', 404)
        return category

    def _categories_payload(cursor, department_id):
        return {'categories': queries.categories(cursor, department_id, include_inactive=True)}

    @cards_route('/categories', methods=('POST',))
    def client_cards_category_create(ctx):
        """Новая причина; titles — сразу несколько (вставленный список, по строке на причину)."""
        data = _json()
        department_id = _managed_department(ctx, data.get('department_id'))
        raw = data.get('titles') if isinstance(data.get('titles'), list) else [data.get('title')]
        titles = [title for title in (rules.clean_text(item, TITLE_LIMIT) for item in raw) if title]
        if not titles:
            return _bad('Напишите название причины', 'CLIENT_CARDS_EMPTY_TITLE')
        if len(titles) > BULK_TITLES_LIMIT:
            return _bad('За один раз — не больше %d причин' % BULK_TITLES_LIMIT, 'CLIENT_CARDS_TOO_MANY')
        parent_id = _int(data.get('parent_id'))
        # Чья причина — водителя, клиента или третьего лица. Нужна основной причине;
        # подтема берёт ветку у причины над собой.
        try:
            audience = rules.normalize_audience(data.get('audience'))
        except ValueError as error:
            return _bad(str(error), 'CLIENT_CARDS_BAD_AUDIENCE')
        now = rules.now_almaty()
        with db._get_cursor() as cursor:
            if parent_id is not None:
                # Подтема встаёт под причину любого уровня — до потолка глубины дерева.
                branch = rules.category_path(queries.category_index(cursor, department_id), parent_id)
                if not branch:
                    return _bad('Причина для подтемы не найдена', 'CLIENT_CARDS_NOT_FOUND', 404)
                if len(branch) >= rules.CATEGORY_MAX_DEPTH:
                    return _bad('Глубже %d уровней причины не вкладываются' % rules.CATEGORY_MAX_DEPTH,
                                'CLIENT_CARDS_TOO_DEEP')
            for title in titles:
                queries.create_category(cursor, department_id, title=title, parent_id=parent_id,
                                        audience=audience, by=ctx['user_id'], now=now)
            return jsonify(_categories_payload(cursor, department_id)), 201

    @cards_route('/categories/<int:category_id>', methods=('PATCH', 'DELETE'))
    def client_cards_category(category_id, ctx):
        now = rules.now_almaty()
        with db._get_cursor() as cursor:
            category = _category_for(cursor, ctx, category_id)
            if request.method == 'DELETE':
                # Причину, по которой уже есть обращения, удалить нельзя: отчёт за прошлые
                # месяцы потерял бы строки. Её скрывают — оператору она больше не предлагается.
                if queries.category_in_use(cursor, category_id):
                    return _bad('По этой причине уже есть обращения — её можно только скрыть',
                                'CLIENT_CARDS_CATEGORY_IN_USE', 409)
                queries.delete_category(cursor, category_id)
            else:
                data = _json()
                fields = {}
                if 'title' in data:
                    title = rules.clean_text(data.get('title'), TITLE_LIMIT)
                    if not title:
                        return _bad('Напишите название причины', 'CLIENT_CARDS_EMPTY_TITLE')
                    fields['title'] = title
                if 'is_active' in data:
                    fields['is_active'] = bool(data.get('is_active'))
                queries.update_category(cursor, category_id, fields, now=now)
            return jsonify(_categories_payload(cursor, category['department_id']))

    @cards_route('/categories/reorder', methods=('POST',))
    def client_cards_category_reorder(ctx):
        data = _json()
        department_id = _managed_department(ctx, data.get('department_id'))
        ids = [value for value in (_int(x) for x in (data.get('ids') or [])) if value is not None]
        with db._get_cursor() as cursor:
            queries.reorder_categories(cursor, department_id, _int(data.get('parent_id')), ids,
                                       now=rules.now_almaty())
            return jsonify(_categories_payload(cursor, department_id))

    # ─── Адресаты запросов ───────────────────────────────────────────────────

    def _destination_fields(cursor, data, *, creating):
        fields = {}
        if creating or 'title' in data:
            title = rules.clean_text(data.get('title'), TITLE_LIMIT)
            if not title:
                raise CardError('Напишите название отдела', 'CLIENT_CARDS_EMPTY_TITLE')
            fields['title'] = title
        if 'chat_id' in data:
            chat_id = _int(data.get('chat_id'))
            title = queries.chat_title(cursor, chat_id) if chat_id is not None else None
            if chat_id is not None and title is None:
                raise CardError('Бот не состоит в этой группе — добавьте его и выберите группу заново',
                                'CLIENT_CARDS_BAD_CHAT')
            fields['chat_id'], fields['chat_title'] = chat_id, title
        if 'mention_usernames' in data:
            # Разбор и проверка — те же, что у ников офисов вики и очередей «Обращений»:
            # «@a, t.me/b» и список равноправны, а опечатку в нике лучше отбить сразу, чем
            # узнать о ней по молчанию группы.
            from wiki import offices as wiki_offices

            names, problem = wiki_offices.clean_telegram_usernames(data.get('mention_usernames'))
            if problem:
                raise CardError(problem, 'CLIENT_CARDS_BAD_USERNAME')
            fields['mention_usernames'] = wiki_offices.join_telegram_usernames(names)
        if 'is_active' in data:
            fields['is_active'] = bool(data.get('is_active'))
        return fields

    def _destinations_payload(cursor, department_id):
        return {'destinations': [public_destination(item, manage=True) for item in
                                 queries.destinations(cursor, department_id, include_inactive=True)]}

    @cards_route('/destinations', methods=('POST',))
    def client_cards_destination_create(ctx):
        data = _json()
        department_id = _managed_department(ctx, data.get('department_id'))
        with db._get_cursor() as cursor:
            fields = _destination_fields(cursor, data, creating=True)
            queries.create_destination(cursor, department_id, fields, by=ctx['user_id'], now=rules.now_almaty())
            return jsonify(_destinations_payload(cursor, department_id)), 201

    @cards_route('/destinations/<int:destination_id>', methods=('PATCH', 'DELETE'))
    def client_cards_destination(destination_id, ctx):
        with db._get_cursor() as cursor:
            destination = queries.get_destination(cursor, destination_id)
            if not destination or not access.can_manage(ctx, destination['department_id']):
                return _bad('Адресат не найден', 'CLIENT_CARDS_NOT_FOUND', 404)
            if request.method == 'DELETE':
                # С адресатом, которому уже писали, остаётся переписка — его скрывают.
                if queries.destination_in_use(cursor, destination_id):
                    return _bad('В этот отдел уже уходили запросы — его можно только скрыть',
                                'CLIENT_CARDS_DESTINATION_IN_USE', 409)
                queries.delete_destination(cursor, destination_id)
            else:
                fields = _destination_fields(cursor, _json(), creating=False)
                queries.update_destination(cursor, destination_id, fields, now=rules.now_almaty())
            return jsonify(_destinations_payload(cursor, destination['department_id']))

    @cards_route('/alerts/test', methods=('POST',))
    def client_cards_alert_test(ctx):
        department_id = _managed_department(ctx, _json().get('department_id'))
        return jsonify(service.send_test_alert(ctx, department_id))

    # ─── Журнал и отчёт ──────────────────────────────────────────────────────

    def _period():
        """Период из ?from=&to= (дни включительно). По умолчанию — текущий месяц по сегодня."""
        today = rules.now_almaty().date()
        date_from = _day(request.args.get('from')) or today.replace(day=1)
        date_to = _day(request.args.get('to')) or today
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        if (date_to - date_from).days > REPORT_MAX_DAYS:
            raise CardError('Период — не больше года', 'CLIENT_CARDS_PERIOD_TOO_LONG')
        return date_from, date_to

    def _bounds(date_from, date_to):
        start = datetime.combine(date_from, datetime.min.time())
        return start, datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)

    @cards_route('/journal')
    def client_cards_journal(ctx):
        """Журнал обращений: руководителю — отдела, остальным — свои."""
        department_id = _section_department(ctx, request.args.get('department_id'))
        date_from, date_to = _period()
        start, end = _bounds(date_from, date_to)
        own_only = not access.sees_all(ctx, department_id)
        operator_id = ctx['user_id'] if own_only else _int(request.args.get('operator_id'))
        state = request.args.get('state') if request.args.get('state') in ('done', 'pending') else None
        now = rules.now_almaty()
        with db._get_cursor() as cursor:
            cards, has_more = queries.journal(
                cursor, department_id, operator_id=operator_id, date_from=start, date_to=end,
                category_id=_int(request.args.get('category_id')), state=state,
                search=request.args.get('q'), before_id=_int(request.args.get('before')))
            names = queries.account_names(cursor, [card['account_id'] for card in cards])
            requests = queries.card_requests(cursor, [card['id'] for card in cards])
        admin_url = service.admin_url()
        items = []
        for card in cards:
            item = public_card(card, now)
            account = names.get(card.get('account_id'))
            item['account'] = ({'kind': account['kind'], 'kind_label': rules.KIND_LABELS.get(account['kind']),
                                'tez_id': account['tez_id'], 'full_name': account.get('full_name'),
                                'url': rules.account_url(account['kind'], account['tez_id'], admin_url)}
                               if account else None)
            item['requests'] = [public_card_request(row) for row in requests.get(card['id'], [])]
            items.append(item)
        return jsonify({'items': items, 'has_more': has_more, 'own_only': own_only,
                        'from': date_from.isoformat(), 'to': date_to.isoformat()})

    def _report(ctx):
        department_id = _managed_department(ctx, request.args.get('department_id'))
        date_from, date_to = _period()
        start, end = _bounds(date_from, date_to)
        with db._get_cursor() as cursor:
            data = queries.report(cursor, department_id, date_from=start, date_to=end)
            names = queries.departments(cursor, [department_id])
        return department_id, date_from, date_to, data, (names[0]['name'] if names else '')

    @cards_route('/report')
    def client_cards_report(ctx):
        """Отчёт по причинам, подтемам, городам, дням и операторам (п. 11) — руководителю."""
        _department_id, date_from, date_to, data, _name = _report(ctx)
        data['days'] = [dict(day, date=day['date'].isoformat()) for day in data['days']]
        return jsonify(dict(data, **{'from': date_from.isoformat(), 'to': date_to.isoformat()}))

    @cards_route('/report/export')
    def client_cards_report_export(ctx):
        _department_id, date_from, date_to, data, name = _report(ctx)
        stream = report_xlsx.build_workbook(
            data, department_name=name, date_from=date_from, date_to=date_to,
            generated_by=ctx['name'], generated_at=rules.now_almaty())
        logging.info('client_cards: выгрузка отчёта %s..%s — %s', date_from, date_to, ctx.get('name'))
        return send_file(stream, mimetype=XLSX_MIME, as_attachment=True,
                         download_name=report_xlsx.report_filename(name, date_from, date_to))

    return bp
