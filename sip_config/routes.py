"""Раздел «sip_config»: ручки /api/sip_config — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_sip_config_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, jsonify, request
from database import db
from database import normalize_sip_provider
import logging


def _sip_provider_arg(value):
    """Провайдер из query/тела: ('', None) — не задан, (провайдер, None) или (None, ошибка).

    Разбор общий с БД (normalize_sip_provider), а не свой список строк: молча
    съеденный «binotel» отдал бы тезовцу пароль «база отдела + номер»."""
    if value is None or str(value).strip() == '':
        return '', None
    try:
        return normalize_sip_provider(value), None
    except ValueError as e:
        return None, str(e)

def _sip_target_in_scope(target, department_ids, supervisor_id) -> bool:
    """Попадает ли сотрудник в область видимости запросившего."""
    if department_ids is None:
        return True
    dept = target.get('department_id')
    if dept is not None and int(dept) in set(department_ids):
        return True
    if supervisor_id and target.get('supervisor_id') is not None:
        return int(target['supervisor_id']) == int(supervisor_id)
    return False

def _binotel_operator_payload_error(target, payload):
    """Что не так в форме сотрудника на Binotel; None — всё в порядке.

    Логин и пароль у Binotel персональные, и пустое поле здесь не «взять из
    отдела», а нерабочая регистрация — телефон молча не поднимется. Пароль
    требуем только при первом заполнении: дальше пустое поле означает «не
    менять», как и везде. Исключение — СЕРВЕР: у него с задачи #309 есть общий
    ярус отдела, поэтому пустое поле допустимо ровно тогда, когда отделу задан
    общий сервер; иначе это по-прежнему нерабочая регистрация.
    """
    def _value(key, current):
        # Ключа нет = «не менять» (берём текущее); ключ с пустым значением = стереть.
        if payload.get(key) is None:
            return str(current or '').strip()
        return str(payload[key]).strip()

    # Автодозвон — механика локальной АТС: второй номер, код включения и свой
    # FOP2. В Binotel этого нет вовсе, и молча съесть такое поле нельзя:
    # руководитель решит, что автодозвон настроен.
    if any(str(k).startswith('autodial_') and str(v or '').strip() for k, v in payload.items()):
        return "В Binotel автодозвона нет: поля второго номера должны быть пустыми"
    if not _value('sip_domain', target.get('sip_domain')) \
            and not str(target.get('department_sip_server') or '').strip():
        return "Укажите SIP-сервер Binotel (например sip52.binotel.com)"
    if not _value('sip_login', target.get('sip_login')):
        return "Укажите SIP-логин Binotel — регистрация идёт им, а не внутренним номером"
    if not _value('sip_password', target.get('sip_password')):
        return "Укажите SIP-пароль: у Binotel он персональный и из базы отдела не собирается"
    return None

BINOTEL_EMPLOYEES_MODULE_PATH = '/main/?module=ajax&action=listOfEmployeesForBce&mbav=1'

def _binotel_employee_records(payload):
    """Плоский список записей сотрудников из ответа listOfEmployeesForBce.

    Кабинет раскладывает людей по состояниям присутствия
    (listOfEmployees{Working,Break,Inactive,Without}PresenceStates), а нам нужна
    одна конкретная запись — склеиваем группы в общий список. Ключом словаря
    идёт e-mail: дублируем его внутрь записи, чтобы поиск не зависел от того,
    положил ли кабинет поле email в саму запись.
    """
    records = []
    scopes = [payload if isinstance(payload, dict) else {}]
    page_data = scopes[0].get('pageData')
    if isinstance(page_data, dict):
        scopes.append(page_data)
    for scope in scopes:
        for key, group in scope.items():
            key = str(key)
            if not (key.startswith('listOfEmployees') and key.endswith('PresenceStates')):
                continue
            if isinstance(group, dict):
                items = list(group.items())
            elif isinstance(group, list):
                items = [(None, value) for value in group]
            else:
                continue
            for group_key, record in items:
                if not isinstance(record, dict):
                    continue
                if group_key and not str(record.get('email') or '').strip():
                    record = dict(record, email=group_key)
                records.append(record)
    return records

def _binotel_employee_by_email(records, email):
    """Запись сотрудника по e-mail (он же логин кабинета) или None."""
    wanted = str(email or '').strip().lower()
    if not wanted:
        return None
    for record in records:
        if str(record.get('email') or '').strip().lower() == wanted:
            return record
    return None


def build_sip_config_blueprint(
    *,
    ALLOWED_ORIGINS,
    ROLE_HIERARCHY,
    _build_cors_preflight_response,
    _can_manage_sip_config,
    _department_scope_id_for_requester,
    _get_authenticated_requester,
    _get_role_level,
    _has_min_role,
    _headed_department_ids,
    _is_admin_role,
    _is_allowed_origin,
    _is_global_admin_requester,
    _normalize_origin,
    _normalize_user_role,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('sip_config', __name__, url_prefix='/api')

    def _sip_department_scope(requester_id, role):
        """Область видимости раздела «Настройки SIP».

    Возвращает (department_ids, supervisor_id): None вместо списка отделов —
    без ограничений (админ). Глава отдела видит свои отделы, СВ — свой отдел
    плюс тех, кого ведёт лично.
    """
        if _is_admin_role(role) and not _headed_department_ids(requester_id):
            return None, None
        headed = _headed_department_ids(requester_id)
        if headed:
            return sorted(headed), None
        dept_id = _department_scope_id_for_requester(requester_id)
        return ([dept_id] if dept_id is not None else []), requester_id

    def _sip_section_allowed(requester_id, role, provider) -> bool:
        """Открыт ли запросившему подраздел этого провайдера.

    Глобальный админ ведёт обе телефонии. Остальным подраздел даёт не роль, а
    отдел: главе Тез нечего делать в паролях отдела продаж, и наоборот. Провайдер
    берём из настроек отдела, а не из кода отдела: код — лишь исходное значение,
    переключают отдел в самой панели.
    """
        if _is_global_admin_requester(role, requester_id):
            return True
        department_ids, _supervisor_id = _sip_department_scope(requester_id, role)
        # None = «без ограничений»: у такого запросившего оба подраздела и так свои.
        if department_ids is None:
            return True
        if not department_ids:
            return False
        try:
            configs = db.get_sip_department_configs(department_ids=department_ids)
        except Exception:
            # Провайдер не прочитали — считаем, что доступа нет: пустить «на всякий
            # случай» здесь дороже, чем показать ошибку.
            logging.error("Error reading sip department providers", exc_info=True)
            return False
        return any((c.get('provider') or 'asterisk') == provider for c in configs)

    def _binotel_cabinet_credentials(user_ids):
        """{user_id: (адрес кабинета, логин, пароль)} — учётки для входа в Binotel.

    Узкий геттер живёт здесь, а не в database.py, намеренно: пароль кабинета
    наружу не отдаётся никогда (панель видит только признак «задан»), и нужен он
    ровно одному сценарию — серверному входу от имени самого сотрудника.
    """
        ids = sorted({int(u) for u in (user_ids or []) if u is not None})
        if not ids:
            return {}
        with db._get_cursor() as cursor:
            cursor.execute("""
            SELECT user_id, cabinet_url, cabinet_login, cabinet_password
            FROM binotel_user_accounts
            WHERE user_id = ANY(%s)
        """, (ids,))
            rows = cursor.fetchall()
        return {
            int(row[0]): (
                (row[1] or '').strip() or 'https://my.binotel.kz',
                (row[2] or '').strip(),
                row[3] or '',
            )
            for row in rows
        }

    @bp.route('/sip_config/operators', methods=['GET', 'OPTIONS'])
    @require_api_key
    def sip_config_operators_endpoint():
        """Список сотрудников с их SIP-аккаунтами (основной + автодозвон).

    Область видимости — как в «Учёте сотрудников»: админ видит всех, глава
    отдела свой отдел, СВ — свой отдел и своих операторов."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            role = requester[3]
            if not _can_manage_sip_config(requester_id, role):
                return jsonify({"error": "Forbidden"}), 403
            department_ids, supervisor_id = _sip_department_scope(requester_id, role)
            # Подраздел выбирает фронт: «Таксопарки» шлёт asterisk, «Tez» — binotel.
            # Пусто = всё, что и так в области видимости (админская сводка целиком).
            provider, provider_error = _sip_provider_arg(request.args.get('provider'))
            if provider_error:
                return jsonify({"error": provider_error}), 400
            if provider and not _sip_section_allowed(requester_id, role, provider):
                return jsonify({"error": "Этот раздел телефонии не для вашего отдела"}), 403
            include_inactive = str(request.args.get('include_inactive', '')).strip().lower() in ('1', 'true', 'yes')
            operators = db.get_sip_operators(
                department_ids=department_ids,
                supervisor_id=supervisor_id,
                include_inactive=include_inactive,
            )
            departments = db.get_sip_department_configs(department_ids=department_ids)
            if provider:
                # Фильтруем по провайдеру ОТДЕЛА: своего у сотрудника нет, он весь
                # определяется отделом (department_provider в его строке). Оператор
                # без отдела (взят по supervisor_id) считается на локальной АТС.
                departments = [d for d in departments
                               if (d.get('provider') or 'asterisk') == provider]
                operators = [o for o in operators
                             if (o.get('department_provider') or 'asterisk') == provider]
            # Ключа "settings" здесь больше нет: общий ярус настроек снят, отделы
            # держат свои сервер/базу пароля сами.
            return jsonify({
                "status": "success",
                "operators": operators,
                "departments": departments,
            }), 200
        except Exception as e:
            logging.error(f"Error in sip_config operators: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sip_config/departments/<int:department_id>', methods=['PUT', 'OPTIONS'])
    @require_api_key
    def sip_config_department_endpoint(department_id):
        """Настройки SIP отдела: свой сервер/домен, база пароля и код автодозвона.
    Пустые поля возвращают отдел к общим настройкам."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            role = requester[3]
            if not _can_manage_sip_config(requester_id, role):
                return jsonify({"error": "Forbidden"}), 403
            department_ids, _supervisor_id = _sip_department_scope(requester_id, role)
            if department_ids is not None and int(department_id) not in set(department_ids):
                return jsonify({"error": "Это не ваш отдел"}), 403
            payload = request.get_json(silent=True) or {}
            # Провайдер отдела переключает саму механику (у Binotel нет ни базы
            # пароля, ни автодозвона), поэтому мусор ловим до записи. Ключа нет —
            # БД оставит текущее значение, и переключать подраздел не требуется.
            if payload.get('provider') is not None:
                provider, provider_error = _sip_provider_arg(payload.get('provider'))
                if provider_error:
                    return jsonify({"error": provider_error}), 400
                if provider and not _sip_section_allowed(requester_id, role, provider):
                    return jsonify({"error": "Этот раздел телефонии не для вашего отдела"}), 403
                payload = dict(payload, provider=provider or 'asterisk')
            department = db.update_sip_department_config(department_id, payload, user_id=requester_id)
            return jsonify({"status": "success", "department": department}), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error in sip_config department: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sip_config/operators/bulk', methods=['PUT', 'OPTIONS'])
    @require_api_key
    def sip_config_operators_bulk_endpoint():
        """Массово изменить пароль/домен и вход в FOP2 выбранным сотрудникам.

    Номера остаются персональными — массово меняются только пароль и домен
    (основного номера и номера автодозвона) и выключатель FOP2. Пустое значение
    возвращает общие; непереданное поле не трогаем."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            role = requester[3]
            if not _can_manage_sip_config(requester_id, role):
                return jsonify({"error": "Forbidden"}), 403
            payload = request.get_json(silent=True) or {}
            raw_ids = payload.get('user_ids') or []
            try:
                user_ids = sorted({int(uid) for uid in raw_ids})
            except (TypeError, ValueError):
                return jsonify({"error": "Некорректный список сотрудников"}), 400
            if not user_ids:
                return jsonify({"error": "Не выбран ни один сотрудник"}), 400

            targets = db.get_sip_operators_by_ids(user_ids)
            if len(targets) != len(user_ids):
                return jsonify({"error": "Часть сотрудников не найдена"}), 404
            if any(t.get('role') not in ('operator', 'trainee') for t in targets):
                return jsonify({"error": "SIP-аккаунты ведём только операторам и стажёрам"}), 400
            department_ids, supervisor_id = _sip_department_scope(requester_id, role)
            if any(not _sip_target_in_scope(t, department_ids, supervisor_id) for t in targets):
                return jsonify({"error": "В выборке есть сотрудники не из вашего отдела"}), 403
            # Массовая правка держится на том, что у отдела есть общее: домен и база
            # пароля. У Binotel общего нет ни одного поля, так что и менять скопом
            # нечего — единый домен на всех просто снял бы всех с регистрации.
            if any((t.get('department_provider') or 'asterisk') == 'binotel' for t in targets):
                return jsonify({"error": "Для Binotel массовые изменения не поддерживаются: "
                                         "сервер, логин и пароль у каждого свои"}), 400

            operators = db.bulk_update_user_sip_overrides(user_ids, payload, changed_by=requester_id)
            return jsonify({"status": "success", "operators": operators}), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error in sip_config bulk update: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sip_config/operators/<int:target_user_id>', methods=['PUT', 'OPTIONS'])
    @require_api_key
    def sip_config_operator_update_endpoint(target_user_id):
        """Сохранить SIP-аккаунты сотрудника: номер, персональные пароль/домен,
    второй номер для автодозвона. Номер не должен дублировать чужой."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            role = requester[3]
            if not _can_manage_sip_config(requester_id, role):
                return jsonify({"error": "Forbidden"}), 403
            target = db.get_sip_operator(target_user_id)
            if not target:
                return jsonify({"error": "Сотрудник не найден"}), 404
            if target.get('role') not in ('operator', 'trainee'):
                return jsonify({"error": "SIP-аккаунты ведём только операторам и стажёрам"}), 400
            department_ids, supervisor_id = _sip_department_scope(requester_id, role)
            if not _sip_target_in_scope(target, department_ids, supervisor_id):
                return jsonify({"error": "Сотрудник не из вашего отдела"}), 403
            payload = request.get_json(silent=True) or {}
            # Провайдер берём у отдела цели, а не из тела запроса: форма провайдера
            # не выбирает, она лишь показывает поля того подраздела, где открыта.
            if (target.get('department_provider') or 'asterisk') == 'binotel':
                binotel_error = _binotel_operator_payload_error(target, payload)
                if binotel_error:
                    return jsonify({"error": binotel_error}), 400
            # Занятость номера и SIP-логина проверяет сама save_user_sip_settings —
            # её ValueError ловит except ниже и отдаёт 400 с текстом для человека.
            operator = db.save_user_sip_settings(target_user_id, payload, changed_by=requester_id)
            return jsonify({"status": "success", "operator": operator}), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error in sip_config operator update: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sip_config/history', methods=['GET', 'OPTIONS'])
    @require_api_key
    def sip_config_history_endpoint():
        """История изменений настроек SIP (вкладка «История»). Доступ как у панели."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            role = requester[3]
            if not _can_manage_sip_config(requester_id, role):
                return jsonify({"error": "Forbidden"}), 403
            limit = request.args.get('limit', default=100, type=int)
            history = db.get_sip_config_history(limit=limit)
            department_ids, supervisor_id = _sip_department_scope(requester_id, role)
            if department_ids is not None:
                # Своя история у каждого отдела: главе Тез незачем видеть, кому и
                # когда меняли пароль в отделе продаж. Скоуп тот же, что у списка
                # сотрудников, иначе «История» показывала бы больше самой панели.
                # Уволенных берём тоже (include_inactive): их строки в истории есть.
                allowed_departments = {int(d) for d in department_ids}
                visible_user_ids = {int(o['id']) for o in db.get_sip_operators(
                    department_ids=department_ids,
                    supervisor_id=supervisor_id,
                    include_inactive=True,
                )}

                def _history_row_visible(row):
                    if row.get('target_user_id') is not None:
                        return int(row['target_user_id']) in visible_user_ids
                    if row.get('department_id') is not None:
                        return int(row['department_id']) in allowed_departments
                    # Ни сотрудника, ни отдела — строка снятого общего яруса. Это
                    # архив на всю компанию, показываем только админу.
                    return False

                # Фильтруем уже выбранные limit строк, а не запрашиваем с запасом:
                # «История» — вкладка для беглого взгляда, добор страниц ей не нужен.
                history = [r for r in history if _history_row_visible(r)]
            return jsonify({"status": "success", "history": history}), 200
        except Exception as e:
            logging.error(f"Error in sip_config history: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/sip_config/binotel/resolve_employee_ids', methods=['POST', 'OPTIONS'])
    @require_api_key
    def sip_config_binotel_resolve_employee_ids_endpoint():
        """Подтянуть из кабинета Binotel employeeID, SIP-логин и внутренний номер.

    Руководителю остаётся ввести только учётку кабинета и SIP-пароль: всё
    остальное кабинет знает сам, а переписанный руками employeeID — это молча
    не переключающийся статус. Заходим учёткой САМОГО сотрудника: общей
    админской у нас нет, и чужие записи для задачи не нужны."""
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            role = requester[3]
            if not _can_manage_sip_config(requester_id, role):
                return jsonify({"error": "Forbidden"}), 403
            if not _sip_section_allowed(requester_id, role, 'binotel'):
                return jsonify({"error": "Этот раздел телефонии не для вашего отдела"}), 403
            payload = request.get_json(silent=True) or {}
            raw_ids = payload.get('user_ids') or []
            try:
                user_ids = sorted({int(uid) for uid in raw_ids})
            except (TypeError, ValueError):
                return jsonify({"error": "Некорректный список сотрудников"}), 400
            if not user_ids:
                return jsonify({"error": "Не выбран ни один сотрудник"}), 400

            targets = {int(t['id']): t for t in db.get_sip_operators_by_ids(user_ids)}
            if len(targets) != len(user_ids):
                return jsonify({"error": "Часть сотрудников не найдена"}), 404
            department_ids, supervisor_id = _sip_department_scope(requester_id, role)
            if any(not _sip_target_in_scope(t, department_ids, supervisor_id) for t in targets.values()):
                return jsonify({"error": "В выборке есть сотрудники не из вашего отдела"}), 403
            if any((t.get('department_provider') or 'asterisk') != 'binotel' for t in targets.values()):
                return jsonify({"error": "Связка с кабинетом есть только у отделов на Binotel"}), 400

            # Форму входа держим в одном месте с ночной выгрузкой статусов: она уже
            # знает про прогрев cookie и про признак «остались на форме логина».
            from tez import status_sync as tez_status_sync

            # Кнопку «Определить автоматически» жмут в карточке ДО сохранения:
            # в binotel_user_accounts ещё пусто, а введённая рядом учётка уже есть.
            # Поэтому для одного сотрудника принимаем её из тела запроса; для
            # массового определения источником остаётся только база.
            credentials = _binotel_cabinet_credentials(user_ids)
            if len(user_ids) == 1:
                target_id = user_ids[0]
                saved_url, saved_login, saved_password = credentials.get(target_id, ('', '', ''))
                login = str(payload.get('cabinet_login') or '').strip() or saved_login
                # Пустое поле пароля означает «оставить сохранённый» — ровно как при
                # сохранении карточки, поэтому им не затираем учётку из базы.
                password = str(payload.get('cabinet_password') or '') or saved_password
                url = str(payload.get('cabinet_url') or '').strip() or saved_url
                credentials[target_id] = (url or 'https://my.binotel.kz', login, password)
            operators = []
            resolved = 0
            for user_id in user_ids:
                target = targets[user_id]
                cabinet_url, cabinet_login, cabinet_password = credentials.get(user_id, ('', '', ''))
                item = {
                    "user_id": user_id,
                    "employee_id": target.get('binotel_employee_id') or "",
                    "sip_login": target.get('sip_login') or "",
                    "sip_number": target.get('sip_number') or "",
                }
                try:
                    if not cabinet_login or not cabinet_password:
                        raise ValueError("Не заполнены логин и пароль кабинета Binotel")
                    client = tez_status_sync.BinotelClient(
                        cabinet_url, cabinet_login, cabinet_password).authenticate()
                    response = client.session.get(
                        cabinet_url.rstrip('/') + BINOTEL_EMPLOYEES_MODULE_PATH,
                        timeout=tez_status_sync.HTTP_TIMEOUT,
                        headers={"X-Requested-With": "XMLHttpRequest"},
                    )
                    response.raise_for_status()
                    record = _binotel_employee_by_email(
                        _binotel_employee_records(response.json()), cabinet_login)
                    if not record:
                        raise ValueError("В кабинете нет сотрудника с таким e-mail")
                    update = {}
                    employee_id = str(record.get('employeeID') or '').strip()
                    sip_login = str(record.get('extHash') or '').strip()
                    ext_number = str(record.get('extNumber') or '').strip()
                    # Пишем только расхождения: одинаковое значение всё равно ушло бы
                    # в ранний return, но лишняя строка в истории изменений — мусор.
                    if employee_id and employee_id != (target.get('binotel_employee_id') or ''):
                        update['binotel_employee_id'] = employee_id
                    if sip_login and sip_login != (target.get('sip_login') or ''):
                        update['sip_login'] = sip_login
                    if ext_number and ext_number != (target.get('sip_number') or ''):
                        update['sip_number'] = ext_number
                    saved = (db.save_user_sip_settings(user_id, update, changed_by=requester_id)
                             if update else target)
                    item.update({
                        "employee_id": saved.get('binotel_employee_id') or "",
                        "sip_login": saved.get('sip_login') or "",
                        "sip_number": saved.get('sip_number') or "",
                    })
                    resolved += 1
                except Exception as e:
                    # Кабинет отвечает по одному сотруднику: чужой просроченный
                    # пароль не повод терять уже прочитанные записи остальных.
                    logging.error(
                        f"Error resolving binotel employee for user {user_id}: {e}", exc_info=True)
                    # ValueError здесь — наши же проверки, они уже по-русски;
                    # всё остальное (сеть, HTTP, битый JSON) заворачиваем сами,
                    # чтобы руководителю не прилетело сообщение библиотеки.
                    item["error"] = (str(e) if isinstance(e, ValueError) and str(e)
                                     else "Не удалось прочитать кабинет Binotel: %s" % (e or "нет ответа"))
                operators.append(item)
            return jsonify({"status": "success", "resolved": resolved, "operators": operators}), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error in sip_config binotel resolve: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    return bp
