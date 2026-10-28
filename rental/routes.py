# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Аренда авто» (Flask Blueprint /api/rental).

Blueprint собирается фабрикой и получает зависимости аргументами — bot_schedule2 сам
подключает этот модуль, обратный импорт был бы циклом (как у twofa/ и thermoboxes/).

Соглашения портала: методы включают OPTIONS и первым делом отдаётся preflight,
авторизация — общий require_api_key, ошибка — {"error": "...", "code": "..."}.

Ручки ночного отчёта (#398):
    GET    ''                              экран: права, отчёт за ?date=&kind= (без них — последний) со
                                           строками, границы истории для календаря
    GET    /reports/<id>                   отчёт со строками (смена отчёта, опрос во время сбора)
    POST   /reports                        собрать отчёт текущего слота (пропустил планировщик)
    POST   /reports/<id>/refresh           собрать заново отчёт этой ночи; ручное «Состояние» остаётся
    PUT    /rows/<id>/state                «Состояние» вручную: base / repair / sale / null
    GET    /reports/<id>/export            выгрузка в Excel

История неприкосновенна: старый отчёт пересобрать нельзя — только отчёт, чей слот ещё
идёт (rules.CATCH_UP_HOURS). Иначе «данные за 28.10 01:00» молча стали бы дневными.

Ручки наличия и направлений (#391):
    GET    /fleet                          карточки, свободные (ручные) и занятые по Yume, парки
    POST   /fleet/refresh                  обновить снимок Yume сейчас
    POST   /cards, PUT|DELETE /cards/<id>  карточка: завести, поправить, скрыть (аренда, руководители)
    PUT    /cards/<id>/free                свободные машины карточки — вручную (ТЗ 3.1)
    GET    /cards/<id>/log                 история правок карточки
    GET    /directions                     реестр: оператору ОП — свои, аренде и руководителям — все
    POST   /directions                     «Направить в офис»
    PATCH  /directions/<id>                статус и комментарий (аренда, руководители)
    GET    /directions/<id>/log            история статусов и комментариев
    GET    /conditions, /conditions/<id>   «Условия работы»: текущая версия и прошлые
    POST   /conditions                     новая версия (аренда, руководители)
"""

import logging
from datetime import date
from functools import wraps
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

from . import access, catalog, photos, queries, report as report_xlsx, rules, tariffs
from .service import Busy


def _iso(value):
    return value.isoformat(timespec='seconds') if value else None


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bad(message, code, status=400):
    return jsonify({"error": message, "code": code}), status


def public_report(report):
    if not report:
        return None
    return {
        'id': report['id'], 'kind': report['kind'], 'kind_label': rules.KIND_LABELS.get(report['kind']),
        'slot_at': _iso(report['slot_at']), 'status': report['status'],
        'collected_at': _iso(report.get('collected_at')), 'started_at': _iso(report.get('started_at')),
        'sources': report.get('sources') or {}, 'error': report.get('error'),
        'shows_rent': report['kind'] == rules.KIND_NIGHT,
    }


def public_row(row, names):
    return {
        'id': row['id'], 'plate': row['plate'], 'gps_site': row.get('gps_site'),
        'gps_sites': row.get('gps_sites') or [], 'gps_status': row['gps_status'],
        'signal_at': _iso(row.get('signal_at')), 'address': row.get('address'),
        'lat': row.get('lat'), 'lon': row.get('lon'), 'rent_status': row.get('rent_status'),
        'in_fleet': bool(row.get('in_fleet')), 'model': row.get('model'),
        'state': row.get('state'), 'state_source': row.get('state_source'),
        'state_by_name': names.get(row.get('state_by')) if row.get('state_by') else None,
        'state_at': _iso(row.get('state_at')), 'needs_check': rules.needs_check(row),
    }


def build_rental_blueprint(*, db, require_api_key, build_cors_preflight_response, resolve_requester, service,
                           gcs=None):
    """gcs — {'bucket_name': () -> str, 'client': () -> Client} для фотографий машин (как у «Посылок»);
    без него снимки не грузятся, а раздел работает."""
    bp = Blueprint('rental', __name__, url_prefix='/api/rental')
    gcs = gcs or {}

    def rental_route(rule, methods=('GET',)):
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
                    ctx = service.context(requester_id)
                    if not ctx:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    if not access.can_open_section(ctx):
                        return _bad('Раздел «Аренда авто» вам не открыт', 'RENTAL_SECTION_CLOSED', 403)
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    logging.exception('rental: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Аренда авто»",
                                    "detail": type(exc).__name__}), 500

            return wrapper

        return decorator

    def _report_payload(cursor, report):
        rows = queries.rows_for_report(cursor, report['id'])
        names = queries.user_names(cursor, [row.get('state_by') for row in rows])
        return public_report(report), [public_row(row, names) for row in rows]

    def _slot_info(ctx, now, cursor):
        """Текущий слот: какой отчёт сейчас должен быть и можно ли его собрать кнопкой."""
        kind, slot = rules.current_slot(now)
        existing = queries.find_report(cursor, kind, slot)
        in_window = rules.can_catch_up(slot, now)
        next_kind, next_at = rules.next_slot(now)
        return {
            'kind': kind, 'kind_label': rules.KIND_LABELS[kind], 'slot_at': _iso(slot),
            'report_id': existing['id'] if existing else None,
            'in_window': in_window,
            'can_collect': existing is None and (in_window or access.can_collect_any_time(ctx)),
            'next_kind_label': rules.KIND_LABELS[next_kind], 'next_at': _iso(next_at),
        }

    # ─── Экран ───────────────────────────────────────────────────────────────

    def _requested_slot():
        """?date=ГГГГ-ММ-ДД&kind=night|morning — отчёт за выбранные дату и время; иначе None."""
        kind = request.args.get('kind')
        try:
            day = date.fromisoformat(str(request.args.get('date') or ''))
        except ValueError:
            return None
        return (kind, day) if kind in rules.KINDS else None

    @rental_route('')
    def rental_screen(ctx):
        """Экран: права раздела; ночному отчёту — ещё отчёт за выбранные дату и время (без выбора —
        последний) и границы истории. Оператору ОП ночной отчёт не открыт — только права."""
        now = queries.now_almaty()
        if not access.can_open_night_report(ctx):
            return jsonify({'capabilities': access.capabilities(ctx), 'server_time': _iso(now)})
        requested = _requested_slot()
        with db._get_cursor() as cursor:
            queries.expire_stale(cursor, now)
            if requested:
                selected = queries.find_report(cursor, requested[0], rules.slot_at(requested[0], requested[1]))
            else:
                selected = queries.latest_report(cursor)
            report, rows = _report_payload(cursor, selected) if selected else (None, [])
            first, last = queries.report_bounds(cursor)
            slot = _slot_info(ctx, now, cursor)
        return jsonify({
            'capabilities': access.capabilities(ctx),
            'report': report,
            'rows': rows,
            # Что выбрано: отчёта за выбранные дату и время может и не быть — экран так и скажет.
            'selected': ({'date': requested[1].isoformat(), 'kind': requested[0]} if requested
                         else ({'date': selected['slot_at'].date().isoformat(), 'kind': selected['kind']}
                               if selected else None)),
            'history': {'first': first.date().isoformat() if first else None,
                        'last': last.date().isoformat() if last else None},
            'slot': slot,
            'collecting': service.running(),
            'server_time': _iso(now),
            'states': [{'value': key, 'label': rules.STATE_LABELS[key]} for key in rules.STATES],
        })

    @rental_route('/reports/<int:report_id>')
    def rental_report(report_id, ctx):
        with db._get_cursor() as cursor:
            queries.expire_stale(cursor, queries.now_almaty())
            report = queries.get_report(cursor, report_id)
            if not report:
                return _bad('Отчёт не найден', 'RENTAL_NOT_FOUND', 404)
            report_payload, rows = _report_payload(cursor, report)
        return jsonify({'report': report_payload, 'rows': rows, 'collecting': service.running()})

    # ─── Сбор ────────────────────────────────────────────────────────────────

    @rental_route('/reports', methods=('POST',))
    def rental_collect(ctx):
        now = queries.now_almaty()
        kind, slot = rules.current_slot(now)
        with db._get_cursor() as cursor:
            existing = queries.find_report(cursor, kind, slot)
        if existing:
            return _bad('Отчёт за %s уже есть — его можно обновить' % rules.KIND_LABELS[kind], 'RENTAL_EXISTS', 409)
        if not (rules.can_catch_up(slot, now) or access.can_collect_any_time(ctx)):
            return _bad('Отчёт собирается ночью: в 01:00 и 07:00', 'RENTAL_OUT_OF_WINDOW', 409)
        try:
            report = service.start(kind, slot, by=ctx['user_id'])
        except Busy:
            return _bad('Отчёт уже собирается — подождите минуту', 'RENTAL_BUSY', 409)
        return jsonify({'report': public_report(report)}), 202

    @rental_route('/reports/<int:report_id>/refresh', methods=('POST',))
    def rental_refresh(report_id, ctx):
        now = queries.now_almaty()
        with db._get_cursor() as cursor:
            report = queries.get_report(cursor, report_id)
        if not report:
            return _bad('Отчёт не найден', 'RENTAL_NOT_FOUND', 404)
        if not rules.can_catch_up(report['slot_at'], now):
            return _bad('Прошлые отчёты не пересобираются — они остаются такими, какими были в ту ночь',
                        'RENTAL_HISTORY_LOCKED', 409)
        try:
            report = service.start(report['kind'], report['slot_at'], by=ctx['user_id'])
        except Busy:
            return _bad('Отчёт уже собирается — подождите минуту', 'RENTAL_BUSY', 409)
        return jsonify({'report': public_report(report)}), 202

    # ─── «Состояние» ─────────────────────────────────────────────────────────

    @rental_route('/rows/<int:row_id>/state', methods=('PUT',))
    def rental_set_state(row_id, ctx):
        payload = request.get_json(silent=True) or {}
        state = payload.get('state') or None
        outcome, row = service.set_state(ctx, row_id, state)
        if outcome == 'bad_state':
            return _bad('Такого состояния нет', 'RENTAL_BAD_STATE')
        if outcome == 'not_found':
            return _bad('Строка не найдена — обновите отчёт', 'RENTAL_NOT_FOUND', 404)
        if outcome == 'forbidden':
            return _bad('Состояние ставит ночная смена ООЗ', 'RENTAL_FORBIDDEN', 403)
        with db._get_cursor() as cursor:
            names = queries.user_names(cursor, [row.get('state_by')])
        return jsonify({'row': public_row(row, names)})

    # ─── Excel ───────────────────────────────────────────────────────────────

    @rental_route('/reports/<int:report_id>/export')
    def rental_export(report_id, ctx):
        with db._get_cursor() as cursor:
            report = queries.get_report(cursor, report_id)
            if not report:
                return _bad('Отчёт не найден', 'RENTAL_NOT_FOUND', 404)
            rows = queries.rows_for_report(cursor, report_id)
        stream, _written = report_xlsx.build_workbook(report, rows, generated_by=ctx.get('name') or '',
                                                      generated_at=queries.now_almaty())
        filename = report_xlsx.report_filename(report)
        response = Response(stream.getvalue(),
                            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response.headers['Content-Disposition'] = "attachment; filename*=UTF-8''%s" % quote(filename)
        response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response

    # ═══ Наличие авто и направления (#391) ══════════════════════════════════

    _OUTCOMES = {
        'forbidden': ('Это действие вам не открыто', 'RENTAL_FORBIDDEN', 403),
        'not_found': ('Не найдено — обновите страницу', 'RENTAL_NOT_FOUND', 404),
        'no_card': ('Такой карточки машины уже нет — обновите наличие', 'RENTAL_NO_CARD', 404),
        'no_free': ('Свободных машин нет — направить нельзя', 'RENTAL_NO_FREE', 409),
        'photo_limit': ('Больше %d фотографий к одной карточке не добавить' % photos.MAX_PER_CARD,
                        'RENTAL_PHOTO_LIMIT', 409),
    }

    def _outcome(outcome, message=None):
        if outcome == 'bad_input':
            return _bad(message or 'Проверьте поля', 'RENTAL_BAD_INPUT', 400)
        text, code, status = _OUTCOMES.get(outcome, ('Не получилось', 'RENTAL_FAILED', 409))
        return _bad(text, code, status)

    def _need(allowed):
        return None if allowed else _bad('Это действие вам не открыто', 'RENTAL_FORBIDDEN', 403)

    def public_card(card, busy, with_keys, book=None, photo_rows=None):
        payload = {key: card.get(key) for key in ('id', 'title', 'car_class', 'price', 'year', 'fuel', 'color',
                                                  'age', 'insurance_class', 'insurance', 'first_day', 'office')}
        # Свободные — только ручные (ТЗ 3.1); ни разу не указаны — None, а не 0.
        payload['free_count'] = card.get('free_count') if card.get('free_at') else None
        payload['free_by'] = card.get('free_by_name') if card.get('free_at') else None
        payload['free_at'] = _iso(card.get('free_at'))
        payload['busy'] = busy.get(card['id'], {'in_rent': 0, 'booked': 0})
        # Тарифы Яндекса — из «Классификатора авто» (rental/tariffs.py); фото — подписанными адресами.
        payload['tariffs'] = tariffs.card_tariffs(book, card)
        payload['photos'] = photos.sign_urls(gcs, photo_rows or [])
        if with_keys:
            payload['yume_keys'] = card.get('yume_keys') or []
            payload['classifier_key'] = card.get('classifier_key')
        return payload

    def _card_payload(card_id, ctx):
        """Карточка целиком — с тарифами и фото: ответ правки заменяет карточку на экране без потерь."""
        with db._get_cursor() as cursor:
            card = queries.get_card(cursor, card_id)
            rows = queries.card_photos(cursor, [card_id]).get(card['id'], []) if card else []
        return public_card(card, {}, access.can_edit_cards(ctx), tariffs.book(), rows) if card else None

    def public_direction(item):
        return {
            'id': item['id'], 'card_id': item.get('card_id'), 'card_title': item['card_title'],
            'card_class': item.get('card_class'), 'card_terms': item.get('card_terms'), 'office': item.get('office'),
            'phone': item['phone'], 'park': item['park'], 'visit_at': _iso(item['visit_at']),
            'comment': item.get('comment'), 'status': item['status'],
            'status_label': catalog.STATUS_LABELS.get(item['status'], item['status']),
            'created_by': item['created_by'], 'created_by_name': item.get('created_by_name'),
            'created_at': _iso(item['created_at']), 'status_at': _iso(item.get('status_at')),
        }

    def public_log(entries, labels):
        return [{'field': entry['field'], 'label': labels.get(entry['field'], entry['field']),
                 'old': entry['old'], 'new': entry['new'], 'by': entry['by'], 'at': _iso(entry['at'])}
                for entry in entries]

    def _payload():
        return request.get_json(silent=True) or {}

    # ─── Наличие ─────────────────────────────────────────────────────────────

    @rental_route('/fleet')
    def rental_fleet(ctx):
        denied = _need(access.can_view_fleet(ctx))
        if denied:
            return denied
        try:
            service.refresh_fleet()
        except Exception:  # noqa: BLE001 — экран показывает прежний снимок, а не 500
            logging.exception('rental: не поставилось обновление снимка Yume')
        caps = access.capabilities(ctx)
        book = tariffs.book()
        with db._get_cursor() as cursor:
            cards = queries.cards(cursor)
            sync = queries.fleet_sync(cursor)
            parks = queries.sales_parks(cursor) if caps['send'] else []
            photo_rows = queries.card_photos(cursor, [card['id'] for card in cards])
        busy = catalog.count_busy(sync['cars'], cards)
        return jsonify({
            'capabilities': caps,
            'cards': [public_card(card, busy, caps['edit_cards'], book, photo_rows.get(card['id'])) for card in cards],
            # Отбор по тарифам — тарифы города из классификатора (владелец 28.10.2026); пусто — отбора нет.
            'tariffs': tariffs.city_tariffs(book),
            'sync': {'synced_at': _iso(sync['synced_at']), 'error': sync['error'],
                     'refreshing': service.fleet_refreshing()},
            'parks': parks,
            'office': {'name': catalog.OFFICE, 'phone': catalog.OFFICE_PHONE},
            'photos_max': photos.MAX_PER_CARD,
            'server_time': _iso(queries.now_almaty()),
        })

    @rental_route('/fleet/refresh', methods=('POST',))
    def rental_fleet_refresh(ctx):
        denied = _need(access.can_view_fleet(ctx))
        if denied:
            return denied
        return jsonify({'started': service.refresh_fleet(force=True)}), 202

    # ─── Карточки ────────────────────────────────────────────────────────────

    @rental_route('/cards', methods=('POST',))
    def rental_card_create(ctx):
        outcome, result = service.save_card(ctx, _payload())
        if outcome != 'ok':
            return _outcome(outcome, result)
        return jsonify({'card': _card_payload(result['id'], ctx)}), 201

    @rental_route('/cards/<int:card_id>', methods=('PUT', 'DELETE'))
    def rental_card_update(card_id, ctx):
        if request.method == 'DELETE':
            outcome = service.hide_card(ctx, card_id)
            return _outcome(outcome) if outcome != 'ok' else jsonify({'hidden': card_id})
        outcome, result = service.save_card(ctx, _payload(), card_id)
        if outcome != 'ok':
            return _outcome(outcome, result)
        return jsonify({'card': _card_payload(card_id, ctx)})

    @rental_route('/cards/<int:card_id>/free', methods=('PUT',))
    def rental_card_free(card_id, ctx):
        outcome, result = service.set_free_count(ctx, card_id, _payload().get('free_count'))
        if outcome != 'ok':
            return _outcome(outcome, result)
        return jsonify({'card': _card_payload(card_id, ctx)})

    @rental_route('/cards/<int:card_id>/log')
    def rental_card_log(card_id, ctx):
        denied = _need(access.can_edit_cards(ctx))
        if denied:
            return denied
        with db._get_cursor() as cursor:
            entries = queries.card_log(cursor, card_id)
        return jsonify({'items': public_log(entries, dict(catalog.CARD_LABELS, created='Карточка заведена',
                                                          hidden='Карточка скрыта'))})

    # ─── Фотографии карточки ─────────────────────────────────────────────────
    # Как у «Посылок»: один файл на запрос (очередь грузится по одному, осечка на третьем не уносит
    # два уехавших), кадр переводится в WebP, адреса — подписанные ссылки GCS.

    def _uploaded_file():
        """Файл из multipart: (файл, размер) либо (None, None). На multipart get_json() пуст."""
        item = request.files.get('file')
        if item is None:
            return None, None
        item.stream.seek(0, 2)
        size = item.stream.tell()
        item.stream.seek(0)
        return item, size

    @rental_route('/cards/<int:card_id>/photos', methods=('POST',))
    def rental_card_photo_add(card_id, ctx):
        denied = _need(access.can_edit_cards(ctx))
        if denied:
            return denied
        if (request.content_length or 0) > photos.MAX_BYTES:
            return _bad('Файл больше %d МБ' % (photos.MAX_BYTES // (1024 * 1024)), 'RENTAL_PHOTO_TOO_LARGE', 413)
        item, size = _uploaded_file()
        if item is None:
            return _bad('Файл не выбран', 'RENTAL_PHOTO_REJECTED')
        issue = photos.photo_issue(filename=item.filename, content_type=item.mimetype, size=size or 0)
        if issue:
            return _bad(issue, 'RENTAL_PHOTO_REJECTED')
        try:
            prepared = photos.prepare(item.read(), filename=item.filename, content_type=item.mimetype)
            bucket, blob_path, thumb_path = photos.upload(gcs, prepared)
        except photos.PhotoError as refusal:
            return _bad(refusal.message, refusal.code, refusal.status)
        except Exception:  # noqa: BLE001 — бакет не ответил: человеку — причина, в лог — трейсбек
            logging.exception('rental: фотография не уехала в бакет')
            return _bad('Не удалось сохранить фотографию', 'RENTAL_PHOTO_UPLOAD_FAILED', 502)
        refs = [(bucket, blob_path)] + ([(bucket, thumb_path)] if thumb_path else [])
        try:
            outcome, _photo_id = service.attach_photo(ctx, card_id, prepared, bucket=bucket, blob_path=blob_path,
                                                      thumb_path=thumb_path)
        except Exception:
            photos.drop_blobs(gcs, refs)
            raise
        if outcome != 'ok':
            photos.drop_blobs(gcs, refs)
            return _outcome(outcome)
        return jsonify({'card': _card_payload(card_id, ctx)}), 201

    @rental_route('/cards/<int:card_id>/photos/<int:photo_id>', methods=('DELETE',))
    def rental_card_photo_drop(card_id, photo_id, ctx):
        outcome, refs = service.drop_photo(ctx, card_id, photo_id)
        if outcome != 'ok':
            return _outcome(outcome)
        photos.drop_blobs(gcs, refs)
        return jsonify({'card': _card_payload(card_id, ctx)})

    @rental_route('/cards/<int:card_id>/photos/<int:photo_id>/cover', methods=('PUT',))
    def rental_card_photo_cover(card_id, photo_id, ctx):
        outcome = service.cover_photo(ctx, card_id, photo_id)
        if outcome != 'ok':
            return _outcome(outcome)
        return jsonify({'card': _card_payload(card_id, ctx)})

    # ─── Направления ─────────────────────────────────────────────────────────

    @rental_route('/directions', methods=('GET', 'POST'))
    def rental_directions(ctx):
        if request.method == 'POST':
            outcome, result = service.create_direction(ctx, _payload())
            if outcome != 'ok':
                return _outcome(outcome, result)
            return jsonify({'direction': public_direction(result)}), 201
        denied = _need(access.can_view_fleet(ctx))
        if denied:
            return denied
        see_all = access.can_see_all_directions(ctx)
        args = request.args

        def day(name):
            try:
                return date.fromisoformat(str(args.get(name) or ''))
            except ValueError:
                return None

        status = args.get('status') if args.get('status') in catalog.STATUSES else None
        page = max(1, _int(args.get('page')) or 1)
        digits = ''.join(ch for ch in str(args.get('q') or '') if ch.isdigit())[-10:]
        with db._get_cursor() as cursor:
            items, total = queries.directions(
                cursor, own_by=None if see_all else ctx['user_id'], date_from=day('from'), date_to=day('to'),
                status=status, operator_id=_int(args.get('operator')) if see_all else None,
                phone_digits=digits or None, limit=50, offset=(page - 1) * 50)
            operators = queries.direction_operators(cursor) if see_all else []
        return jsonify({
            'items': [public_direction(item) for item in items], 'total': total, 'page': page, 'page_size': 50,
            'operators': operators, 'all': see_all,
            'statuses': [{'value': key, 'label': catalog.STATUS_LABELS[key]} for key in catalog.STATUSES],
        })

    @rental_route('/directions/<int:direction_id>', methods=('PATCH',))
    def rental_direction_update(direction_id, ctx):
        outcome, result = service.update_direction(ctx, direction_id, _payload())
        if outcome != 'ok':
            return _outcome(outcome, result)
        return jsonify({'direction': public_direction(result)})

    @rental_route('/directions/<int:direction_id>/log')
    def rental_direction_log(direction_id, ctx):
        denied = _need(access.can_view_fleet(ctx))
        if denied:
            return denied
        with db._get_cursor() as cursor:
            item = queries.get_direction(cursor, direction_id)
            if not item or (not access.can_see_all_directions(ctx) and item['created_by'] != ctx['user_id']):
                return _outcome('not_found')
            entries = queries.direction_log(cursor, direction_id)
        for entry in entries:
            if entry['field'] == 'status':
                entry['old'] = catalog.STATUS_LABELS.get(entry['old'], entry['old'])
                entry['new'] = catalog.STATUS_LABELS.get(entry['new'], entry['new'])
        return jsonify({'items': public_log(entries, {'status': 'Статус', 'comment': 'Комментарий'})})

    # ─── Условия работы ──────────────────────────────────────────────────────

    def _public_version(version):
        return {'id': version['id'], 'blocks': version['blocks'], 'by': version['by'], 'at': _iso(version['at'])}

    @rental_route('/conditions', methods=('GET', 'POST'))
    def rental_conditions(ctx):
        if request.method == 'POST':
            outcome, result = service.save_conditions(ctx, _payload().get('blocks'))
            if outcome != 'ok':
                return _outcome(outcome, result)
            return jsonify({'current': _public_version(result)})
        denied = _need(access.can_view_fleet(ctx))
        if denied:
            return denied
        with db._get_cursor() as cursor:
            current = queries.conditions_current(cursor)
            versions = queries.conditions_versions(cursor)
        return jsonify({
            'current': _public_version(current) if current else None,
            'versions': [{'id': item['id'], 'by': item['by'], 'at': _iso(item['at'])} for item in versions],
            'can_edit': access.can_edit_conditions(ctx),
        })

    @rental_route('/conditions/<int:version_id>')
    def rental_conditions_version(version_id, ctx):
        denied = _need(access.can_view_fleet(ctx))
        if denied:
            return denied
        with db._get_cursor() as cursor:
            version = queries.conditions_version(cursor, version_id)
        if not version:
            return _outcome('not_found')
        return jsonify({'version': _public_version(version)})

    return bp
