# -*- coding: utf-8 -*-
"""Права раздела «Карточка клиента». Чистая логика: ни базы, ни Flask.

Раздел выдаётся отделу строкой настроек (client_card_settings): есть строка — отдел
в разделе, флаг enabled в ней — телефон открывает карточку операторам. Так руководитель
успевает завести причины и адресатов до того, как карточка появится на линии.

Кто есть кто (задача #335, п. 3 и 8):

    глобальный админ           всё по всем выданным отделам
    глава отдела, супервайзер  всё по своему отделу: настройки, причины, адресаты,
                               все обращения и запросы, отчёт — «видит всю картину»
    остальные сотрудники       карточка в телефоне; в портале — свои запросы и
    отдела                     ответы по ним, свои обращения

Назначение главой ЗАМЕНЯЕТ базовую роль, как во всём портале: админ, возглавляющий
отдел, — руководитель своего отдела, а не глобальный админ.

Контекст (queries.load_access_context):
    user_id, name, role, status, department_id, headed_ids, group_ids,
    departments — {department_id: {'enabled': bool, 'group_ids': [..]}} выданных отделов.
"""

_ADMIN_ROLES = ('super_admin', 'admin')
_SUPERVISOR_ROLES = ('sv', 'supervisor')
_INACTIVE_STATUSES = ('fired', 'dismissal')


def _role(ctx):
    return str(ctx.get('role') or '').strip().lower()


def is_working(ctx):
    return str(ctx.get('status') or 'working').strip().lower() not in _INACTIVE_STATUSES


def _headed(ctx):
    return {int(x) for x in (ctx.get('headed_ids') or ()) if x}


def _provided(ctx):
    """Отделы, которым раздел выдан."""
    return {int(key): value for key, value in (ctx.get('departments') or {}).items()}


def is_global_admin(ctx):
    role = _role(ctx)
    if role == 'super_admin':
        return True
    return role == 'admin' and not _headed(ctx)


def managed_department_ids(ctx):
    """Отделы, где человек руководит разделом: настройки, вся картина, отчёт."""
    if not is_working(ctx):
        return []
    provided = _provided(ctx)
    if is_global_admin(ctx):
        return sorted(provided)
    ids = _headed(ctx) & set(provided)
    own = ctx.get('department_id')
    if _role(ctx) in _SUPERVISOR_ROLES and own is not None and int(own) in provided:
        ids.add(int(own))
    return sorted(ids)


def can_manage(ctx, department_id):
    return department_id is not None and int(department_id) in managed_department_ids(ctx)


def card_department_id(ctx):
    """Отдел, в котором человек работает с карточкой сам, — его собственный, если раздел
    отделу выдан. Руководит он при этом может и другими (глава нескольких отделов)."""
    own = ctx.get('department_id')
    if own is None or not is_working(ctx):
        return None
    return int(own) if int(own) in _provided(ctx) else None


def phone_card_enabled(ctx):
    """Открывает ли телефон карточку этому человеку.

    Отделу карточка включена, и человек входит в выбранные группы (группы не
    выбраны — весь отдел). Руководителю отдела группы не указ: он проверяет
    карточку на себе, не числясь в группе линии.
    """
    department_id = card_department_id(ctx)
    if department_id is None:
        return False
    settings = _provided(ctx)[department_id]
    if not settings.get('enabled'):
        return False
    groups = {int(x) for x in (settings.get('group_ids') or ()) if x}
    if not groups or can_manage(ctx, department_id):
        return True
    return bool(groups & {int(x) for x in (ctx.get('group_ids') or ()) if x})


def can_open_section(ctx):
    """Пускать ли в раздел портала. Проверяется на КАЖДОМ роуте; тот же ответ портал
    получает флагом client_cards_access в профиле и по нему рисует пункт меню.

    Руководителю — всегда (ему настраивать раздел до включения). Сотруднику отдела —
    когда карточка ему включена: до этого у него нет ни обращений, ни запросов.
    """
    if not is_working(ctx):
        return False
    return bool(managed_department_ids(ctx)) or phone_card_enabled(ctx)


def section_department_ids(ctx):
    """Отделы, данные которых человек видит в разделе: где руководит и свой."""
    ids = set(managed_department_ids(ctx))
    own = card_department_id(ctx)
    if own is not None and phone_card_enabled(ctx):
        ids.add(own)
    return sorted(ids)


def sees_all(ctx, department_id):
    """Вся картина отдела (п. 8: «руководитель видит всю картину целиком»)."""
    return can_manage(ctx, department_id)


def can_view_request(ctx, request):
    """Запрос виден автору и руководителю его отдела."""
    if request.get('created_by') is not None and int(request['created_by']) == int(ctx['user_id']):
        return True
    return can_manage(ctx, request.get('department_id'))


def can_act_on_request(ctx, request):
    """Ответить в нить, закрыть, вернуть в работу — тот же круг, что видит запрос:
    руководитель подхватывает запрос оператора, который ушёл со смены."""
    return can_view_request(ctx, request)


def can_edit_card(ctx, card):
    """Править карточку: её оператор и руководитель отдела."""
    if int(card.get('operator_id') or 0) == int(ctx['user_id']):
        return True
    return can_manage(ctx, card.get('department_id'))


def can_view_card(ctx, card):
    """Открыть обращение. Оператору отдела — любое обращение своего отдела: историю
    водителя ведут все операторы линии, и по чужой записи он продолжает разговор."""
    if can_edit_card(ctx, card):
        return True
    own = card_department_id(ctx)
    return own is not None and int(card.get('department_id') or 0) == own and phone_card_enabled(ctx)


def can_unlink(ctx, link, department_id):
    """Снять привязку номера к аккаунту: тот, кто её поставил (ошибся ссылкой), и
    руководитель. Привязку, найденную самим Тез, руками не снимают — она вернётся
    со следующим звонком."""
    if link.get('origin') != 'manual':
        return False
    if link.get('created_by') is not None and int(link['created_by']) == int(ctx['user_id']):
        return True
    return can_manage(ctx, department_id)


def capabilities(ctx):
    """Сводка для экрана: кнопки рисуются по ней, а не по роли."""
    managed = managed_department_ids(ctx)
    return {
        'manage': bool(managed),
        'managed_department_ids': managed,
        'card': phone_card_enabled(ctx),
        'card_department_id': card_department_id(ctx),
        'global_admin': is_global_admin(ctx),
    }
