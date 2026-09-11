# -*- coding: utf-8 -*-
"""SQL раздела «Воронка ОП». Поверх ГОТОВОГО курсора, своего соединения нет.

Курсор приходит аргументом — как в `cdr/queries.py` и `parcels/queries.py`. Так
модуль не тянет пул к боевой базе при импорте и не решает за вызывающего, где
границы транзакции: фиксация суток и запись журнала прогона обязаны попасть в
одну транзакцию, иначе журнал скажет «ок» по данным, которых нет.

Почему реестр операторов свой
-----------------------------
Соблазн собрать список операторов «из тех, кто встретился в лидах» ломает весь
смысл отчёта: оператор, не сделавший НИ ОДНОГО звонка, исчезнет из таблицы — а
супервайзер открывает её как раз ради него. Поэтому реестр строится от кадровых
данных: членство в группе НА ДАТУ (`group_operator_memberships`) плюс код модели
расчёта группы, с откатом на `users.direction_id`.

Опора на код модели, а не на `direction_id`, — не каприз: переименование
направления обнуляет `direction_id` у операторов (это уже случалось), а
`calculation_model_code` живёт и на группе, и на направлении.

Почему часы читаются напрямую, а не методами Database
-----------------------------------------------------
Все готовые методы месячные, а у воронки период произвольный. Хуже другое:
чтение закрытого месяца САМО материализует снимок — то есть GET меняет данные.
Отчёту это не нужно и вредно, поэтому `daily_hours` читается прямым запросом.

Две ловушки часов, обе живые:

* **Ночная смена даёт ДВЕ строки** `daily_hours` на две календарные даты. Суммируя
  по периоду, сложить их правильно, а вот «часы за конкретные сутки» у ночной
  смены — это то, что записано в этих сутках, и удваивать нельзя.
* **`work_time` ноль там, где нет телефона.** Часы считаются из статусов iCORE
  Phone. У Верификаторов (группа 13) за 01–11.09.2026 записи есть, а `work_time`
  во всех нулевой: они работают в чатах. Для таких направлений часы берутся по
  графику смен (`work_shifts`), и источник помечается — в интерфейсе видно, что
  это план смены, а не факт с телефона.

Почему фиксация суток устроена именно так
-----------------------------------------
`freeze_daily` при `force=False` НЕ ТРОГАЕТ уже зафиксированные сутки. Это не
оптимизация, а защита: СРМ переписывает прошлое. Замерено 11.09.2026 — снимок
супервайзера за 01.09 против текущего ответа ручки дал тот же состав лидов (1125
против 1125), но Дозвон 499 → 534 и Согласия 257 → 267, а у одного оператора все
108 лидов ушли в «свободные». Без этой ветки вчерашний отчёт завтра был бы
другим, и объяснить это человеку нечем.

При `force=True` сутки перезаписываются, а КАЖДАЯ изменившаяся метрика уходит в
`op_funnel_drift`. Тот же приём, что у «Топа по регистрациям»: источник, который
переписывает прошлое, обязан оставлять след.
"""

import json
import logging
from datetime import date, datetime, timedelta

from . import metrics
from .schema import DIRECTION_CODES

log = logging.getLogger(__name__)

# Отдел продаж. Раздел живёт только в нём, и это не настройка.
SALES_DEPARTMENT_CODE = 'op'

# Строка «не сопоставленные» в суточном итоге. Ноль, а не NULL: колонка входит в
# первичный ключ, а NULL в ключе развалил бы ON CONFLICT.
UNMAPPED_USER_ID = 0

# Метрики, расхождение по которым записывается в журнал при пересчёте. Ставки и
# часов здесь нет: они меняются кадровыми решениями, а не переписыванием прошлого
# в СРМ, и «дрейфом» это называть неверно.
DRIFT_METRICS = (
    'handled', 'reached', 'not_reached', 'agreed', 'succeeded',
    'rejected', 'untargeted', 'callbacks', 'inbound', 'chats', 'tickets',
)


def _rows(cursor):
    """Список словарей из курсора. RealDictCursor в проекте не везде, поэтому
    собираем сами по cursor.description — так модуль работает с любым."""
    if cursor.description is None:
        return []
    columns = [column[0] for column in cursor.description]
    out = []
    for row in cursor.fetchall():
        if isinstance(row, dict):
            out.append(dict(row))
        else:
            out.append(dict(zip(columns, row)))
    return out


def _one(cursor):
    found = _rows(cursor)
    return found[0] if found else None


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_day(value):
    """Дата из того, что пришло. Строку принимаем: сутки могли приехать из JSON."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()


# ── Контекст доступа ─────────────────────────────────────────────────────────

def load_access_context(cursor, user_id):
    """Всё, что нужно `access.py`, одним походом в базу.

    Три запроса, а не один с джойнами: у супервайзера групп единицы, а джойн
    «пользователь × возглавляемые отделы × группы» размножил бы строки и потребовал
    бы дедупликации на питоне — дороже и запутаннее.
    """
    cursor.execute(
        """
        SELECT u.id AS user_id, u.name, u.role, u.department_id, u.direction_id,
               u.status,
               d.code AS department_code,
               dir.calculation_model_code AS direction_code
        FROM users u
        LEFT JOIN departments d ON d.id = u.department_id
        LEFT JOIN directions dir ON dir.id = u.direction_id
        WHERE u.id = %s
        """,
        (user_id,),
    )
    row = _one(cursor)
    if not row:
        return None

    cursor.execute(
        "SELECT id, code FROM departments WHERE head_user_id = %s AND COALESCE(is_active, TRUE)",
        (user_id,),
    )
    headed = _rows(cursor)

    # Группы супервайзера НА СЕГОДНЯ. Закрытое датой членство не даёт прав:
    # человека сняли с группы, и чужие цифры он видеть не должен.
    cursor.execute(
        """
        SELECT gsm.group_id, g.calculation_model_code, g.name AS group_name
        FROM group_supervisor_memberships gsm
        JOIN groups g ON g.id = gsm.group_id
        WHERE gsm.supervisor_id = %s
          AND gsm.start_date <= CURRENT_DATE
          AND (gsm.end_date IS NULL OR gsm.end_date >= CURRENT_DATE)
        """,
        (user_id,),
    )
    groups = _rows(cursor)

    return {
        'user_id': row.get('user_id'),
        'name': row.get('name') or '',
        'role': row.get('role') or '',
        'status': row.get('status') or '',
        'department_id': row.get('department_id'),
        'department_code': row.get('department_code') or '',
        'direction_code': row.get('direction_code') or '',
        'headed_department_ids': [item.get('id') for item in headed],
        'headed_department_codes': [item.get('code') or '' for item in headed],
        'supervisor_group_ids': [item.get('group_id') for item in groups],
        'group_directions': {
            item.get('group_id'): (item.get('calculation_model_code') or '')
            for item in groups
        },
    }


# ── Реестр операторов ────────────────────────────────────────────────────────

def operators_registry(cursor, direction_code, day_from, day_to):
    """Кого таб обязан показать за период, включая тех, у кого ноль.

    Человек попадает в реестр, если ХОТЬ ОДИН день периода он состоял в группе
    этого направления. Уволенные внутри периода остаются (они работали, их цифры
    в сумме команды) и помечаются `is_fired`.
    """
    cursor.execute(
        """
        WITH membership AS (
            SELECT gom.operator_id,
                   gom.group_id,
                   g.name AS group_name,
                   g.calculation_model_code AS model_code,
                   GREATEST(gom.start_date, %(day_from)s::date) AS from_day,
                   LEAST(COALESCE(gom.end_date, %(day_to)s::date), %(day_to)s::date) AS to_day
            FROM group_operator_memberships gom
            JOIN groups g ON g.id = gom.group_id
            WHERE g.calculation_model_code = %(direction)s
              AND gom.start_date <= %(day_to)s::date
              AND (gom.end_date IS NULL OR gom.end_date >= %(day_from)s::date)
        ),
        -- Люди без членства, но с направлением в карточке: членство могли не
        -- завести, а человек работает. Без этой ветки его строки нет вовсе.
        by_direction AS (
            SELECT u.id AS operator_id, NULL::int AS group_id, ''::varchar AS group_name,
                   dir.calculation_model_code AS model_code,
                   %(day_from)s::date AS from_day, %(day_to)s::date AS to_day
            FROM users u
            JOIN directions dir ON dir.id = u.direction_id
            WHERE dir.calculation_model_code = %(direction)s
              AND NOT EXISTS (SELECT 1 FROM membership m WHERE m.operator_id = u.id)
        ),
        joined AS (
            SELECT * FROM membership
            UNION ALL
            SELECT * FROM by_direction
        )
        SELECT j.operator_id AS user_id,
               u.name,
               u.role,
               u.status,
               u.rate,
               u.hire_date,
               MIN(j.group_id) AS group_id,
               MIN(j.group_name) AS group_name,
               MIN(j.from_day) AS from_day,
               MAX(j.to_day) AS to_day
        FROM joined j
        JOIN users u ON u.id = j.operator_id
        GROUP BY j.operator_id, u.name, u.role, u.status, u.rate, u.hire_date
        ORDER BY u.name
        """,
        {'direction': direction_code, 'day_from': day_from, 'day_to': day_to},
    )
    out = []
    for row in _rows(cursor):
        status = (row.get('status') or '').strip().lower()
        out.append({
            'user_id': row.get('user_id'),
            'name': row.get('name') or '',
            'role': row.get('role') or '',
            'rate': _num(row.get('rate'), 0.0),
            'hire_date': row.get('hire_date'),
            'group_id': row.get('group_id'),
            'group_name': row.get('group_name') or '',
            'is_fired': status in ('fired', 'уволен', 'dismissed'),
            'from_day': row.get('from_day'),
            'to_day': row.get('to_day'),
        })
    return out


# ── Часы ─────────────────────────────────────────────────────────────────────

# Ночной сменой считаем ту, что кончается не позже, чем началась (перевалила за
# полночь), либо начинается с 20:00 и позже. Вторая половина условия нужна для
# смен «20:00–23:59», формально не переходящих сутки. Граница 20:00 взята из
# аукциона СЗоВ, где ночь формализована как 20:00–08:00 — другой формализации
# «ночи» в проекте нет, и заводить вторую значит развести их при первой правке.
NIGHT_SHIFT_FROM_HOUR = 20


def hours_for_period(cursor, user_ids, day_from, day_to, fallback_to_schedule=False):
    """{(user_id, day): {'hours': float, 'source': 'phone'|'schedule', 'shift_kind': ...}}

    `fallback_to_schedule` включается для направлений без телефона: тогда нулевые
    часы заменяются длиной смены из графика. Замена именно НУЛЕВЫХ, а не всех:
    если статусы по человеку всё-таки пришли, факт честнее плана.
    """
    if not user_ids:
        return {}
    ids = tuple(int(value) for value in user_ids)

    cursor.execute(
        """
        SELECT dh.operator_id, dh.day, dh.work_time, dh.group_id
        FROM daily_hours dh
        WHERE dh.operator_id IN %(ids)s AND dh.day BETWEEN %(day_from)s AND %(day_to)s
        """,
        {'ids': ids, 'day_from': day_from, 'day_to': day_to},
    )
    out = {}
    for row in _rows(cursor):
        key = (row.get('operator_id'), row.get('day'))
        out[key] = {
            'hours': _num(row.get('work_time'), 0.0),
            'source': 'phone',
            'group_id': row.get('group_id'),
            'shift_kind': 'day',
        }

    # График смен нужен всегда — из него берётся признак «день/ночь», которого в
    # daily_hours нет вовсе. Часы из него подставляются только по требованию.
    cursor.execute(
        """
        SELECT ws.operator_id, ws.shift_date, ws.start_time, ws.end_time, ws.shift_type
        FROM work_shifts ws
        WHERE ws.operator_id IN %(ids)s AND ws.shift_date BETWEEN %(day_from)s AND %(day_to)s
        """,
        {'ids': ids, 'day_from': day_from, 'day_to': day_to},
    )
    for row in _rows(cursor):
        key = (row.get('operator_id'), row.get('shift_date'))
        start = row.get('start_time')
        end = row.get('end_time')
        planned = 0.0
        shift_kind = 'day'
        if start is not None and end is not None:
            start_h = start.hour + start.minute / 60.0
            end_h = end.hour + end.minute / 60.0
            overnight = end_h <= start_h
            planned = (end_h + 24.0 - start_h) if overnight else (end_h - start_h)
            if overnight or start_h >= NIGHT_SHIFT_FROM_HOUR:
                shift_kind = 'night'
        current = out.get(key)
        if current is None:
            out[key] = {
                'hours': round(planned, 2) if fallback_to_schedule else 0.0,
                'source': 'schedule' if (fallback_to_schedule and planned > 0) else 'phone',
                'group_id': None,
                'shift_kind': shift_kind,
            }
            continue
        current['shift_kind'] = shift_kind
        if fallback_to_schedule and current['hours'] <= 0 and planned > 0:
            current['hours'] = round(planned, 2)
            current['source'] = 'schedule'
    return out


# ── Нормы и пороги ───────────────────────────────────────────────────────────

def targets_for(cursor, direction_code, day, shift_kind='day'):
    """Нормы направления, действовавшие В ЭТИ СУТКИ.

    Берётся последняя запись с `effective_from <= day`. Именно поэтому нормы
    хранятся строками с датой, а не константами: поднятый сегодня план не должен
    переписывать выполнение за прошлый месяц.

    Значение для конкретной смены сильнее общего (`any`): у Верификаторов таргеты
    ночи отличаются от дневных вдвое.
    """
    cursor.execute(
        """
        SELECT DISTINCT ON (metric, shift_kind) metric, shift_kind, value
        FROM op_funnel_targets
        WHERE direction_code = %(direction)s
          AND effective_from <= %(day)s
          AND shift_kind IN ('any', %(shift)s)
        ORDER BY metric, shift_kind, effective_from DESC
        """,
        {'direction': direction_code, 'day': day, 'shift': shift_kind},
    )
    generic, specific = {}, {}
    for row in _rows(cursor):
        bucket = specific if (row.get('shift_kind') == shift_kind) else generic
        bucket[row.get('metric')] = _num(row.get('value'))
    generic.update(specific)
    return generic


def targets_by_shift(cursor, direction_code, day):
    """Нормы для ОБЕИХ смен сразу: {'day': {...}, 'night': {...}}.

    В таблице за период дневные и ночные операторы стоят рядом, и сравнивать их
    с одним таргетом нельзя: у Верификаторов ночная норма вдвое мягче (время
    ответа 180 секунд против 120, чаты 12 в час против 16). Один общий набор
    показывал бы ночной смене красное там, где она в норме.
    """
    return {kind: targets_for(cursor, direction_code, day, kind)
            for kind in ('day', 'night')}


def read_targets(cursor, direction_code):
    cursor.execute(
        """
        SELECT direction_code, shift_kind, metric, effective_from, value, updated_at
        FROM op_funnel_targets
        WHERE direction_code = %s
        ORDER BY metric, shift_kind, effective_from DESC
        """,
        (direction_code,),
    )
    return _rows(cursor)


def set_target(cursor, direction_code, shift_kind, metric, value, effective_from, updated_by):
    """Правка нормы — это НОВАЯ строка с датой, а не UPDATE прежней.

    Так прошлое остаётся посчитанным по тем нормам, что действовали тогда, а в
    журнале видно, кто и когда поднял план. Перезапись той же даты разрешена:
    поправить сегодняшнюю опечатку нужно без следа в истории.
    """
    cursor.execute(
        """
        INSERT INTO op_funnel_targets
            (direction_code, shift_kind, metric, effective_from, value, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (direction_code, shift_kind, metric, effective_from)
        DO UPDATE SET value = EXCLUDED.value,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = NOW()
        """,
        (direction_code, shift_kind, metric, effective_from, value, updated_by),
    )


# ── Лиды ─────────────────────────────────────────────────────────────────────

_LEAD_COLUMNS = (
    'direction_code', 'source', 'stream_type', 'lead_key', 'work_day', 'user_id',
    'owner_raw', 'stage_raw', 'call_status', 'dialog_status', 'reason_raw',
    'reason_code', 'sub_reason_raw', 'reach_outcome', 'dialog_outcome',
    'reason_bucket', 'full_name', 'phone', 'park_name', 'city', 'base_title',
    'comment', 'created_at', 'taken_at', 'updated_at',
)


def delete_leads_for_days(cursor, direction_code, source, stream_type, days):
    """Снести лиды этих суток перед вставкой свежих.

    Именно снести, а не «вставить с обновлением»: лид может ИСЧЕЗНУТЬ из выгрузки
    (его открепили от оператора, перенесли в другую базу, удалили). При upsert без
    удаления он остался бы в наших сутках навсегда и завышал бы «обработано».
    """
    if not days:
        return 0
    cursor.execute(
        """
        DELETE FROM op_funnel_leads
        WHERE direction_code = %s AND source = %s AND stream_type = %s
          AND work_day = ANY(%s)
        """,
        (direction_code, source, stream_type, list(days)),
    )
    return cursor.rowcount or 0


def upsert_leads(cursor, rows, page=1000):
    """Вставка пачками.

    ON CONFLICT нужен не «на всякий случай»: СРМ дописывает лиды прямо во время
    обхода страниц, и один лид приезжает дважды — на своей странице и на
    следующей.

    Но одного ON CONFLICT мало. Postgres не даёт `DO UPDATE` тронуть одну и ту же
    строку дважды в ОДНОМ операторе: два одинаковых ключа в пачке роняют весь
    INSERT ошибкой 21000 «ON CONFLICT DO UPDATE command cannot affect row a second
    time». То есть ровно тот случай, ради которого ON CONFLICT и ставили, ронял
    бы всю выгрузку. Поэтому дубли снимаем ДО вставки, оставляя последнюю версию
    строки — она свежее.
    """
    if not rows:
        return 0
    unique = {}
    for row in rows:
        unique[(row.get('direction_code'), row.get('source'),
                int(row.get('stream_type') or 0), row.get('lead_key'))] = row
    if len(unique) != len(rows):
        log.info('op_funnel: в выгрузке %d повторов лида, оставлены свежие версии',
                 len(rows) - len(unique))
    rows = list(unique.values())
    columns = ', '.join(_LEAD_COLUMNS)
    placeholders = '(' + ', '.join(['%s'] * len(_LEAD_COLUMNS)) + ')'
    updates = ', '.join(
        '%s = EXCLUDED.%s' % (name, name)
        for name in _LEAD_COLUMNS
        if name not in ('direction_code', 'source', 'stream_type', 'lead_key')
    )
    written = 0
    for start in range(0, len(rows), page):
        chunk = rows[start:start + page]
        values = []
        args = []
        for row in chunk:
            values.append(placeholders)
            args.extend(row.get(name) for name in _LEAD_COLUMNS)
        cursor.execute(
            "INSERT INTO op_funnel_leads (%s) VALUES %s "
            "ON CONFLICT (direction_code, source, stream_type, lead_key) "
            "DO UPDATE SET %s, captured_at = NOW()"
            % (columns, ', '.join(values), updates),
            args,
        )
        written += len(chunk)
    return written


def read_leads_page(cursor, direction_code, day_from, day_to, filters=None, limit=50, offset=0):
    """Страница лидов за причиной. Возвращает (строки, всего).

    Ради этой ручки лиды и хранятся: без неё клик по причине отказа некуда вести,
    а это прямой критерий приёмки ТЗ.
    """
    filters = filters or {}
    where = ["l.direction_code = %(direction)s", "l.work_day BETWEEN %(day_from)s AND %(day_to)s"]
    args = {'direction': direction_code, 'day_from': day_from, 'day_to': day_to,
            'limit': int(limit), 'offset': int(offset)}

    if filters.get('bucket'):
        where.append("l.reason_bucket = %(bucket)s")
        args['bucket'] = filters['bucket']
    if filters.get('reason_code'):
        where.append("l.reason_code = %(reason_code)s")
        args['reason_code'] = filters['reason_code']
    if filters.get('user_id') is not None:
        # Ноль — это строка «не сопоставлен»: у таких лидов user_id в базе NULL.
        if int(filters['user_id']) == UNMAPPED_USER_ID:
            where.append("l.user_id IS NULL")
        else:
            where.append("l.user_id = %(user_id)s")
            args['user_id'] = int(filters['user_id'])
    if filters.get('work_day'):
        where.append("l.work_day = %(work_day)s")
        args['work_day'] = filters['work_day']
    if filters.get('reach_outcome'):
        where.append("l.reach_outcome = %(reach_outcome)s")
        args['reach_outcome'] = filters['reach_outcome']
    if filters.get('dialog_outcome'):
        where.append("l.dialog_outcome = %(dialog_outcome)s")
        args['dialog_outcome'] = filters['dialog_outcome']
    if filters.get('stream_type'):
        where.append("l.stream_type = %(stream_type)s")
        args['stream_type'] = int(filters['stream_type'])

    clause = ' AND '.join(where)
    cursor.execute("SELECT COUNT(*) AS total FROM op_funnel_leads l WHERE " + clause, args)
    total = int((_one(cursor) or {}).get('total') or 0)

    cursor.execute(
        """
        SELECT l.lead_key, l.work_day, l.user_id, u.name AS operator_name, l.owner_raw,
               l.full_name, l.phone, l.park_name, l.city, l.base_title,
               l.stage_raw, l.call_status, l.dialog_status,
               l.reason_bucket, l.reason_code, l.reason_raw, l.comment,
               l.reach_outcome, l.dialog_outcome, l.stream_type,
               l.created_at, l.taken_at, l.updated_at
        FROM op_funnel_leads l
        LEFT JOIN users u ON u.id = l.user_id
        WHERE """ + clause + """
        ORDER BY l.work_day DESC, l.updated_at DESC NULLS LAST, l.lead_key
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        args,
    )
    return _rows(cursor), total


# ── Суточный итог ────────────────────────────────────────────────────────────

_DAILY_COLUMNS = (
    'direction_code', 'work_day', 'user_id', 'rate', 'shift_kind', 'group_id',
    'work_hours', 'hours_source',
    'handled', 'reached', 'not_reached', 'agreed', 'succeeded', 'rejected',
    'untargeted', 'callbacks', 'inbound',
    'plan_reached', 'plan_agreed',
    'chats', 'tickets', 'chat_reply_seconds', 'ticket_handle_seconds', 'quality_score',
    'extra',
)


def freeze_daily(cursor, rows, force=False, run_id=None, today=None):
    """Зафиксировать суточные итоги. Возвращает {'frozen', 'redone', 'drift'}.

    Три режима, и разница между ними принципиальная:

    * **Сутки ещё идут** (`work_day >= today`). Фиксировать нечего: смена не
      закончилась, и «итог» за них — это снимок на середину дня. Такие строки
      перезаписываются ВСЕГДА и без `force`, и расхождения по ним в журнал не
      идут — это не дрейф источника, а просто более свежий снимок.

      Без этой ветки раздел ломался наглухо: ночная выгрузка в 05:20 записывала
      сегодняшние сутки нулями (лидов ещё нет, часы не посчитаны), а назавтра те
      же сутки приходили как «вчера», натыкались на существующий ключ и молча
      пропускались. Каждый день навсегда оставался пустым, а журнал прогонов
      показывал «ок».

    * **Закрытые сутки, которых ещё нет в базе.** Пишем как есть.

    * **Закрытые сутки, которые уже зафиксированы.** Без `force` не трогаем
      вовсе: цифру, названную на планёрке, задним числом менять нельзя. С `force`
      перезаписываем, а каждая изменившаяся метрика уходит в `op_funnel_drift`.
    """
    if not rows:
        return {'frozen': 0, 'redone': 0, 'drift': 0}
    today = today or date.today()

    keys = [(row['direction_code'], row['work_day'], row['user_id']) for row in rows]
    cursor.execute(
        """
        SELECT direction_code, work_day, user_id, %s
        FROM op_funnel_daily
        WHERE (direction_code, work_day, user_id) IN %%s
        """ % ', '.join(DRIFT_METRICS),
        (tuple(keys),),
    )
    existing = {
        (row['direction_code'], row['work_day'], row['user_id']): row
        for row in _rows(cursor)
    }

    fresh, redo, drift = [], [], []
    for row in rows:
        key = (row['direction_code'], row['work_day'], row['user_id'])
        was = existing.get(key)
        if was is None:
            fresh.append(row)
            continue
        # Сутки ещё идут — обновляем всегда и молча: это не дрейф, а свежий снимок.
        if _as_day(row['work_day']) >= today:
            redo.append(row)
            continue
        if not force:
            continue
        redo.append(row)
        for metric in DRIFT_METRICS:
            before = _num(was.get(metric), 0.0)
            after = _num(row.get(metric), 0.0)
            if abs(before - after) > 1e-9:
                drift.append((row['direction_code'], row['work_day'], row['user_id'],
                              metric, before, after, run_id))

    _insert_daily(cursor, fresh, update=False)
    _insert_daily(cursor, redo, update=True)

    if drift:
        args = []
        values = []
        for item in drift:
            values.append('(%s, %s, %s, %s, %s, %s, NOW(), %s)')
            args.extend(item[:6] + (item[6],))
        cursor.execute(
            "INSERT INTO op_funnel_drift "
            "(direction_code, work_day, user_id, metric, was, became, noticed_at, run_id) "
            "VALUES " + ', '.join(values),
            args,
        )

    return {'frozen': len(fresh), 'redone': len(redo), 'drift': len(drift)}


def _insert_daily(cursor, rows, update):
    if not rows:
        return
    columns = ', '.join(_DAILY_COLUMNS)
    placeholders = '(' + ', '.join(['%s'] * len(_DAILY_COLUMNS)) + ')'
    values, args = [], []
    for row in rows:
        values.append(placeholders)
        for name in _DAILY_COLUMNS:
            value = row.get(name)
            if name == 'extra':
                value = json.dumps(value or {}, ensure_ascii=False)
            args.append(value)
    if update:
        updates = ', '.join(
            '%s = EXCLUDED.%s' % (name, name)
            for name in _DAILY_COLUMNS
            if name not in ('direction_code', 'work_day', 'user_id')
        )
        tail = "DO UPDATE SET %s, recaptured_at = NOW()" % updates
    else:
        # Без force столкновение означает «эти сутки уже зафиксированы» — молча
        # пропускаем, это штатный путь, а не ошибка.
        tail = "DO NOTHING"
    cursor.execute(
        "INSERT INTO op_funnel_daily (%s) VALUES %s "
        "ON CONFLICT (direction_code, work_day, user_id) %s"
        % (columns, ', '.join(values), tail),
        args,
    )


def frozen_days(cursor, direction_code, day_from, day_to):
    """Сутки периода, по которым итог уже зафиксирован.

    Нужны, чтобы не переписывать лиды и разбивку причин под уже названными
    числами: таблица и расшифровка за ними обязаны описывать один снимок.
    """
    cursor.execute(
        """
        SELECT DISTINCT work_day FROM op_funnel_daily
        WHERE direction_code = %s AND work_day BETWEEN %s AND %s
        """,
        (direction_code, day_from, day_to),
    )
    return {_as_day(row['work_day']) for row in _rows(cursor)}


def drop_orphan_daily(cursor, direction_code, days, keep):
    """Снести суточные строки, которых новый расчёт больше не даёт.

    Это не уборка ради чистоты, а исправление удвоения. Пример из первого
    прогона на проде: пока операторы не были сопоставлены, все 1008 лидов за
    03.09 лежали в строке «Не сопоставлен». После сопоставления они разошлись по
    людям, новый расчёт строки «Не сопоставлен» за эти сутки уже НЕ ДАЁТ — и
    старая осталась нетронутой, потому что `freeze_daily` обновляет только то,
    что ему передали. Итог за сутки стал 2016 вместо 1008.

    Тот же случай возникает, когда оператора перевели в другую группу или
    исключили из направления: его вчерашняя строка осталась бы висеть вечно.

    Трогаем только те сутки, которые и так вправе переписать (`days`), — на
    зафиксированных закрытых сутках ничего не удаляем.
    """
    if not days:
        return 0
    cursor.execute(
        """
        DELETE FROM op_funnel_daily
        WHERE direction_code = %s
          AND work_day = ANY(%s)
          AND (work_day, user_id) NOT IN %s
        """,
        (direction_code, list(days), tuple(keep) if keep else ((None, -1),)),
    )
    return cursor.rowcount or 0


def read_daily(cursor, direction_code, day_from, day_to, user_ids=None):
    where = ["d.direction_code = %(direction)s", "d.work_day BETWEEN %(day_from)s AND %(day_to)s"]
    args = {'direction': direction_code, 'day_from': day_from, 'day_to': day_to}
    if user_ids:
        where.append("d.user_id IN %(ids)s")
        args['ids'] = tuple(int(value) for value in user_ids)
    cursor.execute(
        """
        SELECT d.*, u.name AS operator_name, u.hire_date
        FROM op_funnel_daily d
        LEFT JOIN users u ON u.id = d.user_id
        WHERE """ + ' AND '.join(where) + """
        ORDER BY d.work_day, u.name NULLS FIRST
        """,
        args,
    )
    return _rows(cursor)


def read_reasons(cursor, direction_code, day_from, day_to, bucket=None, user_id=None):
    where = ["r.direction_code = %(direction)s", "r.work_day BETWEEN %(day_from)s AND %(day_to)s"]
    args = {'direction': direction_code, 'day_from': day_from, 'day_to': day_to}
    if bucket:
        where.append("r.bucket = %(bucket)s")
        args['bucket'] = bucket
    if user_id is not None:
        where.append("r.user_id = %(user_id)s")
        args['user_id'] = int(user_id)
    cursor.execute(
        """
        SELECT r.bucket, r.reason_code, MIN(r.reason_title) AS reason_title,
               SUM(r.leads) AS leads
        FROM op_funnel_reasons r
        WHERE """ + ' AND '.join(where) + """
        GROUP BY r.bucket, r.reason_code
        ORDER BY r.bucket, SUM(r.leads) DESC
        """,
        args,
    )
    return _rows(cursor)


def replace_reasons(cursor, direction_code, days, rows):
    """Разбивка по причинам переписывается вместе с сутками целиком.

    Частичное обновление здесь опаснее полного: исчезнувшая причина осталась бы
    в таблице навсегда (в проекте это уже ловили — «частичный пересчёт должен
    писать в свою колонку»).
    """
    if days:
        cursor.execute(
            "DELETE FROM op_funnel_reasons WHERE direction_code = %s AND work_day = ANY(%s)",
            (direction_code, list(days)),
        )
    if not rows:
        return 0
    values, args = [], []
    for row in rows:
        values.append('(%s, %s, %s, %s, %s, %s, %s)')
        args.extend((row['direction_code'], row['work_day'], row['user_id'], row['bucket'],
                     row['reason_code'], row.get('reason_title') or '', int(row.get('leads') or 0)))
    cursor.execute(
        "INSERT INTO op_funnel_reasons "
        "(direction_code, work_day, user_id, bucket, reason_code, reason_title, leads) "
        "VALUES " + ', '.join(values) +
        " ON CONFLICT (direction_code, work_day, user_id, bucket, reason_code) "
        "DO UPDATE SET leads = EXCLUDED.leads, reason_title = EXCLUDED.reason_title",
        args,
    )
    return len(rows)


# ── Сопоставление операторов ─────────────────────────────────────────────────

def touch_operator_map(cursor, source, seen, hint_direction=''):
    """Завести неизвестные имена и освежить отметку «видели».

    Строка заводится САМОЙ выгрузкой, чтобы в интерфейсе было видно, кого именно
    не хватает. Пока человек не сопоставлен, его лиды не теряются — они идут в
    строку «не сопоставлен», и расхождение видно сразу, а не через месяц.
    """
    if not seen:
        return 0
    values, args = [], []
    for key, name in seen.items():
        if not str(key or '').strip():
            continue
        values.append('(%s, %s, %s, %s, NOW(), NOW(), NOW())')
        args.extend((source, str(key)[:190], str(name or '')[:500], hint_direction))
    if not values:
        return 0
    cursor.execute(
        "INSERT INTO op_funnel_operator_map "
        "(source, external_key, external_name, hint_direction, first_seen_at, last_seen_at, updated_at) "
        "VALUES " + ', '.join(values) +
        " ON CONFLICT (source, external_key) DO UPDATE SET "
        " last_seen_at = NOW(),"
        # Имя освежаем: человека могли переименовать в СРМ, и в экране
        # сопоставления должно быть видно текущее написание.
        " external_name = CASE WHEN EXCLUDED.external_name <> '' "
        "                      THEN EXCLUDED.external_name "
        "                      ELSE op_funnel_operator_map.external_name END,"
        " hint_direction = CASE WHEN op_funnel_operator_map.hint_direction = '' "
        "                       THEN EXCLUDED.hint_direction "
        "                       ELSE op_funnel_operator_map.hint_direction END",
        args,
    )
    return len(values)


def read_operator_map(cursor, sources=None, only_pending=False):
    where, args = [], {}
    if sources:
        where.append("m.source IN %(sources)s")
        args['sources'] = tuple(sources)
    if only_pending:
        where.append("m.user_id IS NULL AND m.is_ignored = FALSE")
    clause = (' WHERE ' + ' AND '.join(where)) if where else ''
    cursor.execute(
        """
        SELECT m.source, m.external_key, m.external_name, m.user_id, m.hint_direction,
               m.is_ignored, m.first_seen_at, m.last_seen_at, u.name AS user_name
        FROM op_funnel_operator_map m
        LEFT JOIN users u ON u.id = m.user_id
        """ + clause + """
        ORDER BY m.user_id IS NOT NULL, m.last_seen_at DESC
        """,
        args,
    )
    return _rows(cursor)


def set_operator_map(cursor, source, external_key, user_id, updated_by, is_ignored=False):
    cursor.execute(
        """
        UPDATE op_funnel_operator_map
        SET user_id = %s, is_ignored = %s, updated_by = %s, updated_at = NOW()
        WHERE source = %s AND external_key = %s
        """,
        (user_id, bool(is_ignored), updated_by, source, str(external_key)[:190]),
    )
    return cursor.rowcount or 0


def direction_people(cursor, direction_code):
    """Сотрудники направления для автосвязки: id, ФИО, корпоративная почта.

    Уволенных берём тоже: сделки, сделанные до увольнения, обязаны остаться в
    отчёте за те дни — иначе итог команды за прошлый месяц молча уменьшится.
    """
    cursor.execute(
        """
        SELECT DISTINCT u.id, u.name, u.email
        FROM users u
        LEFT JOIN group_operator_memberships gom ON gom.operator_id = u.id
        LEFT JOIN groups g ON g.id = gom.group_id
        LEFT JOIN directions dir ON dir.id = u.direction_id
        WHERE g.calculation_model_code = %(direction)s
           OR dir.calculation_model_code = %(direction)s
        """,
        {'direction': direction_code},
    )
    return _rows(cursor)


def fill_operator_map(cursor, source, links):
    """Проставить сотрудника там, где его ЕЩЁ НЕ выбрал человек.

    `WHERE user_id IS NULL` здесь главное: ручное решение сильнее автоматики.
    Если супервайзер связал учётку сам — ночная выгрузка не имеет права его
    переписать, даже если «знает лучше». Строки, помеченные «не сопоставлять»
    (бот, тестовая учётка), тоже не трогаем.
    """
    if not links:
        return 0
    written = 0
    for external_key, user_id in links.items():
        if not user_id:
            continue
        cursor.execute(
            """
            UPDATE op_funnel_operator_map
            SET user_id = %s, updated_at = NOW()
            WHERE source = %s AND external_key = %s
              AND user_id IS NULL AND is_ignored = FALSE
            """,
            (int(user_id), source, str(external_key)[:190]),
        )
        written += cursor.rowcount or 0
    return written


def resolve_operator_map(cursor, source):
    """{внешний ключ: user_id} для подстановки при разборе выгрузки."""
    cursor.execute(
        "SELECT external_key, user_id FROM op_funnel_operator_map "
        "WHERE source = %s AND user_id IS NOT NULL",
        (source,),
    )
    return {row['external_key']: row['user_id'] for row in _rows(cursor)}


def resolve_wazzup_map(cursor):
    """Карта Wazzup берётся ГОТОВАЯ: `wazzup_operator_map` существует и заполнена
    (29 строк), дублировать её значит развести два ответа на один вопрос."""
    cursor.execute(
        "SELECT author_id, author_name, user_id FROM wazzup_operator_map "
        "WHERE user_id IS NOT NULL AND COALESCE(is_bot, FALSE) = FALSE"
    )
    return {row['author_id']: row['user_id'] for row in _rows(cursor)}


# ── Справочник причин ────────────────────────────────────────────────────────

def read_reason_dict(cursor, source=None):
    where, args = '', ()
    if source:
        where, args = ' WHERE source = %s', (source,)
    cursor.execute(
        "SELECT source, reason_code, title, bucket, sort_order, aliases, is_hidden, "
        "first_seen_at, updated_at FROM op_funnel_reason_dict" + where +
        " ORDER BY source, sort_order, title",
        args,
    )
    return _rows(cursor)


def upsert_reason_dict(cursor, rows):
    """Пополнение справочника выгрузкой: подпись и корзину, выставленные
    человеком, НЕ трогаем — иначе ночной синк откатывал бы ручную правку."""
    if not rows:
        return 0
    values, args = [], []
    for row in rows:
        values.append('(%s, %s, %s, %s, %s, NOW(), NOW())')
        args.extend((row['source'], row['reason_code'], row.get('title') or '',
                     row.get('bucket') or '', int(row.get('sort_order') or 1000)))
    cursor.execute(
        "INSERT INTO op_funnel_reason_dict "
        "(source, reason_code, title, bucket, sort_order, first_seen_at, updated_at) "
        "VALUES " + ', '.join(values) +
        " ON CONFLICT (source, reason_code) DO NOTHING",
        args,
    )
    return len(rows)


def set_reason_bucket(cursor, source, reason_code, bucket, title, updated_by, is_hidden=None):
    sets = ["bucket = %s", "updated_by = %s", "updated_at = NOW()"]
    args = [bucket, updated_by]
    if title is not None:
        sets.insert(0, "title = %s")
        args.insert(0, title)
    if is_hidden is not None:
        sets.append("is_hidden = %s")
        args.append(bool(is_hidden))
    args.extend((source, reason_code))
    cursor.execute(
        "UPDATE op_funnel_reason_dict SET " + ', '.join(sets) +
        " WHERE source = %s AND reason_code = %s",
        args,
    )
    return cursor.rowcount or 0


# ── Журнал прогонов ──────────────────────────────────────────────────────────

def start_run(cursor, direction_code, source, period_from, period_to, started_by=None, note=''):
    cursor.execute(
        """
        INSERT INTO op_funnel_sync_runs
            (direction_code, source, period_from, period_to, started_by, note)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (direction_code, source, period_from, period_to, started_by, note or ''),
    )
    row = _one(cursor)
    return (row or {}).get('id')


def finish_run(cursor, run_id, status='ok', error=None, **counters):
    if not run_id:
        return
    cursor.execute(
        """
        UPDATE op_funnel_sync_runs
        SET finished_at = NOW(), status = %s, error = %s,
            leads_seen = %s, leads_written = %s, days_frozen = %s, days_redone = %s,
            unmapped = %s, drift_rows = %s
        WHERE id = %s
        """,
        (status, (error or None)[:4000] if error else None,
         int(counters.get('leads_seen') or 0), int(counters.get('leads_written') or 0),
         int(counters.get('days_frozen') or 0), int(counters.get('days_redone') or 0),
         int(counters.get('unmapped') or 0), int(counters.get('drift_rows') or 0),
         run_id),
    )


def read_runs(cursor, direction_code=None, limit=20):
    where, args = '', {'limit': int(limit)}
    if direction_code:
        where = ' WHERE direction_code = %(direction)s'
        args['direction'] = direction_code
    cursor.execute(
        """
        SELECT r.*, u.name AS started_by_name
        FROM op_funnel_sync_runs r
        LEFT JOIN users u ON u.id = r.started_by
        """ + where + """
        ORDER BY r.started_at DESC
        LIMIT %(limit)s
        """,
        args,
    )
    return _rows(cursor)


def read_drift(cursor, direction_code, day_from=None, day_to=None, limit=200):
    where = ["d.direction_code = %(direction)s"]
    args = {'direction': direction_code, 'limit': int(limit)}
    if day_from and day_to:
        where.append("d.work_day BETWEEN %(day_from)s AND %(day_to)s")
        args['day_from'] = day_from
        args['day_to'] = day_to
    cursor.execute(
        """
        SELECT d.work_day, d.user_id, u.name AS operator_name, d.metric,
               d.was, d.became, d.noticed_at
        FROM op_funnel_drift d
        LEFT JOIN users u ON u.id = d.user_id
        WHERE """ + ' AND '.join(where) + """
        ORDER BY d.noticed_at DESC, d.work_day DESC
        LIMIT %(limit)s
        """,
        args,
    )
    return _rows(cursor)


# ── Ручная выгрузка ──────────────────────────────────────────────────────────

def read_manual_rows(cursor, direction_code, day_from, day_to):
    cursor.execute(
        """
        SELECT * FROM op_funnel_manual_rows
        WHERE direction_code = %s AND work_day BETWEEN %s AND %s
        """,
        (direction_code, day_from, day_to),
    )
    return _rows(cursor)


def upsert_manual_rows(cursor, rows, import_id=None):
    """Повторная загрузка за те же сутки ИСПРАВЛЯЕТ, а не удваивает."""
    if not rows:
        return 0
    values, args = [], []
    for row in rows:
        values.append('(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())')
        args.extend((
            row['direction_code'], row['work_day'], row['user_id'],
            row.get('work_hours'), row.get('chats'), row.get('tickets'),
            row.get('chat_reply_seconds'), row.get('ticket_handle_seconds'),
            row.get('sales'), import_id,
        ))
    cursor.execute(
        "INSERT INTO op_funnel_manual_rows "
        "(direction_code, work_day, user_id, work_hours, chats, tickets, "
        " chat_reply_seconds, ticket_handle_seconds, sales, import_id, updated_at) "
        "VALUES " + ', '.join(values) +
        " ON CONFLICT (direction_code, work_day, user_id) DO UPDATE SET "
        " work_hours = EXCLUDED.work_hours, chats = EXCLUDED.chats,"
        " tickets = EXCLUDED.tickets, chat_reply_seconds = EXCLUDED.chat_reply_seconds,"
        " ticket_handle_seconds = EXCLUDED.ticket_handle_seconds, sales = EXCLUDED.sales,"
        " import_id = EXCLUDED.import_id, updated_at = NOW()",
        args,
    )
    return len(rows)


def record_import(cursor, direction_code, file_name, period_from, period_to,
                  rows_read, rows_written, rows_skipped, unmapped, uploaded_by, note=''):
    cursor.execute(
        """
        INSERT INTO op_funnel_manual_imports
            (direction_code, file_name, period_from, period_to, rows_read, rows_written,
             rows_skipped, unmapped, uploaded_by, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        RETURNING id
        """,
        (direction_code, file_name or '', period_from, period_to,
         int(rows_read or 0), int(rows_written or 0), int(rows_skipped or 0),
         json.dumps(unmapped or [], ensure_ascii=False), uploaded_by, note or ''),
    )
    row = _one(cursor)
    return (row or {}).get('id')


def read_imports(cursor, direction_code, limit=20):
    cursor.execute(
        """
        SELECT i.*, u.name AS uploaded_by_name
        FROM op_funnel_manual_imports i
        LEFT JOIN users u ON u.id = i.uploaded_by
        WHERE i.direction_code = %s
        ORDER BY i.uploaded_at DESC
        LIMIT %s
        """,
        (direction_code, int(limit)),
    )
    return _rows(cursor)


# ── Обслуживание ─────────────────────────────────────────────────────────────

def prune_leads(cursor, keep_days):
    """Снимок лидов — это кэш под клик по причине; итоги живут отдельно и дольше."""
    cutoff = date.today() - timedelta(days=int(keep_days))
    cursor.execute("DELETE FROM op_funnel_leads WHERE work_day < %s", (cutoff,))
    return cursor.rowcount or 0


def known_directions(cursor):
    """Направления отдела продаж, которые есть смысл показывать табом.

    Направление считается живым, если есть ХОТЬ ЧТО-ТО: активная группа, само
    направление в справочнике или уже накопленная история в разделе.

    Проверять только активные группы нельзя: группу архивируют при перестановках
    (людей перевели, группу завели заново), и таб вместе со всей историей
    направления исчезал бы с экрана — данные в базе есть, а посмотреть их нечем.
    """
    cursor.execute(
        """
        SELECT code FROM (
            SELECT calculation_model_code AS code FROM groups
             WHERE COALESCE(status, 'active') = 'active'
            UNION
            SELECT calculation_model_code FROM directions
             WHERE COALESCE(is_active, TRUE)
            UNION
            SELECT direction_code FROM op_funnel_daily
        ) AS live
        WHERE code = ANY(%s)
        """,
        (list(DIRECTION_CODES),),
    )
    found = {row['code'] for row in _rows(cursor) if row.get('code')}
    return tuple(code for code in DIRECTION_CODES if code in found)
