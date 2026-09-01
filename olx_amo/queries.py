# -*- coding: utf-8 -*-
"""SQL-слой робота OLX → amoCRM.

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Так же устроены wiki, crm,
parcels и call_qa.

Почему это важно именно здесь: обработка одного обращения трогает три таблицы —
состояние чата, журнал и счётчики прогона. Если бы каждая функция брала своё
соединение, чат мог бы оказаться помеченным обработанным, а строки журнала по
нему не появиться, и обращение исчезло бы бесследно. Один курсор = одна
транзакция = чат и журнал всегда согласованы.
"""

from datetime import datetime, timedelta

from . import cabinets
from .schema import JOURNAL_RESULTS

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Смещение Алматы от UTC. Render живёт в UTC, и «сегодня» у него до 06:00 по
# Алматы ещё вчерашнее — а по «сегодня» считается дедупликация лидов. Считаем
# сдвигом, а не ZoneInfo: у Казахстана с 01.03.2024 одна зона без перевода
# часов, а tzdata на контейнере может и отсутствовать. Тот же приём в
# parcels/queries.py.
_ALMATY_OFFSET = timedelta(hours=5)

# SQLSTATE нарушения уникальности. Держим строкой, а не импортом psycopg2:
# модуль читается тестами напрямую, а импорт драйвера тянет за собой пул.
_UNIQUE_VIOLATION = '23505'


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


def today_almaty():
    return now_almaty().date()


# ─────────────────────────────────────────────────────────────────────────────
# Строки в словари
# ─────────────────────────────────────────────────────────────────────────────
#
# Пул отдаёт ОБЫЧНЫЙ курсор psycopg2 — без RealDictCursor, — то есть строки
# приходят кортежами. Соседние разделы раскладывают их руками по фиксированному
# списку колонок (parcels._parcel_row); здесь колонок много и они растут вместе
# с журналом, поэтому имена берём из cursor.description. Это дешевле ошибки
# «сдвинулся индекс после ALTER TABLE», которую руками не поймать.

def _one(cursor):
    row = cursor.fetchone()
    return _as_dict(cursor, row) if row is not None else None


def _all(cursor):
    rows = cursor.fetchall() or []
    names = [c[0] for c in (cursor.description or [])]
    return [dict(zip(names, row)) for row in rows]


def _as_dict(cursor, row):
    if isinstance(row, dict):
        return row
    names = [c[0] for c in (cursor.description or [])]
    return dict(zip(names, row))


def _scalar(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return list(row.values())[0]
    return row[0]


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────
#
# Запрос тот же, что у соседних разделов (parcels/crm/wiki): профиль плюс
# отделы, которыми смотрящий заведует. Дублирование здесь осознанное — общего
# места под такой контекст в проекте нет, а тянуть его из чужого пакета значило
# бы связать раздел с чужой схемой прав.

_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, name, role, department_id
      FROM users
     WHERE id = %(user_id)s
),
headed AS (
    SELECT d.id, d.code FROM departments d
     WHERE d.head_user_id = %(user_id)s AND d.is_active
)
SELECT
    (SELECT name          FROM me),
    (SELECT role          FROM me),
    (SELECT department_id FROM me),
    (SELECT d.code FROM departments d WHERE d.id = (SELECT department_id FROM me)),
    COALESCE((SELECT array_agg(id)   FROM headed), '{}'),
    COALESCE((SELECT array_agg(code) FROM headed), '{}')
"""


def load_access_context(cursor, user_id):
    """Профиль и периметр одним запросом. None — пользователя нет."""
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, headed, headed_codes = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': str(role or '').strip().lower(),
        'department_id': department_id,
        'department_code': department_code,
        'headed_department_ids': list(headed or []),
        'headed_department_codes': list(headed_codes or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Учётки кабинетов
# ─────────────────────────────────────────────────────────────────────────────

def ensure_accounts(cursor):
    """Завести строку на каждый кабинет из справочника. Идемпотентно.

    Справочник — источник правды по составу кабинетов, база хранит только их
    изменчивое состояние. Поэтому строки создаём отсюда, а не миграцией: список
    кабинетов правится в коде, и новый кабинет не должен требовать SQL руками.
    """
    for cab in cabinets.CABINETS:
        cursor.execute(
            """
            INSERT INTO olx_accounts (code, olx_id, state)
            VALUES (%(code)s, %(olx_id)s, 'not_configured')
            ON CONFLICT (code) DO UPDATE SET olx_id = EXCLUDED.olx_id
            """,
            {'code': cab.code, 'olx_id': cab.olx_id},
        )


def get_account(cursor, code):
    cursor.execute("SELECT * FROM olx_accounts WHERE code = %s", (code,))
    return _one(cursor)


def list_accounts(cursor):
    cursor.execute("SELECT * FROM olx_accounts ORDER BY code")
    return _all(cursor)


def save_tokens(cursor, code, access_token, expires_at, refresh_token, scope=None):
    """Сложить свежую пару токенов.

    refresh_token пишем ТОЛЬКО когда он пришёл: при обновлении OLX может его не
    прислать, и затереть старый значило бы потерять доступ к кабинету до нового
    согласия владельца в браузере.
    """
    cursor.execute(
        """
        UPDATE olx_accounts
           SET access_token = %(access)s,
               access_token_expires_at = %(expires)s,
               refresh_token = COALESCE(%(refresh)s, refresh_token),
               token_scope = COALESCE(%(scope)s, token_scope),
               authorized_at = {now},
               state = 'ok',
               last_error = NULL,
               last_error_at = NULL,
               consecutive_failures = 0,
               updated_at = {now}
         WHERE code = %(code)s
        """.format(now=_NOW),
        {'code': code, 'access': access_token, 'expires': expires_at,
         'refresh': refresh_token, 'scope': scope},
    )


def set_account_state(cursor, code, state, error=None):
    """Пометить состояние кабинета. Ошибка увеличивает счётчик подряд идущих сбоев."""
    cursor.execute(
        """
        UPDATE olx_accounts
           SET state = %(state)s,
               last_error = %(error)s,
               last_error_at = CASE WHEN %(error)s IS NULL THEN last_error_at ELSE {now} END,
               consecutive_failures = CASE WHEN %(error)s IS NULL
                                           THEN 0 ELSE consecutive_failures + 1 END,
               updated_at = {now}
         WHERE code = %(code)s
        """.format(now=_NOW),
        {'code': code, 'state': state, 'error': (error or None)},
    )


def mark_polled(cursor, code, saw_message=False, made_lead=False):
    """Отметить, что опрос состоялся. Ставится и на ПУСТОМ опросе.

    Это и есть противоядие от «тихого» простоя из раздела 7 ТЗ: без такой
    отметки «обращений нет» неотличимо от «робот перестал видеть чаты».
    """
    cursor.execute(
        """
        UPDATE olx_accounts
           SET last_poll_at = {now},
               last_message_at = CASE WHEN %(saw)s THEN {now} ELSE last_message_at END,
               last_lead_at = CASE WHEN %(lead)s THEN {now} ELSE last_lead_at END,
               updated_at = {now}
         WHERE code = %(code)s
        """.format(now=_NOW),
        {'code': code, 'saw': bool(saw_message), 'lead': bool(made_lead)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Состояние чатов
# ─────────────────────────────────────────────────────────────────────────────

def get_thread(cursor, cabinet_code, thread_id):
    cursor.execute(
        "SELECT * FROM olx_threads WHERE cabinet_code = %s AND thread_id = %s",
        (cabinet_code, str(thread_id)),
    )
    return _one(cursor)


def threads_state(cursor, cabinet_code, thread_ids):
    """Состояние сразу по списку чатов. Один запрос вместо пятидесяти.

    Опрос смотрит до двухсот чатов кабинета за цикл и по каждому должен решить,
    появилось ли новое. Спрашивать базу по одному значило бы двести обращений к
    пулу дважды в минуту на каждый из девяти кабинетов.
    """
    ids = [str(t) for t in (thread_ids or []) if t is not None]
    if not ids:
        return []
    cursor.execute(
        """
        SELECT thread_id, last_message_id, last_message_at, last_unread_count,
               canned_reply_sent_at
          FROM olx_threads
         WHERE cabinet_code = %(cabinet_code)s
           AND thread_id = ANY(%(ids)s)
        """,
        {'cabinet_code': cabinet_code, 'ids': ids},
    )
    return _all(cursor)


def upsert_thread(cursor, cabinet_code, thread_id, **fields):
    """Завести или обновить закладку по чату.

    Ключ — пара (кабинет, чат): идентификаторы чатов уникальны внутри кабинета,
    но между кабинетами совпасть могут.
    """
    allowed = ('advert_id', 'advert_title', 'interlocutor_id', 'interlocutor_name',
               'last_message_id', 'last_message_at', 'amo_lead_id', 'phone_normalized',
               'last_unread_count')
    payload = {k: fields.get(k) for k in allowed}
    payload['cabinet_code'] = cabinet_code
    payload['thread_id'] = str(thread_id)
    payload['seen'] = int(fields.get('messages_seen') or 0)

    # COALESCE(EXCLUDED.x, старое) — обновляем только то, что реально приехало:
    # опрос возвращает не все поля сразу, и `None` не должен затирать уже
    # известное название вакансии или имя кандидата.
    sets = ",\n               ".join(
        "{0} = COALESCE(EXCLUDED.{0}, olx_threads.{0})".format(k) for k in allowed)
    cursor.execute(
        """
        INSERT INTO olx_threads (cabinet_code, thread_id, advert_id, advert_title,
                                 interlocutor_id, interlocutor_name, last_message_id,
                                 last_message_at, amo_lead_id, phone_normalized,
                                 last_unread_count, messages_seen)
        VALUES (%(cabinet_code)s, %(thread_id)s, %(advert_id)s, %(advert_title)s,
                %(interlocutor_id)s, %(interlocutor_name)s, %(last_message_id)s,
                %(last_message_at)s, %(amo_lead_id)s, %(phone_normalized)s,
                %(last_unread_count)s, %(seen)s)
        ON CONFLICT (cabinet_code, thread_id) DO UPDATE
           SET {sets},
               messages_seen = olx_threads.messages_seen + EXCLUDED.messages_seen,
               updated_at = {now}
        RETURNING *
        """.format(sets=sets, now=_NOW),
        payload,
    )
    return _one(cursor)


def mark_canned_reply_sent(cursor, cabinet_code, thread_id):
    """Запомнить, что автоответ по этому обращению уже ушёл.

    ТЗ запрещает слать заготовленное сообщение одному кандидату дважды в рамках
    одного обращения, поэтому отметка живёт на чате, а не в журнале.

    Это ВСТАВКА с обновлением, а не просто UPDATE. Строки чата в этот момент
    может ещё не быть: закладка по чату пишется в конце разбора, а автоответ
    уходит в середине. UPDATE по несуществующей строке молча трогает ноль строк,
    отметка не сохраняется — и на следующем опросе кандидат получает то же
    сообщение снова.
    """
    cursor.execute(
        """
        INSERT INTO olx_threads (cabinet_code, thread_id, canned_reply_sent_at)
        VALUES (%(cabinet_code)s, %(thread_id)s, {now})
        ON CONFLICT (cabinet_code, thread_id) DO UPDATE
           SET canned_reply_sent_at = {now}, updated_at = {now}
        """.format(now=_NOW),
        {'cabinet_code': cabinet_code, 'thread_id': str(thread_id)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Журнал обращений
# ─────────────────────────────────────────────────────────────────────────────

def write_journal(cursor, cabinet_code, result, **fields):
    """Записать обработанное обращение. Возвращает строку журнала или None.

    None означает ровно одно: сработал уникальный индекс дедупликации, то есть
    по этому номеру в этом кабинете сегодня сделка уже есть. Это штатный исход,
    а не ошибка, — вызывающий пишет вместо неё строку `duplicate`.
    """
    if result not in JOURNAL_RESULTS:
        raise ValueError('неизвестный результат обработки: %r' % (result,))

    payload = {
        'cabinet_code': cabinet_code,
        'result': result,
        'thread_id': _text(fields.get('thread_id')),
        'message_id': _text(fields.get('message_id')),
        'message_at': fields.get('message_at'),
        'lead_created_at': fields.get('lead_created_at'),
        'latency_ms': fields.get('latency_ms'),
        'phone_raw': _cut(fields.get('phone_raw'), 64),
        'phone_normalized': fields.get('phone_normalized'),
        'tag': fields.get('tag'),
        'amo_lead_id': fields.get('amo_lead_id'),
        'amo_contact_id': fields.get('amo_contact_id'),
        'error_text': _cut(fields.get('error_text'), 2000),
        'excerpt': _cut(fields.get('message_excerpt'), 1000),
    }

    cursor.execute("SAVEPOINT olx_journal_write")
    try:
        cursor.execute(
            """
            INSERT INTO olx_journal (cabinet_code, thread_id, message_id, message_at,
                                     lead_created_at, latency_ms, phone_raw,
                                     phone_normalized, tag, result, amo_lead_id,
                                     amo_contact_id, error_text, message_excerpt)
            VALUES (%(cabinet_code)s, %(thread_id)s, %(message_id)s, %(message_at)s,
                    %(lead_created_at)s, %(latency_ms)s, %(phone_raw)s,
                    %(phone_normalized)s, %(tag)s, %(result)s, %(amo_lead_id)s,
                    %(amo_contact_id)s, %(error_text)s, %(excerpt)s)
            RETURNING *
            """,
            payload,
        )
        # Строку забираем ЗДЕСЬ, до RELEASE SAVEPOINT.
        #
        # Курсор psycopg2 держит результат ПОСЛЕДНЕГО запроса. `RELEASE
        # SAVEPOINT` — тоже запрос, и он затирает выдачу INSERT ... RETURNING:
        # следующий fetchone() падает с «no results to fetch». На проде это
        # выглядело безобидно — «ошибка записи в журнал», — но откатывало всю
        # транзакцию вместе с отметкой «автоответ уже отправлен», и кандидат
        # получал заготовленное сообщение заново каждые полминуты. 172 копии
        # одному человеку, 01.09.2026.
        written = _one(cursor)
    except Exception as exc:
        # Откатываем ТОЧКУ, а не транзакцию: в ней уже лежит состояние чата.
        # Сделать это надо в любом случае — после ошибки транзакция иначе
        # остаётся aborted, и следующий запрос по тому же курсору упадёт.
        cursor.execute("ROLLBACK TO SAVEPOINT olx_journal_write")
        # Как «повтор» трактуем ТОЛЬКО нарушение уникальности (SQLSTATE 23505):
        # это и есть срабатывание индекса дедупликации. Всё остальное —
        # настоящая поломка (пропало поле, не та длина, оборвалось соединение),
        # и молча выдать её за дубль значило бы спрятать сбой и потерять
        # обращение, которое ТЗ терять запрещает.
        if getattr(exc, 'pgcode', None) != _UNIQUE_VIOLATION:
            raise
        return None
    cursor.execute("RELEASE SAVEPOINT olx_journal_write")
    return written


def find_recent_lead(cursor, cabinet_code, phone_normalized, day=None):
    """Уже была сегодня сделка по этому номеру в этом кабинете?

    Дешёвая проверка ПЕРЕД походом в amoCRM. Она не заменяет уникальный индекс —
    девять кабинетов опрашиваются параллельно, и гонку ловит только база, — но
    экономит лишний запрос на запись в CRM в самом частом случае.
    """
    cursor.execute(
        """
        SELECT id, amo_lead_id, created_at
          FROM olx_journal
         WHERE cabinet_code = %(cabinet_code)s
           AND phone_normalized = %(phone)s
           AND result IN ('lead_created', 'manual_review')
           AND message_at::date = %(day)s
         ORDER BY id DESC
         LIMIT 1
        """,
        {'cabinet_code': cabinet_code, 'phone': phone_normalized,
         'day': day or today_almaty()},
    )
    return _one(cursor)


def journal_page(cursor, date_from=None, date_to=None, cabinet_code=None,
                 result=None, phone=None, limit=100, offset=0):
    """Лента журнала за период. Пагинация обычная, порядок — свежие сверху."""
    where = ["1 = 1"]
    params = {'limit': max(1, min(int(limit or 100), 1000)), 'offset': max(0, int(offset or 0))}
    if date_from:
        where.append("created_at >= %(date_from)s")
        params['date_from'] = date_from
    if date_to:
        where.append("created_at < %(date_to)s")
        params['date_to'] = date_to
    if cabinet_code:
        where.append("cabinet_code = %(cabinet_code)s")
        params['cabinet_code'] = cabinet_code
    if result:
        where.append("result = %(result)s")
        params['result'] = result
    if phone:
        where.append("phone_normalized = %(phone)s")
        params['phone'] = phone

    clause = " AND ".join(where)
    cursor.execute("SELECT COUNT(*) AS total FROM olx_journal WHERE " + clause, params)
    total = _scalar(cursor) or 0

    cursor.execute(
        "SELECT * FROM olx_journal WHERE " + clause +
        " ORDER BY id DESC LIMIT %(limit)s OFFSET %(offset)s", params)
    return {'total': int(total), 'items': _all(cursor)}


def daily_summary(cursor, day=None):
    """Сводка за день по кабинетам — раздел 7 ТЗ.

    Среднее и максимальное время доставки, число не уложившихся в SLA, ошибки и
    лиды на ручную проверку. Медиану не берём намеренно: заказчик просил именно
    среднее и максимум, а по максимуму и видно выбросы.
    """
    cursor.execute(
        """
        SELECT cabinet_code,
               COUNT(*)                                          AS total,
               COUNT(*) FILTER (WHERE result = 'lead_created')    AS leads,
               COUNT(*) FILTER (WHERE result = 'duplicate')       AS duplicates,
               COUNT(*) FILTER (WHERE result = 'manual_review')   AS manual,
               COUNT(*) FILTER (WHERE result = 'canned_reply')    AS replies,
               COUNT(*) FILTER (WHERE result = 'error')           AS errors,
               COUNT(*) FILTER (WHERE latency_ms > 60000)         AS sla_missed,
               AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency_ms,
               MAX(latency_ms)                                    AS max_latency_ms
          FROM olx_journal
         WHERE created_at::date = %(day)s
         GROUP BY cabinet_code
         ORDER BY cabinet_code
        """,
        {'day': day or today_almaty()},
    )
    return _all(cursor)


def pending_retries(cursor, older_than_minutes=1, limit=200):
    """Обращения, упавшие при передаче в amoCRM и ждущие повтора.

    ТЗ: «при ошибке обращение должно повторно ставиться в очередь на отправку, а
    не удаляться». Очередь не заводим отдельной таблицей — очередь и есть эти
    строки журнала: у них `result='error'` и нет сделки.
    """
    cursor.execute(
        """
        SELECT *
          FROM olx_journal
         WHERE result = 'error'
           AND amo_lead_id IS NULL
           AND created_at < {now} - (%(minutes)s * INTERVAL '1 minute')
         ORDER BY id
         LIMIT %(limit)s
        """.format(now=_NOW),
        {'minutes': int(older_than_minutes or 1), 'limit': max(1, min(int(limit or 200), 1000))},
    )
    return _all(cursor)


def resolve_retry(cursor, journal_id, result, **fields):
    """Закрыть повторную попытку: строка журнала переходит из error в исход."""
    cursor.execute(
        """
        UPDATE olx_journal
           SET result = %(result)s,
               amo_lead_id = COALESCE(%(amo_lead_id)s, amo_lead_id),
               amo_contact_id = COALESCE(%(amo_contact_id)s, amo_contact_id),
               lead_created_at = COALESCE(%(lead_created_at)s, lead_created_at),
               latency_ms = COALESCE(%(latency_ms)s, latency_ms),
               error_text = %(error_text)s,
               attempts = attempts + 1
         WHERE id = %(id)s
        RETURNING *
        """,
        {'id': journal_id, 'result': result,
         'amo_lead_id': fields.get('amo_lead_id'),
         'amo_contact_id': fields.get('amo_contact_id'),
         'lead_created_at': fields.get('lead_created_at'),
         'latency_ms': fields.get('latency_ms'),
         'error_text': _cut(fields.get('error_text'), 2000)},
    )
    return _one(cursor)


# ─────────────────────────────────────────────────────────────────────────────
# Прогоны опроса
# ─────────────────────────────────────────────────────────────────────────────

def start_poll_run(cursor, cabinet_code):
    cursor.execute(
        "INSERT INTO olx_poll_runs (cabinet_code) VALUES (%s) RETURNING id",
        (cabinet_code,))
    return _scalar(cursor)


def finish_poll_run(cursor, run_id, **counters):
    cursor.execute(
        """
        UPDATE olx_poll_runs
           SET finished_at = {now},
               threads_seen = %(threads)s,
               messages_seen = %(messages)s,
               leads_created = %(leads)s,
               replies_sent = %(replies)s,
               errors = %(errors)s,
               error_text = %(error_text)s
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': run_id,
         'threads': int(counters.get('threads_seen') or 0),
         'messages': int(counters.get('messages_seen') or 0),
         'leads': int(counters.get('leads_created') or 0),
         'replies': int(counters.get('replies_sent') or 0),
         'errors': int(counters.get('errors') or 0),
         'error_text': _cut(counters.get('error_text'), 2000)},
    )


def list_alert_chats(cursor):
    """Чаты, выбранные для отбивки. Порядок — по названию, как в списке."""
    cursor.execute(
        "SELECT chat_id, title, chat_type, last_sent_at FROM olx_alert_chats "
        "ORDER BY title NULLS LAST, chat_id")
    return _all(cursor)


def set_alert_chats(cursor, chats, actor_id=None):
    """Заменить выбор целиком на переданный список.

    Полная замена, а не «добавить/убрать по одному»: экран отдаёт состояние
    списка целиком, и разбирать на стороне сервера, что именно изменилось,
    значило бы считать одно и то же дважды и разъезжаться при гонке двух
    вкладок. `last_sent_at` у переживших замену чатов сохраняется — иначе
    «когда последний раз уходило» обнулялось бы от каждой правки списка.
    """
    wanted = {}
    for chat in chats or []:
        try:
            chat_id = int(chat.get('chat_id'))
        except (TypeError, ValueError):
            continue
        wanted[chat_id] = {
            'chat_id': chat_id,
            'title': _cut(chat.get('title'), 255),
            'chat_type': _cut(chat.get('chat_type'), 32),
            'actor': actor_id,
        }

    if wanted:
        cursor.execute(
            "DELETE FROM olx_alert_chats WHERE NOT (chat_id = ANY(%(ids)s))",
            {'ids': list(wanted)})
    else:
        cursor.execute("DELETE FROM olx_alert_chats")

    for payload in wanted.values():
        cursor.execute(
            """
            INSERT INTO olx_alert_chats (chat_id, title, chat_type, added_by)
            VALUES (%(chat_id)s, %(title)s, %(chat_type)s, %(actor)s)
            ON CONFLICT (chat_id) DO UPDATE
               SET title = COALESCE(EXCLUDED.title, olx_alert_chats.title),
                   chat_type = COALESCE(EXCLUDED.chat_type, olx_alert_chats.chat_type)
            """,
            payload,
        )
    return list_alert_chats(cursor)


def mark_alert_sent(cursor, chat_id):
    cursor.execute(
        "UPDATE olx_alert_chats SET last_sent_at = {now} WHERE chat_id = %(chat_id)s"
        .format(now=_NOW),
        {'chat_id': int(chat_id)})


def alert_states(cursor):
    """Что по каждому поводу уже отправляли. key → {state, detail}."""
    cursor.execute("SELECT key, state, detail, notified_at FROM olx_alerts")
    return {row['key']: row for row in _all(cursor)}


def remember_alert(cursor, key, state, detail=None):
    """Запомнить отправленное состояние, чтобы не повторять его каждые пять минут."""
    cursor.execute(
        """
        INSERT INTO olx_alerts (key, state, detail)
        VALUES (%(key)s, %(state)s, %(detail)s)
        ON CONFLICT (key) DO UPDATE
           SET state = EXCLUDED.state,
               detail = EXCLUDED.detail,
               notified_at = {now}
        """.format(now=_NOW),
        {'key': key, 'state': state, 'detail': _cut(detail, 500)},
    )


def recent_failures(cursor, minutes=15):
    """Сколько обращений не доехало до amoCRM за последние минуты, по кабинетам."""
    cursor.execute(
        """
        SELECT cabinet_code, COUNT(*) AS failures
          FROM olx_journal
         WHERE result = 'error'
           AND created_at >= {now} - (%(minutes)s * INTERVAL '1 minute')
         GROUP BY cabinet_code
        """.format(now=_NOW),
        {'minutes': int(minutes or 15)},
    )
    return {row['cabinet_code']: int(row['failures']) for row in _all(cursor)}


def journal_for_export(cursor, date_from=None, date_to=None, cabinet_code=None):
    """Журнал за период целиком — для выгрузки в файл.

    Без пагинации и без потолка страницы: ТЗ требует «выгрузку за произвольный
    период», а выгрузка по пятьдесят строк выгрузкой не является. Ограничение
    одно — здравый предел, чтобы запрос за год не съел память процесса.
    """
    where = ["1 = 1"]
    params = {'limit': 200000}
    if date_from:
        where.append("created_at >= %(date_from)s")
        params['date_from'] = date_from
    if date_to:
        where.append("created_at < %(date_to)s")
        params['date_to'] = date_to
    if cabinet_code:
        where.append("cabinet_code = %(cabinet_code)s")
        params['cabinet_code'] = cabinet_code

    cursor.execute(
        "SELECT * FROM olx_journal WHERE " + " AND ".join(where) +
        " ORDER BY id LIMIT %(limit)s", params)
    return _all(cursor)


def health(cursor, idle_minutes=15):
    """Состояние робота: кто когда опрашивался и кто молчит дольше порога.

    Порог по умолчанию — 15 минут из пункта 7 ТЗ («максимальное время простоя»).
    """
    cursor.execute(
        """
        SELECT a.code,
               a.state,
               a.is_enabled,
               a.last_poll_at,
               a.last_message_at,
               a.last_lead_at,
               a.last_error,
               a.consecutive_failures,
               (a.last_poll_at IS NULL
                OR a.last_poll_at < {now} - (%(idle)s * INTERVAL '1 minute')) AS is_stale
          FROM olx_accounts a
         ORDER BY a.code
        """.format(now=_NOW),
        {'idle': int(idle_minutes or 15)},
    )
    return _all(cursor)


# ─────────────────────────────────────────────────────────────────────────────

def _text(value):
    return None if value is None else str(value)


def _cut(value, limit):
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit - 1] + '…'
