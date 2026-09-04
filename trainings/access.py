# -*- coding: utf-8 -*-
"""Права раздела «Тренинги» (справочник корпоративных тем).

Отдельным файлом, а не внутри routes.py, по той же причине, что у crm и вики:
правило доступа читают и обработчик, и тест, и оно не должно быть размазано по
разбору запроса.

Кто что может:

    читать темы          админ, глава отдела, СВ, тренер, оператор
    создавать/править    админ, глава отдела, СВ  (решение владельца:
                         «И СВ может создавать темы»)
    архивировать         тот же круг, что правит

Границы отдела. Глава отдела и СВ работают только со своим отделом: тема без
отдела («общая для компании») им недоступна на запись — иначе один СВ раскатал
бы тему на весь портал. Глобальный админ (не глава отдела) и супер-админ —
без границы.

Тренер читает, но не правит. Он проводит тренинги по готовым темам; сам
справочник ведёт тот, кто отвечает за отдел. При этом ЧИТАТЬ раздел он обязан:
до этой правки /api/trainings отдавал ему 403, хотя интерфейс раздел рисовал.
"""

# Роли, которые видят раздел. Оператор видит только свои тренинги — это
# фильтруется в запросе, а не здесь.
READ_ROLES = ('operator', 'trainee', 'trainer', 'sv', 'admin', 'super_admin')

# Роли, которые ведут справочник тем.
MANAGE_ROLES = ('sv', 'admin', 'super_admin')


def normalize_role(role):
    role_norm = str(role or '').strip().lower()
    if role_norm == 'supervisor':
        return 'sv'
    if role_norm in ('superadmin', 'super-admin', 'super admin'):
        return 'super_admin'
    return role_norm


def can_read(role, headed_department_id=None):
    """Виден ли раздел вообще."""
    role_norm = normalize_role(role)
    if headed_department_id is not None:
        return True
    return role_norm in READ_ROLES


def can_manage_topics(role, headed_department_id=None):
    """Можно ли вести справочник тем."""
    role_norm = normalize_role(role)
    if headed_department_id is not None:
        return True
    return role_norm in MANAGE_ROLES


def can_subscribe_reports(role, headed_department_id=None):
    """Может ли пользователь подписаться на Telegram-сводки по тренингам.

    Круг тот же, что у остальных отчётов портала (отчёт по обратной связи,
    отчёт о сменах ставок): админ, супер-админ и глава активного отдела.
    СВ и тренер сюда не входят намеренно: сводка — это срез по отделу целиком,
    а не по своей группе, и рассылать её всем супервайзерам значило бы
    двадцать писем об одном и том же каждое утро.

    Правило живёт здесь, а не в обработчике, потому что его читают ТРИ места:
    ответ /api/training_topics (показывать ли кнопку), роут подписки и роут
    разовой отправки. Разойдясь, они дали бы кнопку, которая отвечает 403.
    """
    if headed_department_id is not None:
        return True
    return normalize_role(role) in ('admin', 'super_admin')


def is_unscoped(role, headed_department_id=None):
    """Работает ли пользователь без границы отдела.

    Супер-админ — всегда. Обычный админ — только если он НЕ назначен главой
    какого-то отдела: назначение главой заменяет базовую роль и вводит строгую
    границу отдела (та же семантика, что во всём портале).
    """
    role_norm = normalize_role(role)
    if role_norm == 'super_admin':
        return True
    if role_norm == 'admin':
        return headed_department_id is None
    return False


def writable_department_id(role, headed_department_id, own_department_id, requested_department_id):
    """Куда пользователю позволено записать тему.

    Возвращает (department_id, error) — error это (сообщение, код) или None.
    Для пользователя с границей отдела запрошенный отдел либо совпадает с его
    собственным, либо не указан (тогда подставляем его отдел). «Общая тема»
    (department_id = NULL) доступна только тем, кто работает без границы.
    """
    if is_unscoped(role, headed_department_id):
        return requested_department_id, None

    scope_id = headed_department_id if headed_department_id is not None else own_department_id
    if scope_id is None:
        return None, ("Ваша учётная запись не привязана к отделу — тему создавать некуда", 403)

    if requested_department_id is None:
        return scope_id, None

    if int(requested_department_id) != int(scope_id):
        return None, ("Тему можно создать только в своём отделе", 403)
    return scope_id, None


def readable_department_ids(role, headed_department_id, own_department_id):
    """Какие отделы пользователь видит в справочнике тем.

    None означает «все» (без фильтра). Иначе — множество id; общие темы
    (department_id IS NULL) видны всем, это учитывается в запросе.
    """
    if is_unscoped(role, headed_department_id):
        return None
    scope_id = headed_department_id if headed_department_id is not None else own_department_id
    if scope_id is None:
        return frozenset()
    return frozenset({int(scope_id)})
