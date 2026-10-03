# -*- coding: utf-8 -*-
"""SQL робота «пропущенные входящие → amoCRM» (задача #291).

Функции принимают ГОТОВЫЙ курсор и транзакцией не управляют — как весь раздел
(cdr/queries.py). Транзакции у робота короткие и лежат МЕЖДУ сетевыми вызовами, а не
вокруг них: у запроса в amoCRM таймаут минута, и держать соединение пула всё это время
значило бы отнимать его у остального приложения (тот же урок, что у робота OLX).

Ключ журнала — ключ касания (linkedid, телефон). Порядок записи — «отметка до действия»:
строка `sending` заводится ДО запроса в amoCRM отдельной транзакцией. Упади процесс между
запросом и отметкой «создано» — строка останется `sending`, и повтор сначала спросит
amoCRM, не появилась ли сделка (урок 01.09.2026: 172 копии автоответа OLX).
"""

from datetime import timedelta

from cdr import touches as touches_mod

SENDING = 'sending'
CREATED = 'created'
REPEAT = 'repeat'
CHAINED = 'chained'
ANSWERED = 'answered'
ERROR = 'error'
FAILED = 'failed'

# Допустимые статусы. CHECK в базе намеренно нет (см. schema.py), поэтому список здесь, и
# писать в журнал можно только через функции этого модуля.
STATUSES = (SENDING, CREATED, REPEAT, CHAINED, ANSWERED, ERROR, FAILED)

# Строка `sending` старше этого — процесс умер посреди запроса, её пора разбирать.
SENDING_STALE_MINUTES = 5
# Пауза перед повтором растёт с числом попыток: 2, 4, 6… минут.
RETRY_STEP_MINUTES = 2


# ─────────────────────────────────────────────────────────────────────────────
# Состояние робота
# ─────────────────────────────────────────────────────────────────────────────

def ensure_state(cursor, now):
    """С какого момента робот разбирает звонки. Первый цикл ставит «сейчас».

    Без этой отметки первый же запуск увидел бы все непринятые за последние часы и завёл
    по ним сделки задним числом — ровно та ловушка, в которую чуть не попал робот OLX.

    Сначала чтение: цикл идёт раз в пятнадцать секунд, и UPSERT на каждом писал бы строку
    5760 раз в сутки ради значения, которое меняется один раз."""
    cursor.execute("SELECT enabled_since FROM cdr_missed_state WHERE id = 1")
    row = cursor.fetchone()
    if row and row[0]:
        return row[0]
    cursor.execute("""
        INSERT INTO cdr_missed_state (id, enabled_since) VALUES (1, %s)
        ON CONFLICT (id) DO UPDATE
            SET enabled_since = COALESCE(cdr_missed_state.enabled_since, EXCLUDED.enabled_since)
        RETURNING enabled_since
    """, (now,))
    row = cursor.fetchone()
    return row[0] if row and row[0] else now


def mark_run(cursor, error=None):
    cursor.execute("""
        UPDATE cdr_missed_state
           SET last_run_at = NOW(),
               last_error = CASE WHEN %(error)s IS NULL THEN last_error ELSE %(error)s END,
               last_error_at = CASE WHEN %(error)s IS NULL THEN last_error_at ELSE NOW() END
         WHERE id = 1
    """, {'error': (str(error)[:500] if error else None)})


# ─────────────────────────────────────────────────────────────────────────────
# Что разбирать
# ─────────────────────────────────────────────────────────────────────────────

_CANDIDATE_KEYS = ('linkedid', 'phone', 'call_day', 'started_at', 'dial_seconds', 'queued_at',
                   'wait_seconds', 'hangup_side', 'line_number', 'queue', 'result')


def candidates(cursor, since, limit=50):
    """Непринятые входящие с `since`, по которым решения ещё нет. Старые — первыми:
    решение по цепочке перезвонов принимается от первого звонка к последнему."""
    cursor.execute("""
        SELECT t.linkedid, t.phone, t.call_day, t.started_at, t.dial_seconds, t.queued_at,
               t.wait_seconds, t.hangup_side, t.line_number, t.queue, t.result
          FROM cdr_touches t
         WHERE t.started_at >= %(since)s
           AND t.call_day >= %(since_day)s
           AND t.call_type = %(missed_type)s
           AND NOT EXISTS (SELECT 1 FROM cdr_missed_leads m
                            WHERE m.linkedid = t.linkedid AND m.phone = t.phone)
         ORDER BY t.started_at, t.linkedid
         LIMIT %(limit)s
    """, {'since': since, 'since_day': since.date(), 'limit': int(limit),
          'missed_type': touches_mod.TYPE_IN_MISSED})
    return [dict(zip(_CANDIDATE_KEYS, row)) for row in cursor.fetchall()]


_PHONE_TOUCH_KEYS = ('linkedid', 'phone', 'started_at', 'call_type', 'talk_seconds',
                     'dial_seconds', 'queued_at', 'wait_seconds')


def phone_touches(cursor, phones, since):
    """Все касания этих номеров с `since` — из них видно, дозвонился ли клиент."""
    phones = sorted({str(p) for p in phones or () if p})
    if not phones:
        return {}
    cursor.execute("""
        SELECT linkedid, phone, started_at, call_type, talk_seconds, dial_seconds,
               queued_at, wait_seconds
          FROM cdr_touches
         WHERE phone = ANY(%(phones)s) AND call_day >= %(since_day)s AND started_at >= %(since)s
    """, {'phones': phones, 'since': since, 'since_day': since.date()})
    out = {}
    for row in cursor.fetchall():
        item = dict(zip(_PHONE_TOUCH_KEYS, row))
        out.setdefault(item['phone'], []).append(item)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Запись решений
# ─────────────────────────────────────────────────────────────────────────────

def _key(touch):
    return str(touch['linkedid'])[:64], str(touch['phone'])[:16]


def insert_decision(cursor, touch, status, reason, ended_at=None, next_linkedid=None,
                    amo_lead_id=None, error=None):
    """Завести решение. True — строка заведена; False — решение уже было (гонка двух
    циклов или повтор после перезапуска), и второй раз ничего делать не надо.

    Строку из RETURNING забираем сразу, до любого следующего запроса на курсоре:
    psycopg2 держит результат ПОСЛЕДНЕГО запроса (урок робота OLX 01.09.2026)."""
    if status not in STATUSES:
        raise ValueError('неизвестный статус журнала пропущенных: %r' % (status,))
    linkedid, phone = _key(touch)
    cursor.execute("""
        INSERT INTO cdr_missed_leads (linkedid, phone, call_day, started_at, ended_at, status,
                                      reason, next_linkedid, amo_lead_id, error, attempts,
                                      sent_at)
        VALUES (%(linkedid)s, %(phone)s, %(call_day)s, %(started_at)s, %(ended_at)s, %(status)s,
                %(reason)s, %(next)s, %(lead)s, %(error)s,
                CASE WHEN %(status)s = 'sending' THEN 1 ELSE 0 END,
                CASE WHEN %(status)s = 'sending' THEN NOW() ELSE NULL END)
        ON CONFLICT (linkedid, phone) DO NOTHING
        RETURNING linkedid
    """, {'linkedid': linkedid, 'phone': phone, 'call_day': touch['call_day'],
          'started_at': touch['started_at'], 'ended_at': ended_at, 'status': status,
          'reason': (str(reason)[:500] if reason else None),
          'next': (str(next_linkedid)[:64] if next_linkedid else None),
          'lead': (int(amo_lead_id) if amo_lead_id else None),
          'error': (str(error)[:500] if error else None)})
    return cursor.fetchone() is not None


def mark_created(cursor, linkedid, phone, lead_id, contact_id=None, reason=None):
    cursor.execute("""
        UPDATE cdr_missed_leads
           SET status = 'created', amo_lead_id = %s, amo_contact_id = %s, error = NULL,
               reason = COALESCE(%s, reason)
         WHERE linkedid = %s AND phone = %s
    """, (int(lead_id), int(contact_id) if contact_id else None,
          (str(reason)[:500] if reason else None), linkedid, phone))


def mark_error(cursor, linkedid, phone, error, final=False):
    cursor.execute("""
        UPDATE cdr_missed_leads SET status = %s, error = %s
         WHERE linkedid = %s AND phone = %s
    """, (FAILED if final else ERROR, str(error or '')[:500], linkedid, phone))


def reclaim(cursor, linkedid, phone, status, attempts):
    """Взять упавшую или зависшую строку на повтор. Возвращает номер попытки или None.

    Сравнение — с ТЕМИ статусом и числом попыток, что прочитал due_retries: при выкладке
    Render какое-то время держит два процесса, и оба могут взять одну строку. Условие
    «status IN ('error', 'sending')» их не разводит — второй UPDATE дождался бы первого и
    прошёл бы по его же `sending`. Число попыток растёт с каждым взятием, поэтому второй
    получит ноль строк."""
    cursor.execute("""
        UPDATE cdr_missed_leads
           SET status = 'sending', attempts = attempts + 1, sent_at = NOW()
         WHERE linkedid = %s AND phone = %s AND status = %s AND attempts = %s
        RETURNING attempts
    """, (linkedid, phone, str(status), int(attempts or 0)))
    row = cursor.fetchone()
    return int(row[0]) if row else None


_RETRY_KEYS = ('linkedid', 'phone', 'call_day', 'started_at', 'status', 'attempts',
               'sent_epoch', 'line_number', 'queue', 'wait_seconds', 'result')

# sent_epoch пустой — запроса в amoCRM по строке не было вовсе (amoCRM была недоступна
# ещё при входе): искать «заведённую, но потерянную» сделку тогда не нужно и нельзя —
# поиск от начала эпохи нашёл бы любую нашу старую сделку этого клиента.


def due_retries(cursor, limit=20):
    """Строки на повтор: упавшие, у которых вышла пауза, и зависшие в `sending`.

    Касание присоединяется ради текста примечания (линия, ожидание, итог); его может уже не
    быть — сутки перечитаны и звонок сменил linkedid, — тогда примечание выйдет короче."""
    cursor.execute("""
        SELECT m.linkedid, m.phone, m.call_day, m.started_at, m.status, m.attempts,
               EXTRACT(EPOCH FROM m.sent_at)::BIGINT, t.line_number, t.queue, t.wait_seconds,
               t.result
          FROM cdr_missed_leads m
          LEFT JOIN cdr_touches t ON t.linkedid = m.linkedid AND t.phone = m.phone
         WHERE (m.status = 'error'
                AND (m.sent_at IS NULL
                     OR m.sent_at < NOW() - make_interval(mins => %(step)s * m.attempts)))
            OR (m.status = 'sending'
                AND m.sent_at < NOW() - make_interval(mins => %(stale)s))
         ORDER BY m.started_at
         LIMIT %(limit)s
    """, {'step': RETRY_STEP_MINUTES, 'stale': SENDING_STALE_MINUTES, 'limit': int(limit)})
    return [dict(zip(_RETRY_KEYS, row)) for row in cursor.fetchall()]


def mark_repeat(cursor, linkedid, phone, lead_id, reason):
    """Строка, ждавшая повтора, закрыта нашей же сделкой, заведённой по другому звонку."""
    cursor.execute("""
        UPDATE cdr_missed_leads
           SET status = 'repeat', amo_lead_id = %s, reason = %s, error = NULL
         WHERE linkedid = %s AND phone = %s
    """, (int(lead_id), str(reason or '')[:500], linkedid, phone))


def pending_rows(cursor, phone, since, linkedid, before):
    """Сколько наших строк по номеру «в полёте» (`sending`/`error`) РАНЬШЕ этого звонка.

    По такой строке сделка в amoCRM могла уже появиться — запрос дошёл, ответ потерялся.
    Пока она не разобрана повтором, новую сделку тому же клиенту заводить нельзя: так
    появилась бы вторая.

    Только более ранние: две ждущие строки одного клиента иначе ждали бы друг друга и обе
    ушли бы в `failed`. Так первая идёт первой, а следующие находят её сделку повтором."""
    cursor.execute("""
        SELECT COUNT(*) FROM cdr_missed_leads
         WHERE phone = %s AND status IN ('sending', 'error') AND started_at >= %s
           AND started_at < %s AND linkedid <> %s
    """, (str(phone)[:16], since, before, str(linkedid or '')[:64]))
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def latest_own_lead(cursor, phone, since):
    """Последняя НАША сделка по номеру с `since`: (lead_id, когда звонил) или None."""
    cursor.execute("""
        SELECT amo_lead_id, started_at FROM cdr_missed_leads
         WHERE phone = %s AND status = 'created' AND amo_lead_id IS NOT NULL
           AND started_at >= %s
         ORDER BY started_at DESC
         LIMIT 1
    """, (str(phone)[:16], since))
    row = cursor.fetchone()
    return (int(row[0]), row[1]) if row else None


# Как далеко назад тянется цепочка «перезвонил и снова не дозвонился». Цепочка растёт
# шагами по минуте, так что трёх часов хватает с огромным запасом.
CHAIN_LOOKBACK = timedelta(hours=3)


def resolve_chain(cursor, phone, before, lead_id=None, answered_reason=None):
    """Раздать итог последнего звонка цепочки предыдущим (`chained`) того же номера.

    Сделка заведена — у предыдущих звонков появляется та же сделка (метка «в amoCRM» в
    разделе); клиент дозвонился — предыдущие тоже закрываются как «дозвонился»."""
    since = before - CHAIN_LOOKBACK
    if lead_id:
        cursor.execute("""
            UPDATE cdr_missed_leads SET amo_lead_id = %s
             WHERE phone = %s AND status = 'chained' AND amo_lead_id IS NULL
               AND started_at < %s AND started_at >= %s
        """, (int(lead_id), str(phone)[:16], before, since))
    elif answered_reason:
        cursor.execute("""
            UPDATE cdr_missed_leads SET status = 'answered', reason = %s
             WHERE phone = %s AND status = 'chained' AND amo_lead_id IS NULL
               AND started_at < %s AND started_at >= %s
        """, (str(answered_reason)[:500], str(phone)[:16], before, since))


def chain_target(cursor, linkedid, phone):
    """Решение по звонку, на который перенесена цепочка: (статус, сделка, причина) или None."""
    cursor.execute("""
        SELECT status, amo_lead_id, reason FROM cdr_missed_leads
         WHERE linkedid = %s AND phone = %s
    """, (str(linkedid)[:64], str(phone)[:16]))
    row = cursor.fetchone()
    return (row[0], row[1], row[2]) if row else None
