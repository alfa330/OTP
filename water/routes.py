"""HTTP-эндпоинты раздела «Учёт воды» (Flask Blueprint /api/water).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2
сам подключает этот модуль, обратный импорт был бы циклом (как у parcels/).

Соглашения портала: методы включают OPTIONS и первым делом отдаётся preflight,
авторизация — общий require_api_key, ошибка — {"error": "...", "code": "..."}.

ВАЖНО ПРО КОДЫ ОТВЕТОВ. `db._get_cursor()` коммитит всё, что записано до
`return … , 4xx` (откат — только по исключению). Поэтому каждый обработчик
сначала проверяет ВСЁ, и только потом пишет: отказ после записи дал бы ответ
«не удалось» при уже списанной воде.
"""

import logging
import re
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, send_file

from . import access, driver as water_driver, notify, queries, report, rules, schema

try:  # psycopg2 есть на проде; в гарнитуре тестов его может не быть
    from psycopg2 import errors as pg_errors
    _UNIQUE_VIOLATION = (pg_errors.UniqueViolation,)
except Exception:  # noqa: BLE001
    _UNIQUE_VIOLATION = ()

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

# Пределы чисел. Блоки — упаковки по 16 бутылок, офис таких держит десятки;
# сто тысяч — заведомо «опечатка в числе», а не партия.
_MAX_BLOCKS = 100000
_MAX_COMMENT = 500
_TARIFF_CODE_RE = re.compile(r'^[a-z][a-z0-9_]{1,39}$')

# Период дашборда по умолчанию — последние 30 дней: средний расход за месяц
# сглаживает и выходные, и «пришёл автобус водителей».
_DASHBOARD_DEFAULT_DAYS = 30


def build_water_blueprint(*, db, require_api_key, build_cors_preflight_response,
                          resolve_requester, sensitive_access_granted,
                          excel_text_warning=None, send_telegram=None, web_app_base_url=None,
                          driver_lookup=None):
    """Собирает Blueprint раздела.

    sensitive_access_granted — (user_id) -> bool, ключ QR-подтверждения общий с
    «Посылками». Обязательный: забытая зависимость должна уронить сборку на
    старте, а не открыть экран с водителями всем.

    send_telegram — (chat_id, text, parse_mode, reply_markup) -> response из
    монолита. Нет его — раздел работает, просто без писем о закупке.

    driver_lookup — поиск водителя в CRM; подменяется в тестах, по умолчанию
    water.driver.lookup.
    """
    lookup_driver = driver_lookup or water_driver.lookup
    bp = Blueprint('water', __name__, url_prefix='/api/water')

    def water_route(rule, methods=('GET',), need=None):
        """Каркас роута: preflight, авторизация, контекст, гейты, ошибки.

        need='issue'  — выдача воды (фронт-офисы, глобальный админ);
        need='manage' — учёт целиком (глава фронт-офисов, глобальный админ).
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

                    if not access.can_open_section(ctx):
                        return jsonify({
                            "error": "Раздел «Учёт воды» вам не открыт",
                            "code": "WATER_SECTION_CLOSED",
                        }), 403

                    # QR — после входа в раздел: предлагать подтвердить доступ к
                    # тому, чего человеку не выдавали, — тупик.
                    if (access.requires_sensitive_qr(ctx)
                            and not sensitive_access_granted(ctx['user_id'])):
                        return jsonify({
                            "error": "Раздел «Учёт воды» откроется после "
                                     "QR-подтверждения доступа",
                            "code": "SENSITIVE_ACCESS_REQUIRED",
                        }), 403

                    if need == 'issue' and not access.can_issue(ctx):
                        return jsonify({
                            "error": "Воду выдают сотрудники фронт-офисов",
                            "code": "WATER_ISSUE_FORBIDDEN",
                        }), 403
                    if need == 'manage' and not access.can_manage(ctx):
                        return jsonify({
                            "error": "Учёт ведёт региональный руководитель",
                            "code": "WATER_MANAGE_FORBIDDEN",
                        }), 403
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('water: ошибка в %s', rule)
                    return jsonify({
                        "error": "Внутренняя ошибка раздела «Учёт воды»",
                        "detail": str(exc)[:200],
                    }), 500

            return wrapper

        return decorator

    def _payload():
        return request.get_json(silent=True) or {}

    def _actor(ctx):
        return {'user_id': ctx['user_id'], 'name': ctx.get('name')}

    def _not_ready():
        return jsonify({
            "error": "Раздел ещё разворачивается — попробуйте после перезапуска сервера",
            "code": "WATER_SCHEMA_NOT_READY",
        }), 409

    # ── Telegram «Требуется закупка» ─────────────────────────────────────
    def _queue_buy_alert(cursor, office, before, after, settings, outbox):
        if not notify.crossed_buy_threshold(before, after, office['buy_threshold']):
            return
        if not callable(send_telegram):
            return
        markup = notify.reply_markup(notify.section_link(web_app_base_url))
        text = notify.buy_message(office, after)
        for recipient in queries.notify_recipients(cursor, settings):
            outbox.append((recipient['chat_id'], text, markup))

    def _flush(outbox):
        """После коммита. Отказ Telegram операцию не отменяет — только в лог."""
        for chat_id, text, markup in outbox:
            try:
                response = send_telegram(chat_id, text, parse_mode='HTML', reply_markup=markup)
                status = getattr(response, 'status_code', 200)
                if status != 200:
                    logging.warning('water: Telegram не принял уведомление (%s)', status)
            except Exception as exc:  # noqa: BLE001
                logging.warning('water: Telegram недоступен: %s', exc)

    # ── Диагностика ──────────────────────────────────────────────────────
    @water_route('/ping')
    def water_ping(ctx):
        with db._get_cursor() as cursor:
            ready = schema.schema_is_ready(cursor)
            payload = {
                "ok": True,
                "schema_ready": ready,
                "capabilities": access.capabilities(ctx),
                "user_id": ctx['user_id'],
            }
            if ready:
                payload['settings'] = _public_settings(queries.get_settings(cursor))
        return jsonify(payload)

    # ── Офисы учёта ──────────────────────────────────────────────────────
    @water_route('/offices')
    def water_offices(ctx):
        manage = access.can_manage(ctx)
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"offices": [], "directory": []})
            settings = queries.get_settings(cursor)
            offices = queries.list_offices(cursor, settings, include_inactive=manage)
            payload = {"offices": offices}
            if manage:
                taken = queries.taken_office_ids(cursor)
                payload['directory'] = [office for office in queries.directory(cursor)
                                        if office['id'] not in taken]
        return jsonify(payload)

    @water_route('/offices', methods=('POST',), need='manage')
    def water_office_add(ctx):
        data = _payload()
        office_id = _int(data.get('office_id'))
        stock = _int(data.get('stock'))
        if not office_id:
            return _bad('Выберите офис из справочника', 'WATER_OFFICE_REQUIRED')
        if stock is None or not 0 <= stock <= _MAX_BLOCKS:
            return _bad('Укажите, сколько блоков сейчас в офисе', 'WATER_STOCK_INVALID')
        thresholds, error = _thresholds(data)
        if error:
            return error
        try:
            with db._get_cursor() as cursor:
                if not schema.schema_is_ready(cursor):
                    return _not_ready()
                settings = queries.get_settings(cursor)
                error = _thresholds_order(thresholds, settings)
                if error:
                    return error
                wiki_office = queries.directory_office(cursor, office_id)
                if not wiki_office:
                    return _bad('Офиса нет в справочнике фронт-офисов', 'WATER_OFFICE_UNKNOWN', 404)
                if office_id in queries.taken_office_ids(cursor):
                    return _bad('Этот офис уже в учёте', 'WATER_OFFICE_TAKEN', 409)
                office = queries.add_office(
                    cursor, wiki_office=wiki_office, stock=stock,
                    low_threshold=thresholds.get('low_threshold'),
                    buy_threshold=thresholds.get('buy_threshold'),
                    settings=settings, actor=_actor(ctx))
        except _UNIQUE_VIOLATION:
            return _bad('Этот офис уже в учёте', 'WATER_OFFICE_TAKEN', 409)
        return jsonify({"office": office}), 201

    @water_route('/offices/<int:water_office_id>', methods=('PATCH',), need='manage')
    def water_office_update(water_office_id, ctx):
        data = _payload()
        thresholds, error = _thresholds(data)
        if error:
            return error
        fields = dict(thresholds)
        if 'is_active' in data:
            fields['is_active'] = bool(data.get('is_active'))
        with db._get_cursor() as cursor:
            settings = queries.get_settings(cursor)
            office = queries.read_office(cursor, water_office_id, settings, for_update=True)
            if not office:
                return _bad('Офис не найден', 'WATER_OFFICE_NOT_FOUND', 404)
            # Проверяется то, что ПОЛУЧИТСЯ: присланное поверх уже заданного.
            # И только когда пороги правят: вывод из учёта не должен спотыкаться
            # о пороги, которых человек не трогал.
            if thresholds:
                merged = {
                    name: fields[name] if name in fields else office['own_%s' % name]
                    for name in ('low_threshold', 'buy_threshold')
                }
                error = _thresholds_order(merged, settings)
                if error:
                    return error
            office = queries.update_office(cursor, water_office_id, fields, settings)
        return jsonify({"office": office})

    @water_route('/offices/<int:water_office_id>/movements')
    def water_office_movements(water_office_id, ctx):
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"items": []})
            items = queries.list_movements(cursor, water_office_id,
                                           limit=min(_int(request.args.get('limit')) or 30, 100))
        return jsonify({"items": items})

    @water_route('/offices/<int:water_office_id>/intake', methods=('POST',), need='manage')
    def water_office_intake(water_office_id, ctx):
        """Поступление: «указать количество — система увеличит остаток» (ТЗ)."""
        data = _payload()
        blocks = _int(data.get('blocks'))
        if blocks is None or not 1 <= blocks <= _MAX_BLOCKS:
            return _bad('Укажите, сколько блоков поступило', 'WATER_BLOCKS_INVALID')
        comment = _clean(data.get('comment'), _MAX_COMMENT)
        return _move(ctx, water_office_id, kind='intake', comment=comment,
                     delta_of=lambda office: blocks)

    @water_route('/offices/<int:water_office_id>/recount', methods=('POST',), need='manage')
    def water_office_recount(water_office_id, ctx):
        """Пересчёт: на полке оказалось не столько, сколько считала система.

        Причина обязательна: пересчёт меняет остаток без водителя и без
        накладной, и через месяц по журналу иначе не понять, куда делись блоки.
        """
        data = _payload()
        stock = _int(data.get('stock'))
        if stock is None or not 0 <= stock <= _MAX_BLOCKS:
            return _bad('Укажите, сколько блоков на самом деле в офисе', 'WATER_STOCK_INVALID')
        comment = _clean(data.get('comment'), _MAX_COMMENT)
        if not comment:
            return _bad('Напишите причину пересчёта', 'WATER_COMMENT_REQUIRED')
        return _move(ctx, water_office_id, kind='recount', comment=comment,
                     delta_of=lambda office: stock - int(office['stock']))

    def _move(ctx, water_office_id, *, kind, comment, delta_of):
        outbox = []
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            settings = queries.get_settings(cursor)
            office = queries.read_office(cursor, water_office_id, settings, for_update=True)
            if not office:
                return _bad('Офис не найден', 'WATER_OFFICE_NOT_FOUND', 404)
            if not office['is_active']:
                return _bad('Офис выведен из учёта', 'WATER_OFFICE_INACTIVE', 409)
            delta = int(delta_of(office))
            if kind == 'recount' and delta == 0:
                return _bad('Остаток и так такой — пересчитывать нечего', 'WATER_RECOUNT_SAME')
            before = int(office['stock'])
            movement = queries.apply_movement(cursor, office, kind=kind, delta=delta,
                                              comment=comment, actor=_actor(ctx))
            office = queries.read_office(cursor, water_office_id, settings)
            _queue_buy_alert(cursor, office, before, office['stock'], settings, outbox)
        _flush(outbox)
        return jsonify({"office": office, "movement": movement}), 201

    # ── Остатки по офисам ────────────────────────────────────────────────
    @water_route('/dashboard')
    def water_dashboard(ctx):
        today = queries.today_almaty()
        date_from = _date(request.args.get('date_from'))
        date_to = _date(request.args.get('date_to'))
        if not date_from or not date_to:
            date_to = today
            date_from = today - timedelta(days=_DASHBOARD_DEFAULT_DAYS - 1)
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        if (date_to - date_from).days + 1 > report.EXPORT_MAX_DAYS:
            return _bad('Период длиннее года — выберите короче', 'WATER_PERIOD_TOO_LONG')
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"rows": [], "period": _period_json(date_from, date_to)})
            settings = queries.get_settings(cursor)
            rows = queries.dashboard(cursor, settings, date_from=date_from, date_to=date_to,
                                     today=today)
        return jsonify({"rows": rows, "period": _period_json(date_from, date_to)})

    # ── Проверка права и выдача ──────────────────────────────────────────
    def _lookup(raw):
        """(водитель, ответ-ошибка). CRM — чужой сервис, её отказ — понятный текст.

        Тексты клиента CRM писались для формы «Посылок», где ФИО можно вписать
        руками («…или заполните вручную»). Здесь ручного пути нет и быть не
        может — право на воду решает CRM, — поэтому такие ответы переписаны.
        """
        try:
            return lookup_driver(raw), None
        except water_driver.DriverLookupError as exc:
            message = _CRM_MESSAGES.get(exc.code, exc.message)
            return None, (jsonify({"error": message, "code": exc.code}), exc.status)

    @water_route('/check', methods=('POST',))
    def water_check(ctx):
        """ID водителя → его данные из CRM и вердикт «положена ли вода».

        Офис необязателен: колл-центр проверяет право без офиса (воду тогда не
        считаем), фронт-офис присылает свой — и видит, хватит ли её.
        """
        data = _payload()
        water_office_id = _int(data.get('water_office_id'))
        found, error = _lookup(data.get('link') or data.get('account_id'))
        if error:
            return error
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            settings = queries.get_settings(cursor)
            office = None
            if water_office_id:
                office = queries.read_office(cursor, water_office_id, settings)
                if not office or not office['is_active']:
                    return _bad('Офис не найден в учёте', 'WATER_OFFICE_NOT_FOUND', 404)
            history = queries.person_history(cursor, found['account_id'], found.get('iin'))
        verdict = rules.evaluate(found, history, settings, today=queries.today_almaty(),
                                 stock=office['stock'] if office else None)
        return jsonify({
            "driver": water_driver.public(found),
            "verdict": verdict,
            "history": [_history_brief(item) for item in history[:5]],
        })

    @water_route('/issues', methods=('POST',), need='issue')
    def water_issue(ctx):
        """Выдать воду. Право проверяется ЗАНОВО, по свежим данным CRM и под
        замком: то, что человек видел на экране минуту назад, могло устареть
        (соседний офис уже выдал, остаток кончился)."""
        data = _payload()
        water_office_id = _int(data.get('water_office_id'))
        if not water_office_id:
            return _bad('Выберите офис, в котором выдаёте воду', 'WATER_OFFICE_REQUIRED')
        requested = data.get('blocks')
        blocks = _int(requested) if requested not in (None, '') else None
        if requested not in (None, '') and (blocks is None or blocks < 1):
            return _bad('Количество блоков — целое число от одного', 'WATER_BLOCKS_INVALID')
        expected_kind = str(data.get('kind') or '').strip() or None
        if expected_kind and expected_kind not in schema.ISSUE_KINDS:
            return _bad('Неизвестный вид выдачи', 'WATER_KIND_INVALID')
        # Отметка сотрудника «ФК пройден, проверил во Флите» — нужна только
        # новому водителю и только когда CRM статуса ФК не знает (rules.py).
        fk_confirmed = data.get('fk_confirmed') is True

        found, error = _lookup(data.get('link') or data.get('account_id'))
        if error:
            return error

        outbox = []
        try:
            with db._get_cursor() as cursor:
                if not schema.schema_is_ready(cursor):
                    return _not_ready()
                settings = queries.get_settings(cursor)
                office = queries.read_office(cursor, water_office_id, settings, for_update=True)
                if not office or not office['is_active']:
                    return _bad('Офис не найден в учёте', 'WATER_OFFICE_NOT_FOUND', 404)
                queries.lock_driver(cursor, found['account_id'], found.get('iin'))
                history = queries.person_history(cursor, found['account_id'], found.get('iin'))
                today = queries.today_almaty()
                verdict = rules.evaluate(found, history, settings, today=today,
                                         stock=office['stock'], fk_confirmed=fk_confirmed)
                if not verdict['allowed']:
                    return jsonify({
                        "error": 'Выдать воду нельзя: %s' % '; '.join(
                            _lower_first(reason) for reason in verdict['reasons']),
                        "code": "WATER_NOT_ELIGIBLE",
                        "verdict": verdict,
                    }), 409
                # Вид выдачи мог смениться, пока экран был открыт: водитель
                # выполнил первый заказ — и приветственный стал «за активность».
                # Выдавать молча не то, на что нажал человек, нельзя.
                if expected_kind and expected_kind != verdict['kind']:
                    return jsonify({
                        "error": "Условия изменились — проверьте водителя ещё раз",
                        "code": "WATER_VERDICT_CHANGED",
                        "verdict": verdict,
                    }), 409
                count = blocks if blocks is not None else verdict['blocks_max']
                if count > verdict['blocks_max']:
                    # Потолок бывает от настроек, а бывает от полки: сосед
                    # успел выдать, и воды осталось меньше, чем на экране.
                    # Свежий вердикт едет в ответ — экран сбросит счётчик.
                    setting = int(settings.get('%s_blocks' % verdict['kind']) or 1)
                    message = ('В офисе осталось %s' % rules.blocks_word(int(office['stock']))
                               if verdict['blocks_max'] < setting
                               else 'За одну выдачу — не больше %s'
                               % rules.blocks_genitive(verdict['blocks_max']))
                    return jsonify({"error": message, "code": "WATER_BLOCKS_TOO_MANY",
                                    "verdict": verdict}), 400
                before = int(office['stock'])
                issue = queries.create_issue(cursor, office, found, verdict,
                                             blocks=count, actor=_actor(ctx))
                office = queries.read_office(cursor, water_office_id, settings)
                _queue_buy_alert(cursor, office, before, office['stock'], settings, outbox)
                after_history = [dict(issue, orders_total=(found.get('orders') or {}).get('total'),
                                      driver_account_id=found['account_id'])] + history
                verdict_after = rules.evaluate(found, after_history, settings, today=today,
                                               stock=office['stock'], fk_confirmed=fk_confirmed)
        except _UNIQUE_VIOLATION:
            # Второй приветственный тому же человеку — отсекла база (частичный
            # уникальный индекс). До сюда доходит только гонка двух офисов.
            return _bad('Приветственный блок этому водителю уже выдан', 'WATER_WELCOME_TAKEN', 409)
        _flush(outbox)
        logging.info('Вода: выдано %s (%s) водителю %s в «%s», выдал %s',
                     count, issue['kind'], found['account_id'], office['name'], ctx.get('name'))
        return jsonify({"issue": issue, "office": office, "verdict": verdict_after}), 201

    # ── Журнал выдач ─────────────────────────────────────────────────────
    @water_route('/issues')
    def water_issues(ctx):
        filters, error = _filters_from_args(request.args)
        if error:
            return error
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"items": [], "total": 0})
            items, total = queries.list_issues(
                cursor, filters,
                limit=_int(request.args.get('limit')) or 50,
                offset=_int(request.args.get('offset')) or 0)
        return jsonify({"items": items, "total": total})

    @water_route('/issues/export')
    def water_issues_export(ctx):
        """Выгрузка журнала. Читающий роут: «сохранить то, что вижу» — то же,
        что «посмотреть». Отбор — тот же разбор, что у списка; период
        обязателен и ЗАМЕЩАЕТ даты экранного фильтра."""
        filters, error = _filters_from_args(request.args)
        if error:
            return error
        date_from = _date(request.args.get('date_from'))
        date_to = _date(request.args.get('date_to'))
        if not date_from or not date_to:
            return _bad('Выберите период выгрузки', 'WATER_PERIOD_REQUIRED')
        if date_to < date_from:
            date_from, date_to = date_to, date_from
        if (date_to - date_from).days + 1 > report.EXPORT_MAX_DAYS:
            return _bad('Период длиннее года — выберите короче', 'WATER_PERIOD_TOO_LONG')
        filters = dict(filters, date_from=date_from, date_to=date_to)
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            items, total = queries.issues_for_export(cursor, filters, limit=report.EXPORT_LIMIT)
            note = _filters_note(cursor, filters)
        stream, written = report.build_workbook(
            items, period_from=date_from, period_to=date_to,
            generated_by=ctx.get('name') or '', filters_note=note, total=total,
            generated_at=queries.now_almaty(), text_warning_patch=excel_text_warning)
        logging.info('Вода: выгрузка %d выдач из %d за %s..%s, собрал %s',
                     written, total, date_from, date_to, ctx.get('name'))
        return send_file(stream, mimetype=XLSX_MIME, as_attachment=True,
                         download_name=report.report_filename(date_from, date_to))

    @water_route('/filters')
    def water_filters(ctx):
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return jsonify({"staff": [], "parks": [], "offices": []})
            values = queries.filter_values(cursor)
            settings = queries.get_settings(cursor)
            values['offices'] = [
                {'id': office['id'], 'city': office['city'], 'name': office['name'],
                 'is_active': office['is_active']}
                for office in queries.list_offices(cursor, settings, include_inactive=True)]
        return jsonify(values)

    # ── Условия программы и пороги ───────────────────────────────────────
    @water_route('/settings')
    def water_settings(ctx):
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            settings = queries.get_settings(cursor)
            payload = {"settings": _public_settings(settings)}
            if access.can_manage(ctx):
                candidates = queries.notify_candidates(cursor)
                # Выбранных, кто перестал быть кандидатом (уволен, переведён),
                # экран не показывает — значит, и в черновик они не едут:
                # иначе их нельзя было бы ни увидеть, ни снять галочку.
                ids = {person['id'] for person in candidates}
                payload['settings']['notify_user_ids'] = [
                    uid for uid in settings['notify_user_ids'] if uid in ids]
                payload['candidates'] = candidates
                payload['head_ids'] = queries.front_office_head_ids(cursor)
        return jsonify(payload)

    @water_route('/settings', methods=('PUT',), need='manage')
    def water_settings_save(ctx):
        data = _payload()
        fields, error = _settings_fields(data)
        if error:
            return error
        with db._get_cursor() as cursor:
            if not schema.schema_is_ready(cursor):
                return _not_ready()
            current = queries.get_settings(cursor)
            merged = dict(current, **fields)
            if merged['buy_threshold'] > merged['low_threshold']:
                return _bad('Порог «требуется закупка» не может быть больше порога '
                            '«низкий остаток»', 'WATER_THRESHOLDS_ORDER')
            # Офис со СВОИМ одним порогом сравнивает его с общим вторым. Новый
            # общий порог не должен молча перевернуть пару у такого офиса.
            clashing = [office['name'] for office in queries.list_offices(
                cursor, merged, include_inactive=True)
                if office['buy_threshold'] > office['low_threshold']]
            if clashing:
                return _bad('С такими порогами у офисов %s порог закупки станет выше порога '
                            '«низкий остаток» — сначала поправьте свои пороги этих офисов'
                            % ', '.join(clashing),
                            'WATER_THRESHOLDS_ORDER')
            if 'notify_user_ids' in fields:
                # Проверяем только НОВЫХ получателей. Уже выбранный, которого с
                # тех пор уволили или перевели, не должен запирать сохранение
                # всех остальных условий программы.
                allowed = {person['id'] for person in queries.notify_candidates(cursor)}
                known = set(current.get('notify_user_ids') or [])
                unknown = [uid for uid in fields['notify_user_ids']
                           if uid not in allowed and uid not in known]
                if unknown:
                    return _bad('Получателем можно выбрать только сотрудника фронт-офисов '
                                'или администратора', 'WATER_NOTIFY_UNKNOWN')
            saved = queries.update_settings(cursor, fields, _actor(ctx))
        payload = _public_settings(saved)
        payload['notify_user_ids'] = saved['notify_user_ids']
        return jsonify({"settings": payload})

    return bp


# ─────────────────────────────────────────────────────────────────────────────
# Разбор и проверка полей
# ─────────────────────────────────────────────────────────────────────────────

def _bad(message, code, status=400):
    return jsonify({"error": message, "code": code}), status


# Тексты отказов CRM для раздела воды — без «заполните вручную» (см. _lookup).
_CRM_MESSAGES = {
    'crm_unavailable': 'CRM не ответила — попробуйте ещё раз через минуту',
    'crm_bad_payload': 'CRM вернула неожиданный ответ — попробуйте ещё раз через минуту',
    'not_configured': 'Связь с CRM не настроена — сообщите администратору',
}


def _lower_first(text):
    """Причину после двоеточия — со строчной, но аббревиатуру не ломаем:
    «CRM не отдала…» не должно стать «cRM не отдала…»."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def _int(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not re.match(r'^-?\d{1,9}$', text):
        return None
    return int(text)


def _date(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _clean(value, limit):
    text = str(value or '').strip()
    return text[:limit] if text else None


def _period_json(date_from, date_to):
    return {'from': date_from.isoformat(), 'to': date_to.isoformat(),
            'days': (date_to - date_from).days + 1}


def _public_settings(settings):
    """Условия программы для экрана. Кому пишем — только руководителю."""
    return {key: settings.get(key) for key in (
        'min_trips', 'cooldown_days', 'welcome_blocks', 'activity_blocks', 'tariffs',
        'low_threshold', 'buy_threshold', 'updated_by_name', 'updated_at')}


def _threshold_value(data, name):
    """(есть ли поле, значение|None, ошибка). null — «как у всех»."""
    if name not in data:
        return False, None, None
    raw = data.get(name)
    if raw is None or raw == '':
        return True, None, None
    value = _int(raw)
    if value is None or not 0 <= value <= _MAX_BLOCKS:
        return True, None, _bad('Порог — целое число блоков от нуля', 'WATER_THRESHOLD_INVALID')
    return True, value, None


def _thresholds(data):
    fields = {}
    for name in ('low_threshold', 'buy_threshold'):
        present, value, error = _threshold_value(data, name)
        if error:
            return None, error
        if present:
            fields[name] = value
    return fields, None


def _thresholds_order(own, settings):
    """Порог закупки не выше порога «низкий» — на ДЕЙСТВУЮЩИХ значениях офиса:
    свой порог сравнивается с общим, если второй не задан."""
    low = own.get('low_threshold')
    buy = own.get('buy_threshold')
    low = settings['low_threshold'] if low is None else low
    buy = settings['buy_threshold'] if buy is None else buy
    if buy > low:
        return _bad('Порог «требуется закупка» не может быть больше порога «низкий остаток»',
                    'WATER_THRESHOLDS_ORDER')
    return None


_SETTINGS_LIMITS = {
    'min_trips': (0, 1000, 'Поездок для выдачи — целое число от 0 до 1000'),
    'cooldown_days': (0, 365, 'Дней между выдачами — целое число от 0 до 365'),
    'welcome_blocks': (1, 50, 'Приветственная выдача — от 1 до 50 блоков'),
    'activity_blocks': (1, 50, 'Выдача за активность — от 1 до 50 блоков'),
    'low_threshold': (0, _MAX_BLOCKS, 'Порог «низкий остаток» — целое число блоков'),
    'buy_threshold': (0, _MAX_BLOCKS, 'Порог «требуется закупка» — целое число блоков'),
}


def _settings_fields(data):
    """Все поля проверяются ДО записи: частично сохранённые условия программы
    хуже несохранённых."""
    fields = {}
    for name, (low, high, message) in _SETTINGS_LIMITS.items():
        if name not in data:
            continue
        value = _int(data.get(name))
        if value is None or not low <= value <= high:
            return None, _bad(message, 'WATER_SETTINGS_INVALID')
        fields[name] = value
    if 'tariffs' in data:
        raw = data.get('tariffs')
        if not isinstance(raw, list):
            return None, _bad('Тарифы — список кодов', 'WATER_SETTINGS_INVALID')
        codes = []
        for item in raw:
            code = str(item or '').strip().lower()
            if not _TARIFF_CODE_RE.match(code):
                return None, _bad('Неизвестный код тарифа: %s' % str(item)[:40],
                                  'WATER_SETTINGS_INVALID')
            if code not in codes:
                codes.append(code)
        if not codes:
            return None, _bad('Выберите хотя бы один тариф программы', 'WATER_TARIFFS_REQUIRED')
        if len(codes) > 30:
            return None, _bad('Слишком много тарифов', 'WATER_SETTINGS_INVALID')
        fields['tariffs'] = codes
    if 'notify_user_ids' in data:
        raw = data.get('notify_user_ids')
        if not isinstance(raw, list):
            return None, _bad('Получатели — список сотрудников', 'WATER_SETTINGS_INVALID')
        ids = []
        for item in raw:
            value = _int(item)
            if value is None or value < 1:
                return None, _bad('Получатели — список сотрудников', 'WATER_SETTINGS_INVALID')
            if value not in ids:
                ids.append(value)
        if len(ids) > 30:
            return None, _bad('Слишком много получателей', 'WATER_SETTINGS_INVALID')
        fields['notify_user_ids'] = ids
    return fields, None


def _filters_from_args(args):
    """Отбор журнала — одно место на список и выгрузку."""
    kind = str(args.get('kind') or '').strip() or None
    if kind and kind not in schema.ISSUE_KINDS:
        return None, _bad('Неизвестный вид выдачи', 'WATER_KIND_INVALID')
    return {
        'date_from': _date(args.get('date_from')),
        'date_to': _date(args.get('date_to')),
        'city': _clean(args.get('city'), 120),
        'water_office_id': _int(args.get('water_office_id')),
        'issued_by': _int(args.get('issued_by')),
        'park': _clean(args.get('park'), 160),
        'kind': kind,
        'query': _clean(args.get('q'), 200),
    }, None


def _filters_note(cursor, filters):
    """Отбор словами — для листа «Контекст». Даты стоят там отдельной строкой."""
    parts = []
    if filters.get('city'):
        parts.append('город: %s' % filters['city'])
    if filters.get('water_office_id'):
        name = None
        try:
            office = queries.read_office(cursor, filters['water_office_id'],
                                         queries.get_settings(cursor))
            name = office and office['name']
        except Exception:  # noqa: BLE001
            logging.exception('water: не удалось назвать офис для «Контекста»')
        parts.append('офис: %s' % (name or '№%s' % filters['water_office_id']))
    if filters.get('issued_by'):
        name = next((person['name'] for person in queries.filter_values(cursor)['staff']
                     if person['id'] == filters['issued_by']), None)
        parts.append('сотрудник: %s' % (name or '№%s' % filters['issued_by']))
    if filters.get('park'):
        parts.append('парк: %s' % filters['park'])
    if filters.get('kind'):
        parts.append('вид: %s' % report.kind_label(filters['kind']).lower())
    if filters.get('query'):
        parts.append('поиск: «%s»' % filters['query'])
    return '; '.join(parts)


def _history_brief(item):
    brief = rules.issue_brief(item)
    brief['issued_by_name'] = item.get('issued_by_name')
    return brief
