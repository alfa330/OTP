# -*- coding: utf-8 -*-
"""Выкачка источников и фиксация суточных итогов «Воронки ОП».

Зачем вообще фиксировать, а не считать на лету
----------------------------------------------
Потому что СРМ переписывает прошлое. Замерено 11.09.2026 на живых данных: снимок
супервайзера за 01.09 против ответа партнёрской ручки через десять дней — состав
лидов совпал ровно (1125 против 1125), а исходы уехали: Дозвон 499 → 534,
Согласия 257 → 267. У одного оператора все 108 лидов ушли в «свободные», их
просто открепили. Операторы продолжают звонить по старым лидам, а статус у лида
в СРМ ОДИН, истории нет.

Если считать отчёт на лету, вчерашние цифры завтра будут другими, и объяснить это
человеку нечем — он их уже назвал на планёрке. Поэтому суточный итог пишется один
раз, а пересчёт возможен только явным «перечитать сутки», и тогда каждая
изменившаяся метрика уходит в `op_funnel_drift`.

Порядок работы
--------------
    1. открыть прогон в журнале (кто, что, за какой период);
    2. выкачать источник целиком в память — сутки отдела это тысячи строк, не
       миллионы, а частичная запись хуже отсутствующей;
    3. завести встреченных операторов в таблице сопоставления;
    4. разобрать лиды и ПЕРЕЗАПИСАТЬ сутки (удалить и вставить, а не обновить:
       лид может исчезнуть из выгрузки, и при обновлении он остался бы у нас
       навсегда, завышая «обработано»);
    5. собрать суточные итоги по реестру операторов — включая тех, у кого ноль;
    6. зафиксировать их;
    7. закрыть прогон со счётчиками.

Ошибка источника закрывает прогон статусом `error` с внятным текстом и НЕ летит
наружу пятисоткой: у ручек четыре разных отказа (401 на отозванном токене,
HTML-200 у Laravel, обрыв посреди пагинации, 422 на кривом периоде), и «почему
цифры не обновились» должно отвечаться из раздела, а не из логов Render.

Строка «не сопоставлен»
-----------------------
Лиды оператора, которого ещё не связали с сотрудником, не выбрасываются — они
складываются в строку `user_id = 0`. Без неё сумма по операторам не сходится с
итогом команды, и это выглядит как ошибка расчёта, а не как пробел в
сопоставлении, который чинится за минуту.
"""

import logging
from datetime import date, datetime, timedelta

from . import metrics, operator_match, queries, sources
from .schema import (DIRECTION_CODES, SOURCE_AMO, SOURCE_CRM_PAID_HIRE,
                     SOURCE_CRM_STREAM, SOURCE_CRM_TICKETS, SOURCE_MANUAL,
                     SOURCE_WAZZUP)

log = logging.getLogger(__name__)

# Какой источник кормит каждое направление.
DIRECTION_SOURCE = {
    'op_potok': SOURCE_CRM_STREAM,
    'op_yandex_reg': SOURCE_CRM_PAID_HIRE,
    'op_osnova': SOURCE_AMO,
    'op_verificator': SOURCE_WAZZUP,
}

# Потоки «Поток-1» и «Поток-2»: одно направление, две базы и две группы (14 и 38).
POTOK_STREAM_TYPES = (1, 2)

# У кого часы берутся по графику смен. Верификаторы работают в чатах, статусов
# iCORE Phone у них нет: за 01–11.09.2026 записи в daily_hours есть, а work_time
# во всех нулевой (проверено на проде). Решение владельца — брать план смены.
SCHEDULE_HOURS_DIRECTIONS = frozenset(('op_verificator',))

# Сколько последних суток перечитывает ночная джоба по умолчанию. Двое, а не
# одни: СРМ дописывает вчерашний день утром, и односуточное окно теряло бы
# поздние правки.
NIGHTLY_WINDOW_DAYS = 2

# Потолок периода одной выгрузки. У «Потока» это ~100 тысяч лидов в месяц —
# столько в память влезает, а квартал уже нет.
MAX_PERIOD_DAYS = 62


def _days(day_from, day_to):
    out, current = [], day_from
    while current <= day_to:
        out.append(current)
        current += timedelta(days=1)
    return out


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()


def _seniority_months(hire_date, on_day):
    if not hire_date:
        return None
    hire = _as_date(hire_date)
    return max(0, (on_day.year - hire.year) * 12 + (on_day.month - hire.month))


class SyncError(RuntimeError):
    """Отказ источника, который надо показать человеку, а не спрятать в лог."""


# ── Выкачка источников ───────────────────────────────────────────────────────

def _pull_stream(cursor, direction_code, day_from, day_to, reason_index):
    """Оба потока одного направления. Возвращает (строки, встреченные, всего лидов)."""
    from .crm_client import CrmFunnelClient

    client = CrmFunnelClient.from_config()
    owner_map = queries.resolve_operator_map(cursor, SOURCE_CRM_STREAM)
    rows, seen, total = [], {}, 0
    for stream_type in POTOK_STREAM_TYPES:
        leads = list(client.fetch_stream_leads(day_from, day_to, stream_type=stream_type))
        total += len(leads)
        part, part_seen = sources.stream_rows(leads, stream_type, direction_code,
                                              owner_map, reason_index)
        rows.extend(part)
        seen.update(part_seen)
    return rows, seen, total


def _pull_paid_hire(cursor, direction_code, day_from, day_to):
    from .crm_client import CrmFunnelClient

    client = CrmFunnelClient.from_config()
    owner_map = queries.resolve_operator_map(cursor, SOURCE_CRM_PAID_HIRE)
    leads = list(client.fetch_paid_hire_leads(day_from, day_to))
    rows, seen = sources.paid_hire_rows(leads, direction_code, owner_map)
    return rows, seen, len(leads)


def _pull_tickets(cursor, direction_code, day_from, day_to):
    """Обращения СРМ за период → суточная нагрузка по обработчику.

    Сопоставление у обращений своё (источник `crm_tickets`): в них человек
    называется так, как его завели в СРМ, и это третье написание после портала и
    Wazzup. Неизвестных заводим в таблицу — пусть супервайзер свяжет.

    Отказ ручки РОНЯЕТ выгрузку направления, и это осознанно. Соблазн «показать
    хотя бы чаты» приводит к худшему из возможных: сутки фиксируются с нулевыми
    тикетами, прогон помечается «ок», и назавтра эти сутки уже не перечитаются —
    ноль останется навсегда, а объяснить его будет нечем. Упавший прогон честнее:
    он виден в журнале с причиной, а следующей ночью сутки выгрузятся заново,
    потому что зафиксировать их не успели.
    """
    from .crm_client import TICKET_SLUGS, CrmFunnelClient

    client = CrmFunnelClient.from_config()
    rows = list(client.fetch_tickets(day_from, day_to, slugs=list(TICKET_SLUGS)))

    queries.touch_operator_map(cursor, SOURCE_CRM_TICKETS,
                               sources.tickets_assignees_seen(rows), direction_code)
    name_map = {
        key.strip().lower(): user_id
        for key, user_id in queries.resolve_operator_map(cursor, SOURCE_CRM_TICKETS).items()
    }
    return sources.tickets_daily(rows, name_map)


def _pull_amo(cursor, direction_code, day_from, day_to):
    """Сделки «Основы» плюс автосвязка ответственных с сотрудниками.

    Справочник пользователей amoCRM открылся 11.09.2026 (учётка стала
    администратором), поэтому связка строится сама: по корпоративной почте, с
    запасом на разнописание транслитерации. Связываем ТОЛЬКО однозначное и
    только там, где человек ещё не решил сам, — ручное решение сильнее нашего.

    Без справочника (403, отозвали права) всё продолжает работать: в отчёте
    останутся числовые id, и их свяжут на экране сопоставления.
    """
    leads, stage_names, amo_users = sources.fetch_amo_sales_leads(day_from, day_to)

    if amo_users:
        people = queries.direction_people(cursor, direction_code)
        matched = operator_match.auto_link(amo_users, people)
        if matched['links']:
            queries.touch_operator_map(
                cursor, SOURCE_AMO,
                {code: item['amo_name'] or item['amo_email']
                 for code, item in matched['links'].items()},
                direction_code)
            queries.fill_operator_map(
                cursor, SOURCE_AMO,
                {code: item['user_id'] for code, item in matched['links'].items()})
        if matched['ambiguous']:
            log.info('op_funnel: под %d учёток amoCRM подошло больше одного сотрудника — '
                     'оставлены человеку', len(matched['ambiguous']))

    owner_map = queries.resolve_operator_map(cursor, SOURCE_AMO)
    rows, seen = sources.amo_rows(leads, stage_names, direction_code, owner_map)
    return rows, seen, len(leads)


def _writable_days(cursor, direction_code, day_from, day_to, force):
    """Сутки, чьи лиды и причины можно переписать.

    Это ровно те сутки, чей итог мы вправе переписать: ещё идущие, ещё не
    зафиксированные и — при `force` — все. Зафиксированные закрытые сутки не
    трогаем, чтобы расшифровка не разъехалась с таблицей.
    """
    days = _days(day_from, day_to)
    if force:
        return days
    today = date.today()
    frozen = queries.frozen_days(cursor, direction_code, day_from, day_to)
    return [day for day in days if day >= today or day not in frozen]


def _merge_load(rows, count_key, seconds_key):
    """Сложить нагрузку по (оператор, сутки), а не оставить последнюю строку.

    Возвращает {(user_id, work_day): {count_key, seconds_key}}. Среднее время
    взвешивается числом единиц: иначе автор с двумя чатами тянул бы среднее так
    же сильно, как автор с двумястами.
    """
    merged = {}
    for row in rows or []:
        key = (row.get('user_id') or 0, row['work_day'])
        item = merged.get(key)
        if item is None:
            item = merged[key] = {count_key: 0, '_weighted': 0.0, '_weight': 0}
        count = int(row.get(count_key) or 0)
        item[count_key] += count
        seconds = row.get(seconds_key)
        if seconds is not None and count > 0:
            item['_weighted'] += float(seconds) * count
            item['_weight'] += count
    for item in merged.values():
        weight = item.pop('_weight')
        weighted = item.pop('_weighted')
        item[seconds_key] = round(weighted / weight, 1) if weight else None
    return merged


# ── Сборка суточных итогов ───────────────────────────────────────────────────

def _daily_rows(cursor, direction_code, day_from, day_to, lead_rows, chat_rows, manual_rows,
                ticket_rows=None):
    """Собрать строки `op_funnel_daily` по реестру операторов и периоду.

    Реестр ведущий, а не лиды: оператор без единого звонка обязан остаться в
    таблице — ради него отчёт и открывают.
    """
    source = DIRECTION_SOURCE.get(direction_code, SOURCE_CRM_STREAM)
    registry = queries.operators_registry(cursor, direction_code, day_from, day_to)
    by_user = {item['user_id']: item for item in registry}

    fallback = direction_code in SCHEDULE_HOURS_DIRECTIONS
    hours = queries.hours_for_period(cursor, list(by_user), day_from, day_to,
                                     fallback_to_schedule=fallback)

    # Лиды по (оператор, сутки). Несопоставленные идут в ноль — см. шапку.
    buckets = {}
    for row in lead_rows:
        key = (row.get('user_id') or 0, row['work_day'])
        buckets.setdefault(key, []).append(row)

    # Чаты и тикеты СКЛАДЫВАЕМ по ключу, а не перезаписываем.
    #
    # Несопоставленные операторы все идут под user_id = 0, и простое присваивание
    # оставляло бы от них одного — последнего в списке. На проде это значит:
    # трое неизвестных авторов за сутки, в строке «Не сопоставлен» чаты только
    # одного, а разница просто исчезает из итога команды. Время ответа при этом
    # усредняем по числу чатов: у автора с двумя чатами и у автора с двумястами
    # вес разный.
    chats = _merge_load(chat_rows, count_key='chats', seconds_key='reply_seconds')
    tickets_by_key = _merge_load(ticket_rows, count_key='tickets',
                                 seconds_key='handle_seconds')
    manual = {(row['user_id'], row['work_day']): row for row in (manual_rows or [])}

    # Кого вообще показывать: реестр плюс все, у кого есть хоть что-то за период.
    seen_keys = set(buckets) | set(chats) | set(manual) | set(tickets_by_key)
    people = set(by_user) | {user_id for user_id, _ in seen_keys}

    out = []
    for work_day in _days(day_from, day_to):
        targets_by_shift = {}
        for user_id in sorted(people):
            person = by_user.get(user_id) or {}
            hour_row = hours.get((user_id, work_day)) or {}
            shift_kind = hour_row.get('shift_kind') or 'day'
            if shift_kind not in targets_by_shift:
                targets_by_shift[shift_kind] = queries.targets_for(
                    cursor, direction_code, work_day, shift_kind)
            targets = targets_by_shift[shift_kind]

            manual_row = manual.get((user_id, work_day)) or {}
            work_hours = hour_row.get('hours') or 0.0
            hours_source = hour_row.get('source') or 'phone'
            if manual_row.get('work_hours') is not None:
                # Ручная строка сильнее расчётной: её внёс человек, который
                # смотрел в график и в переписку.
                work_hours = float(manual_row['work_hours'])
                hours_source = 'manual'

            acc = metrics.aggregate_leads(buckets.get((user_id, work_day), []))
            # `reached` нужен плану «Яндекс Регистрации»: там план согласий
            # считается от фактических дозвонов через целевую конверсию.
            plan = metrics.plan_for_day(work_hours, targets, reached=acc['reached'])

            chat_row = chats.get((user_id, work_day)) or {}
            chat_count = manual_row.get('chats')
            if chat_count is None:
                chat_count = chat_row.get('chats') or 0
            reply_seconds = manual_row.get('chat_reply_seconds')
            if reply_seconds is None:
                reply_seconds = chat_row.get('reply_seconds')
            # Тикеты: ручная выгрузка сильнее автоматической. Человек грузит файл
            # именно тогда, когда автоматике верить нельзя, — например пока в
            # периметр токена СРМ не добавили учётки направления.
            ticket_row = tickets_by_key.get((user_id, work_day)) or {}
            tickets = manual_row.get('tickets')
            if tickets is None:
                tickets = ticket_row.get('tickets') or 0
            tickets = int(tickets or 0)
            ticket_seconds = manual_row.get('ticket_handle_seconds')
            if ticket_seconds is None:
                ticket_seconds = ticket_row.get('handle_seconds')

            if source == SOURCE_WAZZUP:
                # У Верификаторов воронки обзвона нет: «обработано» — это чаты
                # плюс тикеты, ровно как на листе супервайзера (задача #305,
                # колонка «Обработано» = «Чаты WZ» + «Тикеты»). Считать его по
                # дозвонам здесь значит показать ноль там, где человек отработал
                # смену, — и весь таб выглядел бы сломанным.
                handled = int(chat_count or 0) + tickets
            else:
                handled = metrics.handled_for_source(acc, source)

            has_anything = (
                acc['leads_total'] or work_hours or chat_count
                or tickets or user_id in by_user
            )
            if not has_anything:
                continue

            out.append({
                'direction_code': direction_code,
                'work_day': work_day,
                'user_id': user_id,
                'rate': person.get('rate') or 0,
                'shift_kind': shift_kind,
                'group_id': person.get('group_id') or hour_row.get('group_id'),
                'work_hours': round(float(work_hours), 2),
                'hours_source': hours_source,
                'handled': handled,
                'reached': acc['reached'],
                'not_reached': acc['not_reached'],
                'agreed': acc['agreed'],
                'succeeded': acc['succeeded'],
                'rejected': acc['rejected'],
                'untargeted': acc['untargeted'],
                'callbacks': acc['callbacks'],
                'inbound': acc['inbound'],
                'plan_reached': plan['plan_reached'],
                'plan_agreed': plan['plan_agreed'],
                'chats': int(chat_count or 0),
                'tickets': tickets,
                'chat_reply_seconds': reply_seconds,
                'ticket_handle_seconds': ticket_seconds,
                'quality_score': None,
                'extra': {
                    'moved': acc['moved'],
                    'leads_total': acc['leads_total'],
                    'seniority_months': _seniority_months(person.get('hire_date'), work_day),
                    'sales': manual_row.get('sales'),
                },
            })
    return out


# ── Основной проход ──────────────────────────────────────────────────────────

def sync_direction(db, direction_code, day_from, day_to, force=False, started_by=None,
                   note=''):
    """Выгрузить и зафиксировать период одного направления.

    Возвращает словарь-итог: сколько лидов видели, сколько записали, сколько
    суток зафиксировали и переписали, сколько операторов не сопоставлено.
    """
    if direction_code not in DIRECTION_CODES:
        raise SyncError('Неизвестное направление: %s' % direction_code)
    day_from = _as_date(day_from)
    day_to = _as_date(day_to)
    if day_to < day_from:
        raise SyncError('Конец периода раньше начала')
    if (day_to - day_from).days + 1 > MAX_PERIOD_DAYS:
        raise SyncError('Период больше %d суток — выгружайте частями' % MAX_PERIOD_DAYS)

    source = DIRECTION_SOURCE.get(direction_code, SOURCE_CRM_STREAM)
    summary = {
        'direction_code': direction_code,
        'source': source,
        'period_from': day_from.isoformat(),
        'period_to': day_to.isoformat(),
        'leads_seen': 0,
        'leads_written': 0,
        'days_frozen': 0,
        'days_redone': 0,
        'unmapped': 0,
        'drift_rows': 0,
        'status': 'ok',
        'error': None,
    }

    with db._get_cursor() as cursor:
        run_id = queries.start_run(cursor, direction_code, source, day_from, day_to,
                                   started_by, note)
        summary['run_id'] = run_id

    try:
        with db._get_cursor() as cursor:
            reason_index = _reason_index(cursor, source)

            lead_rows, seen, total = [], {}, 0
            chat_rows = []
            ticket_rows = []

            if direction_code == 'op_potok':
                lead_rows, seen, total = _pull_stream(cursor, direction_code, day_from,
                                                      day_to, reason_index)
            elif direction_code == 'op_yandex_reg':
                lead_rows, seen, total = _pull_paid_hire(cursor, direction_code,
                                                         day_from, day_to)
            elif direction_code == 'op_osnova':
                lead_rows, seen, total = _pull_amo(cursor, direction_code, day_from, day_to)
            elif direction_code == 'op_verificator':
                wz_map = queries.resolve_wazzup_map(cursor)
                chat_rows = sources.wazzup_daily(cursor, day_from, day_to, wz_map)
                total = sum(row['chats'] for row in chat_rows)
                # Сопоставление у Wazzup своё и уже заполненное; здесь только
                # показываем в разделе тех, кого в нём ещё нет.
                seen_authors = sources.wazzup_authors_seen(cursor, day_from, day_to)
                queries.touch_operator_map(cursor, SOURCE_WAZZUP, seen_authors,
                                           direction_code)
                ticket_rows = _pull_tickets(cursor, direction_code, day_from, day_to)
                total += sum(row['tickets'] for row in ticket_rows)

            summary['leads_seen'] = total

            if seen:
                queries.touch_operator_map(cursor, source, seen, direction_code)

            if lead_rows:
                # Пополнение справочника причин: новая причина обязана появиться
                # в разбивке в тот же день, а корзину человек поправит потом.
                dictionary = {}
                for row in lead_rows:
                    code = row.get('reason_code')
                    if code and code not in dictionary:
                        dictionary[code] = {
                            'source': source,
                            'reason_code': code,
                            'title': row.get('reason_raw') or code,
                            'bucket': row.get('reason_bucket') or '',
                        }
                queries.upsert_reason_dict(cursor, list(dictionary.values()))

                # Лиды и разбивку причин переписываем ТОЛЬКО у тех суток, чей итог
                # мы вправе переписать.
                #
                # Иначе таблица и расшифровка расходятся: карточка оператора
                # показывает вчерашний зафиксированный дозвон, а клик по причине
                # открывает сегодняшний список лидов, который с ним не сходится.
                # Человек видит «40 отказов» в строке и 47 фамилий за ней и
                # перестаёт верить обоим числам.
                days = _writable_days(cursor, direction_code, day_from, day_to, force)
                if days:
                    if direction_code == 'op_potok':
                        for stream_type in POTOK_STREAM_TYPES:
                            queries.delete_leads_for_days(cursor, direction_code, source,
                                                          stream_type, days)
                    else:
                        queries.delete_leads_for_days(cursor, direction_code, source, 0, days)
                    writable = set(days)
                    fresh_leads = [row for row in lead_rows if row['work_day'] in writable]
                    summary['leads_written'] = queries.upsert_leads(cursor, fresh_leads)
                    queries.replace_reasons(cursor, direction_code, days,
                                            sources.reasons_from_rows(fresh_leads))

            manual_rows = queries.read_manual_rows(cursor, direction_code, day_from, day_to)
            daily = _daily_rows(cursor, direction_code, day_from, day_to,
                                lead_rows, chat_rows, manual_rows, ticket_rows)
            frozen = queries.freeze_daily(cursor, daily, force=force, run_id=run_id,
                                          today=date.today())
            summary['days_frozen'] = frozen['frozen']
            summary['days_redone'] = frozen['redone']
            summary['drift_rows'] = frozen['drift']

            pending = queries.read_operator_map(cursor, sources=[source], only_pending=True)
            summary['unmapped'] = len(pending)

            queries.finish_run(cursor, run_id, status='ok', **_counters(summary))
    except Exception as exc:  # noqa: BLE001 — отказ обязан доехать до человека
        summary['status'] = 'error'
        summary['error'] = str(exc)
        log.exception('op_funnel: выгрузка %s за %s—%s не удалась',
                      direction_code, day_from, day_to)
        try:
            with db._get_cursor() as cursor:
                queries.finish_run(cursor, run_id, status='error', error=str(exc),
                                   **_counters(summary))
        except Exception:  # noqa: BLE001 — журнал не должен маскировать причину
            log.exception('op_funnel: не удалось записать отказ в журнал прогонов')
    return summary


# Счётчики для журнала — отдельной функцией, потому что в `summary` лежат ещё и
# служебные ключи (направление, статус, текст ошибки), а `finish_run` принимает
# их именованными аргументами: передать словарь целиком значит получить
# «got multiple values for keyword argument 'status'» ровно в тот момент, когда
# выгрузка уже отработала и результат потерять обиднее всего.
_COUNTER_KEYS = ('leads_seen', 'leads_written', 'days_frozen', 'days_redone',
                 'unmapped', 'drift_rows')


def _counters(summary):
    return {key: summary.get(key) or 0 for key in _COUNTER_KEYS}


def _reason_index(cursor, source):
    """Справочник причин из базы поверх сида в metrics.

    База сильнее кода: человек уже переложил причину в нужную корзину, и ночной
    синк не должен это откатывать.
    """
    index = dict(metrics.build_reason_index())
    for row in queries.read_reason_dict(cursor, source):
        key = metrics.normalize_reason_key(row.get('reason_code'))
        value = (row.get('reason_code'), row.get('title') or '', row.get('bucket') or '')
        if key:
            index[key] = value
        title_key = metrics.normalize_reason_key(row.get('title'))
        if title_key:
            index[title_key] = value
        for alias in (row.get('aliases') or []):
            alias_key = metrics.normalize_reason_key(alias)
            if alias_key:
                index[alias_key] = value
    return index


def sync_all(db, day_from=None, day_to=None, force=False, directions=None):
    """Ночной проход по всем направлениям.

    По умолчанию перечитывает последние ЗАКРЫТЫЕ сутки — вчера и позавчера, а не
    вчера и сегодня. Сегодняшние сутки в 05:20 пусты по определению: дневная
    смена ещё не звонила, часы не посчитаны. Фиксировать их нельзя, и брать в
    окно тоже незачем — человек увидит текущий день кнопкой «Обновить», и та
    перезапишет его сколько угодно раз (`freeze_daily` считает незакрытые сутки
    снимком, а не итогом).
    """
    today = date.today()
    day_to = _as_date(day_to) if day_to else (today - timedelta(days=1))
    day_from = _as_date(day_from) if day_from else (day_to - timedelta(days=NIGHTLY_WINDOW_DAYS - 1))

    out = []
    for direction_code in (directions or DIRECTION_CODES):
        try:
            out.append(sync_direction(db, direction_code, day_from, day_to, force=force,
                                      note='ночная выгрузка'))
        except Exception as exc:  # noqa: BLE001
            # Отказ одного направления не должен уносить остальные три.
            log.exception('op_funnel: ночная выгрузка %s не удалась', direction_code)
            out.append({'direction_code': direction_code, 'status': 'error',
                        'error': str(exc)})
    return out
