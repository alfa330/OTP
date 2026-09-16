"""HTTP-эндпоинты раздела «Ссылка на подписание» (Flask Blueprint).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль, и обратный импорт был
бы циклом (ровно как в parcels/routes.py и driver_chats/routes.py).

Соглашения те же, что у остальных роутов портала: методы всегда включают
OPTIONS и первым делом отдаётся preflight, авторизация — общий require_api_key,
ошибка — {"error": "..."} с осмысленным кодом.

Три ручки:
    GET  /ping       живость, права, дневной остаток
    POST /generate   ИИН → ссылка; каждая попытка — строка журнала
    GET  /journal    журнал с фильтрами (админы и главы отделов раздела)
"""

import logging
from datetime import datetime, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

from . import access, iin as iin_rules, queries, sapar_link, schema

# Сколько раз в сутки один человек может спросить генератор. Предохранитель
# от перебора ИИН, а не бизнес-правило: у фронт-офиса в разгар подписания
# бывает по нескольку десятков водителей в день, полторы сотни — с большим
# запасом. Опечатки в счёт не идут (schema.VENDOR_OUTCOMES). Порог виден в
# /ping, интерфейс предупреждает о нём заранее, когда остаток мал.
DAILY_LIMIT = 150

JOURNAL_PAGE_SIZE = 50
JOURNAL_MAX_PAGE_SIZE = 200

# Текст для оператора, когда генератор молчит. Без адреса и без имени вендора
# в подробностях: оператор до самого генератора добираться не должен.
UNAVAILABLE_MESSAGE = ('Сервис подписания сейчас не отвечает. '
                       'Попробуйте ещё раз через минуту.')


def build_sign_links_blueprint(*, db, require_api_key, build_cors_preflight_response,
                               resolve_requester, sensitive_access_granted,
                               client_ip=None, generate=None):
    """Собирает Blueprint раздела.

    sensitive_access_granted — (user_id) -> bool: подтверждена ли ТЕКУЩАЯ сессия
    QR-кодом. Аргумент обязательный, без значения по умолчанию: забытая
    зависимость должна уронить сборку блюпринта на старте, а не тихо открыть
    раздел всем.

    client_ip — () -> str: адрес запроса за прокси Render (X-Forwarded-For).
    generate — (iin) -> dict: клиент генератора; подменяется в тестах.
    """
    bp = Blueprint('sign_links', __name__, url_prefix='/api/sign_links')
    generate = generate or sapar_link.generate

    def _ip():
        try:
            return client_ip() if client_ip else request.remote_addr
        except Exception:  # noqa: BLE001
            return None

    def _ua():
        return (request.headers.get('User-Agent') or '')[:500]

    def sign_links_route(rule, methods=('GET',), journal=False):
        """Общий каркас роута: preflight, авторизация, контекст, гейты, ошибки.

        journal=True — роут показывает журнал: доступен глобальным админам и
        главам отделов раздела. Проверка здесь, чтобы не повторять её в каждом
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
                    # прямым адресом ?view=sign_links.
                    if not access.can_open_section(ctx):
                        return jsonify({
                            "error": "Раздел «Ссылка на подписание» вам не открыт",
                            "code": "SIGN_LINKS_SECTION_CLOSED",
                        }), 403

                    # Второй гейт — QR-подтверждение сессии. Стоит ПОСЛЕ первого:
                    # предлагать подтвердить доступ к тому, чего человеку не
                    # выдавали, — тупик, из которого он не выйдет.
                    if (access.requires_sensitive_qr(ctx)
                            and not sensitive_access_granted(ctx['user_id'])):
                        return jsonify({
                            "error": "Раздел «Ссылка на подписание» откроется после "
                                     "QR-подтверждения доступа",
                            "code": "SENSITIVE_ACCESS_REQUIRED",
                        }), 403

                    if journal and not access.can_view_journal(ctx):
                        return jsonify({
                            "error": "Журнал доступен администраторам и руководителям отделов",
                            "code": "SIGN_LINKS_JOURNAL_CLOSED",
                        }), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('sign_links: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Ссылка на подписание»",
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

    def _limits(cursor, ctx):
        used = queries.used_today(cursor, ctx['user_id'])
        return {'per_day': DAILY_LIMIT, 'used_today': used,
                'left_today': max(0, DAILY_LIMIT - used)}

    # ── Живость и права ───────────────────────────────────────────────────
    @sign_links_route('/ping')
    def sign_links_ping(ctx):
        """Живость раздела + права текущего пользователя + дневной остаток.

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
                "departments": list(access.SECTION_DEPARTMENT_CODES),
            }
            if ready:
                payload['limits'] = _limits(cursor, ctx)
        return jsonify(payload)

    # ── Генерация ссылки ──────────────────────────────────────────────────
    @sign_links_route('/generate', methods=('POST',))
    def sign_links_generate(ctx):
        """ИИН → ссылка на подписание. Каждая попытка — строка журнала.

        Порядок проверок: ИИН → дневной предел → генератор. Опечатка не
        тратит предел и не ходит к вендору; в журнал при этом пишется — вопрос
        «кто перебирал ИИН» именно про такие строки.
        """
        raw = str(_payload().get('iin') or '')
        iin, error_code = iin_rules.validate(raw)
        if error_code:
            if error_code != 'empty':
                with db._get_cursor() as cursor:
                    queries.log_request(cursor, ctx, iin=iin_rules.normalize(raw),
                                        outcome='invalid', error_text=error_code,
                                        ip_address=_ip(), user_agent=_ua())
            return jsonify({
                "error": iin_rules.error_message(error_code),
                "code": "IIN_INVALID",
                "field": "iin",
                "reason": error_code,
            }), 400

        with db._get_cursor() as cursor:
            used = queries.used_today(cursor, ctx['user_id'])
            if used >= DAILY_LIMIT:
                queries.log_request(cursor, ctx, iin=iin, outcome='limit',
                                    ip_address=_ip(), user_agent=_ua())
                return jsonify({
                    "error": "Дневной предел запросов исчерпан — новые ссылки можно "
                             "получить завтра",
                    "code": "SIGN_LINKS_DAILY_LIMIT",
                    "limits": {'per_day': DAILY_LIMIT, 'used_today': used, 'left_today': 0},
                }), 429

        # Курсор на время похода к вендору НЕ держим: генератор отвечает
        # секунды, а слот пула на это время нужен другим запросам.
        result = generate(iin)
        outcome = result.get('outcome') or sapar_link.UNAVAILABLE
        link = result.get('link') if outcome == sapar_link.LINK else None

        with db._get_cursor() as cursor:
            entry = queries.log_request(
                cursor, ctx, iin=iin, outcome=outcome,
                vendor_message=result.get('message'),
                link_issued=bool(link),
                link_host=sapar_link.link_host(link) if link else None,
                error_text=result.get('error'),
                latency_ms=result.get('latency_ms'),
                ip_address=_ip(), user_agent=_ua(),
            )
            limits = _limits(cursor, ctx)

        if outcome == sapar_link.UNAVAILABLE:
            return jsonify({
                "error": UNAVAILABLE_MESSAGE,
                "code": "SIGN_LINK_UNAVAILABLE",
                "outcome": outcome,
                "request_id": entry['id'],
                "limits": limits,
            }), 502

        return jsonify({
            "outcome": outcome,
            "iin": iin,
            # Ссылка уходит оператору ОДИН раз, в этом ответе: в базе её нет,
            # повторно её не спросить — только сгенерировать заново.
            "link": link,
            "message": result.get('message'),
            "request_id": entry['id'],
            "created_at": entry['created_at'],
            "limits": limits,
        }), 200

    # ── Журнал ────────────────────────────────────────────────────────────
    def _journal_filters(ctx):
        def _day(name):
            value = (request.args.get(name) or '').strip()
            if not value:
                return None
            try:
                return datetime.strptime(value[:10], '%Y-%m-%d')
            except ValueError:
                return None

        date_from = _day('date_from')
        date_to = _day('date_to')
        outcomes = [item.strip() for item in (request.args.get('outcomes') or '').split(',')
                    if item.strip() in schema.OUTCOMES]

        scope = access.journal_scope(ctx)
        department = (request.args.get('department') or '').strip().lower() or None
        # Глава видит только свой отдел: чужой код в фильтре не расширяет
        # границу, а сужает выборку до пустой — граница держится в SQL.
        if department and scope is not None and department not in scope:
            department = '__none__'

        return {
            'scope': scope,
            'date_from': date_from,
            # Верхняя граница — начало СЛЕДУЮЩИХ суток: иначе «по 16 сентября»
            # молча теряло бы всё, что было в этот день после полуночи.
            'date_to': (date_to + timedelta(days=1)) if date_to else None,
            'outcomes': outcomes or None,
            'user_id': _int_or_none(request.args.get('user_id')),
            'department_code': department,
            'iin': request.args.get('iin') or None,
        }

    @sign_links_route('/journal', journal=True)
    def sign_links_journal(ctx):
        filters = _journal_filters(ctx)
        page = max(1, _int_or_none(request.args.get('page')) or 1)
        size = min(JOURNAL_MAX_PAGE_SIZE,
                   max(1, _int_or_none(request.args.get('page_size')) or JOURNAL_PAGE_SIZE))
        filters['limit'] = size
        filters['offset'] = (page - 1) * size
        with db._get_cursor() as cursor:
            result = queries.journal_page(cursor, filters)
            people = queries.journal_people(cursor, scope=filters['scope'])
        return jsonify({
            **result,
            'page': page,
            'page_size': size,
            'people': people,
            'scope': filters['scope'],
        }), 200

    return bp
