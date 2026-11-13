"""Раздел «group_late»: ручки /api/group_late_bot — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_group_late_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, jsonify, request, send_file
from database import db
from datetime import datetime
from datetime import timedelta
from io import BytesIO
import group_late
import logging
import threading
import time


GROUP_LATE_BOT_DEPARTMENT_SCOPES = {'front_office': 'Регионы'}

_GROUP_LATE_BOT_SCOPE_CACHE = {'ts': 0.0, 'by_department_id': None}

_GROUP_LATE_BOT_SCOPE_CACHE_TTL = 600  # отделы меняются раз в никогда

GROUP_LATE_BOT_FULL_DEPARTMENT_CODE = 'hr'

_GROUP_LATE_BOT_FULL_DEPARTMENT_CACHE = {'ts': 0.0, 'id': None}

_GROUP_LATE_BOT_FULL_DEPARTMENT_CACHE_TTL = 600

GROUP_LATE_ATTENDANCE_MAX_DAYS = 365

GROUP_LATE_REPORT_MAX_EMPLOYEES = 50

def _group_late_bot_scope_forbidden(action):
    """Действие вне границы отдела: правится не свой чат, не своё правило и т. п."""
    return jsonify({"error": f"{action} доступно только администраторам бота"}), 403

GROUP_LATE_CLOCKSTER_ROSTER_MAX_AGE = timedelta(hours=26)

_group_late_roster_refresh_lock = threading.Lock()

def _group_late_plan_rule_in_scope(rule, scope):
    """Правило «своё», только если его адресат целиком внутри отдела запрашивающего."""
    if not scope:
        return True
    needle = str(scope).strip().casefold()
    if str(rule.get('scope') or '') == 'department':
        return str(rule.get('target') or '').strip().casefold() == needle
    return needle in str(rule.get('target_label') or '').casefold()


def build_group_late_blueprint(
    *,
    ALLOWED_ORIGINS,
    ROLE_HIERARCHY,
    _build_cors_preflight_response,
    _get_authenticated_requester,
    _get_role_level,
    _group_late_generate_report,
    _group_late_poll_cycle,
    _group_late_run_on_bot_loop,
    _group_late_send_event,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_allowed_origin,
    _is_global_admin_requester,
    _normalize_origin,
    _normalize_user_role,
    bot,
    group_late_pool,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('group_late', __name__, url_prefix='/api')

    def _group_late_bot_scope_by_department_id():
        """{id нашего отдела: название отдела Workpace} по GROUP_LATE_BOT_DEPARTMENT_SCOPES."""
        now = time.time()
        cache = _GROUP_LATE_BOT_SCOPE_CACHE
        if cache['by_department_id'] is not None and now - cache['ts'] < _GROUP_LATE_BOT_SCOPE_CACHE_TTL:
            return cache['by_department_id']
        try:
            mapping = {}
            for department in (db.get_departments() or []):
                code = str(department.get('code') or '').strip().lower()
                scope = GROUP_LATE_BOT_DEPARTMENT_SCOPES.get(code)
                if scope and department.get('id') is not None:
                    mapping[int(department['id'])] = scope
        except Exception:
            logging.exception("group_late: не удалось сопоставить отделы")
            return cache['by_department_id'] or {}
        cache['ts'], cache['by_department_id'] = now, mapping
        return mapping

    def _group_late_bot_department_scope(requester_id, role):
        """Отдел Workpace, которым ограничен глава отдела. None — ограничений нет."""
        if not _is_admin_role(role):
            return None
        headed_id = _headed_department_id(requester_id)
        if headed_id is None:
            return None
        return _group_late_bot_scope_by_department_id().get(int(headed_id))

    def _group_late_bot_full_department_id():
        """id отдела, которому раздел открыт целиком (по коду, с кэшем)."""
        now = time.time()
        cache = _GROUP_LATE_BOT_FULL_DEPARTMENT_CACHE
        if cache['id'] is not None and now - cache['ts'] < _GROUP_LATE_BOT_FULL_DEPARTMENT_CACHE_TTL:
            return cache['id']
        found = None
        try:
            for department in (db.get_departments() or []):
                if str(department.get('code') or '').strip().lower() == GROUP_LATE_BOT_FULL_DEPARTMENT_CODE:
                    found = int(department['id'])
                    break
        except Exception:
            logging.exception("group_late: не удалось определить отдел полного доступа")
            return cache['id']
        cache.update(ts=now, id=found)
        return found

    def _group_late_bot_has_full_access(requester_id, role):
        """Член отдела кадрового учёта видит раздел целиком, как глобальный админ.

    Роль тут не при чём намеренно: кадровый учёт ведут рядовые сотрудники отдела
    (у постановщика задачи #273 роль hr_manager, уровень как у оператора), а
    признак доступа — членство в отделе."""
        department_id = _group_late_bot_full_department_id()
        if department_id is None:
            return False
        try:
            return db.get_user_department_id(requester_id) == department_id
        except Exception:
            logging.exception("group_late: не удалось прочитать отдел пользователя %s", requester_id)
            return False

    def _group_late_bot_guard():
        """(requester_id, department_scope, err).

    department_scope — название отдела Workpace, которым ограничен запрашивающий:
    у глобального админа None (видит весь раздел), у члена отдела кадрового учёта
    тоже None, у главы отдела из GROUP_LATE_BOT_DEPARTMENT_SCOPES — его отдел.
    Каждый эндпоинт обязан протащить это значение в db-вызов: там на нём и
    держится граница."""
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return None, None, (jsonify({"error": message}), status_code)
        role = _normalize_user_role(requester[3])
        if _is_global_admin_requester(role, requester_id):
            return requester_id, None, None
        if _group_late_bot_has_full_access(requester_id, role):
            return requester_id, None, None
        scope = _group_late_bot_department_scope(requester_id, role)
        if scope:
            return requester_id, scope, None
        return requester_id, None, (jsonify({"error": "forbidden"}), 403)

    def _group_late_bot_actor(requester_id):
        """Кто выполнил действие — попадает в created_by, чтобы в разделе было видно,
    что правку сделали с сайта, а не командой в Telegram."""
        try:
            requester = db.get_user(id=int(requester_id))
            name = str(requester[2] or '').strip() if requester else ''
        except Exception:
            name = ''
        return f"web:{requester_id}:{name}" if name else f"web:{requester_id}"

    def _group_late_send_chat_message(chat_id, text):
        """Отправить сообщение нашим ботом из потока Flask. (message_id, error)."""
        async def _send():
            return await _group_late_send_event(chat_id, text)

        try:
            return _group_late_run_on_bot_loop(_send, timeout=30)
        except Exception as error:
            return None, str(error)[:300]

    def _group_late_fetch_chat_card(chat_id):
        """Название и тип чата из Telegram: по одному chat_id в разделе не понять,
    что это за чат."""
        async def _get_chat():
            return await bot.get_chat(chat_id)

        try:
            chat = _group_late_run_on_bot_loop(_get_chat, timeout=20)
            return {'title': getattr(chat, 'title', None) or getattr(chat, 'username', None),
                    'chat_type': getattr(chat, 'type', None)}
        except Exception as error:
            logging.debug("group_late: getChat(%s) не удался: %s", chat_id, error)
            return {'title': None, 'chat_type': None}

    def _group_late_refresh_clockster_roster_if_stale():
        """Подтягивает состав Clockster, если его нет или он старше суток с запасом.

    Замок не ждём: второй одновременный запрос просто отдаёт то, что уже есть, а
    не идёт в Clockster повторно. Любая ошибка источника раздел не роняет —
    справочник отдаётся без центрального офиса, как было до задачи #307."""
        from group_late import clockster as _clockster
        from group_late import config as _group_late_config
        if not _group_late_config.is_clockster_configured():
            return
        if not _group_late_roster_refresh_lock.acquire(blocking=False):
            return
        try:
            synced_at = db.glb_roster_synced_at('clockster')
            if synced_at is not None:
                now = datetime.now(synced_at.tzinfo) if synced_at.tzinfo else datetime.now()
                if now - synced_at < GROUP_LATE_CLOCKSTER_ROSTER_MAX_AGE:
                    return
            saved = db.glb_sync_employees(
                _clockster.roster(_clockster.clockster_client.get_users()), source='clockster')
            logging.info("Отметки: состав Clockster подтянут при открытии раздела — %s чел.", saved)
        except Exception:
            logging.exception("Отметки: состав Clockster при открытии раздела не обновлён")
        finally:
            _group_late_roster_refresh_lock.release()

    def _group_late_plan_snapshot(scope):
        """Снимок «план из iCore» для отделов, доступных запрашивающему.

    Состав Workpace берём из кэша `glb_employees`, а не из самого Workpace: раздел
    на сайте туда не ходит, справочник ему привозит опрос."""
        pairs = {
            name: code for name, code in group_late.icore_plan.plan_pairs().items()
            if not scope or name.casefold() == str(scope).casefold()
        }
        if not pairs:
            return pairs, {'people': [], 'shifts': [], 'unlinked': [], 'departments': {}}
        today = datetime.now(group_late.TZ).date()
        snapshot = db.glb_icore_plan_snapshot(
            pairs, today, today + timedelta(days=13),
            db.glb_cached_employee_roster(pairs.keys()),
        )
        return pairs, snapshot

    @bp.route('/group_late_bot/overview', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_overview():
        """Шапка раздела: состояние опроса Workpace, объёмы отбивок и отчётов."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            overview = db.get_group_late_overview(days=request.args.get('days', 7), department=scope)
            overview['status'] = 'success'
            # Без доступов к Workpace опрос не заводится и отчёты собрать нечем —
            # раздел должен сказать об этом прямо, а не показывать пустые графики.
            overview['workpace_configured'] = group_late.is_configured()
            # Отдел, которым ограничен раздел: по нему фронт прячет общие для компании
            # действия и не даёт выбрать чужой отдел в фильтрах.
            overview['department_scope'] = scope
            return jsonify(overview), 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot overview failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/chats', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_chats():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            if request.method == 'GET':
                result = db.get_group_late_chats(days=request.args.get('days', 7), department=scope)
                result['status'] = 'success'
                return jsonify(result), 200

            payload = request.get_json(silent=True) or {}
            chat_id = payload.get('chat_id')
            title = payload.get('title')
            # В границах отдела фильтр у чата всегда один — свой: иначе чат отдела получал
            # бы чужие нарушения, а уже подключённый чужой чат уехал бы под наш фильтр
            # (add_group_late_chat пишет через ON CONFLICT DO UPDATE).
            departments = payload.get('departments')
            if scope:
                if db.glb_chat_department_state(chat_id, scope) == 'foreign':
                    return _group_late_bot_scope_forbidden(
                        'Чат закреплён за другим отделом, его настройка')
                departments = [scope]
            # Название чата знает Telegram — берём оттуда, если его не указали руками.
            card = _group_late_fetch_chat_card(chat_id) if chat_id else {}
            result = db.add_group_late_chat(
                chat_id,
                title=title or card.get('title'),
                chat_type=card.get('chat_type'),
                departments=departments,
                note=payload.get('note'),
                created_by=_group_late_bot_actor(requester_id),
            )
            # Приветствие сразу проверяет, что бот действительно состоит в чате.
            # Чат в списке остаётся в любом случае — предупреждение показываем в UI.
            if payload.get('send_welcome', True):
                message_id, send_error = _group_late_send_chat_message(
                    result['chat_id'], group_late.messages.build_welcome_message())
                result['welcome_sent'] = bool(message_id)
                if not message_id:
                    result['warning'] = ("Чат сохранён, но приветствие отправить не удалось — "
                                         f"проверьте, что бот добавлен в чат ({send_error})")
            return jsonify({"status": "success", **result}), 201
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot chats failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/chats/<chat_id>', methods=['PATCH', 'DELETE', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_chat_item(chat_id):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            if request.method == 'DELETE':
                return jsonify({"status": "success",
                                **db.delete_group_late_chat(chat_id, department=scope)}), 200

            payload = request.get_json(silent=True) or {}
            result = {}
            if any(key in payload for key in ('title', 'note', 'enabled')):
                result = db.update_group_late_chat(
                    chat_id,
                    title=payload.get('title'),
                    note=payload.get('note'),
                    enabled=payload.get('enabled'),
                    department=scope,
                )
            if 'departments' in payload:
                result = db.set_group_late_chat_departments(
                    chat_id,
                    [scope] if scope else payload.get('departments'),
                    created_by=_group_late_bot_actor(requester_id),
                    department=scope,
                )
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot chat update failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/available_chats', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_available_chats():
        """Группы, где бот уже есть, но рассылка ещё не подключена."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        # Список групп, где бот уже состоит, общий: без него чат не подключить, а об
        # отделах он ничего не рассказывает.
        _, _, err = _group_late_bot_guard()
        if err:
            return err
        try:
            result = db.get_group_late_available_chats()
            result['status'] = 'success'
            return jsonify(result), 200
        except Exception as error:
            logging.exception("group_late available chats failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/chats/<chat_id>/test', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_chat_test(chat_id):
        """Проверка связи: бот шлёт в чат тестовое сообщение и возвращает его название."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        # Тест пишет в чат от имени бота — в чужой чат писать нельзя.
        if scope:
            try:
                own = db.glb_chat_department_state(chat_id, scope) == 'own'
            except ValueError as error:
                return jsonify({"error": str(error)}), 400
            if not own:
                return _group_late_bot_scope_forbidden(
                    'Чат закреплён за другим отделом, отправка в него')
        now_text = datetime.now(group_late.TZ).strftime('%H:%M')
        message_id, send_error = _group_late_send_chat_message(
            chat_id, group_late.messages.build_test_message(now_text))
        if not message_id:
            return jsonify({"error": f"Бот не смог написать в этот чат: {send_error}"}), 502
        card = _group_late_fetch_chat_card(chat_id)
        if card.get('title'):
            db.glb_update_chat_title(chat_id, card['title'])
        return jsonify({"status": "success", "message_id": message_id, **card}), 200

    @bp.route('/group_late_bot/departments', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_departments():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            result = db.get_group_late_departments(department=scope)
            result['status'] = 'success'
            return jsonify(result), 200
        except Exception as error:
            logging.exception("group_late_bot departments failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/departments/sync', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_departments_sync():
        """Перечитать справочник отделов из Workpace."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        # Пересчёт справочника из Workpace ничего не раскрывает (в ответе только счётчик)
        # и главе отдела нужен так же, как админу, — поэтому доступен и в границах отдела.
        _, _, err = _group_late_bot_guard()
        if err:
            return err
        if not group_late.is_configured():
            return jsonify({"error": "Не заданы WORKPACE_LOGIN / WORKPACE_PASSWORD",
                            "code": "WORKPACE_NOT_CONFIGURED"}), 503
        try:
            employees = group_late.workpace_client.get_employees()
            count = db.glb_sync_departments(group_late.count_departments(employees))
            # Состав отделов освежаем той же кнопкой: вкладка «Сотрудники» показывает
            # весь отдел, а не только нарушителей, и ждать опроса ради этого незачем.
            staff = db.glb_sync_employees(group_late.employee_roster(employees))
            return jsonify({"status": "success", "departments": count, "employees": staff}), 200
        except group_late.WorkpaceError as error:
            return jsonify({"error": f"Workpace недоступен: {error}"}), 502
        except Exception as error:
            logging.exception("group_late departments sync failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/mutes', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_mutes():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            if request.method == 'GET':
                result = db.get_group_late_mutes(department=scope)
                result['status'] = 'success'
                return jsonify(result), 200

            payload = request.get_json(silent=True) or {}
            mute_kind = str(payload.get('mute_kind') or '').strip().lower()
            mute_value = payload.get('mute_value')
            chat_id = payload.get('chat_id')
            # В границах отдела правило либо действует в своём чате, либо глушит свой же
            # отдел целиком. Глобальное правило по сотруднику или «выключить всё» замолчало
            # бы и чужие чаты, поэтому оно остаётся за администраторами бота.
            if scope:
                if mute_kind == 'dept':
                    mute_value = scope
                if chat_id:
                    if db.glb_chat_department_state(chat_id, scope) != 'own':
                        return _group_late_bot_scope_forbidden(
                            'Чат закреплён за другим отделом, правило тишины в нём')
                elif mute_kind != 'dept':
                    return _group_late_bot_scope_forbidden('Правило тишины во всех чатах')
            result = db.add_group_late_mute(
                mute_kind,
                mute_value=mute_value,
                chat_id=chat_id,
                created_by=_group_late_bot_actor(requester_id),
            )
            return jsonify({"status": "success", **result}), 201
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot mutes failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/mutes/<int:mute_id>', methods=['DELETE', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_mute_item(mute_id):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            return jsonify({"status": "success",
                            **db.delete_group_late_mute(mute_id=mute_id, department=scope)}), 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 404
        except Exception as error:
            logging.exception("group_late_bot mute delete failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/events', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_events():
        """Лента отбивок: что нашли, куда отправили, кто отбил."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            result = db.get_group_late_events(
                date_from=request.args.get('date_from'),
                date_to=request.args.get('date_to'),
                event_type=request.args.get('event_type'),
                department=scope or request.args.get('department'),
                chat_id=request.args.get('chat_id'),
                search=request.args.get('q'),
                limit=request.args.get('limit', 100),
                offset=request.args.get('offset', 0),
            )
            result['status'] = 'success'
            return jsonify(result), 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot events failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/attendance', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_attendance():
        """Отметки прихода и ухода за период по обоим источникам (задачи #273, #307).

    Период больше не ограничен неделей: прошедшие дни лежат в кэше
    (`group_late.attendance_cache`), а из внешних API тянется только сегодняшний.
    Дни, которые не успели добрать за один заход, возвращаются в `pending_days`
    — молча обрезать период нельзя, иначе кадровик решит, что в эти дни никто не
    отмечался."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        if not group_late.is_configured():
            return jsonify({"error": "Не заданы WORKPACE_LOGIN / WORKPACE_PASSWORD",
                            "code": "WORKPACE_NOT_CONFIGURED"}), 503
        try:
            from group_late import attendance as _attendance

            def _as_day(value, fallback):
                text = str(value or '').strip()[:10]
                if not text:
                    return fallback
                try:
                    return datetime.strptime(text, '%Y-%m-%d').date()
                except ValueError:
                    raise ValueError('Дата должна быть в формате ГГГГ-ММ-ДД')

            from group_late import attendance_cache as _attendance_cache
            department = scope or request.args.get('department')
            refresh = str(request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes')
            # «Последние отметки» (30.09.2026): без периода — каждый сотрудник один
            # раз, днём своей последней отметки, от самых свежих.
            latest = str(request.args.get('mode') or '').strip().lower() == 'latest'
            if latest:
                date_start = date_end = None
                payload = _attendance_cache.latest_rows(db, department=department, refresh=refresh)
            else:
                today = datetime.now(group_late.TZ).date()
                date_start = _as_day(request.args.get('date_start'), today)
                date_end = _as_day(request.args.get('date_end'), date_start)
                if date_end < date_start:
                    date_start, date_end = date_end, date_start
                if (date_end - date_start).days > GROUP_LATE_ATTENDANCE_MAX_DAYS:
                    return jsonify({
                        "error": f"Период не должен превышать "
                                 f"{GROUP_LATE_ATTENDANCE_MAX_DAYS + 1} дн.",
                        "code": "PERIOD_TOO_LONG",
                    }), 400
                payload = _attendance_cache.rows_for(
                    db, date_start, date_end, department=department, refresh=refresh)
            rows = _attendance.search_rows(payload['rows'], request.args.get('q'))
            rows = _attendance.sort_rows(rows, request.args.get('sort'))

            # Фильтры экрана: тип отметки и конкретные сотрудники (ТЗ просит отчёт
            # «по выбранному(-ым) сотруднику(-ам)»).
            kind = (request.args.get('kind') or '').strip().lower()
            if kind == 'in':
                rows = [r for r in rows if r['fact_in']]
            elif kind == 'out':
                rows = [r for r in rows if r['fact_out']]
            elif kind == 'absent':
                rows = [r for r in rows if r['status'] == _attendance.STATUS_ABSENT]
            employees = [e for e in (request.args.get('employees') or '').split(',') if e.strip()]
            if employees:
                wanted = {e.strip() for e in employees}
                rows = [r for r in rows if r['employee_id'] in wanted]

            # Счётчики по статусам — для полосы «Все · Опоздали · Не отметились…» над
            # списком. Считаются ДО фильтра по статусу и до страниц: иначе, выбрав
            # «Опоздали», человек видел бы у остальных статусов нули и не понимал бы,
            # сколько всего людей за период.
            status_counts = {}
            for row in rows:
                status_counts[row['status']] = status_counts.get(row['status'], 0) + 1
            statuses = {s.strip() for s in (request.args.get('status') or '').split(',') if s.strip()}
            if statuses:
                rows = [r for r in rows if r['status'] in statuses]

            total = len(rows)
            try:
                limit = max(1, min(int(request.args.get('limit', 200)), 1000))
                offset = max(0, int(request.args.get('offset', 0)))
            except (TypeError, ValueError):
                limit, offset = 200, 0

            return jsonify({
                'status': 'success',
                'rows': rows[offset:offset + limit],
                'total': total,
                'mode': 'latest' if latest else 'period',
                'date_start': date_start.isoformat() if date_start else None,
                'date_end': date_end.isoformat() if date_end else None,
                'department_scope': scope,
                'clockster_error': payload.get('clockster_error'),
                'pending_days': payload.get('pending_days') or [],
                'status_counts': status_counts,
                'built_days': payload.get('built_days', 0),
                'cached_days': payload.get('cached_days', 0),
            }), 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot attendance failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/directory', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_directory():
        """Справочники фильтров раздела «Отметки»: подразделения и состав (ТЗ #307).

    Отдельно от `/departments`: тот справочник — про отделы Workpace и
    маршрутизацию уведомлений по чатам, а здесь нужен ещё и центральный офис из
    Clockster. Уведомления по нему не уходят и уходить не должны (п. 3), поэтому
    в маршрутизации этих подразделений нет — а в фильтре и в выборе людей для
    отчёта они обязаны быть."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        _group_late_refresh_clockster_roster_if_stale()
        try:
            payload = db.glb_attendance_directory(department=scope)
            payload['status'] = 'success'
            payload['department_scope'] = scope
            return jsonify(payload), 200
        except Exception as error:
            logging.exception("group_late_bot directory failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/plan_rules', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_plan_rules():
        """Свой график смен раздела «Отметки» (ТЗ #307, п. 5).

    План ставится на проект целиком или на конкретного человека — в том числе на
    выходные дни, которых нет ни в Workpace, ни в Clockster. Наш план ДОПОЛНЯЕТ
    источники: он появляется только там, где смены нет. Иначе одно правило на
    отдел перебило бы настоящий сменный график колл-центра."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, scope, err = _group_late_bot_guard()
        if err:
            return err
        if request.method == 'GET':
            try:
                return jsonify({'status': 'success', 'items': db.glb_plan_rules(department=scope),
                                'department_scope': scope}), 200
            except Exception as error:
                logging.exception("group_late_bot plan rules failed")
                return jsonify({"error": str(error)}), 500

        payload = request.get_json(silent=True) or {}
        if not _group_late_plan_rule_in_scope(payload, scope):
            return _group_late_bot_scope_forbidden('График на чужое подразделение')
        try:
            rule_id = db.glb_save_plan_rule(payload, created_by=requester_id)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot plan rule save failed")
            return jsonify({"error": str(error)}), 500
        return jsonify({'status': 'success', 'id': rule_id}), 200

    @bp.route('/group_late_bot/plan_rules/<int:rule_id>', methods=['DELETE', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_plan_rule_item(rule_id):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            if scope:
                mine = {int(item['id']) for item in db.glb_plan_rules(department=scope)}
                if int(rule_id) not in mine:
                    return _group_late_bot_scope_forbidden('График чужого подразделения')
            if not db.glb_delete_plan_rule(rule_id):
                return jsonify({"error": "Правило не найдено"}), 404
            return jsonify({'status': 'success'}), 200
        except Exception as error:
            logging.exception("group_late_bot plan rule delete failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/employees', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_employees():
        """Дисциплина по сотрудникам за период, разложенная по отделам Workpace."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            result = db.get_group_late_employee_stats(
                date_from=request.args.get('date_from'),
                date_to=request.args.get('date_to'),
                department=scope or request.args.get('department'),
                search=request.args.get('q'),
                # Где сотрудника Workpace искать у нас: ровно там, где пара отделов
                # объявлена вручную. Тот же справочник, что режет раздел главе отдела.
                match_departments={
                    workpace_name: department_id
                    for department_id, workpace_name in _group_late_bot_scope_by_department_id().items()
                },
            )
            result['status'] = 'success'
            return jsonify(result), 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot employees failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/plan_links', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_plan_links():
        """Мост «карточка Workpace → наш сотрудник» для отделов с планом из iCore.

    Автоматически карточки сшиваются по ФИО; здесь видно результат и правится то,
    что автоматике не даётся: смена фамилии, две карточки на одного человека,
    чужая компания холдинга в том же отделе Workpace."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, scope, err = _group_late_bot_guard()
        if err:
            return err

        try:
            pairs, snapshot = _group_late_plan_snapshot(scope)
            if request.method == 'GET':
                shifts_by_user = {}
                for shift in snapshot['shifts']:
                    shifts_by_user[shift['user_id']] = shifts_by_user.get(shift['user_id'], 0) + 1
                people = [
                    {**{key: person[key] for key in ('user_id', 'name', 'city', 'cards', 'link_source')},
                     'department_name': snapshot['departments'].get(person['department_code']),
                     'shifts_ahead': shifts_by_user.get(person['user_id'], 0)}
                    for person in snapshot['people']
                ]
                people.sort(key=lambda item: item['name'].lower())
                return jsonify({
                    'status': 'success',
                    'departments': [{'workpace_name': name, 'department_code': code}
                                    for name, code in sorted(pairs.items())],
                    'people': people,
                    'unlinked': snapshot['unlinked'],
                    'schedule_days': 14,
                }), 200

            payload = request.get_json(silent=True) or {}
            ext_id = str(payload.get('workpace_ext_id') or '').strip()
            if not ext_id:
                return jsonify({"error": "workpace_ext_id обязателен"}), 400

            # Граница отдела: править можно только карточки своих отделов и привязывать
            # только к своим сотрудникам. Обе стороны берём из уже собранного снимка —
            # он и так посчитан по доступным отделам.
            known_cards = {card['ext_id'] for person in snapshot['people'] for card in person['cards']}
            known_cards.update(item['ext_id'] for item in snapshot['unlinked'])
            if ext_id not in known_cards:
                return _group_late_bot_scope_forbidden('Изменение привязки этой карточки')

            if payload.get('reset'):
                result = db.glb_delete_employee_link(ext_id)
            else:
                # user_id: null — это «не наш сотрудник», осмысленное значение. Поэтому
                # отсутствие поля и явный null различаем: молча исключить карточку из-за
                # опечатки в запросе хуже, чем отказать.
                if 'user_id' not in payload:
                    return jsonify({"error": "нужен user_id (или null, чтобы исключить карточку)"}), 400
                user_id = payload.get('user_id')
                if user_id is not None:
                    known_users = {person['user_id'] for person in snapshot['people']}
                    if int(user_id) not in known_users:
                        return _group_late_bot_scope_forbidden('Привязка к этому сотруднику')
                result = db.glb_set_employee_link(ext_id, user_id, linked_by=requester_id)
            result['status'] = 'success'
            return jsonify(result), 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late_bot plan_links failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/reports', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_reports():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        requester_id, scope, err = _group_late_bot_guard()
        if err:
            return err
        if request.method == 'GET':
            try:
                result = db.get_group_late_reports(
                    date_from=request.args.get('date_from'),
                    date_to=request.args.get('date_to'),
                    status=request.args.get('status'),
                    source=request.args.get('source'),
                    limit=request.args.get('limit', 50),
                    offset=request.args.get('offset', 0),
                    department=scope,
                )
                result['status'] = 'success'
                return jsonify(result), 200
            except ValueError as error:
                return jsonify({"error": str(error)}), 400
            except Exception as error:
                logging.exception("group_late_bot reports failed")
                return jsonify({"error": str(error)}), 500

        # Отчёт за период тянет из Workpace каждый день диапазона, поэтому считаем в
        # фоне и сразу отдаём id карточки — раздел показывает её как «формируется».
        if not group_late.is_configured():
            return jsonify({"error": "Не заданы WORKPACE_LOGIN / WORKPACE_PASSWORD",
                            "code": "WORKPACE_NOT_CONFIGURED"}), 503
        payload = request.get_json(silent=True) or {}
        date_from = str(payload.get('date_from') or '').strip()
        date_to = str(payload.get('date_to') or '').strip() or date_from
        # Отчёт «по всем отделам» в границах отдела недоступен: собирается только свой.
        department = scope or str(payload.get('department') or '').strip()
        send_to_chat_id = str(payload.get('send_to_chat_id') or '').strip()
        # Отчёт по конкретным людям (ТЗ #273). Пустой список = весь отдел, как раньше.
        employees = [str(x).strip() for x in (payload.get('employees') or []) if str(x).strip()]
        if len(employees) > GROUP_LATE_REPORT_MAX_EMPLOYEES:
            return jsonify({"error": f"За один раз можно выбрать не больше "
                                     f"{GROUP_LATE_REPORT_MAX_EMPLOYEES} сотрудников"}), 400
        try:
            if scope and send_to_chat_id and db.glb_chat_department_state(send_to_chat_id, scope) != 'own':
                return _group_late_bot_scope_forbidden(
                    'Чат закреплён за другим отделом, отправка отчёта в него')
            report_id = db.glb_create_report(
                date_from, date_to, department=department or None, source='web',
                requested_chat_id=send_to_chat_id or None,
                requested_by=_group_late_bot_actor(requester_id),
                employees=employees or None,
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            logging.exception("group_late report create failed")
            return jsonify({"error": str(error)}), 500

        group_late_pool.submit(
            _group_late_generate_report, report_id, date_from, date_to, department, send_to_chat_id,
            employees or None,
        )
        return jsonify({"status": "success", "report_id": report_id, "report_status": "running"}), 202

    @bp.route('/group_late_bot/reports/<int:report_id>/file', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_report_file(report_id):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            report_file = db.get_group_late_report_file(report_id, department=scope)
            if not report_file:
                return jsonify({"error": "Файл отчёта не найден"}), 404
            return send_file(
                BytesIO(report_file['content']),
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=report_file['file_name'] or f"report_{report_id}.xlsx",
            )
        except Exception as error:
            logging.exception("group_late_bot report file failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/poll_runs', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_poll_runs():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        # Состояние опроса — здоровье общего источника данных, а не чьи-то цифры.
        _, _, err = _group_late_bot_guard()
        if err:
            return err
        try:
            result = db.get_group_late_poll_runs(limit=request.args.get('limit', 30))
            result['status'] = 'success'
            return jsonify(result), 200
        except Exception as error:
            logging.exception("group_late_bot poll runs failed")
            return jsonify({"error": str(error)}), 500

    @bp.route('/group_late_bot/poll', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_poll():
        """Прогнать опрос Workpace прямо сейчас, не дожидаясь двухминутной джобы."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        # Опрос прогоняет ВСЮ компанию и рассылает найденное по всем чатам, поэтому
        # запускать его вручную может только администратор бота: расписание общее.
        if scope:
            return _group_late_bot_scope_forbidden('Внеочередной опрос Workpace')
        if not group_late.is_configured():
            return jsonify({"error": "Не заданы WORKPACE_LOGIN / WORKPACE_PASSWORD",
                            "code": "WORKPACE_NOT_CONFIGURED"}), 503
        try:
            # Цикл целиком уходит в event loop бота: сеть и база внутри него сами
            # уезжают в пул потоков, а отправка идёт из «родного» цикла aiogram.
            result = _group_late_run_on_bot_loop(_group_late_poll_cycle, timeout=180)
        except RuntimeError as error:
            return jsonify({"error": str(error)}), 503
        except Exception as error:
            logging.exception("group_late manual poll failed")
            return jsonify({"error": str(error)}), 500
        if not result.get('ok'):
            return jsonify({"error": result.get('error') or 'Опрос не удался'}), 502
        return jsonify({"status": "success", **result}), 200

    @bp.route('/group_late_bot/mark_photos', methods=['POST', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_mark_photos():
        """Какие отметки окна «Отметки за день» с фото (13.11.2026).

    Тело: {date, marks: [{key, id?, at?, employee_id?, employee?}]} — отметки
    Workpace одного дня. Отдаются только найденные и только в границах отдела
    запрашивающего; фото у Workpace есть не у всех отметок."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        if not group_late.is_configured():
            return jsonify({"error": "Не заданы WORKPACE_LOGIN / WORKPACE_PASSWORD",
                            "code": "WORKPACE_NOT_CONFIGURED"}), 503
        payload = request.get_json(silent=True) or {}
        try:
            from group_late import mark_photos as _mark_photos
            from group_late.workpace import WorkpaceError
            try:
                result = _mark_photos.lookup(db, payload.get('date'), payload.get('marks'), scope=scope)
            except WorkpaceError as error:
                logging.warning("Отметки: фото отметок — Workpace не ответил: %s", error)
                return jsonify({"error": "Workpace сейчас не отвечает"}), 502
            return jsonify({"status": "success", **result}), 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception:
            logging.exception("group_late_bot mark photos failed")
            return jsonify({"error": "Не удалось получить фото отметок"}), 500

    @bp.route('/group_late_bot/mark_photos/<mark_id>', methods=['GET', 'OPTIONS'])
    @require_api_key
    def api_group_late_bot_mark_photo(mark_id):
        """Снимок одной отметки Workpace — только той, что уже нашёл поиск выше
    в границах отдела запрашивающего."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        _, scope, err = _group_late_bot_guard()
        if err:
            return err
        try:
            from group_late import mark_photos as _mark_photos
            from group_late.workpace import WorkpaceError
            try:
                data, content_type = _mark_photos.photo(db, mark_id, scope=scope)
            except LookupError:
                return jsonify({"error": "Фото нет"}), 404
            except WorkpaceError as error:
                logging.warning("Отметки: фото отметки — Workpace не ответил: %s", error)
                return jsonify({"error": "Workpace сейчас не отвечает"}), 502
        except Exception:
            logging.exception("group_late_bot mark photo failed")
            return jsonify({"error": "Не удалось получить фото"}), 500
        response = send_file(BytesIO(data), mimetype=content_type)
        # Фото лица — персональные данные: только в браузере смотрящего, не в общих кэшах.
        response.headers['Cache-Control'] = 'private, max-age=3600'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    return bp
