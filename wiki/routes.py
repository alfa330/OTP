"""HTTP-эндпоинты раздела «Вики» (Flask Blueprint).

Blueprint создаётся фабрикой и получает зависимости снаружи, а не импортирует
bot_schedule2: импорт был бы циклическим (bot_schedule2 подключает этот модуль),
и пакет wiki перестал бы импортироваться в тестах.

В проекте это первый Blueprint — остальные 362 роута объявлены плоско в
bot_schedule2.py. Заводим его именно здесь, чтобы не добавлять к файлу на 48k
строк ещё семьдесят: каждый роут вики обязан иметь шапку OPTIONS, свой guard и
свой try/except (глобального @app.errorhandler в проекте нет), и в плоском виде
это нечитаемо.

Соглашения, унаследованные от остальных роутов проекта:
  * методы всегда включают 'OPTIONS', и первым делом отдаётся preflight;
  * авторизация — общий декоратор require_auth (он же require_api_key);
  * ошибки отдаются как {"error": "..."} с осмысленным кодом.
"""

from functools import wraps

from flask import Blueprint, jsonify, request

from . import access as wiki_access
from . import queries


def build_wiki_blueprint(*, db, require_api_key, build_cors_preflight_response,
                         resolve_requester, client_ip=None, gcs=None,
                         session_id_provider=None):
    """Собирает Blueprint раздела.

    Все зависимости приходят аргументами — импортировать их из bot_schedule2
    нельзя, там на строке 42 идёт импорт этого модуля, и вышел бы цикл.

    db                           — экземпляр Database (нужен ради _get_cursor);
    require_api_key              — декоратор аутентификации (он же require_auth);
    build_cors_preflight_response— ответ на OPTIONS;
    resolve_requester            — () -> (user_id, user_row, error),
                                   где error = (message, status_code) или None;
    client_ip                    — () -> str, для журнала;
    gcs                          — {'signed_url': fn, 'bucket_name': fn} для
                                   прокси файлов: подпись выдаётся на каждый
                                   запрос, а не вшивается в тело статьи;
    session_id_provider          — _current_session_id_from_access_token, чтобы
                                   версия статьи знала, из какой сессии её правили.

    Префикс ОБЯЗАН начинаться с /api: на этом завязаны CORS и оба before_request
    (hydrate_user_context_from_jwt первой строкой отсекает всё, что не /api/,
    и без него в Blueprint не будет g.user_id — любой guard вернул бы 401).
    """
    bp = Blueprint('wiki', __name__, url_prefix='/api/wiki')

    def _ip():
        try:
            return client_ip() if client_ip else None
        except Exception:
            return None

    def wiki_route(rule, methods=('GET',), capability=None):
        """Общий декоратор роута: preflight, авторизация, контекст доступа, ошибки.

        capability — имя способности, без которой роут отдаёт 403
        (например 'can_manage_access' для управления правилами).
        Передаёт в обработчик именованный аргумент ctx с контекстом доступа.
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
                        context = queries.load_access_context(cursor, requester_id)
                        if not context:
                            return jsonify({"error": "Пользователь не найден"}), 404

                        capabilities = wiki_access.resolve_capabilities(
                            context['otp_role'],
                            context['wiki_roles'],
                            is_department_head=bool(context['headed_department_ids']),
                        )
                        context['capabilities'] = capabilities

                        if capability and not capabilities.get(capability):
                            return jsonify({
                                "error": "Недостаточно прав для этого действия",
                                "code": "WIKI_FORBIDDEN",
                                "required": capability,
                            }), 403

                        return handler(*args, cursor=cursor, ctx=context, **kwargs)
                except Exception as exc:  # noqa: BLE001 — общего errorhandler в проекте нет
                    import logging
                    logging.exception('wiki: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Вики»",
                        "detail": str(exc)[:200],
                    }), 500

            return wrapper

        return decorator

    # ── Диагностика ──────────────────────────────────────────────────────
    @wiki_route('/ping')
    def wiki_ping(cursor, ctx):
        """Живость раздела + что именно видит текущий пользователь.

        schema_ready отличает «раздел ещё не разворачивали» от «раздел сломан» —
        без этого первый запуск выглядит как отказ.
        """
        ready = queries.schema_is_ready(cursor)
        payload = {
            "ok": True,
            "schema_ready": ready,
            "capabilities": ctx['capabilities'],
            "access_mode": ctx['access_mode'],
            "wiki_roles": [r.get('code') for r in ctx['wiki_roles']],
            "subjects": wiki_access.collect_subjects(
                user_id=ctx['user_id'],
                otp_role=ctx['otp_role'],
                department_id=ctx['department_id'],
                headed_department_ids=ctx['headed_department_ids'],
                direction_id=ctx['direction_id'],
                group_ids=ctx['group_ids'],
                wiki_role_ids=[r.get('id') for r in ctx['wiki_roles']],
            ),
        }
        if ready:
            payload['counters'] = queries.counters(cursor)
        return jsonify(payload)

    @wiki_route('/me')
    def wiki_me(cursor, ctx):
        """Профиль пользователя в границах раздела: способности и периметр."""
        return jsonify({
            "user_id": ctx['user_id'],
            "otp_role": ctx['otp_role'],
            "capabilities": ctx['capabilities'],
            "access_mode": ctx['access_mode'],
            "wiki_roles": ctx['wiki_roles'],
            "department_id": ctx['department_id'],
            "headed_department_ids": ctx['headed_department_ids'],
            "group_ids": ctx['group_ids'],
        })

    # Структура, доступы и статьи — отдельными модулями, чтобы этот файл остался
    # про каркас Blueprint'а, а не превратился во второй bot_schedule2.py.
    from . import routes_structure
    routes_structure.register(bp, wiki_route, db, _ip)

    from . import routes_articles
    routes_articles.register(bp, wiki_route, db, _ip, gcs or {})

    from . import routes_edit
    routes_edit.register(bp, wiki_route, db, _ip, session_id_provider)

    return bp
