# -*- coding: utf-8 -*-
"""HTTP раздела «Воронка ОП» (Flask Blueprint).

Зависимости приходят аргументами фабрики, а не импортом `bot_schedule2`: тот сам
подключает этот модуль, и обратный импорт был бы циклом (как в `cdr/routes.py`,
`wiki/routes.py`, `parcels/routes.py`).

Гейт стоит на КАЖДОМ роуте, а не только в меню: спрятанный пункт доступом не
является, раздел открывается прямым адресом. Сужение по направлению тоже здесь —
`?direction=op_osnova` подставляется руками, и без проверки супервайзер
Верификаторов прочитал бы «Основу» мимо своих табов.

Что отдают ручки
----------------
    GET  /api/op_funnel/meta          направления пользователя, права, свежесть
    GET  /api/op_funnel/overview      сводка периода, сравнение, воронка, аномалии
    GET  /api/op_funnel/operators     строки по операторам со всеми конверсиями
    GET  /api/op_funnel/days          по дням: команда и разрез по операторам
    GET  /api/op_funnel/reasons       разбивка причин
    GET  /api/op_funnel/leads         страница лидов за причиной
    POST /api/op_funnel/sync          выгрузка за период (force — перечитать)
    GET  /api/op_funnel/export        книга xlsx
    GET/POST /api/op_funnel/mapping   сопоставление операторов
    GET/POST /api/op_funnel/reasons/dict  справочник причин
    GET/POST /api/op_funnel/targets   нормы и пороги
    POST /api/op_funnel/manual/import загрузка выгрузки СРМ
    GET  /api/op_funnel/runs          журнал прогонов и расхождений

Проценты отдаются ДОЛЯМИ (0.508), а не числами 50,8: форматирование — дело
фронта, и один формат в API проще, чем два соглашения. `null` вместо нуля там,
где данных нет: «ноль процентов» и «нет данных» на экране выглядят по-разному.

Выгрузка синхронная. Длинная часть (поход в СРМ) уже произошла на `/sync`, а
сборка книги из своей базы — это секунды, при потолке waitress в 120.
"""

import io
import logging
from datetime import date, datetime, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, send_file

from . import access, metrics, queries, report, schema, sync as sync_mod
from .schema import DIRECTION_CODES, SOURCE_CRM_TICKETS, SOURCE_MANUAL

log = logging.getLogger(__name__)

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Человеческие названия направлений. Здесь, а не на фронте: то же название идёт
# в имя файла выгрузки и в журнал прогонов.
DIRECTION_TITLES = {
    'op_osnova': 'Основа',
    'op_potok': 'Поток',
    'op_yandex_reg': 'Яндекс Регистрация',
    'op_verificator': 'Верификатор',
}

# У «Потока» две базы: группы 14 и 38 работают потоки 1 и 2.
DIRECTION_STREAMS = {'op_potok': (1, 2)}

# Направления без воронки обзвона: там нагрузка и качество, а не дозвоны.
LOAD_DIRECTIONS = frozenset(('op_verificator',))

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Потолок периода на чтение. Год — это уже не «посмотреть», а выгрузка; за ней
# есть /export, который пишет лист лидов потоково.
MAX_READ_DAYS = 366

# Сколько лидов кладём в книгу. У «Потока» месяц — около 100 тысяч строк, и
# потолок листа Excel в миллион здесь ни при чём: упираешься в терпение.
MAX_EXPORT_LEADS = 200000

MAX_IMPORT_BYTES = 10 * 1024 * 1024


def build_op_funnel_blueprint(*, db, require_api_key, build_cors_preflight_response,
                              resolve_requester, excel_text_warning=None):
    """Своего пула у раздела нет: тяжёлую работу делает `/sync`, а чтение идёт по
    своей базе."""
    bp = Blueprint('op_funnel', __name__, url_prefix='/api/op_funnel')

    # ── каркас роута ─────────────────────────────────────────────────────────

    def funnel_route(rule, methods=('GET',)):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _row, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    with db._get_cursor() as cursor:
                        if not schema.schema_is_ready(cursor):
                            return jsonify({
                                "error": "Раздел «Воронка ОП» не развернулся: нет таблиц. "
                                         "Смотрите логи старта приложения.",
                                "code": "OP_FUNNEL_SCHEMA_MISSING",
                            }), 503
                        ctx = queries.load_access_context(cursor, requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if not access.can_open_section(ctx):
                        return jsonify({"error": "Раздел «Воронка ОП» вам не открыт",
                                        "code": "OP_FUNNEL_SECTION_CLOSED"}), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except _Forbidden as exc:
                    # Проверяется ДО _BadRequest: _Forbidden наследует его, и при
                    # обратном порядке отказ по правам приезжал бы как 400 —
                    # фронт показал бы «поправьте запрос» вместо «нет доступа».
                    return jsonify({"error": str(exc), "code": exc.code}), 403
                except _BadRequest as exc:
                    return jsonify({"error": str(exc), "code": exc.code}), 400
                except Exception:  # noqa: BLE001
                    log.exception('op_funnel: отказ на %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Воронка ОП»",
                                    "code": "OP_FUNNEL_FAILED"}), 500
            return wrapper
        return decorator

    # ── /meta ────────────────────────────────────────────────────────────────

    @funnel_route('/meta')
    def op_funnel_meta(ctx):
        with db._get_cursor() as cursor:
            live = set(queries.known_directions(cursor))
            allowed = [code for code in access.visible_directions(ctx) if code in live]
            # Если в портале не заведено ни одной группы направления, таб не
            # показываем: пустой экран без объяснения хуже отсутствующего.
            freshness = {}
            for code in allowed:
                runs = queries.read_runs(cursor, code, limit=1)
                pending = queries.read_operator_map(
                    cursor, sources=[sync_mod.DIRECTION_SOURCE.get(code)], only_pending=True)
                last = runs[0] if runs else {}
                freshness[code] = {
                    'last_run_at': _iso(last.get('finished_at') or last.get('started_at')),
                    'status': last.get('status') or 'never',
                    'error': last.get('error'),
                    'unmapped': len(pending),
                    'drift_rows': int(last.get('drift_rows') or 0),
                }
        return jsonify({
            'directions': [
                {'code': code,
                 'title': DIRECTION_TITLES.get(code, code),
                 'streams': list(DIRECTION_STREAMS.get(code, ())),
                 'kind': 'load' if code in LOAD_DIRECTIONS else 'funnel'}
                for code in allowed
            ],
            'capabilities': access.capabilities(ctx),
            'freshness': freshness,
            'today': date.today().isoformat(),
        })

    # ── /overview ────────────────────────────────────────────────────────────

    @funnel_route('/overview')
    def op_funnel_overview(ctx):
        direction, day_from, day_to = _scope(ctx)
        base_from, base_to = metrics.previous_period(day_from, day_to)

        with db._get_cursor() as cursor:
            rows = queries.read_daily(cursor, direction, day_from, day_to)
            base_rows = queries.read_daily(cursor, direction, base_from, base_to)
            runs = queries.read_runs(cursor, direction, limit=1)
            pending = queries.read_operator_map(
                cursor, sources=[sync_mod.DIRECTION_SOURCE.get(direction)], only_pending=True)
            targets = queries.targets_for(cursor, direction, day_to)

        summary = _sum_rows(rows)
        summary['rates'] = metrics.derive_rates(summary)
        base = _sum_rows(base_rows)
        base['rates'] = metrics.derive_rates(base)

        anomalies = metrics.detect_anomalies(
            [dict(row, work_day=row.get('work_day')) for row in rows],
            amber_from=(targets.get('amber_from') or 80) / 100.0,
        )
        names = {row.get('user_id'): row.get('operator_name') for row in rows}
        for item in anomalies:
            item['name'] = names.get(item.get('user_id')) or 'Не сопоставлен'
            item['work_day'] = _iso(item.get('work_day'))

        last = runs[0] if runs else {}
        hours_source = _dominant(rows, 'hours_source') or 'phone'

        return jsonify({
            'direction': {'code': direction, 'title': DIRECTION_TITLES.get(direction, direction),
                          'streams': list(DIRECTION_STREAMS.get(direction, ())),
                          'kind': 'load' if direction in LOAD_DIRECTIONS else 'funnel'},
            'period': {'from': day_from.isoformat(), 'to': day_to.isoformat(),
                       'days': (day_to - day_from).days + 1},
            'summary': summary,
            'compare': metrics.compare(_flat(summary), _flat(base)),
            'funnel': _funnel_steps(summary, direction),
            'anomalies': anomalies,
            'targets': targets,
            'freshness': {
                'last_run_at': _iso(last.get('finished_at') or last.get('started_at')),
                'status': last.get('status') or 'never',
                'error': last.get('error'),
                'unmapped': len(pending),
                'drift_rows': int(last.get('drift_rows') or 0),
            },
            'hours_source': hours_source,
        })

    # ── /operators ───────────────────────────────────────────────────────────

    @funnel_route('/operators')
    def op_funnel_operators(ctx):
        direction, day_from, day_to = _scope(ctx)
        base_from, base_to = metrics.previous_period(day_from, day_to)
        with db._get_cursor() as cursor:
            rows = queries.read_daily(cursor, direction, day_from, day_to)
            base_rows = queries.read_daily(cursor, direction, base_from, base_to)
            targets = queries.targets_for(cursor, direction, day_to)
            by_shift = queries.targets_by_shift(cursor, direction, day_to)
        current = _by_operator(rows, by_shift if direction in LOAD_DIRECTIONS else None)
        previous = _by_operator(base_rows)
        for item in current:
            was = next((x for x in previous if x['user_id'] == item['user_id']), None)
            item['compare'] = metrics.compare(_flat(item), _flat(was or {})) if was else {}
        return jsonify({
            'operators': current,
            'total': _sum_rows(rows) | {'rates': metrics.derive_rates(_sum_rows(rows))},
            'targets': targets,
        })

    # ── /days ────────────────────────────────────────────────────────────────

    @funnel_route('/days')
    def op_funnel_days(ctx):
        direction, day_from, day_to = _scope(ctx)
        with db._get_cursor() as cursor:
            rows = queries.read_daily(cursor, direction, day_from, day_to)
            targets = queries.targets_for(cursor, direction, day_to)

        by_day, grid = {}, {}
        for row in rows:
            day = _iso(row.get('work_day'))
            bucket = by_day.setdefault(day, _empty_sum())
            _add_row(bucket, row)
            grid.setdefault(row.get('user_id') or 0, {})[day] = _cell(row)
        days = []
        for day in sorted(by_day):
            item = by_day[day]
            item['rates'] = metrics.derive_rates(item)
            item['day'] = day
            days.append(item)
        names = {}
        for row in rows:
            names[row.get('user_id') or 0] = row.get('operator_name') or 'Не сопоставлен'
        return jsonify({
            'days': days,
            'heatmap': [{'user_id': user_id, 'name': names.get(user_id), 'cells': cells}
                        for user_id, cells in grid.items()],
            'targets': targets,
        })

    # ── /reasons ─────────────────────────────────────────────────────────────

    @funnel_route('/reasons')
    def op_funnel_reasons(ctx):
        direction, day_from, day_to = _scope(ctx)
        bucket = (request.args.get('bucket') or '').strip() or None
        user_id = request.args.get('user_id', type=int)
        with db._get_cursor() as cursor:
            rows = queries.read_reasons(cursor, direction, day_from, day_to, bucket, user_id)
            dictionary = {(row['source'], row['reason_code']): row
                          for row in queries.read_reason_dict(cursor)}
        source = sync_mod.DIRECTION_SOURCE.get(direction)
        buckets = {}
        for row in rows:
            known = dictionary.get((source, row['reason_code'])) or {}
            if known.get('is_hidden'):
                continue
            item = buckets.setdefault(row['bucket'], {'bucket': row['bucket'],
                                                      'leads': 0, 'reasons': []})
            leads = int(row.get('leads') or 0)
            item['leads'] += leads
            item['reasons'].append({
                'code': row['reason_code'],
                'title': known.get('title') or row.get('reason_title') or row['reason_code'],
                'leads': leads,
            })
        for item in buckets.values():
            item['reasons'].sort(key=lambda x: -x['leads'])
            for reason in item['reasons']:
                reason['share'] = (reason['leads'] / item['leads']) if item['leads'] else None
        return jsonify({'buckets': list(buckets.values())})

    # ── /leads ───────────────────────────────────────────────────────────────

    @funnel_route('/leads')
    def op_funnel_leads(ctx):
        direction, day_from, day_to = _scope(ctx)
        limit = min(request.args.get('limit', DEFAULT_PAGE_SIZE, type=int) or DEFAULT_PAGE_SIZE,
                    MAX_PAGE_SIZE)
        offset = max(request.args.get('offset', 0, type=int) or 0, 0)
        filters = {
            'bucket': (request.args.get('bucket') or '').strip() or None,
            'reason_code': (request.args.get('reason_code') or '').strip() or None,
            'user_id': request.args.get('user_id', type=int),
            'work_day': _date_arg('work_day', required=False),
            'reach_outcome': (request.args.get('reach_outcome') or '').strip() or None,
            'dialog_outcome': (request.args.get('dialog_outcome') or '').strip() or None,
            'stream_type': request.args.get('stream_type', type=int),
        }
        with db._get_cursor() as cursor:
            rows, total = queries.read_leads_page(cursor, direction, day_from, day_to,
                                                  filters, limit, offset)
        return jsonify({
            'leads': [_lead_out(row) for row in rows],
            'total': total, 'limit': limit, 'offset': offset,
        })

    # ── /sync ────────────────────────────────────────────────────────────────

    @funnel_route('/sync', methods=('POST',))
    def op_funnel_sync(ctx):
        if not access.can_sync(ctx):
            return jsonify({"error": "Обновлять данные вам не разрешено",
                            "code": "OP_FUNNEL_SYNC_FORBIDDEN"}), 403
        payload = request.get_json(silent=True) or {}
        direction = _direction_arg(ctx, payload.get('direction'))
        day_from = _parse_date(payload.get('from'), 'from')
        day_to = _parse_date(payload.get('to'), 'to')
        force = bool(payload.get('force'))
        if force and not access.can_edit_targets(ctx):
            # Перечитывание зафиксированных суток меняет уже названные цифры —
            # это решение руководителя, а не рядовое обновление.
            return jsonify({"error": "Перечитать зафиксированные сутки может только "
                                     "руководитель отдела",
                            "code": "OP_FUNNEL_FORCE_FORBIDDEN"}), 403
        try:
            result = sync_mod.sync_direction(db, direction, day_from, day_to, force=force,
                                             started_by=ctx.get('user_id'))
        except sync_mod.SyncError as exc:
            # Это отказ АРГУМЕНТА («период больше 62 суток», «конец раньше
            # начала»), а не поломка. Голая пятисотка превращала внятный текст в
            # «Внутренняя ошибка раздела», и человек не понимал, что поправить.
            raise _BadRequest(str(exc), 'OP_FUNNEL_BAD_SYNC_PERIOD')
        status = 200 if result.get('status') == 'ok' else 502
        return jsonify(result), status

    # ── /export ──────────────────────────────────────────────────────────────

    @funnel_route('/export')
    def op_funnel_export(ctx):
        if not access.can_export(ctx):
            return jsonify({"error": "Выгрузка вам не разрешена",
                            "code": "OP_FUNNEL_EXPORT_FORBIDDEN"}), 403
        direction, day_from, day_to = _scope(ctx)
        base_from, base_to = metrics.previous_period(day_from, day_to)
        with db._get_cursor() as cursor:
            rows = queries.read_daily(cursor, direction, day_from, day_to)
            base_rows = queries.read_daily(cursor, direction, base_from, base_to)
            reasons = queries.read_reasons(cursor, direction, day_from, day_to)
            targets = queries.targets_for(cursor, direction, day_to)
            runs = queries.read_runs(cursor, direction, limit=1)
            leads, leads_total = queries.read_leads_page(
                cursor, direction, day_from, day_to, {}, MAX_EXPORT_LEADS, 0)

        summary = _sum_rows(rows)
        summary['rates'] = metrics.derive_rates(summary)
        base = _sum_rows(base_rows)
        base['rates'] = metrics.derive_rates(base)
        last = runs[0] if runs else {}

        stream, _written = report.build_workbook(
            leads,
            direction={'code': direction, 'title': DIRECTION_TITLES.get(direction, direction),
                       'streams': list(DIRECTION_STREAMS.get(direction, ()))},
            period_from=day_from, period_to=day_to,
            summary=summary,
            operators=_by_operator(rows),
            days=_by_day(rows),
            reasons=reasons,
            targets=targets,
            freshness={'last_run_at': _iso(last.get('finished_at')),
                       'status': last.get('status') or 'never',
                       'unmapped': 0, 'drift_rows': int(last.get('drift_rows') or 0)},
            hours_source=_dominant(rows, 'hours_source') or 'phone',
            compare=metrics.compare(_flat(summary), _flat(base)),
            generated_by=ctx.get('name') or '',
            leads_total=leads_total,
            text_warning_patch=excel_text_warning,
        )
        return send_file(stream, mimetype=XLSX_MIME, as_attachment=True,
                         download_name=report.report_filename(
                             {'code': direction, 'title': DIRECTION_TITLES.get(direction)},
                             day_from, day_to))

    # ── /mapping ─────────────────────────────────────────────────────────────

    @funnel_route('/mapping', methods=('GET', 'POST'))
    def op_funnel_mapping(ctx):
        if not access.can_map_operators(ctx):
            return jsonify({"error": "Сопоставлять операторов вам не разрешено",
                            "code": "OP_FUNNEL_MAPPING_FORBIDDEN"}), 403
        if request.method == 'GET':
            allowed = access.visible_directions(ctx)
            if not allowed:
                # Без видимых направлений отдаём пустую карту, а не «всё»:
                # иначе фильтр по источникам исчезал и человек видел чужое.
                return jsonify({'mapping': [], 'people': []})
            wanted = {sync_mod.DIRECTION_SOURCE.get(code) for code in allowed}
            # Источники, не привязанные к одному направлению: ручная загрузка и
            # обращения СРМ. Без них строки этих двух источников не показывались
            # НИКОМУ, и связать имя из файла супервайзера было нечем.
            wanted.update((SOURCE_MANUAL, SOURCE_CRM_TICKETS))
            with db._get_cursor() as cursor:
                rows = queries.read_operator_map(cursor,
                                                 sources=sorted(s for s in wanted if s))
                people = _sales_people(cursor)
            return jsonify({'mapping': rows, 'people': people})

        payload = request.get_json(silent=True) or {}
        source = (payload.get('source') or '').strip()
        external_key = (payload.get('external_key') or '').strip()
        if not source or not external_key:
            raise _BadRequest('Не указан источник или ключ оператора', 'OP_FUNNEL_BAD_MAPPING')

        # Источник принадлежит направлению, и править чужой нельзя: без этой
        # проверки СВ «Верификатора» переписывал бы сопоставление «Основы» —
        # доступ к самой ручке есть у всех, кто вправе сопоставлять.
        allowed_sources = {sync_mod.DIRECTION_SOURCE.get(code)
                           for code in access.visible_directions(ctx)}
        allowed_sources.update((SOURCE_MANUAL, SOURCE_CRM_TICKETS))
        if source not in allowed_sources:
            raise _Forbidden('Этот источник относится к направлению, которое вам не открыто')
        user_id = payload.get('user_id')
        is_ignored = bool(payload.get('is_ignored'))
        with db._get_cursor() as cursor:
            changed = queries.set_operator_map(cursor, source, external_key,
                                               int(user_id) if user_id else None,
                                               ctx.get('user_id'), is_ignored)
        if not changed:
            return jsonify({"error": "Такой строки сопоставления нет",
                            "code": "OP_FUNNEL_MAPPING_MISSING"}), 404
        return jsonify({'ok': True, 'note': 'Сопоставление применится к новым выгрузкам; '
                                            'чтобы пересчитать прошлое, перечитайте период.'})

    # ── /reasons/dict ────────────────────────────────────────────────────────

    @funnel_route('/reasons/dict', methods=('GET', 'POST'))
    def op_funnel_reason_dict(ctx):
        if request.method == 'GET':
            source = (request.args.get('source') or '').strip() or None
            with db._get_cursor() as cursor:
                return jsonify({'reasons': queries.read_reason_dict(cursor, source)})
        if not access.can_edit_targets(ctx):
            return jsonify({"error": "Править справочник причин может только руководитель отдела",
                            "code": "OP_FUNNEL_DICT_FORBIDDEN"}), 403
        payload = request.get_json(silent=True) or {}
        source = (payload.get('source') or '').strip()
        code = (payload.get('reason_code') or '').strip()
        if not source or not code:
            raise _BadRequest('Не указан источник или код причины', 'OP_FUNNEL_BAD_REASON')
        with db._get_cursor() as cursor:
            changed = queries.set_reason_bucket(
                cursor, source, code, (payload.get('bucket') or '').strip(),
                payload.get('title'), ctx.get('user_id'), payload.get('is_hidden'))
        if not changed:
            return jsonify({"error": "Такой причины в справочнике нет",
                            "code": "OP_FUNNEL_REASON_MISSING"}), 404
        return jsonify({'ok': True, 'note': 'Новая корзина применится к следующим выгрузкам.'})

    # ── /targets ─────────────────────────────────────────────────────────────

    @funnel_route('/targets', methods=('GET', 'POST'))
    def op_funnel_targets(ctx):
        if request.method == 'GET':
            direction = _direction_arg(ctx, request.args.get('direction'))
            with db._get_cursor() as cursor:
                return jsonify({'targets': [_target_out(row)
                                            for row in queries.read_targets(cursor, direction)],
                                'can_edit': access.can_edit_targets(ctx)})
        if not access.can_edit_targets(ctx):
            return jsonify({"error": "Менять нормы может только руководитель отдела: "
                                     "норма — это обязательство, а не настройка экрана",
                            "code": "OP_FUNNEL_TARGETS_FORBIDDEN"}), 403
        payload = request.get_json(silent=True) or {}
        direction = _direction_arg(ctx, payload.get('direction'))
        metric = (payload.get('metric') or '').strip()
        if not metric:
            raise _BadRequest('Не указан показатель', 'OP_FUNNEL_BAD_TARGET')
        try:
            value = float(payload.get('value'))
        except (TypeError, ValueError):
            raise _BadRequest('Значение нормы должно быть числом', 'OP_FUNNEL_BAD_TARGET')
        shift_kind = (payload.get('shift_kind') or 'any').strip() or 'any'
        effective_from = _parse_date(payload.get('effective_from') or date.today().isoformat(),
                                     'effective_from')
        with db._get_cursor() as cursor:
            queries.set_target(cursor, direction, shift_kind, metric, value,
                               effective_from, ctx.get('user_id'))
        return jsonify({'ok': True,
                        'note': 'Норма действует с указанной даты; посчитанное раньше '
                                'не меняется.'})

    # ── /manual/import ───────────────────────────────────────────────────────

    @funnel_route('/manual/import', methods=('POST',))
    def op_funnel_manual_import(ctx):
        if not access.can_import_manual(ctx):
            return jsonify({"error": "Загружать выгрузку вам не разрешено",
                            "code": "OP_FUNNEL_IMPORT_FORBIDDEN"}), 403
        direction = _direction_arg(ctx, request.form.get('direction'))
        uploaded = request.files.get('file')
        if uploaded is None:
            raise _BadRequest('Файл не приложен', 'OP_FUNNEL_NO_FILE')
        blob = uploaded.read()
        if len(blob) > MAX_IMPORT_BYTES:
            raise _BadRequest('Файл больше 10 МБ', 'OP_FUNNEL_FILE_TOO_BIG')

        from .manual_import import parse_manual_workbook  # локально: тянет openpyxl

        with db._get_cursor() as cursor:
            people = _sales_people(cursor, direction)
            # Подтверждённые человеком написания имён: в файле встречаются
            # «Аман Алан» при «Алан Аман Нурбекұлы» в портале и «Саркытова»
            # против «Сарқытова». Угадывать такое нельзя, а один раз связать —
            # можно, и дальше загрузка идёт молча.
            confirmed = {
                _lower(row.get('external_key')): row.get('user_id')
                for row in queries.read_operator_map(cursor, sources=['manual'])
                if row.get('user_id')
            }
            parsed = parse_manual_workbook(blob, direction, people,
                                           name_to_user=confirmed)
            if parsed['unmapped']:
                # Неузнанные имена заводим в таблице сопоставления, чтобы человек
                # связал их на вкладке «Настройки», а не искал глазами в файле.
                queries.touch_operator_map(
                    cursor, 'manual',
                    {item['name']: item['name'] for item in parsed['unmapped']},
                    direction)
            if parsed['rows']:
                import_id = queries.record_import(
                    cursor, direction, uploaded.filename or '',
                    parsed['period_from'], parsed['period_to'],
                    parsed['rows_read'], len(parsed['rows']), parsed['rows_skipped'],
                    parsed['unmapped'], ctx.get('user_id'))
                queries.upsert_manual_rows(cursor, parsed['rows'], import_id)
            else:
                import_id = None
        return jsonify({
            'ok': bool(parsed['rows']),
            'import_id': import_id,
            'rows_read': parsed['rows_read'],
            'rows_written': len(parsed['rows']),
            'rows_skipped': parsed['rows_skipped'],
            'unmapped': parsed['unmapped'],
            'period': {'from': _iso(parsed['period_from']), 'to': _iso(parsed['period_to'])},
            'note': 'Числа появятся в таблице после обновления данных за этот период.',
        })

    # ── /runs ────────────────────────────────────────────────────────────────

    @funnel_route('/runs')
    def op_funnel_runs(ctx):
        direction = _direction_arg(ctx, request.args.get('direction'))
        with db._get_cursor() as cursor:
            runs = queries.read_runs(cursor, direction, limit=20)
            drift = queries.read_drift(cursor, direction, limit=100)
            imports = queries.read_imports(cursor, direction, limit=10)
        return jsonify({
            'runs': [_run_out(row) for row in runs],
            'drift': [_drift_out(row) for row in drift],
            'imports': [_import_out(row) for row in imports],
        })

    # ── разбор аргументов ────────────────────────────────────────────────────

    def _scope(ctx):
        direction = _direction_arg(ctx, request.args.get('direction'))
        day_to = _date_arg('to') or date.today()
        day_from = _date_arg('from') or day_to
        if day_from > day_to:
            raise _BadRequest('Начало периода позже конца', 'OP_FUNNEL_BAD_PERIOD')
        if (day_to - day_from).days + 1 > MAX_READ_DAYS:
            raise _BadRequest('Период больше года — выберите отрезок короче',
                              'OP_FUNNEL_PERIOD_TOO_LONG')
        return direction, day_from, day_to

    def _direction_arg(ctx, value):
        code = (value or '').strip()
        if not code:
            allowed = access.visible_directions(ctx)
            if not allowed:
                raise _BadRequest('У вас нет доступных направлений', 'OP_FUNNEL_NO_DIRECTIONS')
            return allowed[0]
        if code not in DIRECTION_CODES:
            raise _BadRequest('Неизвестное направление', 'OP_FUNNEL_BAD_DIRECTION')
        if not access.can_see_direction(ctx, code):
            # 403, а не 404: человек существует, направление существует, прав нет.
            raise _Forbidden('Это направление вам не открыто')
        return code

    return bp


# ── вспомогательное (вне фабрики: чистые функции) ────────────────────────────

class _BadRequest(ValueError):
    def __init__(self, message, code='OP_FUNNEL_BAD_REQUEST'):
        super().__init__(message)
        self.code = code


class _Forbidden(_BadRequest):
    """403 в оболочке 400-го обработчика: текст важнее кода, а код всё равно
    машинный. Отдельный класс нужен, чтобы не спутать с ошибкой разбора."""

    def __init__(self, message):
        super().__init__(message, 'OP_FUNNEL_DIRECTION_FORBIDDEN')


def _parse_date(value, name):
    if value in (None, ''):
        raise _BadRequest('Не указана дата «%s»' % name, 'OP_FUNNEL_BAD_PERIOD')
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        raise _BadRequest('Дата «%s» должна быть в виде ГГГГ-ММ-ДД' % name,
                          'OP_FUNNEL_BAD_PERIOD')


def _date_arg(name, required=False):
    value = (request.args.get(name) or '').strip()
    if not value:
        if required:
            raise _BadRequest('Не указана дата «%s»' % name, 'OP_FUNNEL_BAD_PERIOD')
        return None
    return _parse_date(value, name)


def _lower(value):
    """Ключ сравнения имени: без регистра и без двойных пробелов. Тот же, что в
    `manual_import._norm` — разойтись им нельзя, иначе подтверждённое человеком
    соответствие перестанет находиться."""
    return ' '.join(str(value or '').strip().lower().split())


def _iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=' ', timespec='seconds')
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


_SUM_FIELDS = ('handled', 'reached', 'not_reached', 'agreed', 'succeeded', 'rejected',
               'untargeted', 'callbacks', 'inbound', 'chats', 'tickets')
_SUM_FLOATS = ('work_hours', 'plan_reached', 'plan_agreed')


def _empty_sum():
    out = {name: 0 for name in _SUM_FIELDS}
    out.update({name: 0.0 for name in _SUM_FLOATS})
    out['operators'] = 0
    out['_reply'] = []
    out['_ticket'] = []
    return out


def _add_row(bucket, row):
    for name in _SUM_FIELDS:
        bucket[name] += int(row.get(name) or 0)
    for name in _SUM_FLOATS:
        bucket[name] += float(row.get(name) or 0)
    if row.get('chat_reply_seconds') is not None:
        bucket['_reply'].append(float(row['chat_reply_seconds']))
    if row.get('ticket_handle_seconds') is not None:
        bucket['_ticket'].append(float(row['ticket_handle_seconds']))


def _finish_sum(bucket, people):
    bucket['operators'] = len(people)
    for name in _SUM_FLOATS:
        bucket[name] = round(bucket[name], 2)
    # Среднее время ответа — среднее по суткам операторов, а не по всем чатам:
    # ровно так это считает супервайзер в своём файле (AVERAGE по строкам дня).
    bucket['chat_reply_seconds'] = (round(sum(bucket['_reply']) / len(bucket['_reply']), 1)
                                    if bucket['_reply'] else None)
    bucket['ticket_handle_seconds'] = (round(sum(bucket['_ticket']) / len(bucket['_ticket']), 1)
                                       if bucket['_ticket'] else None)
    bucket.pop('_reply', None)
    bucket.pop('_ticket', None)
    return bucket


def _sum_rows(rows):
    bucket = _empty_sum()
    people = set()
    for row in rows:
        _add_row(bucket, row)
        people.add(row.get('user_id'))
    return _finish_sum(bucket, people)


def _by_operator(rows, targets_by_shift=None):
    """Строки по операторам за период.

    `targets_by_shift` передаётся у направлений без воронки («Верификатор»):
    каждого оператора надо сравнивать с таргетом ЕГО смены, иначе ночная смена
    красится в красное там, где она в норме (её норма вдвое мягче).
    """
    grouped = {}
    for row in rows:
        user_id = row.get('user_id') or 0
        item = grouped.get(user_id)
        if item is None:
            item = grouped[user_id] = _empty_sum()
            item['user_id'] = user_id
            item['name'] = row.get('operator_name') or 'Не сопоставлен'
            item['rate'] = float(row.get('rate') or 0)
            item['group_id'] = row.get('group_id')
            item['shift_kind'] = row.get('shift_kind') or 'day'
            item['hours_source'] = row.get('hours_source') or 'phone'
            item['hire_date'] = _iso(row.get('hire_date'))
            item['days'] = 0
        _add_row(item, row)
        item['days'] += 1
        if row.get('hours_source') == 'schedule':
            item['hours_source'] = 'schedule'
    out = []
    for item in grouped.values():
        _finish_sum(item, {item['user_id']})
        item['operators'] = 1
        item['rates'] = metrics.derive_rates(item)
        if targets_by_shift:
            shift = item.get('shift_kind') or 'day'
            item['rates'].update(
                metrics.verificator_rates(item, targets_by_shift.get(shift) or {}))
        out.append(item)
    # Сначала сопоставленные по алфавиту, «Не сопоставлен» последним: он не
    # человек, а сигнал о пробеле.
    out.sort(key=lambda x: (x['user_id'] == 0, (x.get('name') or '').lower()))
    return out


def _by_day(rows):
    grouped = {}
    for row in rows:
        day = row.get('work_day')
        item = grouped.get(day)
        if item is None:
            item = grouped[day] = _empty_sum()
            item['day'] = _iso(day)
            item['_people'] = set()
        _add_row(item, row)
        item['_people'].add(row.get('user_id'))
    out = []
    for day in sorted(grouped):
        item = grouped[day]
        people = item.pop('_people')
        _finish_sum(item, people)
        item['rates'] = metrics.derive_rates(item)
        out.append(item)
    return out


def _cell(row):
    rate = None
    plan = float(row.get('plan_reached') or 0)
    if plan > 0:
        rate = float(row.get('reached') or 0) / plan
    return {
        'handled': int(row.get('handled') or 0),
        'reached': int(row.get('reached') or 0),
        'agreed': int(row.get('agreed') or 0),
        'hours': float(row.get('work_hours') or 0),
        'plan_rate': rate,
    }


def _flat(row):
    """Плоский вид для `metrics.compare`: конверсии лежат во вложенном `rates`."""
    out = {key: value for key, value in (row or {}).items() if not isinstance(value, (dict, list))}
    out.update((row or {}).get('rates') or {})
    return out


def _funnel_steps(summary, direction):
    """Шаги воронки с процентом перехода. У направлений без обзвона шагов нет —
    там нагрузка, и рисовать воронку из двух одинаковых полос было бы шумом."""
    if direction in LOAD_DIRECTIONS:
        return []
    steps = [
        ('handled', 'Обработано', summary.get('handled')),
        ('reached', 'Дозвон', summary.get('reached')),
        ('agreed', 'Согласия', summary.get('agreed')),
        ('succeeded', 'Успешно', summary.get('succeeded')),
    ]
    out, previous = [], None
    for key, title, value in steps:
        value = int(value or 0)
        rate = (value / previous) if (previous not in (None, 0)) else None
        out.append({'key': key, 'title': title, 'value': value, 'step_rate': rate})
        previous = value
    return out


def _dominant(rows, field):
    counter = {}
    for row in rows:
        value = row.get(field)
        if value:
            counter[value] = counter.get(value, 0) + 1
    if not counter:
        return None
    return max(counter.items(), key=lambda item: item[1])[0]


def _lead_out(row):
    return {
        'lead_key': row.get('lead_key'),
        'work_day': _iso(row.get('work_day')),
        'user_id': row.get('user_id'),
        'operator': row.get('operator_name') or row.get('owner_raw') or '',
        'full_name': row.get('full_name') or '',
        'phone': row.get('phone') or '',
        'park_name': row.get('park_name') or '',
        'city': row.get('city') or '',
        'base_title': row.get('base_title') or '',
        'stage': row.get('stage_raw') or '',
        'call_status': row.get('call_status') or '',
        'dialog_status': row.get('dialog_status') or '',
        'reason': row.get('reason_raw') or '',
        'reason_code': row.get('reason_code') or '',
        'bucket': row.get('reason_bucket') or '',
        'comment': row.get('comment') or '',
        'stream_type': row.get('stream_type'),
        'taken_at': _iso(row.get('taken_at')),
        'updated_at': _iso(row.get('updated_at')),
    }


def _target_out(row):
    return {
        'direction_code': row.get('direction_code'),
        'shift_kind': row.get('shift_kind'),
        'metric': row.get('metric'),
        'effective_from': _iso(row.get('effective_from')),
        'value': float(row.get('value') or 0),
        'updated_at': _iso(row.get('updated_at')),
    }


def _run_out(row):
    return {
        'id': row.get('id'),
        'source': row.get('source'),
        'period': {'from': _iso(row.get('period_from')), 'to': _iso(row.get('period_to'))},
        'started_at': _iso(row.get('started_at')),
        'finished_at': _iso(row.get('finished_at')),
        'status': row.get('status'),
        'leads_seen': row.get('leads_seen'),
        'leads_written': row.get('leads_written'),
        'days_frozen': row.get('days_frozen'),
        'days_redone': row.get('days_redone'),
        'unmapped': row.get('unmapped'),
        'drift_rows': row.get('drift_rows'),
        'error': row.get('error'),
        'started_by': row.get('started_by_name') or '',
        'note': row.get('note') or '',
    }


def _drift_out(row):
    return {
        'work_day': _iso(row.get('work_day')),
        'user_id': row.get('user_id'),
        'operator': row.get('operator_name') or 'Не сопоставлен',
        'metric': row.get('metric'),
        'was': float(row.get('was') or 0),
        'became': float(row.get('became') or 0),
        'noticed_at': _iso(row.get('noticed_at')),
    }


def _import_out(row):
    return {
        'id': row.get('id'),
        'file_name': row.get('file_name'),
        'period': {'from': _iso(row.get('period_from')), 'to': _iso(row.get('period_to'))},
        'rows_read': row.get('rows_read'),
        'rows_written': row.get('rows_written'),
        'rows_skipped': row.get('rows_skipped'),
        'unmapped': row.get('unmapped') or [],
        'uploaded_at': _iso(row.get('uploaded_at')),
        'uploaded_by': row.get('uploaded_by_name') or '',
    }


def _sales_people(cursor, direction_code=None):
    """Сотрудники отдела продаж для выбора в сопоставлении.

    Берём весь отдел, а не только направление: человека переводят между
    направлениями, и в момент сопоставления он может числиться уже в другом.
    """
    cursor.execute(
        """
        SELECT u.id, u.name, u.status, dir.calculation_model_code AS direction_code
        FROM users u
        LEFT JOIN directions dir ON dir.id = u.direction_id
        LEFT JOIN departments d ON d.id = u.department_id
        WHERE d.code = 'op'
        ORDER BY u.name
        """
    )
    columns = [column[0] for column in cursor.description]
    out = []
    for raw in cursor.fetchall():
        row = raw if isinstance(raw, dict) else dict(zip(columns, raw))
        out.append({
            'id': row.get('id'),
            'name': row.get('name') or '',
            'direction_code': row.get('direction_code') or '',
            'is_fired': (row.get('status') or '').strip().lower() in ('fired', 'уволен'),
        })
    return out
