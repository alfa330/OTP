"""SQL-слой раздела «Чаты водителей».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Так же устроены wiki, crm, parcels
и call_qa.

Почему это важно именно здесь: кнопка «Передан» отправляет заметку в чужую
систему и пишет строку в журнал. Заметку отозвать нельзя (метода DELETE у
messages вендор не даёт), поэтому запись в журнал обязана лечь в ту же
транзакцию, что и всё остальное действие: «передал, но в журнале нет» — это
ровно та дыра, ради закрытия которой журнал и просили.
"""

import json
import logging
import re
from datetime import datetime, timedelta

from . import access

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

_ALMATY_OFFSET = timedelta(hours=5)


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────
#
# Направление берём вместе с моделью расчёта: по ней (а не по id и не по имени)
# опознаётся чат-менеджер, которому раздел закрыт. Направления версионируются —
# id 69 лишь текущая версия «Чат менеджера», — а переименование направления
# обнуляет привязку операторов. Модель переживает и то, и другое.

_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, name, role, department_id, direction_id, status
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
    (SELECT dir.calculation_model_code FROM directions dir
      WHERE dir.id = (SELECT direction_id FROM me)),
    (SELECT status        FROM me),
    COALESCE((SELECT array_agg(id)   FROM headed), '{}'),
    COALESCE((SELECT array_agg(code) FROM headed), '{}')
"""


def load_access_context(cursor, user_id):
    """Профиль + периметр одним запросом."""
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, direction_model, status, headed, headed_codes = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': access.normalize_role(role),
        'department_id': department_id,
        'department_code': department_code,
        'direction_model': direction_model,
        'status': status,
        'headed_department_ids': list(headed or []),
        'headed_department_codes': list(headed_codes or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Мост «телефон -> клиент Chat2Desk» по своей базе
# ─────────────────────────────────────────────────────────────────────────────
#
# Телефон уже лежит в c2d_requests: его заполняет ежедневный синк метрик теми же
# строками, что идут в chat_manager_daily_metrics, — то есть БЕСПЛАТНО, без
# отдельного вызова API. Заполнен он у 100 % строк (85 222 из 85 222), а связь
# «телефон -> client_id» однозначна: ни у одного номера за 45 дней нет двух
# разных клиентов (проверено запросом с HAVING count(DISTINCT client_id) > 1).
#
# Ретеншн таблицы 45 дней, поэтому мост находится примерно для 61 % номеров;
# остальным (тем, кто пишет впервые) client_id добирается вызовом /v1/clients.
# Это единственное место, где раздел вообще может потратить квоту на ПОИСК.
#
# НОМЕР ЛЕЖИТ В ОДНОЙ ИЗ ДВУХ КОЛОНОК. С 02.09.2026 водитель может прийти в
# WhatsApp идентификатором вместо номера: тогда в client_phone стоит
# «[wa_gupshup] KZ.1000000000000001», а номер — только в assigned_phone (синк
# пишет её начиная со среза за 17.09.2026). У «Ноль такси» так приходит каждое
# шестое обращение, у iTaxi и Jana — каждое восьмое.
# Ищем по обеим колонкам, у каждой свой индекс.

_LOCAL_CLIENT_SQL = """
    SELECT client_id
      FROM c2d_requests
     WHERE (client_phone = ANY(%(variants)s) OR assigned_phone = ANY(%(variants)s))
       AND client_id IS NOT NULL
     ORDER BY day DESC
     LIMIT 1
"""


def local_client_id(cursor, variants):
    """client_id по телефону из своей базы. Ноль вызовов API."""
    if not variants:
        return None
    cursor.execute(_LOCAL_CLIENT_SQL, {'variants': list(variants)})
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


# Поиск по ХВОСТУ номера — запасной ход для иностранных номеров и для любой
# записи, которой нет среди точных вариантов.
#
# Зачем: точные варианты перебирают то, как номер пишут У НАС («8…», «+7…», без
# кода страны). Кодов стран две сотни, и достроить турецкий «531 729 83 61» до
# «90 531 729 83 61» нельзя ничем, кроме гадания. А хвост у номера один и тот же
# в любой записи.
#
# Выражение в WHERE — ДОСЛОВНО то же, что в индексе idx_c2d_requests_phone_tail
# (driver_chats/schema.py): разойдись они хоть пробелом, планировщик индекс не
# возьмёт и уйдёт в полный проход по 86 тыс. строк (замер: 0,2 с против единиц
# миллисекунд).
#
# Номер ищется в ОБЕИХ колонках — client_phone и assigned_phone (см. мост
# выше), у каждой свой индекс по тому же выражению.
#
# Несколько строк, а не одна: по одному хвосту НЕ должно находиться двух разных
# водителей (проверено на всей базе — 16 421 хвост, ни одного совпадения), но
# если это однажды случится, раздел обязан заметить и спросить номер целиком, а
# не показать переписку соседа.
_LOCAL_CLIENT_BY_TAIL_SQL = r"""
    SELECT client_id, phone, max(day)
      FROM (
        SELECT client_id, client_phone AS phone, day
          FROM c2d_requests
         WHERE right(regexp_replace(client_phone, '\D', '', 'g'), 9) = %(tail)s
           AND client_id IS NOT NULL
           -- Только то, что и правда номер. В этой же колонке вендор держит
           -- идентификаторы WhatsApp («[wa_gupshup] KZ.1000000000000001», 881
           -- клиент с 02.09.2026): у них тоже есть девять последних цифр, и без
           -- этого условия поиск выдал бы за водителя чужой диалог.
           AND client_phone ~ '^[+]?[0-9]{10,15}$'
        UNION ALL
        SELECT client_id, assigned_phone, day
          FROM c2d_requests
         WHERE right(regexp_replace(assigned_phone, '\D', '', 'g'), 9) = %(tail)s
           AND client_id IS NOT NULL
           AND assigned_phone ~ '^[+]?[0-9]{10,15}$'
      ) found
     GROUP BY client_id, phone
     ORDER BY max(day) DESC
     LIMIT 5
"""


def local_client_by_tail(cursor, tail):
    """(client_id, телефон_как_в_базе) по хвосту номера.

    Возвращает:
        None                — не нашли;
        (client_id, phone)  — нашли ровно одного водителя;
        ('ambiguous', None) — хвост делят несколько водителей.

    Водитель — это НОМЕР, а не клиент вендора. Пришёл тот же человек и с
    номером, и идентификатором WhatsApp — у вендора это два клиента, но
    переспрашивать номер целиком тут не о чем: берём самого свежего, как и
    точный поиск (local_client_id). «Несколько водителей» — только когда за
    хвостом стоят разные номера.

    Телефон отдаём В ТОЙ ЗАПИСИ, в какой он лежит у нас: под ней потом
    сохраняется кеш и пишется журнал, и повторный поиск того же водителя
    попадает уже в точный путь, без второго прохода по хвосту.
    """
    if not tail:
        return None
    cursor.execute(_LOCAL_CLIENT_BY_TAIL_SQL, {'tail': str(tail)})
    # Строки уже идут от свежей к старой — ORDER BY в самом запросе.
    rows = [r for r in cursor.fetchall() if r and r[0] is not None]
    if not rows:
        return None
    numbers = {re.sub(r'\D', '', str(row[1] or '')) for row in rows}
    if len(numbers) > 1:
        return ('ambiguous', None)
    return (int(rows[0][0]), rows[0][1])


# ── То же самое, но из потока вебхуков ────────────────────────────────────────
#
# Зачем второй источник. `c2d_requests` наполняется ночным синком, поэтому тот,
# кто написал впервые СЕГОДНЯ, в нём появится только завтра — за него раздел
# платил вызовом `/v1/clients` (до трёх запросов), а переписку брал у вендора
# (ещё один, и не чаще раза в пять минут: столько живёт кеш). События приходят
# сами и лежат в `c2d_webhook_events` неделю, то есть покрывают и сегодняшний
# день, и всё окно раздела — двое суток.
#
# Правила поиска ТЕ ЖЕ, что выше: точные варианты номера, потом хвост из девяти
# цифр, и «несколько водителей за одним хвостом» — это отказ, а не показ
# переписки соседа. Отдельные функции, а не общий SQL с UNION: у таблиц разный
# ретеншн (45 суток против недели) и разные индексы, а слитый запрос заставил бы
# планировщик выбирать между ними на каждый поиск.

# Сколько поток считается живым. То же число, что у табло: вендор отключает
# вебхук после пяти часов неуспешных доставок, а молчание потока внешне
# неотличимо от «сегодня никто не писал».
STREAM_STALE_SECONDS = 1800

# Сообщения ленты. `system_message` тоже здесь: лента показывает служебные
# строки диалога, а `imported_message` — это сообщения, пришедшие напрямую из
# мессенджера, они НЕ поднимают inbox/outbox (см. документацию вендора).
_WEBHOOK_MESSAGE_HOOKS = ('inbox', 'outbox', 'imported_message', 'comment',
                          'system_message')


def webhook_stream_covers(cursor, window_from):
    """Покрывает ли поток событий окно раздела: жив сейчас и начался до его начала.

    Второе условие обязательно: включили вебхук вчера — сегодняшний день он
    закрывает, а позавчерашний нет, и лента вышла бы обрезанной молча.
    Любая неожиданность здесь — это «нет», а не исключение: раздел обязан
    ответить человеку, а вендорский путь работает и без потока."""
    try:
        cursor.execute(
            """
            SELECT MIN(event_at), MAX(event_at),
                   (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') AS now_almaty
              FROM c2d_webhook_events
             WHERE hook_type = ANY(%(hooks)s)
            """,
            {'hooks': list(_WEBHOOK_MESSAGE_HOOKS)})
        row = cursor.fetchone()
        if not row or len(row) < 3 or row[0] is None or row[1] is None:
            return False
        first_at, last_at, now_almaty = row[0], row[1], row[2]
        if (now_almaty - last_at).total_seconds() > STREAM_STALE_SECONDS:
            return False
        return first_at.date() <= window_from
    except Exception:
        logging.warning("Чаты водителей: поток вебхуков недоступен, идём к вендору",
                        exc_info=True)
        return False


_WEBHOOK_CLIENT_SQL = """
    SELECT client_id
      FROM c2d_webhook_events
     WHERE client_phone = ANY(%(variants)s)
       AND client_id IS NOT NULL
     ORDER BY event_at DESC
     LIMIT 1
"""


def webhook_client_id(cursor, variants):
    """client_id по точному номеру из потока событий. Ноль вызовов API."""
    if not variants:
        return None
    cursor.execute(_WEBHOOK_CLIENT_SQL, {'variants': list(variants)})
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


# Выражение хвоста — ДОСЛОВНО то же, что в индексе idx_c2d_webhook_events_phone_tail
# (database.py): разойдись они хоть пробелом, индекс не возьмётся.
_WEBHOOK_CLIENT_BY_TAIL_SQL = r"""
    SELECT client_id, client_phone, max(event_at)
      FROM c2d_webhook_events
     WHERE right(regexp_replace(client_phone, '\D', '', 'g'), 9) = %(tail)s
       AND client_id IS NOT NULL
       -- Только то, что и правда номер: в этой же колонке оказывается
       -- идентификатор WhatsApp, а девять последних цифр есть и у него.
       AND client_phone ~ '^[+]?[0-9]{10,15}$'
     GROUP BY client_id, client_phone
     ORDER BY max(event_at) DESC
     LIMIT 5
"""


def webhook_client_by_tail(cursor, tail):
    """(client_id, номер как в потоке) по хвосту. Форма ответа — как у local_client_by_tail."""
    if not tail:
        return None
    cursor.execute(_WEBHOOK_CLIENT_BY_TAIL_SQL, {'tail': str(tail)})
    rows = [r for r in cursor.fetchall() if r and r[0] is not None]
    if not rows:
        return None
    numbers = {re.sub(r'\D', '', str(row[1] or '')) for row in rows}
    if len(numbers) > 1:
        return ('ambiguous', None)
    return (int(rows[0][0]), rows[0][1])


def webhook_messages(cursor, client_id, window_from, window_to):
    """Сырые события-сообщения клиента за окно, от старых к новым.

    Отдаём тела событий как есть: приводит их к виду ленты `chat2desk.message_from_event`,
    и разбор формы вендора остаётся в одном месте — рядом с разбором ответа `/v1/messages`."""
    if not client_id:
        return []
    cursor.execute(
        """
        SELECT payload
          FROM c2d_webhook_events
         WHERE client_id = %(client_id)s
           AND hook_type = ANY(%(hooks)s)
           AND day BETWEEN %(window_from)s AND %(window_to)s
         ORDER BY event_at, id
        """,
        {'client_id': int(client_id), 'hooks': list(_WEBHOOK_MESSAGE_HOOKS),
         'window_from': window_from, 'window_to': window_to})
    return [row[0] for row in cursor.fetchall() if isinstance(row[0], dict)]


def chat_author_names(cursor, days=30):
    """{id оператора Chat2Desk: имя} из своей базы — подпись автора без вызова API.

    У вендора это `/v1/operators` (кеш процесса на шесть часов), но в потоке событий
    имени нет вовсе, только id, а спрашивать вендора ради подписи — снова платить
    квотой за то, что уже лежит в `c2d_requests` с ночного синка."""
    cursor.execute(
        """
        SELECT DISTINCT ON (c2d_operator_id) c2d_operator_id, c2d_operator_name
          FROM c2d_requests
         WHERE c2d_operator_id IS NOT NULL
           AND NULLIF(c2d_operator_name, '') IS NOT NULL
           AND day >= CURRENT_DATE - %(days)s
         ORDER BY c2d_operator_id, day DESC
        """,
        {'days': int(days)})
    return {int(row[0]): str(row[1]) for row in cursor.fetchall()}


# Справочник таксопарков. Канал в Chat2Desk = парк, на чей номер написал
# водитель, и оператору он нужен первым делом: по одному и тому же телефону
# приходят чаты разных парков.
#
# Берём из своей базы, а не из вендора: `c2d_requests` наполняется ночным синком
# метрик бесплатно и держит 45 дней, а это 14 парков — весь живой трафик.
# Вендорский `/v1/channels` остаётся запасным (и неполным: 15 записей из 23,
# offset он игнорирует).
_CHANNELS_SQL = """
    SELECT channel_id, max(channel_name)
      FROM c2d_requests
     WHERE channel_id IS NOT NULL
       AND COALESCE(NULLIF(TRIM(channel_name), ''), '') <> ''
     GROUP BY channel_id
"""


# Справочник парков держим в процессе. Запрос бесплатный по деньгам, но не по
# времени: индекса под него нет и быть не может — это агрегат по всей таблице,
# Seq Scan по 86 052 строкам ради 14 значений, 45 мс на КАЖДОМ поиске и
# обновлении (замер на проде 07.09.2026). Парков 14 и новый появляется раз в
# месяцы, а только что подключённый и так подхватится: неизвестный канал
# добирается у вендора отдельной веткой в /search.
_CHANNELS_CACHE = {'names': None, 'at': None}
_CHANNELS_TTL = timedelta(minutes=30)


def channel_names(cursor):
    """id канала -> название таксопарка. Ноль вызовов API."""
    at = _CHANNELS_CACHE.get('at')
    if _CHANNELS_CACHE.get('names') is not None and at:
        if now_almaty() - at < _CHANNELS_TTL:
            return _CHANNELS_CACHE['names']
    cursor.execute(_CHANNELS_SQL)
    names = {int(row[0]): row[1] for row in cursor.fetchall() if row[0] is not None}
    _CHANNELS_CACHE['names'] = names
    _CHANNELS_CACHE['at'] = now_almaty()
    return names


# Метаданные заявок — канал, чат-менеджер, оценка водителя. Живут в той же
# бесплатной таблице. Сегодняшних заявок в ней нет (синк идёт в 04:10 за вчера),
# поэтому обогащение НЕОБЯЗАТЕЛЬНОЕ: чат показывается и без него, просто без
# названия канала и оценки.
_REQUEST_META_SQL = """
    SELECT request_id, channel_name, transport, c2d_operator_name,
           request_type, rating_score, rating_text, client_name
      FROM c2d_requests
     WHERE request_id = ANY(%(ids)s)
"""


def request_meta(cursor, request_ids):
    """request_id -> метаданные заявки. Пустой словарь, если данных ещё нет."""
    ids = [int(rid) for rid in (request_ids or []) if rid is not None]
    if not ids:
        return {}
    cursor.execute(_REQUEST_META_SQL, {'ids': ids})
    out = {}
    for row in cursor.fetchall():
        out[int(row[0])] = {
            'channel_name': row[1],
            'transport': row[2],
            'operator_name': row[3],
            'request_type': row[4],
            'rating_score': row[5],
            'rating_text': row[6],
            'client_name': row[7],
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Кеш переписки
# ─────────────────────────────────────────────────────────────────────────────

_CACHE_GET_SQL = """
    SELECT messages, fetched_at, window_from, window_to
      FROM dch_message_cache
     WHERE client_id = %(client_id)s
"""

_CACHE_PUT_SQL = """
    INSERT INTO dch_message_cache
        (client_id, phone, messages, messages_count, window_from, window_to, fetched_at)
    VALUES
        (%(client_id)s, %(phone)s, %(messages)s, %(count)s, %(window_from)s, %(window_to)s, {now})
    ON CONFLICT (client_id) DO UPDATE SET
        phone = EXCLUDED.phone,
        messages = EXCLUDED.messages,
        messages_count = EXCLUDED.messages_count,
        window_from = EXCLUDED.window_from,
        window_to = EXCLUDED.window_to,
        fetched_at = EXCLUDED.fetched_at
""".format(now=_NOW)


def cached_messages(cursor, client_id, window_from, window_to, ttl_seconds):
    """Свежий кеш переписки и время его снятия — или (None, None).

    Кеш существует ради квоты вендора и ради скорости: смена целиком открывает
    один и тот же чат несколько раз подряд, пока водитель на линии. TTL короткий
    (минуты): переписка живая, и показать оператору вчерашнее состояние диалога,
    который идёт прямо сейчас, — хуже, чем сходить в API ещё раз.

    Время снятия отдаём вместе с сообщениями, потому что экран подписывает ленту
    «обновлено в HH:MM». Взять эту минуту с часов браузера значило бы соврать
    ровно на возраст кеша — до пяти минут на разговоре, который идёт прямо
    сейчас, — и обесценить кнопку «Обновить»: подпись менялась бы, а лента нет.
    """
    cursor.execute(_CACHE_GET_SQL, {'client_id': int(client_id)})
    row = cursor.fetchone()
    if not row:
        return None, None
    messages, fetched_at, cached_from, cached_to = row
    if not fetched_at:
        return None, None
    # Окно сдвинулось (наступил новый день) — кеш больше не про то, что просят.
    if cached_from != window_from or cached_to != window_to:
        return None, None
    if (now_almaty() - fetched_at).total_seconds() > max(0, int(ttl_seconds)):
        return None, None
    return (messages or []), fetched_at


_CACHED_CLIENT_SQL = """
    SELECT client_id
      FROM dch_message_cache
     WHERE phone = %(phone)s
     ORDER BY fetched_at DESC
     LIMIT 1
"""


def cached_client_id(cursor, phone):
    """client_id по телефону из СВОЕЙ памяти. Ноль вызовов API, один индекс.

    Зачем отдельно от local_client_id: тот ищет по c2d_requests, которую
    наполняет НОЧНОЙ синк («в 04:10 за вчера»). Водителя, который написал
    впервые сегодня, там нет — и каждый поиск по нему, включая «Обновить»,
    уходил к вендору перебирать записи номера: до трёх вызовов, ~360 мс. А наша
    собственная таблица эту пару узнала ещё на первом поиске.

    Срок жизни у ПАРЫ телефон-клиент не тот, что у сообщений: сообщения
    протухают за пять минут, а клиент за номером закреплён навсегда. Поэтому
    читаем строку без оглядки на fetched_at.
    """
    if not phone:
        return None
    cursor.execute(_CACHED_CLIENT_SQL, {'phone': phone})
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else None


_PENDING_NOTES_SQL = """
    SELECT c2d_message_id, comment_text, channel_id, dialog_id, request_id, created_at
      FROM dch_events
     WHERE kind = 'handoff'
       AND client_id = %(client_id)s
       AND c2d_message_id IS NOT NULL
       AND created_at >= {now} - make_interval(mins => %(minutes)s)
     ORDER BY created_at
""".format(now=_NOW)


def pending_handoff_notes(cursor, client_id, minutes):
    """Заметки «Передан», отправленные только что. Ноль вызовов API.

    Вендор показывает свежесозданное сообщение в /v1/messages примерно через
    минуту (замер 07.09.2026), и всё это время оператор не видит собственный
    комментарий. Журнал же знает про него сразу: там лежит и id, который вернул
    вендор, и точный текст, и адрес чата. Отсюда лента и добирает недостающее.

    Окно намеренно шире наблюдавшегося запаздывания: лишняя заметка ничего не
    портит — доехавшая копия вендора вытеснит её по совпадению id.
    """
    cursor.execute(_PENDING_NOTES_SQL,
                   {'client_id': int(client_id), 'minutes': int(minutes)})
    return [{
        'message_id': row[0],
        'text': row[1] or '',
        'channel_id': row[2],
        'dialog_id': row[3],
        'request_id': row[4],
        'created': row[5],
    } for row in cursor.fetchall()]


def drop_cached_messages(cursor, client_id):
    """Пометить кеш переписки протухшим.

    Зовётся сразу после «Передан»: заметка уже лежит в чате у вендора, но наш
    снимок ей на пять минут старше. Без сброса оператор жмёт «Найти» и НЕ видит
    собственный комментарий — выглядит это как «кнопка не сработала», и человек
    жмёт её второй раз, а отозвать лишнюю заметку через API нельзя.

    Не DELETE, а состаривание: строка держит ещё и пару телефон-клиент
    (cached_client_id), которая к свежести сообщений отношения не имеет. Удалив
    строку, мы бы после каждого «Передан» заставляли следующий же поиск заново
    искать клиента у вендора — то есть тормозили ровно тот случай, ради
    которого кнопку «Обновить» и жмут. Уборщик раздела снимет строку сам, когда
    она выйдет за двухсуточное окно.
    """
    cursor.execute(
        "UPDATE dch_message_cache "
        "   SET fetched_at = %(stale)s "
        " WHERE client_id = %(client_id)s",
        {'client_id': int(client_id), 'stale': datetime(1970, 1, 1)})
    return cursor.rowcount or 0


def store_messages(cursor, client_id, phone, messages, window_from, window_to):
    cursor.execute(_CACHE_PUT_SQL, {
        'client_id': int(client_id),
        'phone': phone,
        'messages': json.dumps(messages, ensure_ascii=False),
        'count': len(messages or []),
        'window_from': window_from,
        'window_to': window_to,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Журнал
# ─────────────────────────────────────────────────────────────────────────────

_EVENT_INSERT_SQL = """
    INSERT INTO dch_events
        (kind, user_id, user_name, user_role, department_id, phone, client_id,
         channel_id, dialog_id, request_id, channel_name, comment_text,
         c2d_message_id, messages_count, ip_address, user_agent)
    VALUES
        (%(kind)s, %(user_id)s, %(user_name)s, %(user_role)s, %(department_id)s,
         %(phone)s, %(client_id)s, %(channel_id)s, %(dialog_id)s, %(request_id)s,
         %(channel_name)s, %(comment_text)s, %(c2d_message_id)s, %(messages_count)s,
         %(ip)s, %(ua)s)
    RETURNING id, created_at
"""


def log_event(cursor, ctx, kind, **fields):
    """Строка журнала. Снимок человека берётся из контекста, а не джойнится потом.

    Журнал отвечает на вопрос «кто это сделал ТОГДА»: человек меняет отдел,
    увольняется, роль ему заменяют назначением главой отдела. Джойн к users в
    момент чтения показал бы сегодняшнее состояние, а не то, что было.
    """
    cursor.execute(_EVENT_INSERT_SQL, {
        'kind': kind,
        'user_id': int(ctx['user_id']),
        'user_name': ctx.get('name'),
        'user_role': ctx.get('role'),
        'department_id': ctx.get('department_id'),
        'phone': fields.get('phone'),
        'client_id': fields.get('client_id'),
        'channel_id': fields.get('channel_id'),
        'dialog_id': fields.get('dialog_id'),
        'request_id': fields.get('request_id'),
        'channel_name': fields.get('channel_name'),
        'comment_text': fields.get('comment_text'),
        'c2d_message_id': fields.get('c2d_message_id'),
        'messages_count': fields.get('messages_count'),
        'ip': fields.get('ip_address'),
        'ua': (fields.get('user_agent') or '')[:500] or None,
    })
    row = cursor.fetchone()
    return {'id': row[0], 'created_at': row[1].isoformat() if row and row[1] else None}


_JOURNAL_WHERE = """
     WHERE (%(date_from)s IS NULL OR e.created_at >= %(date_from)s)
       AND (%(date_to)s   IS NULL OR e.created_at <  %(date_to)s)
       AND (%(kinds)s IS NULL OR e.kind = ANY(%(kinds)s))
       AND (%(user_id)s IS NULL OR e.user_id = %(user_id)s)
       AND (%(phone)s IS NULL OR e.phone = %(phone)s)
"""

_JOURNAL_PAGE_SQL = """
    SELECT e.id, e.kind, e.user_id, e.user_name, e.user_role, e.phone,
           e.client_id, e.channel_id, e.dialog_id, e.request_id, e.channel_name,
           e.comment_text, e.c2d_message_id, e.messages_count,
           e.ip_address, e.created_at
      FROM dch_events e
""" + _JOURNAL_WHERE + """
     ORDER BY e.created_at DESC, e.id DESC
     LIMIT %(limit)s OFFSET %(offset)s
"""

# Счёт выборки — отдельным агрегатом, а не COUNT(*) OVER (). Оконный счётчик
# обнуляется на странице за последней строкой, и пагинация начинает врать
# (та же ловушка, что чинили в разделе «Сессии»).
_JOURNAL_COUNT_SQL = """
    SELECT count(*),
           count(*) FILTER (WHERE e.kind = 'handoff'),
           count(DISTINCT e.user_id),
           count(DISTINCT e.phone)
      FROM dch_events e
""" + _JOURNAL_WHERE


def _journal_params(filters):
    return {
        'date_from': filters.get('date_from'),
        'date_to': filters.get('date_to'),
        'kinds': list(filters['kinds']) if filters.get('kinds') else None,
        'user_id': filters.get('user_id'),
        'phone': filters.get('phone'),
        'limit': int(filters.get('limit') or 50),
        'offset': int(filters.get('offset') or 0),
    }


def _event_row(row):
    return {
        'id': row[0],
        'kind': row[1],
        'user_id': row[2],
        'user_name': row[3],
        'user_role': row[4],
        'phone': row[5],
        'client_id': row[6],
        'channel_id': row[7],
        'dialog_id': row[8],
        'request_id': row[9],
        'channel_name': row[10],
        'comment_text': row[11],
        'c2d_message_id': row[12],
        'messages_count': row[13],
        'ip_address': row[14],
        'created_at': row[15].isoformat() if row[15] else None,
    }


def journal_page(cursor, filters):
    """Страница журнала + сводка по ВСЕЙ выборке (а не по странице)."""
    params = _journal_params(filters)
    cursor.execute(_JOURNAL_PAGE_SQL, params)
    items = [_event_row(row) for row in cursor.fetchall()]
    cursor.execute(_JOURNAL_COUNT_SQL, params)
    # Агрегат в Postgres строку возвращает всегда, но распаковка вслепую делает
    # из пустого ответа 500 вместо пустого журнала. Раздел новый — пусть он
    # честно показывает «действий не было», а не внутреннюю ошибку.
    total, handoffs, people, drivers = cursor.fetchone() or (0, 0, 0, 0)
    return {
        'items': items,
        'total': int(total or 0),
        'summary': {
            'events': int(total or 0),
            'handoffs': int(handoffs or 0),
            'people': int(people or 0),
            'drivers': int(drivers or 0),
        },
    }


def journal_all(cursor, filters, cap=20000):
    """Вся выборка для выгрузки. Потолок — чтобы книга не съела память инстанса."""
    params = _journal_params(filters)
    params['limit'] = int(cap)
    params['offset'] = 0
    cursor.execute(_JOURNAL_PAGE_SQL, params)
    return [_event_row(row) for row in cursor.fetchall()]


_JOURNAL_PEOPLE_SQL = """
    SELECT e.user_id, max(e.user_name), count(*)
      FROM dch_events e
     GROUP BY e.user_id
     ORDER BY count(*) DESC
     LIMIT 200
"""


def journal_people(cursor):
    """Кого вообще видел журнал — для выпадающего фильтра.

    Берём из журнала, а не из справочника сотрудников: в фильтре должны стоять
    те, по кому есть что смотреть, включая уже уволенных.
    """
    cursor.execute(_JOURNAL_PEOPLE_SQL)
    return [{'user_id': row[0], 'name': row[1], 'events': int(row[2] or 0)}
            for row in cursor.fetchall()]
