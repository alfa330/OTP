"""Кто внутри подразделения выполнил задачу — видно не всем. Чистые функции.

ТЗ «Закуп и оплата», п. 9: «Исполнителем по этапу оплаты/пополнения назначается
подразделение, а не конкретный сотрудник. Инициатор не должен знать, кто именно
внутри бухгалтерии или финансового отдела выполняет платеж».

Поэтому тому, кто сам не из этих подразделений и не администратор раздела
(инициатору, его руководителю, утверждающему), в карточке заявки вместо фамилии
бухгалтера или сотрудника финансового отдела показывается название
подразделения: «Счёт оплачен — Бухгалтерия».

Что остаётся как есть:

* в базе автор каждого действия записан поимённо — журнал (п. 18) не теряет, кто
  что сделал; имена видят сами подразделения и администратор раздела;
* согласующие — люди заявки, их знают все её участники («текущего согласующего»
  и «согласовавшего» карточка обязана показывать, пп. 7 и 9);
* действия самого инициатора и системы подписаны своими именами.

Кому показывать имена, решает access.sees_department_people; здесь — только
замена в уже собранной карточке.
"""

from . import workflow

DEPARTMENT_ROLES = (workflow.ROLE_ACCOUNTING, workflow.ROLE_FINANCE)
# Подзадача подразделения → роль-исполнитель: оплата счёта и закрывающие документы
# — бухгалтерия, пополнение карты — финансовый отдел.
DEPARTMENT_KINDS = {item['kind']: item['role'] for item in workflow.SUBTASKS if item['role'] in DEPARTMENT_ROLES}

# События, которые по смыслу принадлежат подразделению, даже когда вид подзадачи
# в них не записан: статус закрывающих документов, возврат средств, закрытие заявки.
_ACCOUNTING_EVENTS = ('docs_status', 'refund', 'closed')
# События инициатора: их автор — он сам либо администратор за него, но не подразделение.
_INITIATOR_EVENTS = ('created', 'generated', 'submitted', 'clarified', 'edited', 'receipt_provided', 'received',
                     'cancelled', 'migrated')


def _label(role):
    return workflow.ROLE_LABELS[role]


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def department_of(user_id, role_members, known=()):
    """Подразделение, в котором состоит человек, либо None. `known` — люди заявки
    (инициатор, руководитель, согласующие): их имена не скрываются."""
    user_id = _int(user_id)
    if user_id is None or user_id in known:
        return None
    for role in DEPARTMENT_ROLES:
        if user_id in {_int(x) for x in (role_members or {}).get(role) or ()}:
            return role
    return None


def hide_in_row(row):
    """Строка реестра или рабочего стола: кто запросил уточнение — подразделение, не человек."""
    role = DEPARTMENT_KINDS.get((row or {}).get('clarify_from'))
    if role and row.get('clarify_by_name'):
        row['clarify_by_name'] = _label(role)
    if row and row.get('current_role_code') in DEPARTMENT_ROLES and row.get('current_assignee_id'):
        # Сотрудника задаче подразделения не назначают (routes.request_assignee); окажись он
        # в данных — фамилия всё равно не выйдет наружу.
        row['current_assignee_name'] = _label(row['current_role_code'])
        row['current_assignee_id'] = None
    return row


def hide_in_subtask(item, payment_method=None):
    """Подзадача: исполнитель и автор запроса — подразделение.

    `payment_method` — способ оплаты заявки: по нему понятно, какое подразделение
    запрашивало информацию у инициатора (счёт — бухгалтерия, карта — финансовый отдел).
    """
    role = DEPARTMENT_KINDS.get(item.get('kind'))
    if role:
        for key in ('assignee_name', 'done_by_name', 'clarify_by_name'):
            if item.get(key):
                item[key] = _label(role)
        for key in ('assignee_id', 'done_by', 'clarify_by'):
            item[key] = None
        return item
    # Подзадача инициатора помнит, кто последним вернул ему заявку, — и после его
    # ответа тоже. Возврат на доработку делает согласующий (его имя остаётся);
    # любая другая причина — это «Запросить информацию» этапа оплаты.
    if (item.get('kind') == workflow.KIND_INITIATION and item.get('clarify_by_name')
            and item.get('clarify_reason') and item.get('clarify_reason') != workflow.CLARIFY_REWORK):
        asked_by = workflow.ROLE_FINANCE if payment_method == workflow.METHOD_CARD else workflow.ROLE_ACCOUNTING
        item['clarify_by_name'] = _label(asked_by)
        item['clarify_by'] = None
    return item


def hide_department_people(full, role_members):
    """Карточка заявки (ответ `_full`) для того, кому имена подразделений не положены.
    Меняет переданный словарь и возвращает его."""
    request = full.get('request') or {}
    initiator_id = _int(request.get('initiator_id'))
    known = {x for x in (initiator_id, _int(request.get('manager_id'))) if x is not None}
    for item in full.get('subtasks') or []:
        if item.get('kind') in workflow.APPROVAL_KINDS:
            known |= {x for x in (_int(item.get('assignee_id')), _int(item.get('done_by'))) if x is not None}

    hide_in_row(request)
    for item in full.get('subtasks') or []:
        hide_in_subtask(item, request.get('payment_method'))

    for event in full.get('events') or []:
        actor_id = _int(event.get('actor_id'))
        if actor_id is None or actor_id == initiator_id or event.get('kind') in _INITIATOR_EVENTS:
            continue
        payload = event.get('payload') or {}
        role = DEPARTMENT_KINDS.get(payload.get('kind')) or DEPARTMENT_KINDS.get(payload.get('subtask_kind'))
        if not role and event.get('kind') in _ACCOUNTING_EVENTS:
            role = workflow.ROLE_ACCOUNTING
        role = role or department_of(actor_id, role_members, known)
        if not role:
            continue
        event['actor_name'] = _label(role)
        event['actor_id'] = None
        if event.get('kind') == 'reassigned' and payload.get('assignee_name'):
            event['payload'] = dict(payload, assignee_name=_label(role), assignee_id=None)

    for item in full.get('attachments') or []:
        uploader = _int(item.get('uploaded_by'))
        if uploader is None or uploader == initiator_id:
            continue
        role = DEPARTMENT_KINDS.get(item.get('subtask_kind')) or department_of(uploader, role_members, known)
        if role:
            item['uploaded_by_name'] = _label(role)
            item['uploaded_by'] = None
    return full
