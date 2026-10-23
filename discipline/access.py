# -*- coding: utf-8 -*-
"""Права раздела «Дисциплина». Чистая логика: ни базы, ни Flask.

Круги доступа (ТЗ п. 3 и решения владельца 20.10.2026):

    супер-админ          все три отдела: «админ и выше» — выше админа только он;
    руководитель         глава СЗоВ, ОП или Теза (departments.head_user_id) —
                         свои отделы. Это «старший супервайзер / руководитель
                         отдела» из ТЗ: утверждает акты и меры, ставит и снимает
                         особый контроль, отменяет записи, настраивает
                         справочники и лестницу, выгружает отчёты;
    супервайзер          СВ этих отделов — ТОЛЬКО свои группы (владелец: «СВ
                         видит только свою группу»). Фиксирует нарушения,
                         прикладывает объяснительные и доказательства, проводит
                         тренинг; меры не назначает и записи не отменяет;
    оператор, стажёр     только свои записи: тип, дата, статус, мера (ТЗ п. 3),
                         и загрузка своей объяснительной;
    админ не-глава       не видит раздел вовсе — прямое слово владельца: «если
                         админ не руководитель этих трёх отделов, он не может
                         просматривать раздел».

«Своя группа» СВ — действующее членство в group_supervisor_memberships на
сегодня по Алматы (дату даёт Python: CURRENT_DATE базы — UTC, и с полуночи до
пяти утра границы членства сдвигались бы на день). Оператор «в группе» СВ —
по своему действующему членству. Вся история человека видна СВ его группы
(ТЗ п. 3: «полную историю любого оператора своей группы»); при переводе она
уходит вместе с человеком к новому СВ, а прежний её больше не видит — даже
записи, которые завёл сам: правило владельца — «СВ видит только свою группу».

Две формы правила видимости — can_view_record здесь и queries.visibility_sql
для списков — сверяются тестом, как у «Жалоб».

QR-подтверждения сессии раздел не спрашивает ни у кого. Сначала оно было у
оператора, как в «Обращениях»; владелец, 23.10.2026: «для операторов сделать
доступ без qr» — оператор открывает свои записи и загружает объяснительную
сразу, в том числе с телефона. Экран (App.jsx) раздел за общий QR-замок тоже не
прячет — тест сверяет обе стороны.
"""

from . import catalog

_SUPERVISOR_ROLES = ('sv', 'supervisor')
_OPERATOR_ROLES = ('operator', 'trainee')
_KNOWN_ROLES = ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee',
                'hr_manager', 'accounting_manager', 'marketing_manager')

SCOPE_ALL = 'all'
SCOPE_MANAGER = 'manager'
SCOPE_GROUPS = 'groups'
SCOPE_OWN = 'own'

# Выкладка по шагам. Владелец, 20.10.2026: «опубликовать на данный момент для
# суперадминов». Пока флаг стоит, раздел открыт только супер-админам: остальным
# его закрывает сервер (can_open_section — на каждом роуте и в колоколе), меню
# его не показывает (DISCIPLINE_SUPER_ADMIN_ONLY в App.jsx, тест сверяет оба
# флага), и ни колокол, ни Telegram никому, кроме супер-админов, не уходят
# (notify.push): пробная запись на живого оператора не должна дойти ни до него,
# ни до его СВ и главы. Открыть раздел по правилам ТЗ — False здесь и в App.jsx.
SUPER_ADMIN_ONLY = True

# Поимённо, пока стоит флаг: эти люди входят в раздел по его обычным правилам
# (оператор — только свои записи и своя объяснительная), и колокол им приходит
# (notify.push). Остальным раздел по-прежнему
# закрыт, их СВ и главе ничего не уходит. Владелец, 23.10.2026: «открой данный
# раздел для … как оператору» — 56, оператор СЗоВ. Зеркало —
# DISCIPLINE_ROLLOUT_USER_IDS в src/App.jsx (пункт меню), списки сверяет тест.
# Новый id — только по слову владельца; ФИО в код не пишем (репозиторий публичный).
ROLLOUT_USER_IDS = frozenset({56})


def normalize_role(role):
    value = str(role or '').strip().lower()
    if value == 'supervisor':
        return 'sv'
    if value in ('superadmin', 'super-admin', 'super admin'):
        return 'super_admin'
    return value if value in _KNOWN_ROLES else 'operator'


def _ids(values):
    result = set()
    for value in values or ():
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _codes(values):
    return {str(code).strip().lower() for code in (values or ()) if code}


def section_department_ids(ctx):
    """id отделов раздела, найденные по кодам: {'szov': 1, ...} → {1, ...}."""
    return _ids((ctx.get('section_departments') or {}).values())


def is_super_admin(ctx):
    return normalize_role(ctx.get('role')) == 'super_admin'


def managed_department_ids(ctx):
    """Отделы, где пользователь — руководитель раздела."""
    if is_super_admin(ctx):
        return section_department_ids(ctx)
    return _ids(ctx.get('headed_department_ids')) & section_department_ids(ctx)


def is_manager(ctx):
    return bool(managed_department_ids(ctx))


def _own_department_in_section(ctx):
    department_id = ctx.get('department_id')
    return department_id is not None and int(department_id) in section_department_ids(ctx)


def is_supervisor(ctx):
    return (normalize_role(ctx.get('role')) == 'sv' and _own_department_in_section(ctx)
            and not is_manager(ctx))


def is_operator(ctx):
    return (normalize_role(ctx.get('role')) in _OPERATOR_ROLES and _own_department_in_section(ctx)
            and not is_manager(ctx))


def in_rollout(ctx):
    """Человек из поимённого списка выкладки (ROLLOUT_USER_IDS)."""
    return _me(ctx) in ROLLOUT_USER_IDS


def can_open_section(ctx):
    """Пускать ли в раздел вообще. Проверяется на КАЖДОМ роуте: спрятанный пункт
    меню — не доступ, раздел открывается и прямым адресом ?view=discipline.
    Поимённый список снимает только флаг выкладки — дальше обычные правила."""
    if SUPER_ADMIN_ONLY and not is_super_admin(ctx) and not in_rollout(ctx):
        return False
    return is_manager(ctx) or is_supervisor(ctx) or is_operator(ctx)


def scope(ctx):
    if is_super_admin(ctx):
        return SCOPE_ALL
    if is_manager(ctx):
        return SCOPE_MANAGER
    if is_supervisor(ctx):
        return SCOPE_GROUPS
    if is_operator(ctx):
        return SCOPE_OWN
    return None


def _me(ctx):
    return int(ctx.get('user_id') or 0)


def _same(left, right):
    return left is not None and right is not None and int(left) == int(right)


def operator_in_scope(ctx, operator):
    """Вправе ли пользователь фиксировать нарушение этому человеку и видеть его
    историю. operator — {'id', 'role', 'department_id', 'group_ids'} на сегодня."""
    if not operator or normalize_role(operator.get('role')) not in _OPERATOR_ROLES:
        return False
    department_id = operator.get('department_id')
    if department_id is None or int(department_id) not in section_department_ids(ctx):
        return False
    if department_id is not None and int(department_id) in managed_department_ids(ctx):
        return True
    if is_supervisor(ctx):
        return bool(_ids(ctx.get('group_ids')) & _ids(operator.get('group_ids')))
    return False


def can_view_record(ctx, record):
    """Видит ли пользователь запись. record — {'operator_id', 'department_id' (отдел
    фиксации), 'created_by', 'operator_department_id', 'operator_group_ids'}."""
    if is_operator(ctx):
        return _same(record.get('operator_id'), _me(ctx))
    managed = managed_department_ids(ctx)
    if managed:
        for key in ('department_id', 'operator_department_id'):
            value = record.get(key)
            if value is not None and int(value) in managed:
                return True
        return False
    if is_supervisor(ctx):
        return bool(_ids(ctx.get('group_ids')) & _ids(record.get('operator_group_ids')))
    return False


def manages_record(ctx, record):
    """Руководитель этой записи: её отдел фиксации или нынешний отдел человека."""
    managed = managed_department_ids(ctx)
    for key in ('department_id', 'operator_department_id'):
        value = record.get(key)
        if value is not None and int(value) in managed:
            return True
    return False


def supervises_record(ctx, record):
    """СВ, который ведёт запись: человек сейчас в его группе."""
    if not is_supervisor(ctx):
        return False
    return bool(_ids(ctx.get('group_ids')) & _ids(record.get('operator_group_ids')))


def _open(record):
    return not record.get('cancelled_at') and not record.get('closed_at')


def can_edit_record(ctx, record):
    """Править поля записи. ТЗ п. 3: «Супервайзер не должен иметь возможности
    удалить или отредактировать запись, созданную другим супервайзером»."""
    if not _open(record):
        return False
    if manages_record(ctx, record):
        return True
    return supervises_record(ctx, record) and _same(record.get('created_by'), _me(ctx))


def can_cancel_record(ctx, record):
    """Отменить ошибочную запись — только руководитель и с обоснованием (п. 3)."""
    return _open(record) and manages_record(ctx, record)


def can_close_record(ctx, record, *, under_control=False):
    """Закрыть запись. На особом контроле — только руководитель: «запись требует
    утверждения руководителем — супервайзер не может закрыть её сам» (п. 10.4)."""
    if not _open(record):
        return False
    if manages_record(ctx, record):
        return True
    return not under_control and supervises_record(ctx, record)


def can_work_record(ctx, record):
    """Объяснительная, отказ, тренинг, доказательства: тот, кто ведёт запись."""
    return _open(record) and (manages_record(ctx, record) or supervises_record(ctx, record))


def can_upload_explanation(ctx, record):
    """Объяснительную загружает СВ (скан, фото) или сам оператор (п. 8.1)."""
    if not _open(record):
        return False
    if is_operator(ctx):
        return _same(record.get('operator_id'), _me(ctx))
    return can_work_record(ctx, record)


def measure_locked(record):
    """Утверждённое удержание не меняет никто: оно могло уйти в выгрузку своего
    месяца, и тихая правка разошлась бы с тем, что забрала бухгалтерия."""
    return record.get('measure_state') == 'approved' and (record.get('measure_finance') or 'none') != 'none'


def can_propose_measure(ctx, record, *, under_control=False):
    """СВ меры не назначает (п. 3), но при нарушении на особом контроле поле
    «Мера» обязательно уже при фиксации (п. 10.4) — тогда он её ПРЕДЛАГАЕТ, а
    утверждает руководитель. Утверждённую руководителем меру СВ не меняет;
    меру, применённую без утверждения (устное замечание), поправить может.
    Руководитель назначает меру любой записи."""
    if not _open(record) or measure_locked(record):
        return False
    if manages_record(ctx, record):
        return True
    approved_by_head = record.get('measure_state') == 'approved' and record.get('measure_approved_by')
    return under_control and supervises_record(ctx, record) and not approved_by_head


def can_approve_measure(ctx, record):
    return _open(record) and manages_record(ctx, record)


def can_see_internal(ctx, record):
    """Оператору — тип, дата, статус, мера (п. 3). Описание обстоятельств,
    доказательства, комментарий руководителя и журнал ему не показываются."""
    return not is_operator(ctx)


def can_manage_department(ctx, department_id):
    return department_id is not None and int(department_id) in managed_department_ids(ctx)


def capabilities(ctx):
    manager = is_manager(ctx)
    supervisor = is_supervisor(ctx)
    return {
        'scope': scope(ctx),
        'can_create': manager or supervisor,
        'can_manage': manager,
        'can_controls': manager,
        'can_settings': manager,
        'can_reports': manager,
        'can_audit': manager,
        'is_operator': is_operator(ctx),
    }


SECTION_DEPARTMENT_CODES = catalog.SECTION_DEPARTMENT_CODES
