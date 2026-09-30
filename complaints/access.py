# -*- coding: utf-8 -*-
"""Права раздела «Жалобы». Чистая логика: ни базы, ни Flask.

Три круга людей, и у каждого своё дело (ТЗ задачи #297):

    кто принимает жалобу   оператор СЗоВ — «обращение оформляется оператором
                           через iCore»; видит только свои жалобы. Работает с
                           ними в «Обращениях», в раздел «Жалобы» не ходит
    кто её разбирает       супервайзер и глава отдела, чьего сотрудника
                           коснулась жалоба; определяют сотрудника, фиксируют
                           итог и проведённую работу
    кто смотрит целиком    глобальный админ — всё, включая аналитику и выгрузку

Жалобы — самое чувствительное, что есть в портале о сотруднике, поэтому круг
уже, чем у «Обращений». Там видно всё всем, кто в разделе (чтобы не заводили
дубли), здесь — ровно по делу:

* оператор видит только то, что завёл сам. Иначе он читал бы жалобы на
  соседа по смене;
* **сотрудник, на которого поступила жалоба, её не видит никогда** — даже если
  он супервайзер или глава (жалоба бывает и на них). Исключение одно —
  глобальный админ: выше него жалобу разбирать некому;
* супервайзер видит жалобы на людей своего отдела (их он и разбирает) и
  жалобы, заведённые операторами его групп (за приёмом он тоже отвечает);
* глава — то же по всем своим отделам.

Две формы одного правила — can_view здесь и queries.visibility_sql для
списка — сверяются тестом (tests/test_complaints_access.py), как у обращений.

Роли и «глобальный админ» — те же функции, что у «Обращений» (crm/access.py):
назначение главой отдела заменяет базовую роль admin, и это правило в портале
одно.
"""

from crm import access as crm_access

from . import catalog

normalize_role = crm_access.normalize_role
is_global_admin = crm_access.is_global_admin
is_department_head = crm_access.is_department_head
is_supervisor = crm_access.is_supervisor

# Кто принимает жалобы: тот же отдел, что заводит обращения, — СЗоВ. Жалобы
# приходят туда же, куда звонит водитель.
INTAKE_DEPARTMENT_CODE = 'szov'

SCOPE_ALL = 'all'
SCOPE_SCOPED = 'scoped'
SCOPE_OWN = 'own'


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _own_code(ctx):
    return str(ctx.get('department_code') or '').strip().lower()


def _headed_ids(ctx):
    return {int(x) for x in (ctx.get('headed_department_ids') or [])}


def can_create(ctx):
    """Заводить жалобы: весь отдел СЗоВ (роль значения не имеет — жалобу
    принимает тот, кому позвонил водитель), его глава и глобальный админ."""
    if is_global_admin(ctx):
        return True
    if INTAKE_DEPARTMENT_CODE in _codes(ctx.get('headed_department_codes')):
        return True
    return _own_code(ctx) == INTAKE_DEPARTMENT_CODE


def is_handler(ctx):
    """Разбирающий: СВ или глава отдела, чьих людей может коснуться жалоба."""
    if _codes(ctx.get('headed_department_codes')) & set(catalog.HANDLER_DEPARTMENT_CODES):
        return True
    return is_supervisor(ctx) and _own_code(ctx) in catalog.HANDLER_DEPARTMENT_CODES


def can_open_section(ctx):
    """Пускать ли в РАЗДЕЛ «Жалобы» — разбор, работа с сотрудником, аналитика.

    Оператор сюда не ходит (решение владельца 29.09.2026): жалобу он заводит
    в «Обращениях» выбором направления и там же видит ответ для водителя и
    вопрос группы. Раздел — для тех, кто разбирает, и для админа.
    """
    return is_global_admin(ctx) or is_handler(ctx)


def can_use(ctx):
    """Пускать ли к API жалоб вообще. Шире раздела: оператор СЗоВ работает со
    своими жалобами из «Обращений» теми же запросами. Проверяется на каждом
    роуте; что именно человеку видно и можно — решают правила ниже."""
    return can_create(ctx) or can_open_section(ctx)


def requires_sensitive_qr(ctx):
    """QR-подтверждение сессии — тем же ключом и по тем же правилам, что у
    «Обращений»: в жалобе ФИО и телефон живого водителя, а рядовой сотрудник
    открывает такие разделы только с ведома старшего."""
    return crm_access.requires_sensitive_qr(ctx)


def visibility_scope(ctx):
    if is_global_admin(ctx):
        return SCOPE_ALL
    if is_department_head(ctx) or is_supervisor(ctx):
        return SCOPE_SCOPED
    return SCOPE_OWN


def _me(ctx):
    return int(ctx.get('user_id') or 0)


def _same(a, b):
    return a is not None and b is not None and int(a) == int(b)


def can_view(ctx, complaint):
    """Видит ли пользователь конкретную жалобу (карточка по прямой ссылке)."""
    if is_global_admin(ctx):
        return True
    me = _me(ctx)
    # Тот, на кого жалуются, свою жалобу не видит — ни как автор, ни как
    # руководитель. Первым правилом, чтобы его не перебило ни одно следующее.
    if _same(complaint.get('employee_id'), me):
        return False
    if _same(complaint.get('created_by'), me):
        return True
    if _same(complaint.get('responsible_id'), me):
        return True
    headed = _headed_ids(ctx)
    if headed:
        for key in ('target_department_id', 'creator_department_id'):
            value = complaint.get(key)
            if value is not None and int(value) in headed:
                return True
    if is_supervisor(ctx):
        if _same(complaint.get('target_department_id'), ctx.get('department_id')):
            return True
        groups = {int(x) for x in (ctx.get('group_ids') or [])}
        creator_groups = {int(x) for x in (complaint.get('creator_group_ids') or [])}
        if groups & creator_groups:
            return True
    return False


def can_handle(ctx, complaint):
    """Разбирать жалобу: определить сотрудника, зафиксировать итог.

    Разбирает отдел того, на кого жалуются. Жалобу на ОП, заведённую
    оператором СЗоВ, супервайзер СЗоВ видит (он отвечает за приём), но разбирать
    её — дело ОП. У жалоб без отдела (аренда, парк, Яндекс) разбирающий —
    руководство того, кто жалобу принял.

    Оператор не разбирает никогда: итог проверки ставит тот, кто проверял.
    """
    if not can_view(ctx, complaint):
        return False
    if is_global_admin(ctx):
        return True
    if _same(complaint.get('responsible_id'), _me(ctx)):
        return True
    target_department = complaint.get('target_department_id')
    headed = _headed_ids(ctx)
    if target_department is not None:
        if int(target_department) in headed:
            return True
        return is_supervisor(ctx) and _same(target_department, ctx.get('department_id'))
    creator_department = complaint.get('creator_department_id')
    if creator_department is not None and int(creator_department) in headed:
        return True
    if is_supervisor(ctx):
        groups = {int(x) for x in (ctx.get('group_ids') or [])}
        creator_groups = {int(x) for x in (complaint.get('creator_group_ids') or [])}
        return bool(groups & creator_groups)
    return False


def can_review(ctx, complaint):
    """Решить по жалобе на проверке (Яндекс): «Отправить в группу» или
    «Решено». Решает тот, кто её разбирает (can_handle: для жалоб без отдела
    это супервайзер группы оператора и глава его отдела), и только пока она
    ждёт проверки."""
    return (complaint.get('review_state') == catalog.REVIEW_PENDING
            and can_handle(ctx, complaint))


def can_set_employee(ctx, complaint):
    """Проставить или поправить сотрудника — только у целей, где он бывает."""
    spec = catalog.target(complaint.get('target')) or {}
    return bool(spec.get('employee')) and can_handle(ctx, complaint)


def can_record_work(ctx, complaint):
    """Записать работу с сотрудником. Сотрудник может быть и не определён —
    тогда записью объясняют, почему работать не с кем (catalog.UNASSIGNED_ACTIONS,
    состав проверяет service.record_work)."""
    return can_set_employee(ctx, complaint)


def can_see_internal(ctx, complaint):
    """Внутреннее обсуждение группы — только разбирающим. «Такие сообщения не
    должны автоматически поступать оператору в iCore, потому что часть
    информации является внутренней»."""
    return can_handle(ctx, complaint)


def can_write_to_group(ctx, complaint):
    """Написать в группу по жалобе: ответить на вопрос или дополнить.

    Только автор — «оператор связывается с водителем, получает дополнительные
    данные и отвечает в рамках этого же обращения». Разбирающий из iCORE в
    группу не пишет: его сообщение уходило бы подписью «Ответ оператора»,
    закрывало бы чужой вопрос и попадало в переписку оператора, а внутреннее
    обсуждение по ТЗ идёт в самой группе.

    Закрытая жалоба ответ на ОТКРЫТЫЙ вопрос не отрезает: итог проверки группа
    могла поставить раньше, чем оператор ответил.
    """
    if not complaint.get('tg_message_id'):
        return False
    if complaint.get('status') == 'closed' and not complaint.get('question_open_at'):
        return False
    return _same(complaint.get('created_by'), _me(ctx)) and can_view(ctx, complaint)


def can_view_analytics(ctx):
    """Аналитика и выгрузка — тем, кто видит больше своего: админу, главам и
    супервайзерам. Каждый считает по своему периметру (visibility_sql)."""
    return is_global_admin(ctx) or is_department_head(ctx) or is_supervisor(ctx)


def can_manage_settings(ctx):
    """Выбрать Telegram-группу жалоб. Глобальный админ и руководство СЗоВ —
    те же люди, что настраивают группы «Обращений» (crm can_manage_queues)."""
    if is_global_admin(ctx):
        return True
    if INTAKE_DEPARTMENT_CODE in _codes(ctx.get('headed_department_codes')):
        return True
    return is_supervisor(ctx) and _own_code(ctx) == INTAKE_DEPARTMENT_CODE


def can_delete(ctx):
    return is_global_admin(ctx)


def capabilities(ctx):
    """Сводка для интерфейса: кнопки рисуются по ней, а не по роли."""
    return {
        'scope': visibility_scope(ctx),
        'can_open': can_open_section(ctx),
        'can_use': can_use(ctx),
        'can_create': can_create(ctx),
        'is_handler': is_handler(ctx) or is_global_admin(ctx),
        'can_view_analytics': can_view_analytics(ctx),
        'can_manage_settings': can_manage_settings(ctx),
        'can_delete': can_delete(ctx),
        'requires_qr': requires_sensitive_qr(ctx),
    }
