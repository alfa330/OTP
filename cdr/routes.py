# -*- coding: utf-8 -*-
"""HTTP раздела «Касания» (Flask Blueprint).

Зависимости приходят аргументами фабрики, а не импортом bot_schedule2: тот сам
подключает этот модуль, и обратный импорт был бы циклом (как в wiki/routes.py,
crm/routes.py и parcels/routes.py).

ПОРТАЛ К СТАНЦИИ НЕ ХОДИТ. Станция стоит в корпоративной сети; 25.08.2026 её
вывели наружу через прокси с basic-auth, в тот же день сервис лёг, и доступ
закрыли. Поэтому за данными ходит мост внутри сети (`cdr_bridge/`), а здесь
только два вида ручек:

    /api/cdr/*         разделу — читают нашу базу, наружу не ходят никогда
    /api/cdr/agent/*   мосту — закрыты общим токеном CDR_AGENT_TOKEN

Ручки моста намеренно БЕЗ `require_api_key`: у моста нет ни cookie портала, ни
JWT. Тот же приём, что у `/api/phone/publish` и `/api/oktell_guard/publish` —
декоратор не вешается, а токен проверяется первой строкой тела через
`hmac.compare_digest` (обычное `==` утекает токен по времени).

Как это выглядит для человека
-----------------------------
Раздел не «строит отчёт по кнопке» и не заводит таблицу заданий. Единица работы —
сутки, и состояние суток само по себе является прогрессом:

    GET  /api/cdr/period   что есть за период + страница таблицы + сводка;
                           недостающие сутки ставит в очередь мосту
    POST /api/cdr/sync     то же явно, с force для перечитывания закрытых суток
    GET  /api/cdr/stats    разрезы по операторам и по дням
    GET  /api/cdr/export   xlsx за период
    POST /api/cdr/directory/refresh   пересобрать справочник номеров

Фронт опрашивает /period, пока `coverage.pending` не станет нулём. Перезапуск
чего угодно — портала, моста, VM — ничего не ломает: незакрытые сутки вернутся в
очередь по возрасту отметки о взятии.

Выгрузка синхронная, в отличие от «Провайдера ЭДО»: там минуты уходили на обход
чужого кабинета, а здесь длинная часть уже произошла, и сборка книги из базы —
это секунды (30 тысяч строк за 4,4 с, замерено 25.08.2026, при потолке waitress
в 120 секунд).
"""

import hmac
import logging
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, request, send_file

from . import (access, config, directory as directory_mod, queries, report, schema,
               sync, touches as touches_mod)

log = logging.getLogger(__name__)

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Страница таблицы. Пятьдесят — как в реестре посылок; больше на экран всё равно
# не помещается, а каждая строка тянет ссылку на запись.
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Сколько суток мост забирает за один заход. По одним: сутки читаются секунды, а
# отдавать их надо сразу — иначе прогресс в разделе стоит на месте всю пачку.
AGENT_JOBS_PER_POLL = 1

# Касаний в одних сутках у отдела продаж ~3,4 тысячи (замерено). Сорок тысяч —
# это десятикратный запас; больше похоже не на сутки, а на ошибку моста.
MAX_TOUCHES_PER_DAY = 40000

# Сколько молчания считаем обрывом. Мост здоровается не реже раза в минуту, так
# что пять минут — это пять пропущенных ударов, а не «он просто задумался».
BRIDGE_SILENT_MINUTES = 5

# Сколько строк отдаём в файл максимум. Квартал ОП — это ~310 тысяч касаний;
# потолок листа Excel миллион, так что упереться можно только в терпение.
MAX_EXPORT_ROWS = 500000

# Насколько долго справочник номеров считается свежим.
DIRECTORY_TTL_HOURS = 12


def build_cdr_blueprint(*, db, require_api_key, build_cors_preflight_response,
                        resolve_requester, excel_text_warning=None):
    """Своего пула у раздела нет и не нужно: тяжёлую работу делает мост внутри
    корпоративной сети, а портал только читает свою базу и собирает книгу."""
    bp = Blueprint('cdr', __name__, url_prefix='/api/cdr')

    # ── каркас пользовательского роута ───────────────────────────────────────

    def cdr_route(rule, methods=('GET',)):
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
                                "error": "Раздел «Касания» не развернулся: нет таблиц. "
                                         "Смотрите логи старта приложения.",
                                "code": "CDR_SCHEMA_MISSING",
                            }), 503
                        ctx = queries.load_access_context(cursor, requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    # Гейт здесь, а не в обработчиках: спрятанный пункт меню
                    # доступом не является, раздел открывается и прямым адресом.
                    if not access.can_open_section(ctx):
                        return jsonify({"error": "Раздел «Касания» вам не открыт",
                                        "code": "CDR_SECTION_CLOSED"}), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
                except Exception as exc:  # noqa: BLE001
                    logging.exception('Касания: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Касания»",
                                    "detail": str(exc)[:200]}), 500
            return wrapper
        return decorator

    # ── каркас роута моста ───────────────────────────────────────────────────

    def agent_route(rule):
        """Без require_api_key: у моста нет ни cookie портала, ни JWT.

        Токен сверяется compare_digest'ом — обычное сравнение строк утекает его
        по времени. Не настроен на сервере (503) и неверен (401) — разные коды:
        мост должен отличать «портал ещё не готов» от «ключ протух».
        """
        def decorator(handler):
            @bp.route(rule, methods=['POST', 'OPTIONS'], endpoint='agent_' + handler.__name__)
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                expected = config.agent_token()
                if not expected:
                    return jsonify({"error": "Мост не настроен на сервере: не задан "
                                             "CDR_AGENT_TOKEN"}), 503
                provided = (request.headers.get('X-Agent-Token') or '').strip()
                if not provided or not _same_token(provided, expected):
                    return jsonify({"error": "Мост не авторизован"}), 401
                try:
                    payload = request.get_json(silent=True) or {}
                    return handler(payload, *args, **kwargs)
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
                except Exception as exc:  # noqa: BLE001
                    logging.exception('Касания: ошибка в ручке моста %s', rule)
                    return jsonify({"error": "Внутренняя ошибка",
                                    "detail": str(exc)[:200]}), 500
            return wrapper
        return decorator

    # ── разбор параметров ────────────────────────────────────────────────────

    def _period():
        day_from = sync.parse_day(request.args.get('date_from'), 'дату начала')
        day_to = sync.parse_day(request.args.get('date_to'), 'дату конца')
        if day_to < day_from:
            day_from, day_to = day_to, day_from
        span = (day_to - day_from).days + 1
        if span > sync.MAX_PERIOD_DAYS:
            raise ValueError(
                'Период %d суток — это слишком много за раз. Максимум %d: сутки '
                'отдела продаж это около 23 тысяч строк на станции.'
                % (span, sync.MAX_PERIOD_DAYS))
        return day_from, day_to

    def _filters():
        def clean(name, limit=48):
            return (str(request.args.get(name) or '').strip()[:limit]) or None
        return {
            'call_type': clean('call_type'),
            'result': clean('result'),
            'ext': clean('ext', 8),
            'queue': clean('queue', 8),
            # Телефон ищем по вхождению: человек помнит четыре последние цифры.
            'phone': ''.join(ch for ch in str(request.args.get('phone') or '')
                             if ch.isdigit())[:16] or None,
            'talked_only': str(request.args.get('talked_only') or '').lower()
                           in ('1', 'true', 'yes'),
        }

    def _filters_note(filters):
        labels = {'call_type': 'тип', 'result': 'результат', 'ext': 'внутренний номер',
                  'queue': 'очередь', 'phone': 'телефон'}
        parts = ['%s = %s' % (labels[key], filters[key])
                 for key in labels if filters.get(key)]
        if filters.get('talked_only'):
            parts.append('только состоявшиеся разговоры')
        return '; '.join(parts)

    # ── справочник номеров ───────────────────────────────────────────────────

    def _ensure_directory(force=False):
        """Свежий справочник, при необходимости пересобранный.

        Наружу не ходит: справочник агентов станции присылает мост и он лежит в
        cdr_agent_state. Поэтому пересборка работает и когда мост молчит — просто
        по последнему присланному снимку.
        """
        with db._get_cursor() as cursor:
            stored = queries.load_directory(cursor)
            updated_at = queries.directory_updated_at(cursor)
        if stored and not force and updated_at is not None:
            age = queries.now_almaty() - _naive(updated_at)
            if age < timedelta(hours=DIRECTORY_TTL_HOURS):
                return stored
        with db._get_cursor() as cursor:
            agents = queries.load_station_agents(cursor)
            db_rows = queries.db_operator_rows(cursor)
            built = directory_mod.build_directory(db_rows, agents)
            if built:
                queries.save_directory(cursor, built)
        return built or stored

    def _resolver(force=False):
        return directory_mod.resolver(_ensure_directory(force=force))

    # ── покрытие периода ─────────────────────────────────────────────────────

    def _coverage(day_from, day_to, enqueue, requested_by=None):
        today = queries.today_almaty()
        with db._get_cursor() as cursor:
            states = queries.day_states(cursor, day_from, day_to)
            missing = sync.days_needing_sync(states, day_from, day_to, today)
            if enqueue and missing:
                queries.enqueue_days(cursor, missing, requested_by)
                states = queries.day_states(cursor, day_from, day_to)
            bridge = queries.agent_state(cursor)
        # Ожидание считаем по свежему состоянию и ИМЕННО как «чего ещё нет», а не
        # как «что надо переспросить»: сутки, которые мост уже читает, не надо
        # переспрашивать, но готовыми они не стали — иначе полоса покажет 100% на
        # середине работы, фронт перестанет опрашивать и не увидит данных.
        awaiting = sync.days_awaiting(states, day_from, day_to, today)
        days_total = (day_to - day_from).days + 1
        done = _settled_days(states, day_from, day_to, today)
        errors = [row for row in states if row['status'] == 'error']
        return {
            'days_total': days_total,
            'days_done': done,
            'days_missing': len(awaiting),
            'pending': len(awaiting),
            'rows_fetched': sum(row['rows_fetched'] for row in states),
            'percent': int(round(100.0 * (days_total - len(awaiting)) / days_total))
                       if days_total else 100,
            'errors': [{'day': row['day'], 'error': row['error']} for row in errors],
            'days': states,
            'bridge': _bridge_view(bridge),
        }

    # ── роуты раздела ────────────────────────────────────────────────────────

    @cdr_route('/meta')
    def cdr_meta(ctx):
        with db._get_cursor() as cursor:
            updated_at = queries.directory_updated_at(cursor)
            bridge = queries.agent_state(cursor)
            cursor.execute("SELECT MIN(day), MAX(day) FROM cdr_sync_days "
                           "WHERE status = 'done'")
            first, last = cursor.fetchone() or (None, None)
        return jsonify({
            'capabilities': access.capabilities(ctx),
            'bridge': _bridge_view(bridge),
            'directory_updated_at': updated_at.isoformat() if updated_at else None,
            'cached_from': first.isoformat() if first else None,
            'cached_to': last.isoformat() if last else None,
            'max_period_days': sync.MAX_PERIOD_DAYS,
            'today': queries.today_almaty().isoformat(),
        })

    @cdr_route('/period')
    def cdr_period(ctx):
        day_from, day_to = _period()
        filters = _filters()
        page = max(1, int(request.args.get('page') or 1))
        page_size = min(MAX_PAGE_SIZE,
                        max(1, int(request.args.get('page_size') or DEFAULT_PAGE_SIZE)))
        enqueue = str(request.args.get('sync') or '1').lower() not in ('0', 'false', 'no')
        if enqueue and not access.can_sync(ctx):
            enqueue = False

        coverage = _coverage(day_from, day_to, enqueue, ctx['user_id'])
        resolve = _resolver()
        with db._get_cursor() as cursor:
            total = queries.count_touches(cursor, day_from, day_to, filters)
            rows = queries.select_touches(cursor, day_from, day_to, filters,
                                          limit=page_size, offset=(page - 1) * page_size)
            summary = queries.summary(cursor, day_from, day_to, filters)
            values = queries.filter_values(cursor, day_from, day_to)
        for row in rows:
            row['operator'], row['direction'] = resolve(row['ext'], row['started_at'])
        return jsonify({
            'period': {'from': day_from.isoformat(), 'to': day_to.isoformat()},
            'coverage': coverage,
            'summary': summary,
            'total': total,
            'page': page,
            'page_size': page_size,
            'touches': rows,
            'filter_values': values,
        })

    @cdr_route('/sync', methods=('POST',))
    def cdr_sync(ctx):
        if not access.can_sync(ctx):
            return jsonify({"error": "Обновлять данные со станции вам не разрешено",
                            "code": "CDR_SYNC_FORBIDDEN"}), 403
        day_from, day_to = _period()
        if str(request.args.get('force') or '').lower() in ('1', 'true', 'yes'):
            # «Обновить» просят в двух случаях: подозревают, что станция дописала
            # звонки задним числом, либо видят отказ и хотят ещё раз. Поэтому
            # снимаем и отметку «сутки закрыты», и счётчик попыток: человек,
            # который жмёт кнопку, глядя на ошибку, знает, что делает. Дальше
            # сутки попадают в очередь обычным путём.
            with db._get_cursor() as cursor:
                cursor.execute("UPDATE cdr_sync_days SET complete = FALSE, attempts = 0 "
                               "WHERE day BETWEEN %s AND %s", (day_from, day_to))
        return jsonify({'coverage': _coverage(day_from, day_to, True, ctx['user_id'])})

    @cdr_route('/stats')
    def cdr_stats(ctx):
        day_from, day_to = _period()
        filters = _filters()
        resolve = _resolver()
        with db._get_cursor() as cursor:
            by_ext = queries.operator_stats(cursor, day_from, day_to, filters)
            daily = queries.daily_stats(cursor, day_from, day_to, filters)
        return jsonify({'operators': _group_operators(by_ext, resolve), 'daily': daily})

    @cdr_route('/export')
    def cdr_export(ctx):
        day_from, day_to = _period()
        filters = _filters()
        resolve = _resolver()
        with db._get_cursor() as cursor:
            total = queries.count_touches(cursor, day_from, day_to, filters)
            if total > MAX_EXPORT_ROWS:
                return jsonify({
                    "error": "В периоде %d касаний — это больше, чем помещается в один "
                             "файл (%d). Возьмите период короче." % (total, MAX_EXPORT_ROWS),
                    "code": "CDR_EXPORT_TOO_BIG",
                }), 400
            summary = queries.summary(cursor, day_from, day_to, filters)
            by_ext = queries.operator_stats(cursor, day_from, day_to, filters)
            daily = queries.daily_stats(cursor, day_from, day_to, filters)
            by_type = queries.breakdown(cursor, day_from, day_to, 'call_type', filters)
            by_result = queries.breakdown(cursor, day_from, day_to, 'result', filters)
            states = queries.day_states(cursor, day_from, day_to)

            def stream():
                for touch in queries.iter_touches(cursor, day_from, day_to, filters):
                    touch['operator'], touch['direction'] = resolve(
                        touch['ext'], touch['started_at'])
                    yield touch

            days_total = (day_to - day_from).days + 1
            # Считаем «улаженные», а не «закрытые»: сегодняшние сутки complete не
            # получают никогда, и лист «Контекст» иначе писал бы «суток не хватает: 1»
            # у совершенно полного файла.
            done = _settled_days(states, day_from, day_to, queries.today_almaty())
            workbook, written = report.build_workbook(
                stream(),
                period_from=day_from.isoformat(), period_to=day_to.isoformat(),
                summary=summary, operators=_group_operators(by_ext, resolve),
                daily=daily, by_type=by_type, by_result=by_result,
                generated_by=ctx.get('name') or '',
                filters_note=_filters_note(filters),
                coverage={'days_total': days_total, 'days_done': done,
                          'days_missing': days_total - done,
                          'rows_fetched': sum(row['rows_fetched'] for row in states)},
                text_warning_patch=excel_text_warning)
        log.info('Касания: выгрузка %s..%s — %d строк, собрал %s',
                 day_from, day_to, written, ctx.get('name'))
        return send_file(workbook, mimetype=XLSX_MIME, as_attachment=True,
                         download_name=report.report_filename(day_from, day_to))

    @cdr_route('/directory/refresh', methods=('POST',))
    def cdr_directory_refresh(ctx):
        if not access.can_sync(ctx):
            return jsonify({"error": "Пересобирать справочник вам не разрешено"}), 403
        built = _ensure_directory(force=True)
        return jsonify({'operators': len(built or {})})

    # ── роуты моста ──────────────────────────────────────────────────────────

    @agent_route('/agent/poll')
    def poll(payload):
        """Мост здоровается и забирает задание. Один запрос вместо двух.

        Отдаём и границы чтения — вместе с часовым хвостом следующих суток.
        Считает их портал, а не мост: правило «сутки плюс час» — часть логики
        раздела, и разъехаться двум её копиям нельзя.
        """
        agent_id = str(payload.get('agent_id') or payload.get('hostname') or '')[:120]
        with db._get_cursor() as cursor:
            queries.agent_seen(
                cursor,
                hostname=str(payload.get('hostname') or '')[:120] or None,
                version=str(payload.get('version') or '')[:40] or None,
                station_url=str(payload.get('station_url') or '')[:200] or None,
                error=payload.get('error') or None)
            jobs = queries.claim_days(cursor, agent_id, AGENT_JOBS_PER_POLL)
            agents_at = queries.agent_state(cursor).get('agents_at')
            # Уборка кэша — на холостой заход моста: своего планировщика у
            # раздела нет, а работы в этот момент всё равно нет.
            if not jobs and queries.cleanup_due(cursor):
                removed = queries.drop_expired(cursor)
                if removed:
                    log.info('Касания: убрано %d касаний старше срока хранения', removed)
        result = []
        for job in jobs:
            day = sync.parse_day(job['day'])
            from_dt, to_dt = sync.window_for(day)
            result.append({'day': job['day'], 'from_dt': from_dt, 'to_dt': to_dt,
                           'attempt': job['attempts']})
        return jsonify({
            'jobs': result,
            # Справочник агентов просим не чаще раза в полсуток: он меняется,
            # когда кого-то нанимают, а не когда идёт звонок.
            'want_directory': _stale(agents_at, hours=DIRECTORY_TTL_HOURS),
        })

    @agent_route('/agent/day')
    def day(payload):
        """Мост присылает сутки: либо касания, либо отказ.

        Касания складываются в ту же транзакцию, что и отметка «готово», — иначе
        существовал бы момент, когда сутки уже помечены готовыми, а строк ещё нет.
        """
        day_value = sync.parse_day(payload.get('day'), 'сутки')
        error = payload.get('error')
        if error:
            with db._get_cursor() as cursor:
                queries.mark_day_error(cursor, day_value, error)
                queries.agent_seen(cursor, error=error)
            log.warning('Касания: мост не смог забрать %s: %s', day_value,
                        str(error)[:200])
            return jsonify({'status': 'error_recorded'})

        touches = payload.get('touches')
        if not isinstance(touches, list):
            raise ValueError('Ожидался список касаний в поле touches')
        if len(touches) > MAX_TOUCHES_PER_DAY:
            raise ValueError('Слишком много касаний за сутки: %d при потолке %d'
                             % (len(touches), MAX_TOUCHES_PER_DAY))
        rows_fetched = int(payload.get('rows_fetched') or 0)
        clean = _dedupe(_clean_touch(item, day_value) for item in touches)
        complete = day_value < queries.today_almaty()
        with db._get_cursor() as cursor:
            queries.replace_day_touches(cursor, day_value, clean)
            queries.mark_day_done(cursor, day_value, rows_fetched, len(clean), complete)
            queries.agent_seen(cursor, days_sent=1, rows_read=rows_fetched)
        log.info('Касания: мост прислал %s — строк CDR %d, касаний %d',
                 day_value, rows_fetched, len(clean))
        return jsonify({'status': 'ok', 'stored': len(clean), 'complete': complete})

    @agent_route('/agent/directory')
    def agent_directory(payload):
        """Справочник агентов станции: ext → имя. Только станция знает, кто
        владеет номером сейчас, — у нас номер уволившегося остаётся висеть."""
        agents = payload.get('agents')
        if not isinstance(agents, dict):
            raise ValueError('Ожидался словарь agents: {ext: имя}')
        cleaned = {str(k)[:8]: str(v or '')[:200] for k, v in list(agents.items())[:5000]}
        with db._get_cursor() as cursor:
            queries.save_station_agents(cursor, cleaned)
            queries.agent_seen(cursor)
        _ensure_directory(force=True)
        return jsonify({'status': 'ok', 'agents': len(cleaned)})

    return bp


# ── помощники ────────────────────────────────────────────────────────────────

def _settled_days(states, day_from, day_to, today):
    """Сколько суток периода в порядке. То же правило, что у прогресса, — иначе
    «готово» на экране и «полнота данных» в файле разошлись бы."""
    total = (day_to - day_from).days + 1
    return total - len(sync.days_awaiting(states, day_from, day_to, today))


def _same_token(provided, expected):
    """Сверка токена за постоянное время.

    Сравниваем БАЙТЫ, а не строки: `hmac.compare_digest` на строках с не-ASCII
    бросает TypeError, и токен с кириллицей давал бы 500 на каждый запрос моста
    вместо честного 401. Это не выдумка — ровно на этом уже спотыкался агент
    «Ограничителя Перезвона»: заголовки ходят в latin-1, и такой токен ронял
    вообще все его запросы, а снаружи это выглядело как «агент не работает».

    Сам по себе не-ASCII токен рабочим быть не может (в HTTP-заголовок он не
    поместится), но отвечать на него надо «не авторизован», а не падать.
    """
    try:
        return hmac.compare_digest(provided.encode('utf-8'), expected.encode('utf-8'))
    except (AttributeError, UnicodeError):
        return False


def _naive(value):
    """Отметка из базы → наивное местное время для сравнения с now_almaty()."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return datetime.min
    if not isinstance(value, datetime):
        return datetime.min
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone(timedelta(hours=5))).replace(tzinfo=None)


def _stale(value, hours):
    if not value:
        return True
    return (queries.now_almaty() - _naive(value)) > timedelta(hours=hours)


def _bridge_view(state):
    """Состояние моста для интерфейса.

    `connected` — не «когда-то здоровался», а «здоровался только что». Без этого
    «нет данных за вчера» и «мост умер неделю назад» выглядели бы одинаково.
    """
    state = dict(state or {})
    last_seen = state.get('last_seen_at')
    state['connected'] = bool(last_seen) and not _stale_minutes(last_seen,
                                                                BRIDGE_SILENT_MINUTES)
    state['silent_minutes'] = (
        None if not last_seen
        else int((queries.now_almaty() - _naive(last_seen)).total_seconds() // 60))
    return state


def _stale_minutes(value, minutes):
    if not value:
        return True
    return (queries.now_almaty() - _naive(value)) > timedelta(minutes=minutes)


# Ссылка на запись уходит прямо в href на странице раздела. Тело запроса моста
# приходит по сети, и доверять ему схемой URL нельзя: `javascript:` в href — это
# исполнение чужого кода в сессии супервайзера. Пускаем только http и https.
_URL_SCHEMES = ('http://', 'https://')


def _safe_url(value):
    if not value:
        return None
    url = str(value).strip()[:1000]
    return url if url.lower().startswith(_URL_SCHEMES) else None


def _dedupe(items):
    """Убирает повторы по ключу (linkedid, телефон) и выбрасывает пустые.

    Не паранойя, а защита вставки: `INSERT ... ON CONFLICT DO UPDATE` падает с
    «cannot affect row a second time», если один ключ встретился в пачке дважды,
    и уронил бы ВСЕ сутки целиком. Склейка на мосте дублей не делает, но тело
    запроса приходит снаружи, и полагаться на его чистоту нельзя.

    Побеждает последний: если мост почему-то прислал два варианта одного звонка,
    более поздний в списке ближе к тому, что видит станция сейчас.
    """
    out = {}
    for item in items:
        if item:
            out[(item['linkedid'], item['phone'])] = item
    return list(out.values())


def _clean_touch(item, day_value):
    """Приводит присланное мостом касание к тому, что примет база.

    Обрезка здесь, а не на вставке: длинное значение из чужого источника должно
    испортить одну ячейку, а не уронить вставку всех суток целиком.
    """
    if not isinstance(item, dict):
        return None
    started = str(item.get('started_at') or '')[:19]
    if len(started) < 19 or started[:10] != day_value.isoformat():
        # Касание не этих суток — мост читает с часовым хвостом, и хвост не
        # должен удваивать данные: он нужен только чтобы собрать плечи.
        return None
    # Правило нормализации одно на портал и мост — берём его из модуля склейки.
    # Своя копия здесь однажды разошлась бы, и портал принимал бы за клиента то,
    # что склейка клиентом не считает (например номер из трёх цифр).
    phone = touches_mod.norm_phone(item.get('phone'))
    if not phone or not item.get('linkedid'):
        return None
    answered = str(item.get('answered_at') or '')[:19]
    return {
        'linkedid': str(item['linkedid'])[:64],
        'phone': phone,
        'started_at': started,
        'answered_at': answered if len(answered) == 19 else '',
        'ext': str(item.get('ext') or '')[:8],
        'call_type': str(item.get('call_type') or '')[:32] or 'Исходящий',
        'result': str(item.get('result') or '')[:32] or 'неизвестно',
        'talk_seconds': max(0, int(item.get('talk_seconds') or 0)),
        'dial_seconds': max(0, int(item.get('dial_seconds') or 0)),
        'queue': str(item.get('queue') or '')[:64],
        'recording_url': _safe_url(item.get('recording_url')),
        'legs': max(1, min(int(item.get('legs') or 1), 32000)),
    }


def _group_operators(by_ext, resolve):
    """Разрез по номерам → разрез по людям.

    У человека бывает несколько внутренних номеров (пересадили за другой стол), а
    у номера — несколько владельцев по времени. Поэтому группируем по ФИО, а
    номера собираем в строку: «касаний у Жупан Аружан» — это вопрос про человека,
    а не про телефон.
    """
    grouped = {}
    for row in by_ext:
        # Резолвим на СУТКИ строки, а не на начало периода: номер уволившегося
        # отдают новому сотруднику, и если период захватывает день передачи, весь
        # номер записался бы на одного из двоих.
        name, direction = resolve(row['ext'], row.get('day'))
        bucket = grouped.setdefault(name or '—', {
            'operator': name or '—', 'direction': direction, 'exts': set(),
            'touches': 0, 'talks': 0, 'talk_seconds': 0, 'phones': 0,
        })
        if row['ext']:
            bucket['exts'].add(row['ext'])
        bucket['touches'] += row['touches']
        bucket['talks'] += row['talks']
        bucket['talk_seconds'] += row['talk_seconds']
        # Клиенты по номерам почти не пересекаются, но сумма — верхняя оценка, и
        # называть её «уникальными» было бы неправдой.
        bucket['phones'] += row['phones']
    out = []
    for bucket in grouped.values():
        bucket['exts'] = ','.join(sorted(bucket['exts']))
        out.append(bucket)
    out.sort(key=lambda item: -item['touches'])
    return out
