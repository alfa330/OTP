"""Перенос заявок первой версии раздела (12 шагов, задача #179) на этапы и
подзадачи ТЗ «Закуп и оплата» (#381).

Зовётся при старте из schema.init_payments_schema под своим SAVEPOINT и
ничего не делает, когда переносить нечего: заявка считается перенесённой, если у
неё есть хоть одна подзадача. Старые шаги (`payment_request_steps`) остаются в
базе как были — перенос только читает их.

Как двенадцать шагов ложатся на этапы:

    шаг 1            заявка у инициатора (черновик)
    шаг 2            согласование руководителем
    шаг 3            утверждение
    шаги 4–7         закуп утверждён, счёта ещё нет → «Оплата счёта» ждёт счёт от инициатора
    шаги 8–9         счёт у бухгалтерии на проверке (второго согласования счёта в ТЗ нет)
    шаг 10           счёт готов к оплате
    шаг 11           оплачено, инициатор подтверждает получение
    шаг 12           получено, бухгалтерия ждёт оригиналы закрывающих документов

Чего в старых заявках не было, проставляется так, чтобы заявка оставалась
открываемой и доходила до конца: способ оплаты — счёт (у первой версии весь
маршрут был про счёт), тип объекта — услуга (этапа учёта имущества не было),
тип заявки — регулярный платёж, если её создал календарь или тип оплаты был
«фиксированный»/«ежемесячный», иначе новый закуп.

Заявка получает отметку `legacy_step` — шаг, на котором её застал перенос. Тем,
что уже были отправлены на согласование (шаг 2 и дальше), новые требования к
полноте не предъявляются, а дозаполнение пустых прежде полей не считается сменой
существенных условий (workflow.accepted_before): иначе утверждённая заявка ушла
бы на согласование заново из-за новой формы.

Отклонённым и отменённым заявкам подзадача не открывается: закрытая заявка
никого не ждёт и на досках стоять в «Новых» не должна. Вложения первой версии
переводятся с номера шага на этап — файл пройденного этапа снять нельзя.

Каждая заявка переносится под своим SAVEPOINT: строка неожиданной формы не
оставляет непереведёнными остальные; сколько не перенесено, показывает /ping.

Заодно шифруются номера карт, которые первая версия хранила открытым текстом, и
перешифровываются своим ключом те, что были записаны производным (cards.py).
"""

import logging

from . import cards, queries, workflow
from .sqlutil import NOW_SQL

SYSTEM = {'id': None, 'name': 'iCore'}


_PENDING_SQL = ("SELECT r.id FROM payment_requests r WHERE NOT EXISTS "
                "(SELECT 1 FROM payment_subtasks s WHERE s.request_id = r.id) ORDER BY r.id")


def pending(cursor):
    """Заявки первой версии, которые перенос не одолел: у них нет ни одной подзадачи,
    и раздел с ними ничего сделать не даст. Администратору об этом говорит /ping."""
    cursor.execute(_PENDING_SQL)
    return [row[0] for row in cursor.fetchall()]


def migrate(cursor):
    encrypted = _encrypt_cards(cursor)
    rotated = rotate_cards(cursor)
    moved, failed = 0, []
    for request_id in pending(cursor):
        # Свой SAVEPOINT на заявку: строка неожиданной формы не должна оставить
        # непереведёнными все остальные. Сбойную подберёт следующий старт.
        cursor.execute('SAVEPOINT payments_legacy_one')
        try:
            _migrate_one(cursor, request_id)
            cursor.execute('RELEASE SAVEPOINT payments_legacy_one')
            moved += 1
        except Exception:  # noqa: BLE001
            cursor.execute('ROLLBACK TO SAVEPOINT payments_legacy_one')
            failed.append(request_id)
            logging.exception('Оплата счетов: заявка №%s первой версии не переведена', request_id)
    if moved or encrypted or rotated or failed:
        logging.info('Оплата счетов: на этапы переведено заявок — %s (не удалось: %s), зашифровано номеров '
                     'карт — %s, перешифровано своим ключом — %s', moved, failed or 'нет', encrypted, rotated)
    return moved


def rotate_cards(cursor):
    """Перешифровывает номера карт главным ключом. Нужно один раз, после того как
    завели свой `PAYMENTS_CARD_KEY`: до него номера шифровались ключом, выведенным
    из `JWT_SECRET` (см. cards.py). Возвращает число перешифрованных номеров."""
    if not (cards.key_ready() and cards.has_own_key()):
        return 0
    count = 0
    for table in ('payment_requests', 'payment_cards'):
        cursor.execute("SELECT id, card_number_enc FROM %s WHERE card_number_enc IS NOT NULL" % table)
        for row_id, token in cursor.fetchall():
            fresh = cards.rotated(token)
            if fresh:
                cursor.execute("UPDATE %s SET card_number_enc = %%s WHERE id = %%s" % table, (fresh, row_id))
                count += 1
    return count


def _encrypt_cards(cursor):
    cursor.execute(
        "SELECT id, card_number FROM payment_requests "
        "WHERE card_number IS NOT NULL AND card_number <> '' AND card_number_enc IS NULL")
    rows = cursor.fetchall()
    if not rows:
        return 0
    if not cards.key_ready():
        # Сервер без JWT_SECRET (разовый скрипт, тест): шифровать нечем. Номера
        # подождут следующего старта — раздел их всё равно наружу не отдаёт.
        logging.warning('Оплата счетов: нет ключа для номеров карт — %s номеров остались открытыми', len(rows))
        return 0
    for request_id, number in rows:
        digits = cards.digits(number)
        cursor.execute(
            "UPDATE payment_requests SET card_number_enc = %s, card_last4 = %s, card_number = NULL WHERE id = %s",
            (cards.encrypt(digits), cards.last4(digits) or None, request_id))
    return len(rows)


def _migrate_one(cursor, request_id):
    cursor.execute(
        """
        SELECT current_step, status, payment_type, fixed_template_id, initiator_id, initiator_name,
               manager_id, manager_name, amount, created_at, request_kind, payment_method, object_type
          FROM payment_requests WHERE id = %s
        """,
        (request_id,))
    (step, status, payment_type, template_id, initiator_id, initiator_name, manager_id, manager_name,
     amount, created_at, request_kind, payment_method, object_type) = cursor.fetchone()
    step = int(step or 1)

    cursor.execute(
        "SELECT step_no, state, assignee_id, assignee_name, done_at, done_by, done_by_name, comment "
        "FROM payment_request_steps WHERE request_id = %s", (request_id,))
    old = {row[0]: {'state': row[1], 'assignee_id': row[2], 'assignee_name': row[3], 'done_at': row[4],
                    'done_by': row[5], 'done_by_name': row[6], 'comment': row[7]} for row in cursor.fetchall()}

    request_kind = request_kind or (workflow.KIND_REGULAR if template_id or payment_type in ('fixed', 'monthly')
                                    else workflow.KIND_PURCHASE)
    payment_method = payment_method or workflow.METHOD_INVOICE
    object_type = object_type or workflow.OBJECT_SERVICE
    cursor.execute(
        "UPDATE payment_requests SET request_kind = %s, payment_method = %s, object_type = %s, "
        "legacy_step = %s WHERE id = %s",
        (request_kind, payment_method, object_type, min(step, 12), request_id))

    manager_step = (old.get(2) or {}).get('state') != 'skipped'
    manager = {'id': manager_id, 'name': manager_name} if manager_id else None
    route = workflow.build_route(
        {'payment_method': payment_method, 'object_type': object_type},
        initiator={'id': initiator_id, 'name': initiator_name}, manager=manager, manager_step=manager_step)
    queries.ensure_initiation(cursor, request_id, {'id': initiator_id, 'name': initiator_name})
    queries.write_route(cursor, request_id, route)
    # Вложения первой версии привязаны к номеру шага — переводим на этап: файл
    # пройденного этапа тогда не снять, как и у заявок, заведённых по ТЗ.
    cursor.execute(
        "UPDATE payment_attachments SET subtask_kind = CASE "
        "WHEN step_no IN (1, 4, 6, 7) THEN %s WHEN step_no = 2 THEN %s WHEN step_no = 3 THEN %s "
        "WHEN step_no IN (5, 8, 9, 10) THEN %s WHEN step_no = 11 THEN %s WHEN step_no = 12 THEN %s END "
        "WHERE request_id = %s AND subtask_kind IS NULL AND step_no BETWEEN 1 AND 12",
        (workflow.KIND_INITIATION, workflow.KIND_MANAGER, workflow.KIND_APPROVAL, workflow.KIND_INVOICE,
         workflow.KIND_RECEIVING, workflow.KIND_CLOSING, request_id))

    def finish(kind, old_step, outcome):
        source = old.get(old_step) or {}
        cursor.execute(
            "UPDATE payment_subtasks SET state = 'done', outcome = %s, done_at = COALESCE(%s, " + NOW_SQL + "), "
            "done_by = %s, done_by_name = %s, comment = %s "
            "WHERE request_id = %s AND kind = %s AND state <> 'skipped'",
            (outcome, source.get('done_at'), source.get('done_by'), source.get('done_by_name'),
             source.get('comment'), request_id, kind))

    def done_initiation():
        cursor.execute(
            "UPDATE payment_subtasks SET state = 'done', outcome = 'submitted', "
            "done_at = COALESCE(%s, " + NOW_SQL + "), done_by = %s, done_by_name = %s "
            "WHERE request_id = %s AND kind = %s",
            ((old.get(1) or {}).get('done_at'), initiator_id, initiator_name, request_id,
             workflow.KIND_INITIATION))

    # Какие подзадачи уже позади и какая открыта — по номеру старого шага.
    passed, current, current_status = [], workflow.KIND_INITIATION, 'draft'
    ask_invoice = False
    closed = status == 'done'
    if closed:
        step = 13
    if step >= 2:
        current, current_status = workflow.KIND_MANAGER, 'new'
    if step >= 3:
        passed.append((workflow.KIND_MANAGER, 2, 'approved'))
        current, current_status = workflow.KIND_APPROVAL, 'new'
    if step >= 4:
        passed.append((workflow.KIND_APPROVAL, 3, 'approved'))
        current, current_status = workflow.KIND_INVOICE, 'new'
        ask_invoice = step <= 7
    if step >= 8:
        current_status = 'checking'
    if step >= 10:
        current_status = 'ready'
    if step >= 11:
        passed.append((workflow.KIND_INVOICE, 10, 'paid'))
        current, current_status = workflow.KIND_RECEIVING, 'new'
    if step >= 12:
        passed.append((workflow.KIND_RECEIVING, 11, 'received'))
        current, current_status = workflow.KIND_CLOSING, 'awaiting'
    if closed:
        passed.append((workflow.KIND_CLOSING, 12, 'closed'))
        current = None

    if step >= 2:
        done_initiation()
    for kind, old_step, outcome in passed:
        finish(kind, old_step, outcome)

    # Руководителя в маршруте могло не быть (этап пропущен) — тогда «текущей»
    # становится следующая непропущенная подзадача.
    if current == workflow.KIND_MANAGER:
        cursor.execute("SELECT state FROM payment_subtasks WHERE request_id = %s AND kind = %s",
                       (request_id, workflow.KIND_MANAGER))
        row = cursor.fetchone()
        if not row or row[0] == 'skipped':
            current = workflow.KIND_APPROVAL

    docs_status = workflow.DOCS_NONE
    if closed:
        docs_status = workflow.DOCS_CLOSED
    elif step >= 12:
        docs_status = workflow.DOCS_SCAN
    approved_step = old.get(3) or {}
    cursor.execute(
        "UPDATE payment_requests SET closing_docs_status = %s, "
        "submitted_at = CASE WHEN %s THEN COALESCE(%s, created_at) ELSE NULL END, "
        "approved_at = %s, approved_by = %s, approved_by_name = %s, amount_approved = %s WHERE id = %s",
        (docs_status, step >= 2, (old.get(1) or {}).get('done_at'),
         approved_step.get('done_at') if step >= 4 else None,
         approved_step.get('done_by') if step >= 4 else None,
         approved_step.get('done_by_name') if step >= 4 else None,
         amount if step >= 4 else None, request_id))
    if step >= 2:
        refreshed = queries.read_request(cursor, request_id)
        queries.touch_snapshot(cursor, request_id, workflow.essentials(refreshed))

    if current is None:
        cursor.execute(
            "UPDATE payment_requests SET stage = 'closed', current_subtask_id = NULL, current_role_code = NULL, "
            "current_assignee_id = NULL, current_assignee_name = NULL WHERE id = %s", (request_id,))
    elif status == 'active' and current != workflow.KIND_INITIATION:
        opened = queries.open_subtask(cursor, request_id, current, status=current_status)
        if ask_invoice:
            text = workflow.CLARIFY_BY_CODE['invoice_missing']['ask']
            queries.wait_subtask(cursor, opened['id'], reason='invoice_missing', comment=text, actor=SYSTEM,
                                 status_before='new')
            queries.reopen_initiation(cursor, request_id, status='clarification', reason='invoice_missing',
                                      comment=text, actor=SYSTEM, asked_by=workflow.KIND_INVOICE)
    if status in ('rejected', 'cancelled'):
        # Закрытая заявка никого не ждёт: подзадачу ей не открываем (иначе она встала бы
        # в «Новые» на доске), исполнителя у неё нет. Этап — тот, на котором её
        # остановили; отклонённое согласование так и записано — «отклонено».
        if status == 'rejected' and current in workflow.APPROVAL_KINDS:
            finish(current, step, 'rejected')
        cursor.execute(
            "UPDATE payment_requests r SET stage = s.stage, current_subtask_id = s.id, current_role_code = NULL, "
            "current_assignee_id = NULL, current_assignee_name = NULL "
            "FROM payment_subtasks s WHERE s.request_id = r.id AND s.kind = %s AND r.id = %s",
            (current, request_id))
    queries.log_event(cursor, request_id, 'migrated', SYSTEM,
                      comment='Заявка переведена на процесс «Закуп и оплата»: этапы вместо 12 шагов',
                      payload={'from_step': min(step, 12), 'status': status})
