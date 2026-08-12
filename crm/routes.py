"""HTTP-эндпоинты раздела «Обращения» (Flask Blueprint).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль, и обратный импорт
был бы циклом (ровно как в wiki/routes.py).

Соглашения те же, что у остальных роутов портала: методы всегда включают
OPTIONS и первым делом отдаётся preflight, авторизация — общий require_api_key,
ошибка — {"error": "..."} с осмысленным кодом (глобального errorhandler в
проекте нет).

Разделение обязанностей внутри раздела:
    queries.py  — SQL
    access.py   — кто что может
    service.py  — сценарии, где база встречается с Telegram
    routes.py   — только разбор запроса и коды ответов
"""

import json
import logging
from functools import wraps
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import access, queries, schema, service, telegram, transport

# Вложение к обращению. Предел Telegram для загрузки ботом — 20 МБ, больше не
# примет ни при каких условиях, поэтому отсекаем на входе с понятным текстом.
ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024


def build_crm_blueprint(*, db, require_api_key, build_cors_preflight_response,
                        resolve_requester):
    bp = Blueprint('crm', __name__, url_prefix='/api/crm')

    def crm_route(rule, methods=('GET',), manage=False):
        """Общий каркас роута: preflight, авторизация, контекст, ошибки.

        manage=True — роут настройки очередей: доступен только глобальному
        админу, проверка здесь, чтобы не повторять её в каждом обработчике.
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
                    if manage and not access.can_manage_queues(ctx):
                        return jsonify({
                            "error": "Настраивать очереди может только администратор",
                            "code": "CRM_FORBIDDEN",
                        }), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('crm: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Обращения»",
                        "detail": str(exc)[:200],
                    }), 500

            return wrapper

        return decorator

    def _payload():
        """Тело запроса: JSON или multipart (когда приложили файл)."""
        if request.files or request.form:
            data = dict(request.form)
            if 'fields' in data:
                try:
                    data['fields'] = json.loads(data['fields'] or '{}')
                except (TypeError, ValueError):
                    data['fields'] = {}
            return data
        return request.get_json(silent=True) or {}

    def _attachment():
        """Файл из запроса в виде, который понимает service. None, если файла нет."""
        item = request.files.get('attachment')
        if item is None or not str(item.filename or '').strip():
            return None, None
        item.stream.seek(0, 2)
        size = item.stream.tell()
        item.stream.seek(0)
        if size == 0:
            return None, None
        if size > ATTACHMENT_MAX_BYTES:
            return None, 'Файл больше 20 МБ — Telegram его не примет'
        return {
            'filename': item.filename,
            'stream': item.stream,
            'mimetype': item.mimetype,
        }, None

    # ── Диагностика и сводка ─────────────────────────────────────────────
    @crm_route('/ping')
    def crm_ping(ctx):
        """Живость раздела + периметр текущего пользователя.

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
                payload['counters'] = queries.counters(cursor, ctx)
        return jsonify(payload)

    # ── Очереди и тематики ───────────────────────────────────────────────
    @crm_route('/queues')
    def crm_queues(ctx):
        manage = access.can_manage_queues(ctx)
        include_inactive = manage and request.args.get('all') in ('1', 'true', 'yes')
        with db._get_cursor() as cursor:
            items = queries.list_queues(cursor, include_inactive=include_inactive,
                                        expose_chat_id=manage)
        return jsonify({"items": items})

    @crm_route('/queues', methods=('POST',), manage=True)
    def crm_queue_create(ctx):
        data = _payload()
        title = str(data.get('title') or '').strip()
        if not title:
            return jsonify({"error": "Укажите название очереди"}), 400
        chat_id = data.get('chat_id')
        with db._get_cursor() as cursor:
            if chat_id:
                # Название группы берём из реестра чатов бота, а не из запроса:
                # так в карточке очереди всегда стоит то же имя, что в Telegram.
                known = {c['chat_id']: c for c in queries.bot_chats(cursor)}
                chat = known.get(int(chat_id))
                if not chat:
                    return jsonify({"error": "Бот не состоит в этой группе"}), 400
                if chat.get('used_by_queue'):
                    return jsonify({
                        "error": "Группа уже занята очередью «%s»" % chat['used_by_queue'],
                    }), 409
                data['chat_title'] = chat['title']
            item = queries.create_queue(
                cursor, title=title,
                description=(data.get('description') or None),
                chat_id=int(chat_id) if chat_id else None,
                chat_title=data.get('chat_title'),
                department_id=_int_or_none(data.get('department_id')),
                sla_minutes=_int_or_none(data.get('sla_minutes')),
                sort_order=_int_or_none(data.get('sort_order')) or 100,
                created_by=ctx['user_id'],
            )
        return jsonify({"item": item}), 201

    @crm_route('/queues/<int:queue_id>', methods=('PATCH', 'DELETE'), manage=True)
    def crm_queue_modify(queue_id, ctx):
        with db._get_cursor() as cursor:
            if request.method == 'DELETE':
                removed = queries.delete_queue(cursor, queue_id)
                if not removed:
                    return jsonify({
                        "error": "По очереди уже есть обращения — её можно только выключить",
                        "code": "CRM_QUEUE_IN_USE",
                    }), 409
                return jsonify({"status": "deleted"})

            data = _payload()
            changes = {}
            for field in ('title', 'description', 'chat_title'):
                if field in data:
                    changes[field] = (str(data[field]).strip() or None)
            for field in ('sla_minutes', 'sort_order', 'department_id'):
                if field in data:
                    changes[field] = _int_or_none(data[field])
            if 'is_active' in data:
                changes['is_active'] = _bool(data['is_active'])
            if 'chat_id' in data:
                chat_id = _int_or_none(data['chat_id'])
                if chat_id is not None:
                    known = {c['chat_id']: c for c in queries.bot_chats(cursor)}
                    chat = known.get(chat_id)
                    if not chat:
                        return jsonify({"error": "Бот не состоит в этой группе"}), 400
                    existing = queries.get_queue(cursor, queue_id) or {}
                    if chat.get('used_by_queue') and existing.get('chat_id') != chat_id:
                        return jsonify({
                            "error": "Группа уже занята очередью «%s»" % chat['used_by_queue'],
                        }), 409
                    changes['chat_title'] = chat['title']
                changes['chat_id'] = chat_id
            item = queries.update_queue(cursor, queue_id, changes)
        return jsonify({"item": item})

    @crm_route('/queues/<int:queue_id>/topics', methods=('POST',), manage=True)
    def crm_topic_create(queue_id, ctx):
        data = _payload()
        title = str(data.get('title') or '').strip()
        if not title:
            return jsonify({"error": "Укажите название тематики"}), 400
        with db._get_cursor() as cursor:
            topic_id = queries.create_topic(
                cursor, queue_id=queue_id, title=title,
                sort_order=_int_or_none(data.get('sort_order')) or 100,
            )
        return jsonify({"id": topic_id}), 201

    @crm_route('/topics/<int:topic_id>', methods=('PATCH', 'DELETE'), manage=True)
    def crm_topic_modify(topic_id, ctx):
        with db._get_cursor() as cursor:
            if request.method == 'DELETE':
                removed = queries.delete_topic(cursor, topic_id)
                # Тематику с историей не стираем, а выключаем: на неё ссылаются
                # обращения, и отчёт «по тематикам» иначе потерял бы прошлое.
                return jsonify({"status": "deleted" if removed else "deactivated"})
            data = _payload()
            changes = {}
            if 'title' in data:
                changes['title'] = str(data['title']).strip()
            if 'sort_order' in data:
                changes['sort_order'] = _int_or_none(data['sort_order'])
            if 'is_active' in data:
                changes['is_active'] = _bool(data['is_active'])
            queries.update_topic(cursor, topic_id, changes)
        return jsonify({"status": "ok"})

    @crm_route('/chats', manage=True)
    def crm_chats(ctx):
        """Группы, куда добавлен бот, — для привязки очереди."""
        with db._get_cursor() as cursor:
            return jsonify({"items": queries.bot_chats(cursor)})

    # ── Обращения ────────────────────────────────────────────────────────
    @crm_route('/tickets')
    def crm_tickets(ctx):
        args = request.args
        statuses = [s for s in (args.get('status') or '').split(',') if s]
        with db._get_cursor() as cursor:
            items, total = queries.list_tickets(
                cursor, ctx,
                status=statuses or None,
                queue_id=_int_or_none(args.get('queue_id')),
                mine=args.get('mine') in ('1', 'true', 'yes'),
                unread_only=args.get('unread') in ('1', 'true', 'yes'),
                search=(args.get('q') or '').strip() or None,
                limit=_int_or_none(args.get('limit')) or 50,
                offset=_int_or_none(args.get('offset')) or 0,
            )
            counters = queries.counters(cursor, ctx)
        return jsonify({"items": items, "total": total, "counters": counters})

    @crm_route('/tickets', methods=('POST',))
    def crm_ticket_create(ctx):
        if not access.can_create_ticket(ctx):
            return jsonify({"error": "Недостаточно прав"}), 403
        data = _payload()
        subject = str(data.get('subject') or '').strip()
        body = str(data.get('body') or '').strip()
        queue_id = _int_or_none(data.get('queue_id'))
        if not subject:
            return jsonify({"error": "Укажите тему обращения"}), 400
        if not body:
            return jsonify({"error": "Опишите вопрос"}), 400
        if not queue_id:
            return jsonify({"error": "Выберите, куда направить обращение"}), 400

        attachment, attach_error = _attachment()
        if attach_error:
            return jsonify({"error": attach_error}), 400

        priority = str(data.get('priority') or 'normal').lower()
        if priority not in schema.TICKET_PRIORITIES:
            priority = 'normal'
        source = str(data.get('source') or 'manual').lower()
        if source not in schema.TICKET_SOURCES:
            source = 'manual'

        with db._get_cursor() as cursor:
            queue = queries.get_queue(cursor, queue_id)
            if not queue or not queue['is_active']:
                return jsonify({"error": "Очередь недоступна"}), 400
            if not queue['is_ready']:
                return jsonify({
                    "error": "У очереди «%s» не привязана Telegram-группа" % queue['title'],
                }), 400
            topic_id = _int_or_none(data.get('topic_id'))
            ticket_id = queries.create_ticket(
                cursor,
                queue_id=queue_id, topic_id=topic_id,
                subject=subject[:300], body=body,
                priority=priority, source=source,
                client_name=(str(data.get('client_name') or '').strip() or None),
                client_phone=(str(data.get('client_phone') or '').strip() or None),
                created_by=ctx['user_id'], created_by_name=ctx['name'],
                department_id=ctx.get('department_id'),
                due_at=service.compute_due_at(queue.get('sla_minutes')),
            )
            queries.add_event(cursor, ticket_id=ticket_id, kind='created',
                              actor_user_id=ctx['user_id'], actor_name=ctx['name'],
                              payload={'queue': queue['title']})

        # Отправка — уже вне транзакции: сеть не должна держать соединение пула.
        # Обращение существует в любом случае, отказ Telegram лишь помечает
        # доставку как неудачную и оставляет кнопку «Отправить ещё раз».
        sent, send_error = service.deliver_ticket(db, ticket_id, attachment=attachment)

        with db._get_cursor() as cursor:
            ticket = queries.get_ticket(cursor, ticket_id, ctx['user_id'])
        return jsonify({
            "item": ticket,
            "delivered": sent,
            "delivery_error": None if sent else send_error,
        }), 201

    @crm_route('/tickets/<int:ticket_id>')
    def crm_ticket_show(ticket_id, ctx):
        """Карточка обращения: сама запись, переписка и история.

        Открытие карточки автором ГАСИТ его «непрочитано»: уведомление «вам
        ответили» снимается прочтением ответа, а не просмотром колокола.
        """
        with db._get_cursor() as cursor:
            ticket = queries.get_ticket(cursor, ticket_id, ctx['user_id'])
            if not ticket:
                return jsonify({"error": "Обращение не найдено"}), 404
            if not access.can_view_ticket(ctx, ticket):
                return jsonify({"error": "Обращение вне вашего доступа"}), 403
            messages = queries.list_messages(cursor, ticket_id)
            events = queries.list_events(cursor, ticket_id)
            if queries.mark_seen_by_author(cursor, ticket_id, ctx['user_id']):
                ticket['unread'] = False
                ticket['unread_kind'] = None
        return jsonify({
            "item": ticket,
            "messages": messages,
            "events": events,
            "permissions": {
                "can_reply": access.can_reply(ctx, ticket),
                "can_change_status": access.can_change_status(ctx, ticket),
                "can_delete": access.can_delete_ticket(ctx, ticket),
            },
        })

    @crm_route('/tickets/<int:ticket_id>/messages', methods=('POST',))
    def crm_ticket_reply(ticket_id, ctx):
        data = _payload()
        body = str(data.get('body') or '').strip()
        attachment, attach_error = _attachment()
        if attach_error:
            return jsonify({"error": attach_error}), 400
        if not body and attachment is None:
            return jsonify({"error": "Пустое сообщение"}), 400

        with db._get_cursor() as cursor:
            ticket = queries.get_ticket(cursor, ticket_id, ctx['user_id'])
            if not ticket:
                return jsonify({"error": "Обращение не найдено"}), 404
            if not access.can_reply(ctx, ticket):
                return jsonify({"error": "Писать в это обращение нельзя"}), 403

        ok, error = service.post_operator_reply(
            db, ticket_id, body or '📎 Вложение',
            author_user_id=ctx['user_id'], author_name=ctx['name'],
            attachment=attachment,
        )
        if not ok:
            return jsonify({"error": "Сообщение не ушло в Telegram: %s" % error}), 502

        with db._get_cursor() as cursor:
            return jsonify({"messages": queries.list_messages(cursor, ticket_id)})

    @crm_route('/tickets/<int:ticket_id>/status', methods=('POST',))
    def crm_ticket_status(ticket_id, ctx):
        data = _payload()
        status = str(data.get('status') or '').strip().lower()
        if status not in schema.TICKET_STATUSES:
            return jsonify({"error": "Неизвестный статус"}), 400

        with db._get_cursor() as cursor:
            ticket = queries.get_ticket(cursor, ticket_id, ctx['user_id'])
            if not ticket:
                return jsonify({"error": "Обращение не найдено"}), 404
            if not access.can_change_status(ctx, ticket):
                return jsonify({"error": "Менять статус этого обращения нельзя"}), 403

        ok, error = service.change_status_from_system(
            db, ticket_id, status,
            actor_user_id=ctx['user_id'], actor_name=ctx['name'],
        )
        if not ok:
            return jsonify({"error": error}), 400
        with db._get_cursor() as cursor:
            return jsonify({"item": queries.get_ticket(cursor, ticket_id, ctx['user_id'])})

    @crm_route('/tickets/<int:ticket_id>/resend', methods=('POST',))
    def crm_ticket_resend(ticket_id, ctx):
        """Повторная отправка обращения, которое не ушло в Telegram."""
        with db._get_cursor() as cursor:
            ticket = queries.get_ticket(cursor, ticket_id, ctx['user_id'])
            if not ticket:
                return jsonify({"error": "Обращение не найдено"}), 404
            if not access.can_change_status(ctx, ticket):
                return jsonify({"error": "Недостаточно прав"}), 403
            if ticket['delivery_status'] == 'sent':
                return jsonify({"error": "Обращение уже доставлено"}), 409

        ok, error = service.deliver_ticket(db, ticket_id)
        if not ok:
            return jsonify({"error": error}), 502
        with db._get_cursor() as cursor:
            return jsonify({"item": queries.get_ticket(cursor, ticket_id, ctx['user_id'])})

    @crm_route('/tickets/<int:ticket_id>/attachments/<int:message_id>')
    def crm_ticket_attachment(ticket_id, message_id, ctx):
        """Отдаёт вложение из переписки.

        Файл не хранится у нас: Telegram отдаёт его по file_id, и ссылка
        короткоживущая — поэтому прокси, а не сохранённый адрес в базе.
        """
        with db._get_cursor() as cursor:
            ticket = queries.get_ticket(cursor, ticket_id, ctx['user_id'])
            if not ticket:
                return jsonify({"error": "Обращение не найдено"}), 404
            if not access.can_view_ticket(ctx, ticket):
                return jsonify({"error": "Обращение вне вашего доступа"}), 403
            found = queries.find_message_attachment(cursor, ticket_id, message_id)
        if not found:
            return jsonify({"error": "Вложение не найдено"}), 404

        content, error = transport.fetch_file(found['file_id'])
        if content is None:
            return jsonify({"error": "Telegram не отдал файл: %s" % error}), 502
        return send_file(
            BytesIO(content),
            mimetype=found.get('mime') or 'application/octet-stream',
            download_name=found.get('name') or ('attachment-%s' % message_id),
            as_attachment=not str(found.get('mime') or '').startswith('image/'),
        )

    @crm_route('/tickets/<int:ticket_id>', methods=('DELETE',))
    def crm_ticket_delete(ticket_id, ctx):
        with db._get_cursor() as cursor:
            ticket = queries.get_ticket(cursor, ticket_id, ctx['user_id'])
            if not ticket:
                return jsonify({"error": "Обращение не найдено"}), 404
            if not access.can_delete_ticket(ctx, ticket):
                return jsonify({"error": "Удалять обращения может только администратор"}), 403
            cursor.execute('DELETE FROM crm_tickets WHERE id = %s', (ticket_id,))
        return jsonify({"status": "deleted"})

    @crm_route('/meta')
    def crm_meta(ctx):
        """Справочники раздела: статусы и приоритеты одним ответом.

        Подписи живут на сервере, чтобы Telegram и интерфейс называли вещи
        одинаково: «Решено» в чате и «Решено» в карточке — это одно слово.
        """
        return jsonify({
            "statuses": [{"code": code, "label": telegram.STATUS_LABELS[code]}
                         for code in schema.TICKET_STATUSES],
            "priorities": [{"code": code, "label": telegram.PRIORITY_LABELS[code]}
                           for code in schema.TICKET_PRIORITIES],
        })

    return bp


def _int_or_none(value):
    if value in (None, '', 'null'):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')
