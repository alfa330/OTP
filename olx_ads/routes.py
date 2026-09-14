# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Объявления OLX» (Flask Blueprint).

Blueprint собирается фабрикой и получает зависимости аргументами, а не
импортирует bot_schedule2: тот сам подключает этот модуль, и обратный импорт был
бы циклом (ровно как в olx_amo/routes.py, wiki/routes.py и parcels/routes.py).

Ручки
-----
    GET  /api/olx_ads/ping                       — жив ли раздел, что смотрящему можно
    POST /api/olx_ads/sync                       — перечитать объявления из OLX
    GET  /api/olx_ads/adverts                    — список с фильтрами и постраничностью
    GET  /api/olx_ads/adverts/<кабинет>/<id>      — карточка объявления с историей
    POST /api/olx_ads/check                      — проверить текст правилами OLX, ничего не отправляя

    GET  /api/olx_ads/briefs                     — брифы месяца с их кабинетами
    POST /api/olx_ads/briefs                     — завести бриф (кабинеты обязательны)
    PATCH /api/olx_ads/briefs/<id>               — поправить бриф и его кабинеты
    POST /api/olx_ads/briefs/<id>/activate       — включить для его кабинетов
    POST /api/olx_ads/briefs/<id>/deactivate     — выключить

У кабинета одновременно действует не больше одного брифа; кабинет забирает бриф,
включённый последним. Ручки, которые могут перевести кабинет с чужого брифа,
возвращают `moved` — экран говорит об этом человеку словами.

    POST /api/olx_ads/generate                   — ИИ сочиняет черновики для выбранных
    GET  /api/olx_ads/drafts                     — живые черновики
    POST /api/olx_ads/drafts/<кабинет>/<id>       — переписать черновик руками
    POST /api/olx_ads/drafts/discard             — выбросить черновики

    POST /api/olx_ads/apply                      — применить в OLX пачкой
    GET  /api/olx_ads/history                    — история правок
    POST /api/olx_ads/history/<id>/rollback      — вернуть прежний текст
    GET  /api/olx_ads/runs                       — прогоны

Две границы прав, и обе проверяются ЗДЕСЬ, а не в обработчиках
--------------------------------------------------------------
`content=True` — готовить тексты (бриф, генерация, правка черновика). Это работа
маркетолога, и она не выходит за пределы портала.

`apply=True` — писать в OLX. Единственное действие раздела наружу, от лица
компании, и сразу по сотням боевых объявлений. Открыто админу и главам отделов.
"""

import logging
from datetime import date, datetime
from functools import wraps

from flask import Blueprint, jsonify as _flask_jsonify, request

from olx_amo import cabinets as olx_cabinets
from olx_amo import queries as leads_queries

from . import access, queries, schema, service, validate

log = logging.getLogger(__name__)

_MAX_PAGE = 500
_DEFAULT_PAGE = 100


def _plain(value):
    """Привести ответ к JSON: время — ISO-строкой без зоны.

    Своего JSON-провайдера в приложении нет, а Flask по умолчанию пишет datetime
    как RFC 1123 с приписанным «GMT»: «Mon, 14 Sep 2026 10:58:00 GMT». В базе же
    лежат НАСТЕННЫЕ часы Алматы, и браузер, поверив в «GMT», сдвинул бы каждую
    дату в разделе на пять часов — и время правки в истории, и срок объявления.
    «Лиды OLX» обходят это тем же `isoformat()` (`olx_amo/routes.py::_iso`).
    """
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def jsonify(*args, **kwargs):
    """`flask.jsonify`, но через `_plain`.

    Подменено на уровне модуля, а не приписано к каждой ручке: забытое в одной
    ручке преобразование и есть та ошибка, которую мы здесь закрываем, — и новая
    ручка получит правильное время, даже если автор о нём не думал.
    """
    if args and len(args) == 1 and not kwargs:
        return _flask_jsonify(_plain(args[0]))
    return _flask_jsonify(*_plain(list(args)), **_plain(kwargs))


def build_olx_ads_blueprint(*, db, require_api_key, build_cors_preflight_response,
                            resolve_requester):
    """Собирает Blueprint раздела.

    Все зависимости — обязательные keyword-only без значений по умолчанию:
    забытая зависимость должна уронить сборку блюпринта на старте, а не тихо
    открыть запись в боевые кабинеты всем подряд.
    """
    bp = Blueprint('olx_ads', __name__, url_prefix='/api/olx_ads')

    def section_route(rule, methods=('GET',), content=False, apply=False):
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
                        ctx = leads_queries.load_access_context(cursor, requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404

                    # Гейт раздела — до любого обработчика: спрятанный пункт меню
                    # доступом не является.
                    if not access.can_view(ctx):
                        return jsonify({"error": "Раздел недоступен"}), 403
                    if content and not access.can_write_content(ctx):
                        return jsonify({
                            "error": "Готовить тексты в этом разделе вам нельзя"
                        }), 403
                    if apply and not access.can_apply(ctx):
                        return jsonify({
                            "error": "Публиковать изменения в OLX может "
                                     "администратор или глава отдела"
                        }), 403

                    return handler(ctx, *args, **kwargs)
                except service.AdsError as exc:
                    return jsonify({"error": str(exc), "code": exc.code}), 400
                except Exception:
                    log.exception('Объявления OLX: обработчик %s упал',
                                  handler.__name__)
                    return jsonify({"error": "Внутренняя ошибка раздела"}), 500

            return wrapper

        return decorator

    def _body():
        return request.get_json(silent=True) or {}

    def _int(value, default=None):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _targets(raw):
        """Разобрать список выбранных объявлений из тела запроса."""
        out = []
        for item in raw or []:
            cabinet = (item.get('cabinet') or item.get('cabinet_code') or '').strip()
            advert_id = str(item.get('advert_id') or item.get('id') or '').strip()
            if cabinet and advert_id:
                out.append({'cabinet': cabinet, 'advert_id': advert_id})
        return out

    # ── состояние раздела ────────────────────────────────────────────────

    @section_route('/ping')
    def olx_ads_ping(ctx):
        with db._get_cursor() as cursor:
            ready = schema.schema_is_ready(cursor)
            facets = queries.advert_facets(cursor) if ready else {}
            # Карта «кабинет → действующий бриф»: у каждого кабинета может быть
            # свой бриф, и экрану надо знать, какой ИИ возьмёт у объявления.
            briefs_map = queries.briefs_by_cabinet(cursor) if ready else {}
        synced_at = facets.get('synced_at') if facets else None
        return jsonify({
            'ok': True,
            'schema_ready': ready,
            'capabilities': access.capabilities(ctx),
            'facets': facets,
            'briefs_by_cabinet': briefs_map,
            'snapshot_stale': service.snapshot_is_stale(synced_at),
            'limits': {
                'title_min': validate.TITLE_MIN, 'title_max': validate.TITLE_MAX,
                'description_min': validate.DESCRIPTION_MIN,
                'description_max': validate.DESCRIPTION_MAX,
                'max_bulk': service.MAX_BULK,
            },
            'cabinets': [{'code': cab.code, 'title': cab.title,
                          'phone': cab.line_phone,
                          'configured': cab.is_configured()}
                         for cab in olx_cabinets.CABINETS],
        })

    @section_route('/sync', methods=('POST',))
    def olx_ads_sync(ctx):
        """Перечитать объявления из OLX.

        Открыто всем, кто видит раздел: это ЧТЕНИЕ чужой системы, ничего в OLX
        оно не меняет, а без него список устаревает.
        """
        payload = _body()
        only = [code for code in (payload.get('cabinets') or []) if code] or None
        result = service.sync_all(db, only=only)
        with db._get_cursor() as cursor:
            result['facets'] = queries.advert_facets(cursor)
        return jsonify(result)

    # ── объявления ───────────────────────────────────────────────────────

    @section_route('/adverts')
    def olx_ads_list(ctx):
        args = request.args
        limit = min(_int(args.get('limit'), _DEFAULT_PAGE) or _DEFAULT_PAGE, _MAX_PAGE)
        offset = max(_int(args.get('offset'), 0) or 0, 0)

        has_draft = args.get('has_draft')
        has_draft = None if has_draft in (None, '') else has_draft in ('1', 'true', 'yes')

        filters = {
            'cabinet': (args.get('cabinet') or '').strip() or None,
            'status': (args.get('status') or '').strip() or None,
            'city_id': _int(args.get('city_id')),
            'category_id': _int(args.get('category_id')),
            'search': (args.get('search') or '').strip() or None,
            'has_draft': has_draft,
        }
        with db._get_cursor() as cursor:
            items = queries.list_adverts(cursor, limit=limit, offset=offset, **filters)
            total = queries.count_adverts(cursor, **filters)
        return jsonify({'items': items, 'total': total,
                        'limit': limit, 'offset': offset})

    @section_route('/adverts/<code>/<advert_id>')
    def olx_ads_card(ctx, code, advert_id):
        with db._get_cursor() as cursor:
            advert = queries.get_advert(cursor, code, advert_id)
            if not advert:
                return jsonify({'error': 'Объявление не найдено'}), 404
            draft = queries.get_draft(cursor, code, advert_id)
            history = queries.list_history(cursor, limit=50, cabinet=code,
                                           advert_id=advert_id)
        # Полный объект OLX карточке не нужен: тело PUT собирается из объявления,
        # перечитанного из OLX прямо перед записью, а не из снимка.
        advert.pop('payload', None)
        advert['problems'] = validate.check(advert.get('title'),
                                            advert.get('description'),
                                            advert.get('category_id'))
        return jsonify({'advert': advert, 'draft': draft, 'history': history})

    @section_route('/check', methods=('POST',))
    def olx_ads_check(ctx):
        """Проверить текст правилами площадки, ничего не отправляя.

        Нужна редактору: человек видит замечания, пока печатает, а не после
        того, как текст уже ушёл в чужую систему.
        """
        payload = _body()
        title = payload.get('title') or ''
        description = payload.get('description') or ''
        category_id = payload.get('category_id')
        problems = validate.check(title, description, category_id)
        plain = validate.plain_text(description)
        return jsonify({
            'problems': problems,
            'blocking': validate.blocking(problems),
            'title_length': len(title.strip()),
            'description_length': len(plain),
            'capital_share': round(validate.capital_share(plain), 3),
            'suggested_title': validate.trim_title(title),
        })

    # ── бриф месяца ──────────────────────────────────────────────────────

    @section_route('/briefs')
    def olx_ads_briefs(ctx):
        with db._get_cursor() as cursor:
            return jsonify({'items': queries.list_briefs(cursor)})

    def _cabinet_codes(raw):
        """Кабинеты брифа из тела запроса: только известные справочнику, без дублей.

        Возвращает (коды, проблема). Бриф без кабинетов не имеет смысла — ИИ не
        узнает, к каким объявлениям его применять, — поэтому пустой выбор это
        отказ со словами, а не молчаливое сохранение.
        """
        if not isinstance(raw, (list, tuple)):
            return None, 'Кабинеты брифа передаются списком'
        known = {cab.code for cab in olx_cabinets.CABINETS}
        codes = sorted({str(code).strip() for code in raw if str(code).strip()})
        unknown = [code for code in codes if code not in known]
        if unknown:
            return None, 'Неизвестные кабинеты: %s' % (', '.join(unknown),)
        if not codes:
            return None, 'Выберите хотя бы один кабинет, для которого этот бриф'
        return codes, None

    @section_route('/briefs', methods=('POST',), content=True)
    def olx_ads_brief_create(ctx):
        payload = _body()
        title = (payload.get('title') or '').strip()
        if not title:
            return jsonify({'error': 'У брифа должно быть название — например, «Сентябрь»'}), 400
        codes, problem = _cabinet_codes(payload.get('cabinets', []))
        if problem:
            return jsonify({'error': problem}), 400
        moved = []
        with db._get_cursor() as cursor:
            brief_id = queries.create_brief(cursor, payload, ctx.get('user_id'),
                                            ctx.get('name'))
            queries.set_brief_cabinets(cursor, brief_id, codes)
            if payload.get('activate'):
                moved = queries.activate_brief(cursor, brief_id) or []
            brief = queries.get_brief(cursor, brief_id)
        # `moved` — какие кабинеты перешли с других брифов: экран говорит это
        # человеку словами, иначе смена брифа у чужого кабинета прошла бы молча.
        return jsonify({'brief': brief, 'moved': moved}), 201

    @section_route('/briefs/<int:brief_id>', methods=('PATCH',), content=True)
    def olx_ads_brief_update(ctx, brief_id):
        payload = _body()
        if 'title' in payload and not (payload.get('title') or '').strip():
            return jsonify({'error': 'У брифа должно быть название'}), 400
        codes = None
        if 'cabinets' in payload:
            codes, problem = _cabinet_codes(payload.get('cabinets'))
            if problem:
                return jsonify({'error': problem}), 400
        with db._get_cursor() as cursor:
            if not queries.get_brief(cursor, brief_id):
                return jsonify({'error': 'Бриф не найден'}), 404
            queries.update_brief(cursor, brief_id, payload)
            moved = (queries.set_brief_cabinets(cursor, brief_id, codes)
                     if codes is not None else [])
            brief = queries.get_brief(cursor, brief_id)
        return jsonify({'brief': brief, 'moved': moved})

    @section_route('/briefs/<int:brief_id>/activate', methods=('POST',), content=True)
    def olx_ads_brief_activate(ctx, brief_id):
        with db._get_cursor() as cursor:
            if not queries.get_brief(cursor, brief_id):
                return jsonify({'error': 'Бриф не найден'}), 404
            moved = queries.activate_brief(cursor, brief_id)
            if moved is None:
                return jsonify({'error': 'Сначала выберите кабинеты, для которых этот бриф'}), 400
            brief = queries.get_brief(cursor, brief_id)
        return jsonify({'brief': brief, 'moved': moved})

    @section_route('/briefs/<int:brief_id>/deactivate', methods=('POST',), content=True)
    def olx_ads_brief_deactivate(ctx, brief_id):
        """Выключить бриф. Его кабинеты остаются без брифа, пока не включат другой."""
        with db._get_cursor() as cursor:
            if not queries.get_brief(cursor, brief_id):
                return jsonify({'error': 'Бриф не найден'}), 404
            queries.deactivate_brief(cursor, brief_id)
            brief = queries.get_brief(cursor, brief_id)
        return jsonify({'brief': brief})

    # ── ИИ и черновики ───────────────────────────────────────────────────

    @section_route('/generate', methods=('POST',), content=True)
    def olx_ads_generate(ctx):
        """ИИ сочиняет черновики. В OLX при этом ничего не уходит."""
        payload = _body()
        targets = _targets(payload.get('targets'))
        result = service.generate_drafts(
            db, targets, instruction=payload.get('instruction'),
            actor_id=ctx.get('user_id'), actor_name=ctx.get('name'))
        return jsonify(result)

    @section_route('/drafts')
    def olx_ads_drafts(ctx):
        with db._get_cursor() as cursor:
            return jsonify({'items': queries.list_live_drafts(cursor)})

    @section_route('/drafts/<code>/<advert_id>', methods=('POST',), content=True)
    def olx_ads_draft_save(ctx, code, advert_id):
        """Переписать черновик руками. Правка человека поверх текста ИИ — норма."""
        payload = _body()
        title = (payload.get('title') or '').strip()
        description = (payload.get('description') or '').strip()
        if not title or not description:
            return jsonify({'error': 'Нужны и заголовок, и описание'}), 400
        with db._get_cursor() as cursor:
            if not queries.get_advert(cursor, code, advert_id):
                return jsonify({'error': 'Объявление не найдено'}), 404
            draft_id = queries.upsert_draft(
                cursor, code, advert_id, title, description,
                brief_id=payload.get('brief_id'), model=None, origin='human',
                actor_id=ctx.get('user_id'), actor_name=ctx.get('name'))
            draft = queries.get_draft(cursor, code, advert_id)
        return jsonify({'draft': draft, 'draft_id': draft_id})

    @section_route('/drafts/discard', methods=('POST',), content=True)
    def olx_ads_draft_discard(ctx):
        payload = _body()
        pairs = [(item['cabinet'], item['advert_id'])
                 for item in _targets(payload.get('targets'))]
        with db._get_cursor() as cursor:
            dropped = queries.discard_drafts(cursor, pairs)
        return jsonify({'discarded': dropped})

    # ── запись в OLX ─────────────────────────────────────────────────────

    @section_route('/apply', methods=('POST',), apply=True)
    def olx_ads_apply(ctx):
        """Применить тексты в OLX.

        Берём текст из тела запроса, а если его там нет — из живого черновика.
        Второе и есть обычный путь: человек посмотрел черновики ИИ, отметил
        подходящие и нажал «Применить».
        """
        payload = _body()
        targets = _targets(payload.get('targets'))
        if not targets:
            return jsonify({'error': 'Не выбрано ни одного объявления'}), 400

        explicit = {(item.get('cabinet'), str(item.get('advert_id'))): item
                    for item in (payload.get('targets') or [])
                    if item.get('title') and item.get('description')}

        items, missing = [], []
        with db._get_cursor() as cursor:
            for target in targets:
                key = (target['cabinet'], target['advert_id'])
                given = explicit.get(key)
                if given:
                    items.append({'cabinet': target['cabinet'],
                                  'advert_id': target['advert_id'],
                                  'title': given['title'],
                                  'description': given['description']})
                    continue
                draft = queries.get_draft(cursor, target['cabinet'],
                                          target['advert_id'])
                if draft:
                    items.append({'cabinet': target['cabinet'],
                                  'advert_id': target['advert_id'],
                                  'title': draft['title'],
                                  'description': draft['description']})
                else:
                    missing.append(target)

        if not items:
            return jsonify({'error': 'Для выбранных объявлений нет готового текста: '
                                     'сначала сочините черновик или впишите текст'}), 400

        origin = 'ai' if payload.get('origin') == 'ai' else 'human'
        result = service.apply_many(db, items, actor_id=ctx.get('user_id'),
                                    actor_name=ctx.get('name'), origin=origin)
        result['skipped'] = missing
        return jsonify(result)

    # ── история ──────────────────────────────────────────────────────────

    @section_route('/history')
    def olx_ads_history(ctx):
        args = request.args
        limit = min(_int(args.get('limit'), _DEFAULT_PAGE) or _DEFAULT_PAGE, _MAX_PAGE)
        offset = max(_int(args.get('offset'), 0) or 0, 0)
        filters = {
            'cabinet': (args.get('cabinet') or '').strip() or None,
            'advert_id': (args.get('advert_id') or '').strip() or None,
            'result': (args.get('result') or '').strip() or None,
            'origin': (args.get('origin') or '').strip() or None,
            'run_id': _int(args.get('run_id')),
        }
        with db._get_cursor() as cursor:
            items = queries.list_history(cursor, limit=limit, offset=offset, **filters)
            total = queries.count_history(cursor, **filters)
        return jsonify({'items': items, 'total': total,
                        'limit': limit, 'offset': offset})

    @section_route('/history/<int:entry_id>/rollback', methods=('POST',), apply=True)
    def olx_ads_rollback(ctx, entry_id):
        result = service.rollback(db, entry_id, actor_id=ctx.get('user_id'),
                                  actor_name=ctx.get('name'))
        return jsonify(result)

    @section_route('/runs')
    def olx_ads_runs(ctx):
        with db._get_cursor() as cursor:
            return jsonify({'items': queries.list_runs(cursor, limit=50)})

    return bp
