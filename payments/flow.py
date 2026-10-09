"""Движок процесса «Закуп товара/услуги»: что происходит с заявкой после действия.

Правила — в workflow.py (чистая логика), запросы — в queries.py. Здесь их связка:
прочитать заявку, проверить, записать, открыть следующую подзадачу, разослать
уведомления. Flask здесь нет: движком пользуются и ручки (routes.py), и ночной
планировщик (регулярные платежи, напоминания о сроке).

ТЗ «Закуп и оплата», п. 2: «после прохождения соответствующего этапа iCore
автоматически создаёт нужную подзадачу внутри родительской заявки». Это делает
`advance`: подзадача открывается сама, человек её не заводит.

Три места, где заявка возвращается инициатору (и всегда одним способом —
подзадача исполнителя ждёт, подзадача инициатора открыта):

* согласующий нажал «Вернуть на доработку» (п. 7);
* бухгалтерия или финансовый отдел нажали «Запросить информацию» (п. 8);
* сам раздел: заявка дошла до оплаты счёта, а счёта нет либо нет действующего
  договора (правило «свыше 300 000 ₸ — только с договором»).

Когда инициатор отвечает, заявка возвращается ТОМУ, КТО СПРОСИЛ, — если
существенные условия (сумма, поставщик, способ оплаты, карта, компания…) не
менялись. Если менялись — согласование идёт заново: согласованное «250 000»
нельзя молча превратить в «900 000».

Про уведомления. Строка колокола пишется здесь же, в транзакции действия.
Telegram — дополнительный канал о НОВОЙ ЗАДАЧЕ (п. 17), поэтому в него уходит
только то, что требует действия; сообщения копятся в `outbox` и отправляются
вызывающим ПОСЛЕ коммита — иначе человек получил бы ссылку на заявку, которой в
базе ещё нет.
"""

import logging

from . import access, assets as assets_sql, directory, notices, notify, queries, workflow

SYSTEM = {'id': None, 'name': 'iCore'}


class FlowError(Exception):
    def __init__(self, message, code='PAYMENT_FLOW', status=400, missing=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.missing = list(missing or [])


def _fail_missing(missing, code='PAYMENT_INCOMPLETE'):
    raise FlowError('; '.join(missing), code=code, missing=missing)


# ─── Уведомления ─────────────────────────────────────────────────────────────

def open_to(user_ids):
    """Только те, кому раздел открыт: звать в закрытый раздел бессмысленно."""
    return {int(uid) for uid in (user_ids or ()) if uid and access.can_open_section({'user_id': uid})}


def subtask_user_ids(subtask, members):
    """Кого касается подзадача: исполнитель либо все участники его подразделения (п. 9)."""
    if not subtask:
        return set()
    if subtask.get('assignee_id'):
        return {int(subtask['assignee_id'])}
    return {int(uid) for uid in (members or {}).get(subtask.get('role_code')) or ()}


def _notify(cursor, request, event, user_ids, *, outbox, base_url, subtask_id=None, actionable=False,
            exclude=(), dedupe_key=None):
    """Кладёт уведомление в колокол; если оно требует действия — ещё и в Telegram.

    Задача, у которой нет ни одного получателя с доступом к разделу (исполнителю
    раздел не открыт, руководитель вне периметра), не теряется молча (п. 1 ТЗ):
    о ней узнают администраторы раздела — они вправе выполнить её за исполнителя.
    """
    skip = {int(x) for x in exclude if x}
    reachable = open_to(user_ids)
    ids = reachable - skip
    if actionable and not reachable:
        ids = set(access.SECTION_ADMIN_USER_IDS) - skip
        event = notify.unreachable(event)
    if not ids:
        return
    notices.push(cursor, user_ids=ids, request_id=request['id'], kind=event['kind'], title=event['title'],
                 body=event.get('body'), subtask_id=subtask_id, actionable=actionable,
                 tone=event.get('tone', 'default'), dedupe_key=dedupe_key)
    if actionable and outbox is not None:
        link = notify.request_link(base_url, request['id'])
        for recipient in queries.telegram_recipients(cursor, user_ids=ids):
            outbox.append((recipient['chat_id'], event['telegram'], notify.reply_markup(link)))


def announce_task(cursor, request_id, kind, *, outbox=None, base_url=None, exclude=()):
    """Сообщить исполнителям открытой подзадачи, что она ждёт их (после переназначения)."""
    request = queries.read_request(cursor, request_id)
    subtask = queries.read_subtask(cursor, request_id, kind)
    if not request or not subtask or subtask['state'] != 'open':
        return
    _notify(cursor, request, notify.task(request, subtask),
            subtask_user_ids(subtask, queries.role_member_ids(cursor)), outbox=outbox, base_url=base_url,
            subtask_id=subtask['id'], actionable=True, exclude=exclude)


def _notify_initiator(cursor, request, event, *, outbox, base_url, actor=None, **kwargs):
    _notify(cursor, request, event, [request.get('initiator_id')], outbox=outbox, base_url=base_url,
            exclude=[(actor or {}).get('id')], **kwargs)


# ─── Сборка контекста заявки ─────────────────────────────────────────────────

def _file_kinds(cursor, request_id):
    return [item['kind'] for item in queries.list_attachments(cursor, request_id)]


def submit_problems(cursor, request, *, invoice_required=True):
    """Чего заявке не хватает для отправки (workflow.missing_for_submit на данных базы)."""
    request_id = request['id']
    return workflow.missing_for_submit(
        request,
        items=queries.list_items(cursor, request_id),
        offers=queries.list_offers(cursor, request_id),
        attachments=queries.list_attachments(cursor, request_id),
        settings=directory.get_settings(cursor),
        card_number_ok=bool(request.get('has_card')),
        invoice_required=invoice_required,
    )


def approval_basis(cursor, request, on_date=None):
    """Матрица согласования для заявки на данных справочников (п. 6)."""
    template = None
    if request.get('request_kind') == workflow.KIND_REGULAR and request.get('fixed_template_id'):
        template = directory.read_template(cursor, request['fixed_template_id'])
    return workflow.resolve_approval(
        request=request,
        counterparty=directory.read_counterparty(cursor, request.get('counterparty_id')),
        template=template,
        limits=directory.list_limits(cursor, only_active=True),
        routes=directory.list_routes(cursor, only_active=True),
        on_date=on_date or queries.today_almaty(),
    )


def _approver_of(cursor, basis, initiator):
    """Утверждающий, названный матрицей, — если он вправе утвердить ЭТУ заявку.

    Не вправе: сам инициатор (свою заявку не согласуют) и уволенный сотрудник,
    оставшийся в лимите, маршруте или карточке поставщика. Тогда заявка уходит
    роли целиком — остальным утверждающим, а причина дописывается в основание.
    """
    user_id = basis.get('approver_user_id')
    if not user_id:
        return None
    person = queries.user_brief(cursor, user_id)
    if not person or person.get('fired'):
        reason = '%s больше не работает в компании' % (basis.get('approver_name') or 'Согласующий')
    elif initiator.get('id') and int(user_id) == int(initiator['id']):
        reason = 'Согласующий по матрице — сам инициатор, свою заявку не согласуют'
    else:
        return {'id': user_id, 'name': basis.get('approver_name')}
    basis['trace'].append({'ok': False, 'text': reason})
    basis.update({'approver_user_id': None, 'approver_name': None, 'source': 'role', 'limit_id': None,
                  'limit_number': None, 'basis': 'Любой из утверждающих'})
    return None


_ROLE_WITHOUT_PEOPLE = {
    workflow.ROLE_APPROVER: 'Утвердить заявку некому: в роли «Утвердитель» нет никого, кроме вас',
    workflow.ROLE_ACCOUNTING: 'Оплатить счёт некому: в роли «Бухгалтерия» нет участников',
    workflow.ROLE_FINANCE: 'Пополнить карту некому: в роли «Финансовый отдел» нет участников',
    workflow.ROLE_ASSET_KEEPER: 'Поставить имущество на учёт некому: не назначен ответственный за учёт имущества',
}


def route_gaps(route, members, initiator_id):
    """Подзадачи маршрута, которые некому выполнить: роль без участников.

    П. 1 ТЗ — «исключить потерю заявок»: заявка, ушедшая роли без людей, не
    появилась бы ни у кого на рабочем столе. Своя заявка утверждающему не
    считается (см. access.approves_own_request).
    """
    gaps = []
    for entry in route:
        role = entry['role_code']
        if entry.get('state') == 'skipped' or entry.get('assignee_id') or role not in _ROLE_WITHOUT_PEOPLE:
            continue
        people = {int(uid) for uid in (members or {}).get(role) or ()}
        if role == workflow.ROLE_APPROVER:
            people.discard(int(initiator_id or 0))
        if not people and _ROLE_WITHOUT_PEOPLE[role] not in gaps:
            gaps.append(_ROLE_WITHOUT_PEOPLE[role])
    return gaps


def _invoice_gate(cursor, request):
    """Можно ли отдавать счёт бухгалтерии: (код причины, текст) либо (None, None)."""
    if not workflow.invoice_ready(request, queries.list_attachments(cursor, request['id'])):
        return 'invoice_missing', workflow.CLARIFY_BY_CODE['invoice_missing']['ask']
    contract = directory.read_contract(cursor, request.get('contract_id'))
    ok, reason = workflow.contract_gate(
        request.get('amount'), contract, counterparty_id=request.get('counterparty_id'),
        on_date=request.get('invoice_date') or queries.today_almaty())
    if not ok:
        return 'contract_required', reason
    return None, None


# ─── Движение по маршруту ────────────────────────────────────────────────────

def advance(cursor, request_id, actor, *, outbox=None, base_url=None):
    """Открывает следующую подзадачу маршрута либо закрывает заявку.

    Возвращает вид открытой подзадачи, `'initiation'` — если заявка ушла
    инициатору за счётом или договором, либо None — заявка закрыта.
    """
    request = queries.read_request(cursor, request_id)
    subtasks = queries.list_subtasks(cursor, request_id)
    following = next((item for item in subtasks if item['state'] == 'pending'), None)
    if not following:
        _finish(cursor, request, subtasks, actor, outbox=outbox, base_url=base_url)
        return None
    kind = following['kind']
    members = queries.role_member_ids(cursor)

    if kind == workflow.KIND_INVOICE:
        reason, text = _invoice_gate(cursor, request)
        if reason:
            # Подзадача бухгалтерии создана (п. 5.1), но на доске она сразу в
            # «Требуется уточнение»: платить не по чему, пока нет счёта/договора.
            opened = queries.open_subtask(cursor, request_id, kind)
            queries.wait_subtask(cursor, opened['id'], reason=reason, comment=text, actor=SYSTEM,
                                 status_before=workflow.initial_status(kind))
            initiation = queries.reopen_initiation(cursor, request_id, status='clarification', reason=reason,
                                                   comment=text, actor=SYSTEM, asked_by=kind)
            queries.log_event(cursor, request_id, 'clarification', SYSTEM, comment=text,
                              payload={'kind': kind, 'reason': reason, 'system': True})
            request = queries.read_request(cursor, request_id)
            _notify_initiator(cursor, request, notify.clarification(
                request, reason_label=workflow.clarify_label(reason), comment=text,
                by_label=workflow.ROLE_LABELS[workflow.ROLE_ACCOUNTING]),
                outbox=outbox, base_url=base_url, subtask_id=initiation['id'], actionable=True)
            return workflow.KIND_INITIATION

    opened = queries.open_subtask(cursor, request_id, kind)
    request = queries.read_request(cursor, request_id)
    _notify(cursor, request, notify.task(request, opened), subtask_user_ids(opened, members),
            outbox=outbox, base_url=base_url, subtask_id=opened['id'], actionable=True,
            exclude=[(actor or {}).get('id')])
    if kind == workflow.KIND_CLOSING and request.get('closing_docs_status') == workflow.DOCS_NONE:
        # П. 17: инициатору — «необходимо приложить закрывающие документы».
        _notify(cursor, request, notify.docs_needed(request), [request.get('initiator_id')],
                outbox=outbox, base_url=base_url, subtask_id=None, actionable=True)
    return kind


def _finish(cursor, request, subtasks, actor, *, outbox=None, base_url=None):
    """Маршрут пройден: проверить условия закрытия (п. 15) и закрыть заявку."""
    attachments = queries.list_attachments(cursor, request['id'])
    blockers = workflow.closing_blockers(
        request, subtasks, assets=assets_sql.for_request(cursor, request['id']),
        has_handover_act=any(item['kind'] == 'handover_act' for item in attachments))
    if blockers:
        raise FlowError('Заявку нельзя закрыть — не выполнено: ' + '; '.join(blockers),
                        code='PAYMENT_CLOSE_BLOCKED', status=409, missing=blockers)
    queries.close_request(cursor, request['id'], 'done', actor)
    notices.clear_request(cursor, request['id'])
    closed = queries.read_request(cursor, request['id'])
    _notify_initiator(cursor, closed, notify.closed(closed), outbox=outbox, base_url=base_url, actor=actor)


# ─── Отправка заявки инициатором ─────────────────────────────────────────────

def submit(cursor, request_id, actor, *, comment=None, outbox=None, base_url=None, by_calendar=False):
    """Инициатор отправляет заявку: первый раз — на согласование, после возврата —
    тому, кто спрашивал, либо на согласование заново (если менялись существенные
    условия). Возвращает {'rerouted', 'next'}.

    `by_calendar` — заявку по регулярному платежу отправляет календарь: счёта у
    неё ещё нет, его запросят перед оплатой.
    """
    request = queries.read_request(cursor, request_id, lock=True)
    if not request:
        raise FlowError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
    if not access.with_initiator(request):
        raise FlowError('Заявка уже отправлена', code='PAYMENT_NOT_WITH_INITIATOR', status=409)
    missing = submit_problems(cursor, request, invoice_required=not by_calendar)
    if missing:
        _fail_missing(missing)

    initiation = queries.read_subtask(cursor, request_id, workflow.KIND_INITIATION)
    asked_by = (initiation or {}).get('outcome')
    first_time = not request.get('submitted_at')
    snapshot = request.get('submitted_snapshot')
    changed = [] if first_time else workflow.essentials_diff(snapshot, request)
    rerouted = first_time or workflow.essentials_changed(snapshot, request)
    initiator = {'id': request.get('initiator_id'), 'name': request.get('initiator_name')}
    manager = queries.resolve_manager(cursor, initiator['id']) if initiator['id'] else None

    if not rerouted and asked_by == workflow.KIND_INVOICE:
        # Отвечает на «нет счёта / нет договора»: проверяем, что теперь есть.
        reason, text = _invoice_gate(cursor, request)
        if reason:
            raise FlowError(text, code='PAYMENT_INCOMPLETE', missing=[text])

    if initiation:
        notices.clear_subtask(cursor, initiation['id'])
        queries.complete_subtask(cursor, initiation['id'], actor, outcome='submitted', comment=comment)

    if rerouted:
        basis = approval_basis(cursor, request)
        approver = _approver_of(cursor, basis, initiator)
        route = workflow.build_route(request, initiator=initiator, manager=manager,
                                     manager_step=basis['manager_step'], approver=approver)
        gaps = route_gaps(route, queries.role_member_ids(cursor), initiator['id'])
        if gaps:
            raise FlowError('%s. Обратитесь к администратору раздела' % '; '.join(gaps),
                            code='PAYMENTS_ROLES_NOT_CONFIGURED', status=409, missing=gaps)
        queries.write_route(cursor, request_id, route)
        queries.mark_submitted(cursor, request_id, snapshot=workflow.essentials(request), basis=basis,
                               manager=manager, set_manager=True, reset_approval=True)
        queries.log_event(
            cursor, request_id, 'submitted', actor, comment=comment,
            payload={'first': first_time, 'changed': changed, 'approver': basis.get('approver_name'),
                     'basis': basis.get('basis'), 'manager_step': basis.get('manager_step')})
        following = advance(cursor, request_id, actor, outbox=outbox, base_url=base_url)
        return {'rerouted': True, 'next': following, 'changed': changed}

    queries.mark_submitted(cursor, request_id, snapshot=workflow.essentials(request))
    queries.log_event(cursor, request_id, 'clarified', actor, comment=comment, payload={'kind': asked_by})
    asker = queries.read_subtask(cursor, request_id, asked_by) if asked_by else None
    if asker and asker['state'] == 'waiting':
        resumed = queries.resume_subtask(cursor, request_id, asked_by)
        members = queries.role_member_ids(cursor)
        refreshed = queries.read_request(cursor, request_id)
        _notify(cursor, refreshed, notify.task(refreshed, resumed), subtask_user_ids(resumed, members),
                outbox=outbox, base_url=base_url, subtask_id=resumed['id'], actionable=True,
                exclude=[(actor or {}).get('id')])
        return {'rerouted': False, 'next': asked_by, 'changed': []}
    following = advance(cursor, request_id, actor, outbox=outbox, base_url=base_url)
    return {'rerouted': False, 'next': following, 'changed': []}


# ─── Действия подзадач ───────────────────────────────────────────────────────

def load_for_action(cursor, request_id, kind):
    """Заявка (под блокировкой) и её подзадача, готовая к действию."""
    request = queries.read_request(cursor, request_id, lock=True)
    if not request:
        raise FlowError('Заявка не найдена', code='PAYMENT_REQUEST_NOT_FOUND', status=404)
    if request['status'] != 'active':
        raise FlowError('Заявка уже закрыта', code='PAYMENT_REQUEST_CLOSED', status=409)
    subtask = queries.read_subtask(cursor, request_id, kind)
    if not subtask or subtask['state'] != 'open':
        raise FlowError('Этап «%s» сейчас не ждёт действия — обновите страницу' % workflow.subtask_title(kind),
                        code='PAYMENT_SUBTASK_NOT_OPEN', status=409)
    return request, subtask


def move(cursor, request_id, kind, status, actor):
    """Сменить рабочий статус открытой подзадачи — перенос карточки по доске."""
    request, subtask = load_for_action(cursor, request_id, kind)
    if not workflow.can_move(kind, status):
        raise FlowError('В эту колонку карточку переносит действие, а не перестановка',
                        code='PAYMENT_STATUS_INVALID', status=409)
    if subtask.get('status') == status:
        return False
    queries.set_subtask_status(cursor, subtask['id'], status)
    queries.log_event(cursor, request_id, 'subtask_moved', actor,
                      payload={'kind': kind, 'from': subtask.get('status'), 'to': status})
    return True


def act(cursor, request_id, kind, action, actor, *, fields=None, new_file_kinds=(), assets=None,
        outbox=None, base_url=None):
    """Выполнить действие подзадачи. Файлы к этому моменту уже сохранены в заявке
    (в той же транзакции) — проверка смотрит на всё, что в ней лежит.

    Возвращает {'next': вид следующей подзадачи | None}.
    """
    fields = dict(fields or {})
    comment = str(fields.get('comment') or '').strip() or None
    request, subtask = load_for_action(cursor, request_id, kind)
    if action not in workflow.ACTIONS.get(kind, ()) or action == 'submit':
        raise FlowError('У этого этапа нет такого действия', code='PAYMENT_ACTION_UNKNOWN', status=400)

    # Обязательное вложение действия считается по файлам ЭТОЙ подзадачи:
    # платёжка из прошлой попытки оплаты не подтверждает новую.
    own_kinds = {item['kind'] for item in queries.list_attachments(cursor, request_id)
                 if item.get('subtask_kind') == kind} | set(new_file_kinds or ())
    missing = workflow.missing_for_action(kind, action, request=request, fields=fields,
                                          file_kinds=own_kinds, assets=assets or ())
    if missing:
        _fail_missing(missing, code='PAYMENT_ACTION_INCOMPLETE')

    done = dict(outbox=outbox, base_url=base_url)

    # ── Согласование ──
    if action == 'approve':
        notices.clear_subtask(cursor, subtask['id'])
        queries.complete_subtask(cursor, subtask['id'], actor, outcome='approved', comment=comment)
        queries.log_event(cursor, request_id, 'approved', actor, comment=comment, payload={'kind': kind})
        if kind == workflow.KIND_APPROVAL:
            queries.mark_approved(cursor, request_id, actor, workflow.to_decimal(request.get('amount')))
            _notify_initiator(cursor, request, notify.approved(request, by_name=(actor or {}).get('name')),
                              actor=actor, **done)
        return {'next': advance(cursor, request_id, actor, **done)}

    if action == 'return':
        return _ask_initiator(cursor, request, subtask, actor, reason=workflow.CLARIFY_REWORK,
                              comment=comment, status='rework', event_kind='returned', **done)

    if action == 'reject':
        notices.clear_subtask(cursor, subtask['id'])
        queries.complete_subtask(cursor, subtask['id'], actor, outcome='rejected', comment=comment)
        queries.close_request(cursor, request_id, 'rejected', actor, comment)
        notices.clear_request(cursor, request_id)
        _notify_initiator(cursor, request, notify.rejected(request, by_name=(actor or {}).get('name'),
                                                           comment=comment), actor=actor, **done)
        return {'next': None}

    # ── Оплата ──
    if action == 'request_info':
        return _ask_initiator(cursor, request, subtask, actor, reason=fields.get('reason'),
                              comment=comment, status='clarification', event_kind='clarification', **done)

    if action in ('pay', 'top_up'):
        paid_on = fields.get('paid_on')
        paid_amount = workflow.to_decimal(fields.get('paid_amount'))
        changes = queries.update_request_fields(cursor, request_id, {
            'paid_on': paid_on, 'paid_amount': paid_amount, 'paid_comment': comment})
        notices.clear_subtask(cursor, subtask['id'])
        # Оплачено — напоминания о сроке оплаты больше не о чем: гасим их, не дожидаясь,
        # пока каждый получатель откроет заявку.
        for reminder in ('due_soon', 'overdue'):
            notices.clear_kind(cursor, request_id, reminder)
        queries.complete_subtask(cursor, subtask['id'], actor, outcome='paid', comment=comment)
        queries.log_event(cursor, request_id, 'paid' if action == 'pay' else 'topped_up', actor,
                          comment=comment, payload={'kind': kind, 'paid_on': paid_on,
                                                    'paid_amount': paid_amount, 'changes': changes})
        refreshed = queries.read_request(cursor, request_id)
        event = notify.paid(refreshed) if action == 'pay' else notify.topped_up(refreshed)
        _notify_initiator(cursor, refreshed, event, actor=actor, **done)
        return {'next': advance(cursor, request_id, actor, **done)}

    # ── Чек и получение ──
    if action == 'provide':
        notices.clear_subtask(cursor, subtask['id'])
        queries.complete_subtask(cursor, subtask['id'], actor, outcome='provided', comment=comment)
        queries.log_event(cursor, request_id, 'receipt_provided', actor, comment=comment, payload={'kind': kind})
        _docs_received(cursor, request_id, actor)
        return {'next': advance(cursor, request_id, actor, **done)}

    if action == 'confirm':
        received = {'received_on': fields.get('received_on'),
                    'received_quantity': str(fields.get('received_quantity') or '').strip() or None}
        queries.update_request_fields(cursor, request_id, received)
        notices.clear_subtask(cursor, subtask['id'])
        queries.complete_subtask(cursor, subtask['id'], actor, outcome='received', comment=comment)
        queries.log_event(cursor, request_id, 'received', actor, comment=comment,
                          payload=dict(received, kind=kind))
        if set(workflow.CLOSING_DOC_KINDS) & set(_file_kinds(cursor, request_id)):
            _docs_received(cursor, request_id, actor)
        return {'next': advance(cursor, request_id, actor, **done)}

    # ── Имущество ──
    if action == 'register':
        numbers = [str(item.get('inventory_number') or '').strip().lower() for item in assets or []]
        if len(set(numbers)) != len(numbers):
            raise FlowError('Инвентарные номера в заявке повторяются', code='PAYMENT_ASSET_DUPLICATE', status=409)
        for item in assets or []:
            taken = assets_sql.inventory_taken(cursor, item.get('inventory_number'))
            if taken:
                raise FlowError('Инвентарный номер %s уже занят: «%s»' % (item.get('inventory_number'), taken['name']),
                                code='PAYMENT_ASSET_DUPLICATE', status=409)
        created = [assets_sql.create_asset(cursor, request_id=request_id, fields=item, actor=actor)
                   for item in assets or []]
        notices.clear_subtask(cursor, subtask['id'])
        queries.complete_subtask(cursor, subtask['id'], actor, outcome='registered', comment=comment)
        queries.log_event(cursor, request_id, 'asset_registered', actor, comment=comment,
                          payload={'kind': kind, 'asset_ids': created,
                                   'inventory_numbers': [item.get('inventory_number') for item in assets or []]})
        return {'next': advance(cursor, request_id, actor, **done)}

    # ── Закрывающие документы ──
    if action == 'docs_original':
        _set_docs(cursor, request, workflow.DOCS_ORIGINAL, actor, comment)
        return {'next': kind}

    if action == 'docs_close':
        _set_docs(cursor, request, workflow.DOCS_CLOSED, actor, comment)
        notices.clear_subtask(cursor, subtask['id'])
        notices.clear_kind(cursor, request_id, 'docs_needed')
        queries.complete_subtask(cursor, subtask['id'], actor, outcome='closed', comment=comment)
        return {'next': advance(cursor, request_id, actor, **done)}

    raise FlowError('Действие не поддерживается', code='PAYMENT_ACTION_UNKNOWN', status=400)


def _ask_initiator(cursor, request, subtask, actor, *, reason, comment, status, event_kind,
                   outbox=None, base_url=None):
    """Подзадача ждёт, заявка у инициатора: возврат на доработку или запрос информации."""
    request_id = request['id']
    notices.clear_subtask(cursor, subtask['id'])
    queries.wait_subtask(cursor, subtask['id'], reason=reason, comment=comment, actor=actor)
    initiation = queries.reopen_initiation(cursor, request_id, status=status, reason=reason, comment=comment,
                                           actor=actor, asked_by=subtask['kind'])
    queries.log_event(cursor, request_id, event_kind, actor, comment=comment,
                      payload={'kind': subtask['kind'], 'reason': reason})
    refreshed = queries.read_request(cursor, request_id)
    by_name = (actor or {}).get('name')
    if event_kind == 'returned':
        event = notify.returned(refreshed, by_name=by_name, comment=comment)
    else:
        event = notify.clarification(refreshed, reason_label=workflow.clarify_label(reason), comment=comment,
                                     by_label=subtask.get('role_label') or by_name)
    _notify_initiator(cursor, refreshed, event, outbox=outbox, base_url=base_url, actor=actor,
                      subtask_id=initiation['id'], actionable=True)
    return {'next': workflow.KIND_INITIATION}


def _set_docs(cursor, request, status, actor, comment=None):
    """Статус закрывающих документов (п. 14) с записью в историю. Назад статус не идёт."""
    current = request.get('closing_docs_status') or workflow.DOCS_NONE
    if workflow.CLOSING_DOC_ORDER[status] <= workflow.CLOSING_DOC_ORDER.get(current, 0):
        return False
    queries.set_closing_docs(cursor, request['id'], status)
    queries.log_event(cursor, request['id'], 'docs_status', actor, comment=comment,
                      payload={'from': current, 'to': status})
    return True


def _docs_received(cursor, request_id, actor):
    """В заявке появился скан закрывающего документа: «не получены» → «скан получен»."""
    request = queries.read_request(cursor, request_id)
    if _set_docs(cursor, request, workflow.DOCS_SCAN, actor):
        notices.clear_kind(cursor, request_id, 'docs_needed')
        return True
    return False


def files_added(cursor, request_id, kinds, actor):
    """К заявке приложили файлы вне действия подзадачи. Накладная, акт или чек
    после получения — это закрывающий документ: его статус сдвигается сам."""
    if not set(workflow.CLOSING_DOC_KINDS) & set(kinds or ()):
        return False
    receiving = queries.read_subtask(cursor, request_id, workflow.KIND_RECEIVING)
    # Документы считаются закрывающими с момента получения: счёт-фактура,
    # приложенная к заявке до оплаты, ещё ничего не закрывает.
    if not receiving or receiving['state'] != 'done':
        return False
    return _docs_received(cursor, request_id, actor)


def cancel(cursor, request_id, actor, comment=None, *, base_url=None):
    """Отмена заявки. Тот, у кого она была в работе, и инициатор узнают об этом из
    колокола: иначе карточка просто исчезла бы с доски исполнителя."""
    request = queries.read_request(cursor, request_id)
    holders = {request.get('initiator_id')}
    if request.get('current_assignee_id'):
        holders.add(request['current_assignee_id'])
    elif request.get('current_role_code'):
        holders |= set(queries.role_member_ids(cursor).get(request['current_role_code']) or ())
    queries.close_request(cursor, request_id, 'cancelled', actor, comment)
    notices.clear_request(cursor, request_id)
    _notify(cursor, request, notify.cancelled(request, comment=comment), holders, outbox=None,
            base_url=base_url, exclude=[(actor or {}).get('id')])


# ─── Сроки (п. 17) ───────────────────────────────────────────────────────────

def remind_deadlines(cursor, *, today=None, outbox=None, base_url=None):
    """Напоминания о приближении срока и о просрочке — раз на срок, не каждый день.

    Получают тот, у кого заявка сейчас (человек либо его подразделение), и
    инициатор. Возвращает число созданных уведомлений.
    """
    today = today or queries.today_almaty()
    soon_days = directory.get_settings(cursor)[workflow.SETTING_DUE_SOON_DAYS]
    members = queries.role_member_ids(cursor)
    created = 0
    for request, mark in queries.due_requests(cursor, today, soon_days):
        due_on = workflow._as_date(request.get('due_on'))
        holder = set()
        if request.get('current_assignee_id'):
            holder.add(int(request['current_assignee_id']))
        elif request.get('current_role_code'):
            holder |= {int(uid) for uid in members.get(request['current_role_code']) or ()}
        recipients = open_to(holder | {request.get('initiator_id')})
        if not recipients:
            continue
        if mark == 'overdue':
            event = notify.overdue(request, (today - due_on).days)
        else:
            event = notify.due_soon(request, (due_on - today).days)
        created += notices.push(
            cursor, user_ids=recipients, request_id=request['id'], kind=event['kind'], title=event['title'],
            body=event.get('body'), tone=event.get('tone', 'default'),
            dedupe_key='%s:%s:%s' % (event['kind'], request['id'], due_on.isoformat()))
    if created:
        logging.info('Оплата счетов: напоминаний о сроках — %s', created)
    return created
