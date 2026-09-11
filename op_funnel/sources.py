# -*- coding: utf-8 -*-
"""Приведение сырья источников к строкам `op_funnel_leads`.

Классификация исходов здесь НЕ живёт — она целиком в `metrics.py`, и этот модуль
только раскладывает поля источника по колонкам и зовёт нужный классификатор.
Разделение не косметическое: формулы сверены с файлами супервайзеров построчно,
и вторая их копия рядом с разбором HTTP-ответа разъехалась бы на первой правке.

Сутки лида: по какому полю
--------------------------
У каждого источника своё, и оно повторяет то, по которому САМ источник считает
период, — иначе «за 1 сентября» в разделе и на экране СРМ будут разные лиды:

    Поток          taken_at, а если лид не брали — created_at
    Платный найм   driver_registered_at (дата регистрации водителя)
    amoCRM         created_at сделки

**Пояс не сдвигаем.** Времена источников уже местные (Алматы), и переводить их в
UTC нельзя: в проекте на этом горели — отчёт рейтингов Chat2Desk уехал на пять
часов, потому что время положили в ключ идентичности. Разбираем строку «как
есть» и держим как naive datetime.

Верификаторы: чаты считаются иначе, чем кажется
-----------------------------------------------
Супервайзеру нужно ВРЕМЯ ОТВЕТА, а не длина переписки. Это разные величины:
длительность эпизода (`ended_at - started_at`) в среднем 2000–3300 секунд, а
таргет времени ответа — 120 секунд днём. Поэтому «среднее время обработки чата»
считается как медиана задержки между входящим сообщением и первым ответом
человека, а не как длина эпизода.

Границы суток берутся так же, как в `database.py::wazzup_operator_analytics`
(`%s::date::timestamp AT TIME ZONE 'Asia/Almaty'`) — там это уже сделано верно, и
две разные границы суток дали бы два разных ответа на один вопрос.

**Окно тридцати суток.** `wazzup_messages` и `wazzup_chats` вычищаются кроном
раз в сутки, поэтому агрегат обязан писаться сразу: задним числом глубже месяца
историю не восстановить никак.
"""

import logging
from datetime import date, datetime, timedelta

from . import metrics
from .schema import (SOURCE_AMO, SOURCE_CRM_PAID_HIRE, SOURCE_CRM_STREAM,
                     SOURCE_WAZZUP)

log = logging.getLogger(__name__)

# Воронка «Отдел продаж» в amoCRM. Остальные четыре воронки аккаунта («14 дней»,
# «40 дней», «Рассылка», тестовая) к отделу продаж отношения не имеют, и без
# фильтра в «Основу» попадали бы чужие сделки.
AMO_SALES_PIPELINE_ID = metrics.AMO_SALES_PIPELINE_ID

# Кастомные поля сделки, снятые живьём 11.09.2026.
AMO_FIELD_PARK = 915311        # «Таксопарк привлечения»
AMO_FIELD_CITY = 1073351       # «Город привлечения»

_AMO_PAGE_LIMIT = 250


def _text(value, limit=None):
    out = '' if value is None else str(value).strip()
    return out[:limit] if limit else out


def parse_moment(value):
    """Время источника как naive datetime. Пояс не трогаем (см. шапку)."""
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)):
        # amoCRM отдаёт время числом (unix). Переводим в местное, потому что и
        # аккаунт, и отдел живут в одном поясе.
        return datetime.fromtimestamp(float(value))
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('T', ' ')
    if text.endswith('Z'):
        text = text[:-1]
    if '+' in text[10:]:
        text = text[:text.index('+', 10)]
    for pattern in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
                    '%d.%m.%Y %H:%M:%S', '%d.%m.%Y'):
        try:
            return datetime.strptime(text[:len(pattern) + 4].strip(), pattern)
        except ValueError:
            continue
    log.warning('op_funnel: не разобрано время %r', value)
    return None


def _day_of(moment):
    return moment.date() if isinstance(moment, datetime) else None


def _base_row(direction_code, source, stream_type, lead_key, work_day, owner_raw,
              user_id, outcome):
    return {
        'direction_code': direction_code,
        'source': source,
        'stream_type': int(stream_type or 0),
        'lead_key': _text(lead_key, 64),
        'work_day': work_day,
        'user_id': user_id,
        'owner_raw': _text(owner_raw, 500),
        'stage_raw': '',
        'call_status': '',
        'dialog_status': '',
        'reason_raw': '',
        'reason_code': outcome.get('reason_code') or '',
        'sub_reason_raw': '',
        'reach_outcome': outcome.get('reach_outcome') or 'new',
        'dialog_outcome': outcome.get('dialog_outcome') or 'none',
        'reason_bucket': outcome.get('reason_bucket') or '',
        'full_name': '',
        'phone': '',
        'park_name': '',
        'city': '',
        'base_title': '',
        'comment': '',
        'created_at': None,
        'taken_at': None,
        'updated_at': None,
    }


# ── «Поток» ──────────────────────────────────────────────────────────────────

def stream_rows(leads, stream_type, direction_code, owner_to_user=None,
                reason_index=None):
    """Лиды потока → строки таблицы. Возвращает (строки, встреченные операторы).

    Встреченные операторы возвращаются отдельно, чтобы выгрузка САМА заводила их
    в таблице сопоставления: так в интерфейсе видно, кого не хватает, а лиды
    несопоставленного человека не теряются — они уходят в строку «не сопоставлен».
    """
    owner_to_user = owner_to_user or {}
    rows, seen = [], {}
    for lead in leads or []:
        owner = _text(lead.get('owner'))
        taken = parse_moment(lead.get('taken_at'))
        created = parse_moment(lead.get('created_at'))
        work_day = _day_of(taken) or _day_of(created)
        if not work_day:
            # Лид без обеих дат отнести не к чему. Молча выбрасывать нельзя —
            # это дырка в сумме, поэтому пишем в лог с ключом.
            log.warning('op_funnel: лид потока %s без дат, пропущен', lead.get('lead_id'))
            continue
        if owner:
            seen[owner] = owner
        outcome = metrics.classify_stream_lead(
            lead.get('call_status'), lead.get('dialog_status'),
            lead.get('reject_reason'), reason_index=reason_index,
        )
        row = _base_row(direction_code, SOURCE_CRM_STREAM, stream_type,
                        lead.get('lead_id'), work_day, owner,
                        owner_to_user.get(owner), outcome)
        row.update({
            'call_status': _text(lead.get('call_status'), 200),
            'dialog_status': _text(lead.get('dialog_status'), 200),
            'reason_raw': _text(lead.get('reject_reason'), 500),
            'full_name': _text(lead.get('full_name'), 500),
            'phone': _text(lead.get('phone'), 24),
            'park_name': _text(lead.get('park_name'), 500),
            'base_title': _text(lead.get('base_title'), 100),
            'comment': _text(lead.get('comment'), 1000),
            'created_at': created,
            'taken_at': taken,
            'updated_at': parse_moment(lead.get('updated_at')),
        })
        rows.append(row)
    return rows, seen


# ── «Яндекс Регистрация» / платный найм ──────────────────────────────────────

def paid_hire_rows(leads, direction_code, owner_to_user=None):
    owner_to_user = owner_to_user or {}
    rows, seen = [], {}
    for lead in leads or []:
        owner = _text(lead.get('owner'))
        registered = parse_moment(lead.get('driver_registered_at'))
        work_day = _day_of(registered)
        if not work_day:
            log.warning('op_funnel: лид платного найма %s без даты регистрации',
                        lead.get('lead_id'))
            continue
        if owner:
            seen[owner] = owner
        outcome = metrics.classify_paid_hire_lead(
            lead.get('status'), lead.get('closed_reason_code'),
            lead.get('closed_reason'), lead.get('closed_sub_reason'),
        )
        row = _base_row(direction_code, SOURCE_CRM_PAID_HIRE, 0,
                        lead.get('lead_id'), work_day, owner,
                        owner_to_user.get(owner), outcome)
        row.update({
            'stage_raw': _text(lead.get('status'), 200),
            'reason_raw': _text(lead.get('closed_reason'), 500),
            'sub_reason_raw': _text(lead.get('closed_sub_reason'), 200),
            'full_name': _text(lead.get('full_name'), 500),
            'phone': _text(lead.get('phone'), 24),
            'park_name': _text(lead.get('park_name'), 500),
            'city': _text(lead.get('city'), 200),
            'comment': _text(lead.get('comment'), 1000),
            'created_at': registered,
            'taken_at': registered,
            'updated_at': parse_moment(lead.get('updated_at')),
        })
        rows.append(row)
    return rows, seen


# ── «Основа ОП» / amoCRM ─────────────────────────────────────────────────────

def _amo_custom_field(lead, field_id):
    for field in (lead.get('custom_fields_values') or []):
        if field.get('field_id') == field_id:
            values = field.get('values') or []
            if values:
                return _text((values[0] or {}).get('value'))
    return ''


def amo_rows(leads, stage_names, direction_code, responsible_to_user=None):
    """Сделки воронки «Отдел продаж» → строки таблицы.

    `stage_names` — справочник ЭТОЙ воронки, а не всех сразу. Разница не
    теоретическая: статус 142 здесь называется «ПРОШЕЛ РЕГИСТРАЦИЮ», а в
    остальных четырёх воронках аккаунта — «Успешно реализовано», и общий
    справочник затирает подпись (живой дефект ночной выгрузки `amo_leads.py`).
    """
    responsible_to_user = responsible_to_user or {}
    stage_names = stage_names or {}
    rows, seen = [], {}
    for lead in leads or []:
        created = parse_moment(lead.get('created_at'))
        work_day = _day_of(created)
        if not work_day:
            continue
        responsible = lead.get('responsible_user_id')
        key = str(responsible or '')
        if key:
            seen[key] = key
        status_id = lead.get('status_id')
        stage = stage_names.get(status_id) or _text(lead.get('status_name'))
        loss = ((lead.get('_embedded') or {}).get('loss_reason') or {})
        loss_name = _text(loss.get('name')) if isinstance(loss, dict) else ''
        if not loss_name and isinstance(loss, list) and loss:
            loss_name = _text((loss[0] or {}).get('name'))

        outcome = metrics.classify_amo_lead(stage, loss_name or None, status_id)
        row = _base_row(direction_code, SOURCE_AMO, 0, lead.get('id'), work_day,
                        key, responsible_to_user.get(key), outcome)
        row.update({
            'stage_raw': _text(stage, 300),
            'reason_raw': loss_name[:500],
            'full_name': _text(lead.get('name'), 500),
            'park_name': _amo_custom_field(lead, AMO_FIELD_PARK)[:500],
            'city': _amo_custom_field(lead, AMO_FIELD_CITY)[:200],
            'created_at': created,
            'taken_at': created,
            'updated_at': parse_moment(lead.get('updated_at')),
        })
        rows.append(row)
    return rows, seen


def load_amo_stage_names(client, pipeline_id=AMO_SALES_PIPELINE_ID):
    """Справочник этапов ОДНОЙ воронки.

    Здесь и лежит отличие от `amo_leads._load_stage_names`, который обходит все
    воронки подряд: системные статусы 142/143 есть в каждой, и последняя воронка
    перетирает подпись предыдущих. Для «Основы» это критично — именно 142 и 143
    несут регистрацию и закрытие, то есть всю воронку.
    """
    data = client.get('/api/v4/leads/pipelines')
    for pipeline in ((data or {}).get('_embedded') or {}).get('pipelines') or []:
        if pipeline.get('id') != pipeline_id:
            continue
        return {
            status.get('id'): status.get('name')
            for status in (pipeline.get('_embedded') or {}).get('statuses') or []
        }
    raise RuntimeError('amoCRM: воронка %s не найдена — её переименовали или закрыли доступ'
                       % pipeline_id)


def load_amo_users(client):
    """Справочник пользователей amoCRM: id, имя, корпоративная почта.

    Открыт только администраторам. Наша учётка стала администратором 11.09.2026 —
    до этого `/api/v4/users` отвечал 403 «Admin access only», и имя ответственного
    взять было негде. Отказ здесь НЕ роняет выгрузку: без справочника в отчёте
    останутся числовые id, и их свяжет человек на экране сопоставления — так
    раздел работал до появления доступа.
    """
    users, page = [], 1
    while page <= 20:
        try:
            data = client.get('/api/v4/users', {'limit': 250, 'page': page})
        except Exception as exc:  # noqa: BLE001
            log.warning('op_funnel: справочник пользователей amoCRM недоступен (%s), '
                        'ответственные останутся числами', exc)
            return users
        chunk = ((data or {}).get('_embedded') or {}).get('users') or []
        users.extend({
            'id': item.get('id'),
            'name': item.get('name') or '',
            'email': item.get('email') or '',
        } for item in chunk)
        if len(chunk) < 250:
            break
        page += 1
    return users


def fetch_amo_sales_leads(day_from, day_to, client=None):
    """Сделки воронки «Отдел продаж» за период. Возвращает (сделки, справочник этапов).

    Клиент — `amo_leads.AmoClient`: он уже умеет вход логином/паролем, живёт с
    40-минутным токеном, сам перелогинивается на 401 и переживает обрыв
    соединения посреди пагинации (замерено: 5 прогонов из 39 обрывались на
    десятой странице).
    """
    import amo_leads  # локально: модуль читает окружение на импорте

    client = client or amo_leads.AmoClient()
    stage_names = load_amo_stage_names(client)
    users = load_amo_users(client)

    start = datetime.combine(day_from, datetime.min.time())
    end = datetime.combine(day_to, datetime.max.time())

    url = '/api/v4/leads'
    params = {
        'limit': _AMO_PAGE_LIMIT,
        'page': 1,
        'with': 'loss_reason',
        'filter[pipeline_id]': AMO_SALES_PIPELINE_ID,
        'filter[created_at][from]': int(start.timestamp()),
        'filter[created_at][to]': int(end.timestamp()),
        'order[created_at]': 'asc',
    }
    leads = []
    while True:
        data = client.get(url, params)
        if not data:
            break
        page = ((data or {}).get('_embedded') or {}).get('leads') or []
        if not page:
            break
        leads.extend(page)
        # Идём по ссылке самого API: условие «страница короче лимита» теряет
        # сделки на неполных страницах — это уже ловили в amo_leads.py.
        next_url = ((data.get('_links') or {}).get('next') or {}).get('href')
        if not next_url:
            break
        url, params = next_url, None
    log.info('op_funnel: amoCRM отдал %d сделок воронки %s за %s—%s, пользователей %d',
             len(leads), AMO_SALES_PIPELINE_ID, day_from, day_to, len(users))
    return leads, stage_names, users


# ── «Верификатор» / Wazzup ───────────────────────────────────────────────────

# Больше этого разрыв между входящим и ответом считаем новым обращением, а не
# «долгим ответом»: иначе сообщение, пришедшее ночью и отвеченное утром, даёт
# восьмичасовое «время ответа» и убивает среднее по всей смене.
REPLY_WINDOW_SECONDS = 6 * 3600


def wazzup_daily(cursor, day_from, day_to, operator_map=None):
    """Суточная нагрузка Верификаторов: чаты и время ответа по оператору.

    Возвращает список {user_id, work_day, chats, reply_seconds}.

    «Чат» — это пара (сутки, чат), в которой оператор хоть раз ответил сам.
    Определение взято у существующей аналитики Wazzup, чтобы числа раздела и
    вкладки «Аналитика» не разошлись: там оно такое же.
    """
    cursor.execute(
        """
        WITH bounds AS (
            -- Окно сдвинуто на границу смены: сутки начинаются в 08:00 и
            -- заканчиваются в 08:00 следующего дня. Иначе ночная часть смены
            -- уезжала бы в соседние сутки — та же поправка, что у тикетов.
            SELECT ((%(day_from)s::date::timestamp + %(cutoff)s * INTERVAL '1 hour')
                        AT TIME ZONE 'Asia/Almaty') AS from_ts,
                   (((%(day_to)s::date + 1)::timestamp + %(cutoff)s * INTERVAL '1 hour')
                        AT TIME ZONE 'Asia/Almaty') AS to_ts
        ),
        msg AS (
            SELECT m.chat_id,
                   m.channel_id,
                   m.dt,
                   m.is_echo,
                   m.author_id,
                   ((m.dt AT TIME ZONE 'Asia/Almaty')
                        - %(cutoff)s * INTERVAL '1 hour')::date AS local_day
            FROM wazzup_messages m, bounds b
            WHERE m.dt >= b.from_ts AND m.dt < b.to_ts
        ),
        -- Ответ оператора и ближайшее входящее до него в том же чате.
        replies AS (
            SELECT o.author_id,
                   o.local_day,
                   o.chat_id,
                   EXTRACT(EPOCH FROM (o.dt - (
                       SELECT MAX(i.dt) FROM msg i
                       WHERE i.chat_id = o.chat_id AND i.is_echo = FALSE AND i.dt < o.dt
                   ))) AS wait_seconds
            FROM msg o
            WHERE o.is_echo = TRUE AND o.author_id IS NOT NULL
        ),
        chats AS (
            SELECT author_id, local_day, COUNT(DISTINCT chat_id) AS chats
            FROM msg
            WHERE is_echo = TRUE AND author_id IS NOT NULL
            GROUP BY author_id, local_day
        ),
        waits AS (
            SELECT author_id, local_day,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY wait_seconds) AS median_wait
            FROM replies
            WHERE wait_seconds IS NOT NULL
              AND wait_seconds > 0
              AND wait_seconds <= %(window)s
            GROUP BY author_id, local_day
        )
        SELECT c.author_id, c.local_day, c.chats, w.median_wait
        FROM chats c
        LEFT JOIN waits w ON w.author_id = c.author_id AND w.local_day = c.local_day
        ORDER BY c.local_day, c.author_id
        """,
        {'day_from': day_from, 'day_to': day_to, 'window': REPLY_WINDOW_SECONDS,
         'cutoff': SHIFT_DAY_CUTOFF_HOUR},
    )
    columns = [column[0] for column in cursor.description]
    operator_map = operator_map or {}
    out = []
    for raw in cursor.fetchall():
        row = raw if isinstance(raw, dict) else dict(zip(columns, raw))
        author_id = row.get('author_id')
        user_id = operator_map.get(author_id)
        if user_id is None:
            # Неизвестный автор — не выбрасываем: он попадёт в строку
            # «не сопоставлен», и расхождение будет видно сразу.
            pass
        out.append({
            'user_id': user_id,
            'author_id': author_id,
            'work_day': row.get('local_day'),
            'chats': int(row.get('chats') or 0),
            'reply_seconds': (round(float(row['median_wait']), 1)
                              if row.get('median_wait') is not None else None),
        })
    return out


# ── Обращения СРМ («тикеты») ─────────────────────────────────────────────────

# Какое из четырёх времён считать «временем обработки тикета».
#
# Замерено на 215 обращениях за 01.06–11.09.2026:
#   time_to_accept   медиана 534 с   — сколько обращение ждало, пока его возьмут
#   time_to_process  медиана  18 с   — сколько его обрабатывали
#   time_to_close    медиана   0 с
#   time_total       медиана 48 ч    — весь жизненный цикл, почти целиком ожидание
#
# В файле супервайзера «Ср. время обработки тикет» — это сотни секунд (например
# 1367 секунд на 8 тикетов = 171). То есть это НЕ `time_total` (там двое суток) и
# не голый `time_to_process`. Берём сумму «дождался + обработали»: она отвечает
# на вопрос «сколько времени тикет занимал у отдела», и порядок величины совпал.
# Сверить точно можно будет, когда в периметр токена добавят учётки направления —
# пока видны только обращения, заведённые «ИИ Агентом».
TICKET_HANDLE_FIELDS = ('time_to_accept', 'time_to_process')

# Сутки СМЕНЫ, а не календаря: всё, что раньше этого часа, относится к смене,
# начавшейся накануне вечером.
#
# Ночная смена идёт с 20:00 до 08:00, и тикет, заведённый в 02:00, для
# супервайзера принадлежит вчерашней смене — в его файле он стоит во вчерашней
# вкладке. Календарная дата создания из СРМ кладёт его в сегодня, и у ночных
# операторов расходилась половина строк: у одной 15 против 6, у другой 9 против 3.
#
# Граница 08:00 не подобрана, а взята из уже принятой в проекте формализации ночи
# (20:00–08:00 в аукционе смен СЗоВ). Проверено на эталоне за 01–08.09.2026: без
# переноса точно совпадало 33 строки «оператор × сутки» из 57, с переносом — 39, а
# у ночных операторов почти все стали точными. Сдвиг на 09:00 давал 41, но это уже
# подгонка под шум: смысла у такой границы нет.
SHIFT_DAY_CUTOFF_HOUR = 8


def _ticket_day(row):
    """Сутки СМЕНЫ для тикета. Дата в ответе — «ДД.ММ.ГГГГ», время — «ЧЧ:ММ:СС»."""
    text = str(row.get('created_date') or '').strip()
    if not text:
        return None
    day = None
    for pattern in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            day = datetime.strptime(text, pattern).date()
            break
        except ValueError:
            continue
    if day is None:
        return None
    hour = str(row.get('created_time') or '').strip()[:2]
    if hour.isdigit() and int(hour) < SHIFT_DAY_CUTOFF_HOUR:
        return day - timedelta(days=1)
    return day


def _ticket_seconds(row):
    total = 0
    found = False
    for field in TICKET_HANDLE_FIELDS:
        value = row.get(field)
        if value in (None, ''):
            continue
        try:
            total += float(value)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None


def tickets_daily(rows, name_to_user=None):
    """Обращения → суточная нагрузка по оператору.

    Возвращает [{user_id, assignee, work_day, tickets, handle_seconds}].

    Считаем по `assignee` — тому, кто обращение ОБРАБОТАЛ, а не завёл. В файле
    супервайзера колонка «Тикеты» означает именно обработанные, и это же
    единственное поле, где сейчас видны имена верификаторов: `created_by` у всех
    записей равен «ИИ Агент», потому что в периметр токена входит одна учётка.

    Среднее время — по тем тикетам, у которых оно есть: у новых и незакрытых
    этапы не наступили, и считать их нулями значит занизить среднее вдвое.
    """
    name_to_user = name_to_user or {}
    buckets = {}
    for row in rows or []:
        assignee = _text(row.get('assignee'))
        work_day = _ticket_day(row)
        if not work_day:
            continue
        # «Не указан» — это не человек, а незанятое обращение. В строку
        # «не сопоставлен» оно не идёт: там живут реальные люди без связки.
        if not assignee or assignee.lower() in ('не указан', 'не указано', '-'):
            continue
        key = (assignee, work_day)
        item = buckets.get(key)
        if item is None:
            item = buckets[key] = {
                'user_id': name_to_user.get(_low_name(assignee)),
                'assignee': assignee,
                'work_day': work_day,
                'tickets': 0,
                '_seconds': [],
            }
        item['tickets'] += 1
        seconds = _ticket_seconds(row)
        if seconds is not None:
            item['_seconds'].append(seconds)

    out = []
    for item in buckets.values():
        seconds = item.pop('_seconds')
        item['handle_seconds'] = round(sum(seconds) / len(seconds), 1) if seconds else None
        out.append(item)
    return out


def tickets_assignees_seen(rows):
    """Кого встретили в обработчиках — для экрана сопоставления."""
    seen = {}
    for row in rows or []:
        assignee = _text(row.get('assignee'))
        if assignee and assignee.lower() not in ('не указан', 'не указано', '-'):
            seen[assignee] = assignee
    return seen


def _low_name(value):
    """Ключ сравнения имени: без регистра и двойных пробелов. Тот же, что в
    `manual_import._norm` и в роутах, — разойтись им нельзя."""
    return ' '.join(str(value or '').strip().lower().split())


def wazzup_authors_seen(cursor, day_from, day_to):
    """Авторы, писавшие в период, — для экрана сопоставления."""
    cursor.execute(
        """
        SELECT DISTINCT m.author_id, MAX(m.author_name) AS author_name
        FROM wazzup_messages m
        WHERE m.is_echo = TRUE AND m.author_id IS NOT NULL
          AND m.dt >= %(day_from)s::date::timestamp AT TIME ZONE 'Asia/Almaty'
          AND m.dt < ((%(day_to)s::date + 1)::timestamp AT TIME ZONE 'Asia/Almaty')
        GROUP BY m.author_id
        """,
        {'day_from': day_from, 'day_to': day_to},
    )
    columns = [column[0] for column in cursor.description]
    seen = {}
    for raw in cursor.fetchall():
        row = raw if isinstance(raw, dict) else dict(zip(columns, raw))
        seen[row.get('author_id')] = row.get('author_name') or ''
    return seen


# ── Разбивка причин из строк лидов ───────────────────────────────────────────

def reasons_from_rows(rows):
    """Суточная разбивка причин по операторам: (направление, день, оператор,
    корзина, код) → сколько лидов.

    Считается из тех же строк, что легли в таблицу лидов, а не отдельным
    запросом: два прохода по одним данным неизбежно разойдутся.
    """
    counter = {}
    for row in rows:
        bucket = row.get('reason_bucket') or ''
        code = row.get('reason_code') or ''
        if not bucket or not code:
            continue
        key = (row['direction_code'], row['work_day'], row.get('user_id') or 0, bucket, code)
        found = counter.get(key)
        if found is None:
            counter[key] = {
                'direction_code': row['direction_code'],
                'work_day': row['work_day'],
                'user_id': row.get('user_id') or 0,
                'bucket': bucket,
                'reason_code': code,
                'reason_title': row.get('reason_raw') or code,
                'leads': 1,
            }
        else:
            found['leads'] += 1
    return list(counter.values())
