"""Раздел «chatapp»: ручки /api/chatapp — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_chatapp_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, jsonify, request
from database import db
from datetime import datetime
from datetime import timedelta
from wazzup.names import suggest_user as _wazzup_suggest_user
from zoneinfo import ZoneInfo
import logging
import os
import time


CHATAPP_DEPARTMENT_CODE = (os.getenv('CHATAPP_DEPARTMENT_CODE') or 'tez').strip().lower()

_CHATAPP_DEPARTMENT_CACHE = {'ts': 0.0, 'id': None}

_CHATAPP_DEPARTMENT_CACHE_TTL = 600  # отделы меняются раз в никогда


def build_chatapp_blueprint(
    *,
    ALLOWED_ORIGINS,
    CHATAPP_EPISODE_GAP_HOURS,
    _build_cors_preflight_response,
    _get_authenticated_requester,
    _headed_department_id,
    _is_allowed_origin,
    _is_global_admin_requester,
    _is_supervisor_role,
    _normalize_origin,
    _normalize_user_role,
    require_api_key,
    sync_chatapp_data,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('chatapp', __name__, url_prefix='/api')

    def _chatapp_department_id():
        """id отдела, чья переписка живёт в ChatApp (по коду, с кэшем)."""
        now = time.time()
        if (_CHATAPP_DEPARTMENT_CACHE['id'] is not None
                and now - _CHATAPP_DEPARTMENT_CACHE['ts'] < _CHATAPP_DEPARTMENT_CACHE_TTL):
            return _CHATAPP_DEPARTMENT_CACHE['id']
        found = None
        for dept in (db.get_departments() or []):
            if str(dept.get('code') or '').strip().lower() == CHATAPP_DEPARTMENT_CODE:
                found = int(dept['id'])
                break
        _CHATAPP_DEPARTMENT_CACHE.update(ts=now, id=found)
        return found

    def _chatapp_guard():
        """(requester_id, err) для раздела «Чаты ChatApp».

    Доступ: глобальные админы, глава отдела ТЭЗ и СВ отдела ТЭЗ. СВ и главу
    режем по отделу намеренно — граница отдела в проекте держится строго, и
    главе/СВ чужого отдела переписка ТЭЗ не нужна. _ai_qa_guard здесь не
    годится: он про ОП/СЗоВ."""
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return None, (jsonify({"error": message}), status_code)
        role = _normalize_user_role(requester[3])
        if _is_global_admin_requester(role, requester_id):
            return requester_id, None
        department_id = _chatapp_department_id()
        if department_id is not None:
            if _headed_department_id(requester_id) == department_id:
                return requester_id, None
            if _is_supervisor_role(role) and db.get_user_department_id(requester_id) == department_id:
                return requester_id, None
        return requester_id, (jsonify({"error": "forbidden"}), 403)

    @bp.route('/chatapp/overview', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chatapp_overview():
        """Шапка раздела: лицензии с объёмами, счётчики и когда синкали в последний раз."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, err = _chatapp_guard()
        if err:
            return err
        try:
            with db._get_cursor() as cursor:
                cursor.execute("""
                SELECT c.license_id, c.messenger_type, COUNT(*), MAX(c.last_time)
                  FROM chatapp_chats c
                 GROUP BY c.license_id, c.messenger_type
                 ORDER BY COUNT(*) DESC
            """)
                licenses = [{'licenseId': r[0], 'messengerType': r[1], 'chats': r[2],
                             'lastActivity': r[3].isoformat() if r[3] else None}
                            for r in cursor.fetchall()]
                cursor.execute("""
                SELECT (SELECT COUNT(*) FROM chatapp_chats),
                       (SELECT COUNT(*) FROM chatapp_messages),
                       (SELECT COUNT(*) FROM chatapp_episodes),
                       (SELECT COUNT(*) FROM chatapp_episodes WHERE operator_user_id IS NOT NULL),
                       (SELECT COUNT(*) FROM chatapp_operator_map WHERE user_id IS NULL AND NOT is_bot),
                       (SELECT MAX(synced_at) FROM chatapp_chats),
                       (SELECT MIN(dt) FROM chatapp_messages)
            """)
                row = cursor.fetchone()
            return jsonify({
                "status": "success",
                "licenses": licenses,
                "chats": row[0], "messages": row[1],
                "episodes": row[2], "episodesAttributed": row[3],
                "authorsUnmapped": row[4],
                "lastSyncAt": row[5].isoformat() if row[5] else None,
                "oldestMessageAt": row[6].isoformat() if row[6] else None,
                "configured": bool(os.getenv('CHATAPP_EMAIL') and os.getenv('CHATAPP_APP_ID')),
            }), 200
        except Exception as error:
            logging.exception("chatapp overview failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/chatapp/chats', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chatapp_chats():
        """Список чатов «как в мессенджере»: превью последнего сообщения + счётчики."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, err = _chatapp_guard()
        if err:
            return err
        license_id = (request.args.get('license_id') or '').strip()
        q = (request.args.get('q') or '').strip()
        try:
            limit = min(max(int(request.args.get('limit', 30)), 1), 100)
        except (TypeError, ValueError):
            limit = 30
        try:
            offset = max(int(request.args.get('offset', 0)), 0)
        except (TypeError, ValueError):
            offset = 0
        where, params = ["TRUE"], []
        if license_id:
            where.append("c.license_id = %s")
            params.append(int(license_id))
        if q:
            where.append("(c.name ILIKE %s OR c.phone ILIKE %s OR c.chat_id ILIKE %s)")
            like = f"%{q}%"
            params.extend([like, like, like])
        condition = ' AND '.join(where)
        try:
            with db._get_cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM chatapp_chats c WHERE {condition}", params)
                total = cursor.fetchone()[0]
                # Страницу отбираем сначала, и только для неё считаем превью/счётчики:
                # LATERAL по всей таблице разогнался бы на тысячах чатов.
                cursor.execute(f"""
                WITH page AS (
                    SELECT c.license_id, c.messenger_type, c.chat_id, c.chat_type,
                           c.name, c.phone, c.status, c.last_time,
                           c.responsible_employee_id
                      FROM chatapp_chats c
                     WHERE {condition}
                     ORDER BY c.last_time DESC NULLS LAST
                     LIMIT %s OFFSET %s
                )
                SELECT p.license_id, p.messenger_type, p.chat_id, p.chat_type,
                       p.name, p.phone, p.status, p.last_time,
                       p.responsible_employee_id,
                       COALESCE(u.name, map.employee_name),
                       COALESCE(stat.messages, 0), COALESCE(stat.inbound, 0),
                       stat.last_text, stat.last_side, stat.last_type
                  FROM page p
                  LEFT JOIN chatapp_operator_map map
                         ON map.employee_id = p.responsible_employee_id
                  LEFT JOIN users u ON u.id = map.user_id
                  LEFT JOIN LATERAL (
                        SELECT COUNT(*) AS messages,
                               COUNT(*) FILTER (WHERE m.side = 'in') AS inbound,
                               (ARRAY_AGG(m.text ORDER BY m.dt DESC))[1] AS last_text,
                               (ARRAY_AGG(m.side ORDER BY m.dt DESC))[1] AS last_side,
                               (ARRAY_AGG(m.type ORDER BY m.dt DESC))[1] AS last_type
                          FROM chatapp_messages m
                         WHERE m.license_id = p.license_id
                           AND m.messenger_type = p.messenger_type
                           AND m.chat_id = p.chat_id
                           AND m.type <> 'system'
                  ) stat ON TRUE
                 ORDER BY p.last_time DESC NULLS LAST
            """, params + [limit, offset])
                items = [{'licenseId': r[0], 'messengerType': r[1], 'chatId': r[2],
                          'chatType': r[3], 'name': r[4], 'phone': r[5], 'status': r[6],
                          'lastMessageAt': r[7].isoformat() if r[7] else None,
                          'responsibleEmployeeId': r[8], 'responsibleName': r[9],
                          'messagesCount': r[10], 'inboundCount': r[11],
                          'lastMessageText': r[12], 'lastMessageSide': r[13],
                          'lastMessageType': r[14]}
                         for r in cursor.fetchall()]
            return jsonify({"status": "success", "items": items, "total": total}), 200
        except Exception as error:
            logging.exception("chatapp chats failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/chatapp/chat-messages', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chatapp_chat_messages():
        """Лента чата по возрастанию времени; `before` — курсор для подгрузки вверх."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, err = _chatapp_guard()
        if err:
            return err
        license_id = (request.args.get('license_id') or '').strip()
        messenger_type = (request.args.get('messenger_type') or '').strip()
        chat_id = (request.args.get('chat_id') or '').strip()
        if not license_id or not messenger_type or not chat_id:
            return jsonify({"error": "license_id, messenger_type и chat_id обязательны"}), 400
        before = (request.args.get('before') or '').strip() or None
        try:
            limit = min(max(int(request.args.get('limit', 50)), 1), 200)
        except (TypeError, ValueError):
            limit = 50
        where = ["m.license_id = %s", "m.messenger_type = %s", "m.chat_id = %s"]
        params = [int(license_id), messenger_type, chat_id]
        if before:
            where.append("m.dt < %s")
            params.append(before)
        try:
            with db._get_cursor() as cursor:
                cursor.execute(f"""
                SELECT m.message_id, m.dt, m.side, m.type, m.text, m.file_link,
                       m.file_name, m.file_content_type, m.is_deleted,
                       ({db._CHATAPP_HUMAN_OUT}) AS human_out,
                       COALESCE(map.is_bot, FALSE),
                       COALESCE(u.name, map.employee_name), m.employee_id
                  FROM chatapp_messages m
                  LEFT JOIN chatapp_operator_map map ON map.employee_id = m.employee_id
                  LEFT JOIN users u ON u.id = map.user_id
                 WHERE {' AND '.join(where)}
                 ORDER BY m.dt DESC LIMIT %s""", params + [limit + 1])
                rows = cursor.fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            items = [{'messageId': r[0], 'dt': r[1].isoformat() if r[1] else None,
                      'side': r[2], 'type': r[3], 'text': r[4], 'fileLink': r[5],
                      'fileName': r[6], 'fileContentType': r[7], 'isDeleted': r[8],
                      # робот это или живой оператор — считаем на бэке, чтобы правило
                      # жило в одном месте (см. chatapp_client.is_autoreply)
                      'isAutoreply': bool(r[2] == 'out' and (not r[9] or r[10])),
                      'authorName': r[11] if r[2] == 'out' else None,
                      'employeeId': r[12]}
                     for r in reversed(rows)]
            return jsonify({"status": "success", "items": items, "hasMore": has_more}), 200
        except Exception as error:
            logging.exception("chatapp chat messages failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/chatapp/sync', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_chatapp_sync():
        """Ручной запуск синка (обычно его крутит ночной джоб)."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, err = _chatapp_guard()
        if err:
            return err
        try:
            data = request.get_json(silent=True) or {}
            # rebuild_only — пересобрать эпизоды из уже скачанных сообщений, не
            # трогая API. Нужно, когда длинный синк прервался до сборки (build идёт
            # в самом конце): переписка уже в БД, а эпизодов нет. Дёшево и быстро.
            if data.get('rebuild_only'):
                result = db.build_chatapp_episodes(gap_hours=CHATAPP_EPISODE_GAP_HOURS)
                return jsonify({"status": "success", "rebuilt": True, "episodes": result}), 200
            date_from = (data.get('date_from') or '').strip() or None
            date_to = (data.get('date_to') or '').strip() or None
            if date_from and date_to and date_to < date_from:
                date_from, date_to = date_to, date_from
            result = sync_chatapp_data(days=data.get('days'),
                                       date_from=date_from, date_to=date_to)
            return jsonify({"status": "success", **result}), 200
        except ValueError:
            return jsonify({"error": "Некорректные даты периода"}), 400
        except Exception as error:
            logging.exception("chatapp sync failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/chatapp/authors', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_chatapp_authors():
        """Сотрудники ChatApp с привязкой к нашим операторам + подсказки."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, err = _chatapp_guard()
        if err:
            return err
        try:
            operators = db.list_chatapp_operator_candidates()
            items = []
            for author in db.list_chatapp_authors():
                suggested = None
                if not author.get('user_id'):
                    suggested = _wazzup_suggest_user(author.get('employee_name'), operators)
                items.append({**author, 'suggested_user': suggested})
            return jsonify({"status": "success", "authors": items, "operators": operators}), 200
        except Exception as error:
            logging.exception("chatapp authors failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/chatapp/authors/map', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_chatapp_authors_map():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, err = _chatapp_guard()
        if err:
            return err
        try:
            data = request.get_json(silent=True) or {}
            employee_id = data.get('employee_id')
            if employee_id in (None, ''):
                return jsonify({"error": "employee_id обязателен"}), 400
            user_id = data.get('user_id')
            db.upsert_chatapp_author_map(
                int(employee_id),
                user_id=int(user_id) if user_id not in (None, '') else None,
                is_bot=data.get('is_bot'),
                updated_by=requester_id)
            today = datetime.now(ZoneInfo('Asia/Almaty')).date()
            try:
                chat_metrics = db.refresh_tez_op_chat_metrics(
                    today - timedelta(days=45), today
                )
                return jsonify({"status": "success", "chat_metrics": chat_metrics}), 200
            except Exception as refresh_error:
                # Привязка уже успешно сохранена отдельной транзакцией. Не отвечаем
                # ложным 500: ночной синк повторит rollup, а UI получает явное
                # предупреждение о временно устаревших показателях.
                logging.exception("chatapp author map saved, metrics refresh failed")
                return jsonify({
                    "status": "success",
                    "chat_metrics": {
                        "status": "failed",
                        "error": "refresh_failed",
                        "error_type": type(refresh_error).__name__,
                    },
                    "warning": "Привязка сохранена, показатели чатов обновятся следующим запуском",
                }), 200
        except Exception as error:
            logging.exception("chatapp author map failed")
            return jsonify({"error": str(error)}), 500

    return bp
