"""Уведомления раздела «Почта» в колоколе портала (ТЗ п. 8 «Контроль и уведомления»). SQL без Flask.

ТЗ п. 8: «Канал внутри ICORE обязателен; email/Telegram — опционально». Здесь — канал ICORE:
строка `mail_notifications` — одному человеку об одном событии отправления. Устроено как у
«Оплаты счетов» (payments/notices.py), два вида по тому, как гаснут:

* «от вас ждут действия» (`actionable`) — сверить входящее, передать курьеру, просрочка,
  эскалация. Просмотром не гасится: исчезает, когда дело сделано (сверили, передали, вручили);
* «к сведению» — новое входящее, итог сверки, расхождение, срок подходит. Гаснет, когда
  человек открыл карточку отправления.

Кому что (ТЗ п. 6, 8):
    новое входящее (передано курьеру)     сотрудники офиса получения
    ждёт сверки (доставлено курьером)     сотрудники офиса получения
    итог сверки / расхождение              инициатору; расхождение — ещё координатору
    «Готово к отправке» дольше N раб. дн.   инициатору (N — «Справочники и настройки»)
    срок — следующий рабочий день          инициатору и офису получения
    просрочено                             инициатору, офису получения, координатору
    ежедневная эскалация после 2 раб. дн.  руководителю (администратор раздела, центр, регион)

Заголовки короткие и с номером в начале: настольная панель колокола режет строку по ширине
(~27 знаков), номер отправления должен остаться виден.

«Координатор» и «руководитель» — роли ТЗ п. 11; вкладка «Участники» сейчас выдаёт только
сотрудников офиса, поэтому на деле это администратор раздела. Пока раздел открыт только
суперадминам (access.PILOT_SUPER_ADMIN_ONLY), строки остальным не пишутся: они звали бы туда,
куда человека не пустят. Тычок колоколу шлёт триггер на этой таблице (schema.ensure_bell_triggers).
"""

from . import access, rules

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"
MAX_TITLE = 300

# Сколько рабочих дней просрочки до ежедневной эскалации руководителю (ТЗ п. 8).
ESCALATE_AFTER_DAYS = 2
# Порог «Готово к отправке без передачи курьеру» по умолчанию; меняется в «Справочниках» (ТЗ п. 8).
READY_REMIND_DEFAULT = 1
READY_REMIND_MAX = 10

# Что гаснет само, когда отправление вручено или закрыто: ждать по нему больше нечего.
ON_THE_WAY_KINDS = ('due_soon', 'overdue', 'escalation')
# «Входящее …» — о том, что едет: доставили, сверили или вернули — эта строка уже шум.
ARRIVED_KINDS = ('incoming',) + ON_THE_WAY_KINDS


def _fmt(day):
    return day.strftime('%d.%m.%Y') if day else ''


def _route(shipment):
    return '%s → %s' % (shipment.get('sender_city') or '—', shipment.get('recipient_city') or '—')


def _work_days(count):
    """«1 рабочий день», «3 рабочих дня», «5 рабочих дней»."""
    if count % 10 == 1 and count % 100 != 11:
        return '%d рабочий день' % count
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return '%d рабочих дня' % count
    return '%d рабочих дней' % count


# ─── Кому ────────────────────────────────────────────────────────────────────

def _allowed(cursor, user_ids):
    """Только действующие сотрудники, которым раздел открыт (пилот — одни суперадмины)."""
    ids = sorted({int(x) for x in user_ids if x})
    if not ids:
        return []
    extra = "AND role = 'super_admin'" if access.PILOT_SUPER_ADMIN_ONLY else ''
    cursor.execute("SELECT id FROM users WHERE id = ANY(%s::int[]) AND COALESCE(status, '') <> 'fired' " + extra,
                   (ids,))
    return sorted(row[0] for row in cursor.fetchall())


def office_people(cursor, office_ids):
    """Сотрудники офисов — те, кто отправляет из них и принимает в них."""
    ids = [int(x) for x in office_ids if x]
    if not ids:
        return []
    cursor.execute(
        "SELECT DISTINCT p.user_id FROM mail_participants p "
        "JOIN mail_participant_offices po ON po.user_id = p.user_id "
        "WHERE po.office_id = ANY(%s::int[]) AND p.role IN ('office', 'coordinator')", (ids,))
    return [row[0] for row in cursor.fetchall()]


def _pilot_admins(cursor):
    """Пока раздел в пилоте, его администраторы — суперадмины (access.is_admin), а строк admin и
    central у них в «Участниках» нет: просрочку, расхождение и эскалацию как координатор и
    руководитель в пилоте не получал бы никто. Замещают их суперадмины, заведённые в «Участники»
    (те, кто раздел пробует), — остальным суперадминам колокол о пилоте не шумит, даже если
    тестовое отправление так и брошено «в пути». После пилота — только «Участники»."""
    if not access.PILOT_SUPER_ADMIN_ONLY:
        return []
    cursor.execute(
        "SELECT u.id FROM users u JOIN mail_participants p ON p.user_id = u.id "
        "WHERE u.role = 'super_admin' AND COALESCE(u.status, '') <> 'fired'")
    return [row[0] for row in cursor.fetchall()]


def coordinators(cursor, office_ids):
    """Координатор отправления: администратор раздела и центр — по всем офисам, координатор — по своим."""
    cursor.execute(
        "SELECT DISTINCT p.user_id FROM mail_participants p "
        "LEFT JOIN mail_participant_offices po ON po.user_id = p.user_id "
        "WHERE p.role IN ('admin', 'central') "
        "   OR (p.role = 'coordinator' AND po.office_id = ANY(%s::int[]))", ([int(x) for x in office_ids if x],))
    people = [row[0] for row in cursor.fetchall()]
    return people + _pilot_admins(cursor)


def managers(cursor, office_ids):
    """Руководитель для эскалации: администратор раздела и центр, региональный — по своим офисам."""
    cursor.execute(
        "SELECT DISTINCT p.user_id FROM mail_participants p "
        "LEFT JOIN mail_participant_offices po ON po.user_id = p.user_id "
        "WHERE p.role IN ('admin', 'central') "
        "   OR (p.role = 'regional' AND po.office_id = ANY(%s::int[]))", ([int(x) for x in office_ids if x],))
    people = [row[0] for row in cursor.fetchall()]
    return people + _pilot_admins(cursor)


def _offices(shipment):
    return [shipment.get('sender_office_id'), shipment.get('recipient_office_id')]


# ─── Запись и гашение ────────────────────────────────────────────────────────

def push(cursor, *, user_ids, shipment, kind, title, body=None, actionable=False, tone='default', dedupe_key=None,
         exclude=()):
    """Кладёт уведомление каждому из `user_ids` (кроме `exclude`). Возвращает, скольким положено.

    С `dedupe_key` второе такое же уведомление тому же человеку не создаётся — повторный
    утренний обход ничего не дублирует.
    """
    skip = {int(x) for x in exclude if x}
    ids = [x for x in _allowed(cursor, user_ids) if x not in skip]
    created = 0
    for user_id in ids:
        cursor.execute(
            """
            INSERT INTO mail_notifications (user_id, shipment_id, kind, actionable, title, body, tone, dedupe_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
            """,
            (user_id, int(shipment['id']), kind, bool(actionable), str(title or '')[:MAX_TITLE], body,
             tone if tone in ('default', 'warning') else 'default', dedupe_key))
        created += cursor.rowcount
    return created


def clear(cursor, shipment_id, kinds):
    """Гасит у всех уведомления этих видов по отправлению — дело сделано или ждать нечего."""
    cursor.execute(
        "UPDATE mail_notifications SET read_at = " + _NOW
        + " WHERE read_at IS NULL AND shipment_id = %s AND kind = ANY(%s::varchar[])",
        (int(shipment_id), list(kinds)))
    return cursor.rowcount


def clear_actionable(cursor, shipment_id):
    """Отменили или вернули — гаснет всё, что звало к делу или ждало прибытия."""
    cursor.execute(
        "UPDATE mail_notifications SET read_at = " + _NOW
        + " WHERE read_at IS NULL AND shipment_id = %s AND (actionable OR kind IN ('due_soon', 'incoming'))",
        (int(shipment_id),))
    return cursor.rowcount


def mark_seen(cursor, user_id, shipment_id):
    """Человек открыл карточку — его уведомления «к сведению» по ней прочитаны."""
    cursor.execute(
        "UPDATE mail_notifications SET read_at = " + _NOW
        + " WHERE read_at IS NULL AND NOT actionable AND user_id = %s AND shipment_id = %s",
        (int(user_id), int(shipment_id)))
    return cursor.rowcount


def bell(cursor, user_id, limit):
    """(сколько непрочитанных, первые `limit`) для колокола: сначала то, что ждёт действия."""
    cursor.execute("SELECT COUNT(*) FROM mail_notifications WHERE user_id = %s AND read_at IS NULL",
                   (int(user_id),))
    total = int(cursor.fetchone()[0] or 0)
    if not total:
        return 0, []
    cursor.execute(
        """
        SELECT id, shipment_id, kind, actionable, title, body, tone, created_at
          FROM mail_notifications
         WHERE user_id = %s AND read_at IS NULL
         ORDER BY actionable DESC, id DESC
         LIMIT %s
        """, (int(user_id), int(limit)))
    rows = [{'id': row[0], 'shipment_id': row[1], 'kind': row[2], 'actionable': bool(row[3]), 'title': row[4],
             'body': row[5], 'tone': row[6], 'created_at': row[7]} for row in cursor.fetchall()]
    return total, rows


# ─── События отправления ─────────────────────────────────────────────────────

def after_action(cursor, *, action, shipment, actor_id, outcome=None, note=None, today=None, holidays=None):
    """Уведомления после шага статуса — в той же транзакции, что и сам шаг.

    `shipment` — карточка уже ПОСЛЕ шага (queries.read_shipment), `outcome` — итог сверки,
    `today` и `holidays` — для сроков, которые встают сразу при передаче курьеру.
    """
    number = shipment['number']
    creator = shipment.get('created_by')
    if action == 'hand_over':
        clear(cursor, shipment['id'], ('ready_stale',))
        body = ' · '.join(part for part in (
            shipment.get('items_summary'), 'трек %s' % shipment['track_number'] if shipment.get('track_number') else '',
            'срок до %s' % _fmt(shipment.get('due_on')) if shipment.get('due_on') else '') if part)
        push(cursor, user_ids=office_people(cursor, [shipment.get('recipient_office_id')]), shipment=shipment,
             # Маршрутом, не «из <города>»: название города в падеж не поставить.
             kind='incoming', title='Входящее %s — %s' % (number, _route(shipment)),
             body=body, exclude=(actor_id,))
        # Срок в 1 рабочий день (внутригородская зона) — «следующий рабочий день» уже сегодня:
        # утренний обход сегодня видел отправление ещё не переданным, а завтра будет поздно.
        if today is not None:
            refresh_deadline(cursor, shipment, today=today, holidays=holidays)
    elif action == 'in_transit':
        # Снова в пути (в том числе после «Не удалось вручить») — сроки по текущему дню.
        if today is not None:
            refresh_deadline(cursor, shipment, today=today, holidays=holidays)
    elif action == 'deliver':
        # «Сверьте …» сменяет «Входящее …»: две строки об одном отправлении — шум.
        clear(cursor, shipment['id'], ARRIVED_KINDS)
        push(cursor, user_ids=office_people(cursor, [shipment.get('recipient_office_id')]), shipment=shipment,
             kind='reconcile', actionable=True,
             title='Сверьте %s — доставлено курьером' % number,
             body=shipment.get('items_summary'), exclude=(creator,))
    elif action in ('reconcile', 'resolve'):
        # Проблему решили — её «Расхождение …» больше не горит ни у кого.
        clear(cursor, shipment['id'], ('reconcile',) + ARRIVED_KINDS + (('problem',) if action == 'resolve' else ()))
        if action == 'resolve':
            push(cursor, user_ids=[creator], shipment=shipment, kind='result',
                 title='%s — проблема решена' % number, body=note, exclude=(actor_id,))
        elif outcome == 'accepted':
            push(cursor, user_ids=[creator], shipment=shipment, kind='result',
                 title='%s принято и сверено — %s' % (number, shipment.get('recipient_city') or '—'),
                 body='Сверил(а): %s' % (shipment.get('reconciled_by_name') or '—'), exclude=(actor_id,))
        else:
            # ТЗ п. 6: при расхождении «уведомляются отправитель и ответственный за почту».
            recipients = [creator] + coordinators(cursor, _offices(shipment))
            push(cursor, user_ids=recipients, shipment=shipment, kind='problem', tone='warning',
                 title='Расхождение %s — %s' % (number, _route(shipment)), body=note, exclude=(actor_id,))
    elif action in ('cancel', 'returned'):
        clear_actionable(cursor, shipment['id'])
    elif action == 'start_return':
        # Отправление едет обратно: ждать его у получателя и торопить по сроку больше незачем.
        clear(cursor, shipment['id'], ARRIVED_KINDS)
    elif action == 'not_delivered':
        # Срок у «Не удалось вручить» идёт дальше (rules.SLA_RUNNING): просрочку и эскалацию
        # утренний обход ведёт и по нему; гаснет только «срок завтра».
        clear(cursor, shipment['id'], ('due_soon',))


# ─── Утренний обход: сроки (ТЗ п. 8) ─────────────────────────────────────────

def ready_remind_days(cursor):
    cursor.execute("SELECT value FROM mail_settings WHERE key = 'ready_remind_days'")
    row = cursor.fetchone()
    try:
        value = int(row[0]) if row else READY_REMIND_DEFAULT
    except (TypeError, ValueError):
        value = READY_REMIND_DEFAULT
    return max(1, min(READY_REMIND_MAX, value))


def save_ready_remind_days(cursor, value, actor_id):
    cursor.execute(
        "INSERT INTO mail_settings (key, value, updated_by) VALUES ('ready_remind_days', %s::jsonb, %s) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by, "
        "updated_at = " + _NOW, (str(int(value)), actor_id))


def _shipments(cursor, where, params=None):
    from . import queries

    cursor.execute("SELECT s.id FROM mail_shipments s WHERE " + where + " ORDER BY s.id", params or ())
    return [queries.read_shipment(cursor, row[0]) for row in cursor.fetchall()]


def _deadline(cursor, shipment, *, today, holidays, counts):
    """Срок одного отправления, у которого он идёт (в пути и «Не удалось вручить»): «срок —
    следующий рабочий день», «просрочено», эскалация.

    Сначала гаснет то, что о прежнем сроке или уже не к месту: срок исправили (передачу курьеру,
    тип доставки), «срок завтра» после наступившей просрочки, вчерашняя эскалация. Ставит строки
    только в рабочий день."""
    due = shipment['due_on']
    left = rules.working_days_left(today, due, holidays)
    late = -left if today > due else 0
    sid = shipment['id']
    keys = {'due_soon': 'due_soon:%d:%s' % (sid, due.isoformat()),
            'overdue': 'overdue:%d:%s' % (sid, due.isoformat()),
            'escalation': 'escalation:%d:%s' % (sid, today.isoformat())}
    keep = []
    if today <= due and left <= 1:
        keep.append(keys['due_soon'])
    if today > due:
        keep.append(keys['overdue'])
    if late >= ESCALATE_AFTER_DAYS:
        keep.append(keys['escalation'])
    cursor.execute(
        "UPDATE mail_notifications SET read_at = " + _NOW + " WHERE read_at IS NULL AND shipment_id = %s "
        "AND kind = ANY(%s::varchar[]) AND (dedupe_key IS NULL OR NOT (dedupe_key = ANY(%s::varchar[])))",
        (sid, list(ON_THE_WAY_KINDS), keep))
    if not rules.is_working_day(today, holidays):
        return
    details = ' · '.join(part for part in (_route(shipment), 'трек %s' % shipment['track_number']
                                           if shipment.get('track_number') else '') if part)
    if left == 1:
        sides = [shipment.get('created_by')] + office_people(cursor, [shipment.get('recipient_office_id')])
        counts['due_soon'] += push(
            cursor, user_ids=sides, shipment=shipment, kind='due_soon', tone='warning',
            title='Срок %s — %s, следующий рабочий день' % (shipment['number'], _fmt(due)), body=details,
            dedupe_key=keys['due_soon'])
    elif today > due:
        recipients = ([shipment.get('created_by')] + office_people(cursor, [shipment.get('recipient_office_id')])
                      + coordinators(cursor, _offices(shipment)))
        counts['overdue'] += push(
            cursor, user_ids=recipients, shipment=shipment, kind='overdue', actionable=True, tone='warning',
            title='Просрочено %s — срок был %s' % (shipment['number'], _fmt(due)), body=details,
            dedupe_key=keys['overdue'])
        if late >= ESCALATE_AFTER_DAYS:
            # Ежедневная: вчерашняя строка погашена выше, сегодняшняя встаёт новой — в колоколе
            # одна строка на отправление, но каждый рабочий день свежая.
            counts['escalation'] += push(
                cursor, user_ids=managers(cursor, _offices(shipment)), shipment=shipment, kind='escalation',
                actionable=True, tone='warning',
                title='Эскалация: %s просрочено на %s' % (shipment['number'], _work_days(late)),
                body='%s · отправил(а) %s' % (details, shipment.get('created_by_name') or '—'),
                dedupe_key=keys['escalation'])


def refresh_deadline(cursor, shipment, *, today, holidays):
    """Срок встал или поменялся днём (передали курьеру, снова в пути, исправили передачу или тип
    доставки): строки о прежнем сроке гаснут сразу, а не утренним обходом; по новому — встают,
    если уже пора."""
    counts = {'due_soon': 0, 'overdue': 0, 'escalation': 0}
    if shipment.get('status') in rules.SLA_RUNNING and shipment.get('due_on'):
        _deadline(cursor, shipment, today=today, holidays=holidays, counts=counts)
    return counts


def remind(cursor, *, today, holidays):
    """Напоминания и эскалации по срокам — раз в рабочий день утром. Повторный прогон в тот же
    день ничего не дублирует (dedupe_key). Возвращает {вид: сколько строк положено}."""
    counts = {'ready_stale': 0, 'due_soon': 0, 'overdue': 0, 'escalation': 0}
    if not rules.is_working_day(today, holidays):
        return counts
    threshold = ready_remind_days(cursor)
    for shipment in _shipments(cursor, "s.status IN ('ready', 'courier_requested') AND s.ready_at IS NOT NULL"):
        waited = rules.working_days_left(shipment['ready_at'].date(), today, holidays)
        if waited > threshold:
            counts['ready_stale'] += push(
                cursor, user_ids=[shipment.get('created_by')], shipment=shipment, kind='ready_stale',
                actionable=True, tone='warning',
                title='%s ждёт передачи курьеру с %s' % (shipment['number'], _fmt(shipment['ready_at'].date())),
                body=_route(shipment), dedupe_key='ready_stale:%d' % shipment['id'])
    running = ', '.join("'%s'" % status for status in rules.SLA_RUNNING)
    for shipment in _shipments(cursor, "s.status IN (" + running + ") AND s.due_on IS NOT NULL"):
        _deadline(cursor, shipment, today=today, holidays=holidays, counts=counts)
    return counts
