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
from . import guests as wiki_guests
from . import queries, structure
from .schema import CAPABILITY_TITLES


def build_wiki_blueprint(*, db, require_api_key, build_cors_preflight_response,
                         resolve_requester, sensitive_access_granted,
                         client_ip=None, gcs=None, session_id_provider=None):
    """Собирает Blueprint раздела.

    Все зависимости приходят аргументами — импортировать их из bot_schedule2
    нельзя, там на строке 42 идёт импорт этого модуля, и вышел бы цикл.

    db                           — экземпляр Database (нужен ради _get_cursor);
    require_api_key              — декоратор аутентификации (он же require_auth);
    build_cors_preflight_response— ответ на OPTIONS;
    resolve_requester            — () -> (user_id, user_row, error),
                                   где error = (message, status_code) или None;
    sensitive_access_granted     — (user_id, cursor=...) -> bool: подтверждена
                                   ли ТЕКУЩАЯ сессия QR-кодом. Курсор отдаём
                                   свой — иначе на запрос уходило бы два слота
                                   пула. Ключ живёт в bot_schedule2
                                   (там сессии и подтверждение админом), и
                                   импортировать его сюда нельзя — цикл.
                                   Без значения по умолчанию намеренно: забытая
                                   зависимость обязана уронить сборку блюпринта
                                   на старте, а не тихо открыть раздел всем;
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

    def wiki_route(rule, methods=('GET',), capability=None, capability_from_role=False):
        """Общий декоратор роута: preflight, авторизация, контекст доступа, ошибки.

        capability — имя способности, без которой роут отдаёт 403
        (например 'can_manage_access' для управления правилами).
        Передаёт в обработчик именованный аргумент ctx с контекстом доступа.

        capability_from_role сужает проверку до способностей ДОЛЖНОСТИ: право,
        выписанное правилом на один раздел, такой роут не открывает. Ставится
        там, где действие выходит за пределы содержимого раздела — например
        назначение обязательного чтения целому отделу. Флаг именно на объявлении
        роута, а не в теле обработчика, чтобы у каждой двери было видно, про
        содержимое она или про людей.
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

                        # Субъекты считаем ЗДЕСЬ и кладём в контекст: их
                        # просит и расчёт способностей, и /ping, и периметр.
                        # Второй вывод тех же субъектов был бы вторым
                        # источником истины — на таком раздвоении исходная вика
                        # уже ломалась (см. шапку wiki/perimeter.py).
                        context['subjects'] = wiki_access.collect_subjects(
                            user_id=context['user_id'],
                            otp_role=context['otp_role'],
                            department_id=context['department_id'],
                            headed_department_ids=context['headed_department_ids'],
                            direction_id=context['direction_id'],
                            group_ids=context['group_ids'],
                            wiki_role_ids=[r.get('id') for r in context['wiki_roles']],
                        )
                        # Способности — должность и роли вики ПЛЮС то, что
                        # человеку уже выписали правилами. До 21.08.2026
                        # выписанное право записи гасло молча, потому что
                        # способность выводилась из одной лишь должности
                        # (wiki/access.py: capabilities_from_grants).
                        capabilities = queries.load_capabilities(
                            cursor, context, context['subjects'])

                        # Тумблер «раздел выдан отделу» — на бэкенде, а не
                        # только в меню: гард во фронте отсекает пункт, но не
                        # запрос, и раздел, снятый у отдела, открывался бы
                        # прямым обращением к API.
                        # Супер-админа и администратора структуры тумблер не
                        # касается: иначе, закрыв раздел своему отделу, они
                        # потеряли бы доступ к настройке самого тумблера.
                        # ГОСТЯ тумблер не касается тоже (решение владельца
                        # 25.08.2026): выдать доступ можно любому сотруднику
                        # компании, а вики выдана не каждому отделу. Иначе
                        # выдача сотруднику такого отдела молча оборачивалась бы
                        # 403 на каждом запросе — доступ есть, войти нельзя.
                        # Внутрь он попадает именно как гость: периметр ему
                        # считают те же запросы, и сверх выданного не откроется
                        # ничего.
                        if not context.get('wiki_enabled', True) and not (
                                wiki_access.normalize_role(context['otp_role']) == 'super_admin'
                                or capabilities.get('can_manage_structure')
                                or context.get('has_guest_access')):
                            return jsonify({
                                "error": "Раздел «Вики» не выдан вашему отделу",
                                "code": "WIKI_DEPARTMENT_DISABLED",
                            }), 403

                        # QR-подтверждение сессии оператора. Стоит после
                        # тумблера отдела (иначе человеку из отдела без вики
                        # предложили бы открыть то, чего у отдела нет) и до
                        # прав: подтверждение открывает прежний периметр, а не
                        # расширяет его. Проверка на сервере, а не только во
                        # фронте: экран с замком — удобство, доступом является
                        # ответ сервера.
                        if wiki_access.requires_sensitive_qr(
                                context['otp_role'],
                                is_department_head=bool(context['headed_department_ids']),
                        ) and not sensitive_access_granted(context['user_id'], cursor=cursor):
                            return jsonify({
                                "error": "Раздел «Вики» откроется после "
                                         "QR-подтверждения доступа",
                                "code": "SENSITIVE_ACCESS_REQUIRED",
                            }), 403

                        gate = (context['role_capabilities'] if capability_from_role
                                else capabilities)
                        if capability and not gate.get(capability):
                            # В тексте — КАКОГО права не хватило и откуда оно
                            # берётся. Прежнее «Недостаточно прав для этого
                            # действия» одинаково выглядело у оператора, у
                            # главы отдела и у супер-админа с ролью вики,
                            # которая молча перекрывает права должности
                            # (resolve_capabilities: непустой список ролей вики
                            # ЗАМЕЩАЕТ роль OTP, а не дополняет её). Отличить
                            # эти случаи по тосту было нельзя — только по логам.
                            return jsonify({
                                "error": "Недостаточно прав: нужно «%s»%s" % (
                                    CAPABILITY_TITLES.get(capability, capability),
                                    ' (права выданы ролью в вики: %s)' % ', '.join(
                                        r.get('code') or '?' for r in context['wiki_roles'])
                                    if context['wiki_roles'] else '',
                                ),
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
            # Отдельно — способности одной лишь должности. Разница между
            # наборами и есть ответ «что человеку добавили правилами»: без неё
            # поддержка снова читает один плоский список и не понимает, откуда
            # взялось право (тот же набор отдаёт /access/effective).
            "role_capabilities": ctx['role_capabilities'],
            "access_mode": ctx['access_mode'],
            "wiki_roles": [r.get('code') for r in ctx['wiki_roles']],
            # До какого уровня должности человек вправе открывать разделы;
            # null — не вправе вовсе. Нужен интерфейсу, чтобы показать вкладку
            # «Структура» супервайзеру: способностей can_manage_* у него нет, а
            # операторов он раздавать должен.
            "grant_ceiling": wiki_access.grant_ceiling(
                ctx['otp_role'],
                is_wiki_admin=bool(ctx['wiki_roles'])
                and bool(ctx['capabilities'].get('can_manage_access')),
            ),
            # Журнал открыт с должности СВ и выше (access.may_read_audit).
            # Признак считает сервер и отдаёт готовым по той же причине, что и
            # can_grant_guest ниже: право живёт в ДОЛЖНОСТИ, способностью не
            # выражается, а вторая формула во фронте однажды разойдётся с этой
            # — и вкладка появится у того, кому роут отвечает 403.
            "can_read_audit": wiki_access.may_read_audit(
                ctx['otp_role'],
                is_wiki_admin=bool(ctx['wiki_roles'])
                and bool(ctx['capabilities'].get('can_manage_access')),
            ),
            "subjects": ctx['subjects'],
        }
        if ready:
            # Гостевой доступ отвечает на два разных вопроса, и оба — здесь.
            #
            # guest_access — «до какого срока это открыто МНЕ». Срок обязан быть
            # виден на ЛЮБОЙ вкладке вики, а ping запрашивается на каждом заходе
            # в раздел; отдельная ручка означала бы второй запрос ради подписи в
            # шапке и вкладку, на которой подпись почему-то не появляется.
            #
            # can_grant_guest — «вправе ли я выдавать», по нему рисуется половина
            # «Гостевой доступ»: она видна супервайзеру и выше. Способностью это
            # право не выражается — оно живёт в ДОЛЖНОСТИ, отдельной лестницей
            # (access.GUEST_GRANT_CEILING), потому что «кому по чину» и «что
            # человек делает с содержимым» — разные вопросы.
            #
            # Оба вызова под try: они читают колонки, добавленные миграцией
            # 25.08.2026, а init_wiki_schema идёт одним савпоинтом — чужая
            # падающая миграция унесла бы и эти две колонки (инцидент описан в
            # wiki/schema.py про CREATE UNIQUE INDEX). Ронять на этом /ping
            # нельзя по его же смыслу: диагностика, которая сама отдаёт 500,
            # бесполезна ровно тогда, когда она нужна. Без гостевых полей раздел
            # выглядит как до 25.08.2026, а не сломанным.
            try:
                payload['guest_access'] = wiki_guests.my_active_grants(
                    cursor, ctx['user_id'])
                payload['can_grant_guest'] = wiki_access.guest_grant_ceiling(
                    ctx['otp_role'],
                    is_wiki_admin=bool(ctx['wiki_roles'])
                    and bool(ctx['capabilities'].get('can_manage_access')),
                ) is not None
            except Exception:  # noqa: BLE001 — см. комментарий выше
                import logging
                logging.exception('wiki: гостевой доступ недоступен в /ping')
                payload['guest_access'] = []
                payload['can_grant_guest'] = False
            # Пространства для переключателя — вместе с тумблерами вкладок.
            # Именно здесь, а не в /structure: набор вкладок нужен раньше, чем
            # дерево разделов, и вкладка «Помощник» не должна мигнуть у того,
            # кому её выключили. Порядок и границу считает сервер.
            allowed = set(queries.spaces_for_user(cursor, ctx))
            # Пространства, ВЫДАННЫЕ человеку, — без гостевой прибавки. Разница
            # между списками и есть «я здесь в гостях»: по ней интерфейс не
            # показывает гостю справочники и отчёты пространства, куда его
            # позвали прочитать один раздел. Сервер их и так не отдаст
            # (routes_structure._space_scope), но вкладка, которая отвечает
            # отказом, — это тот же молчаливый отказ, только наоборот.
            own = set(queries.spaces_for_user(cursor, ctx, include_guest=False))
            # Счётчики — по СВОИМ пространствам, а не по всей базе.
            #
            # Прежде здесь правился один лишь ключ 'spaces' («Пространств: 2» у
            # сотрудника Тез КЦ сообщало бы, что рядом живёт чужая вика), а
            # разделы и статьи так и считались по всей базе: плитка «Статей: 340»
            # при двенадцати своих отвечала на тот же вопрос, только числом.
            # Теперь границу знает сам запрос, и правка ключа снаружи не нужна —
            # второе место, где её надо помнить, однажды разошлось бы с первым.
            payload['counters'] = queries.counters(cursor, space_ids=sorted(allowed))
            payload['spaces'] = [
                dict({k: sp[k] for k in ('id', 'name', 'code', 'icon', 'features')},
                     guest_only=sp['id'] not in own)
                for sp in structure.list_spaces(cursor)
                if sp['id'] in allowed
            ]
        return jsonify(payload)

    @wiki_route('/me')
    def wiki_me(cursor, ctx):
        """Профиль пользователя в границах раздела: способности и периметр."""
        return jsonify({
            "user_id": ctx['user_id'],
            "otp_role": ctx['otp_role'],
            "capabilities": ctx['capabilities'],
            "role_capabilities": ctx['role_capabilities'],
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

    # Гостевой доступ — сразу за структурой: право выдавать живёт в правиле
    # раздела (wiki_section_access_rules.can_grant_guest), и читают его обе
    # стороны — «Структура» ставит тумблер, а этот модуль по нему пускает.
    from . import routes_guests
    routes_guests.register(bp, wiki_route, db, _ip)

    from . import routes_articles
    routes_articles.register(bp, wiki_route, db, _ip, gcs or {})

    from . import routes_edit
    # Возвращает общие замыкания (проверка прав на статью, право на раздел,
    # синхронизация индекса ИИ) — их переиспользует перенос из внешней вики.
    edit_helpers = routes_edit.register(bp, wiki_route, db, _ip, session_id_provider)

    from . import routes_import
    routes_import.register(bp, wiki_route, db, _ip, gcs or {})

    from . import routes_ack
    routes_ack.register(bp, wiki_route, db, _ip)

    from . import routes_analytics
    routes_analytics.register(bp, wiki_route, db, _ip)

    from . import routes_parks
    # gcs — ради логотипа парка: он ложится в тот же бакет, что картинки статей.
    routes_parks.register(bp, wiki_route, db, _ip, gcs or {})

    from . import routes_offices
    routes_offices.register(bp, wiki_route, db, _ip)

    from . import routes_ai
    routes_ai.register(bp, wiki_route, db, _ip)

    from . import routes_trainers
    routes_trainers.register(bp, wiki_route, db, _ip)

    # Перенос из внешней вики — после routes_edit: берёт у него помощники.
    from . import routes_migration
    routes_migration.register(bp, wiki_route, db, _ip, session_id_provider,
                              edit_helpers)

    # База знаний Яндекс Про — после routes_migration: делит с ним и проверку на
    # дубль, и очередь модерации. gcs — ради картинок страницы: они ложатся в
    # тот же бакет и тем же переводом в WebP, что картинки статей.
    from . import routes_yandex_pro
    routes_yandex_pro.register(bp, wiki_route, db, _ip, session_id_provider,
                               edit_helpers, gcs or {})

    return bp
