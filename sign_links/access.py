# -*- coding: utf-8 -*-
"""Права раздела «Ссылка на подписание». Чистая логика: ни базы, ни Flask.

Модуль намеренно не импортирует ни database, ни flask — так его можно
импортировать в тестах напрямую (импорт database открывает пул к боевой базе;
та же причина, что у parcels/access.py и driver_chats/access.py).

Постановка владельца (16.09.2026): «мини раздел для отдела СЗоВ и фронт-офисы,
защищённый QR-доступом… журнал запросов с нужными фильтрами (для админов)»; в тот
же день: «раздел так же должен быть у ОП операторов через QR». На портал это
ложится так:

    оператор СЗоВ / фронт-офиса / ОП   вводит ИИН и получает ссылку — после QR
    супервайзер этих отделов           то же; QR ему не задают (он его подтверждает)
    глава любого из этих отделов       то же, плюс журнал СВОЕГО отдела
    глобальный админ                   то же, плюс журнал целиком
    тренер                             не пускается — как в «Обращениях» и «Посылках»
    остальные отделы                   не пускаются

Почему периметр — ОТДЕЛ, а не роль. Ссылку выдаёт тот, к кому пришёл или
позвонил водитель, и это не зависит от того, оператор человек или супервайзер.
Роль решает только две вещи: спрашивать ли QR и показывать ли журнал.

Почему журнал — только админам и главам. Так сформулировал владелец («для
админов»); глава отдела в этом портале — тот же админ, ограниченный своим
отделом (назначение главой ЗАМЕНЯЕТ базовую роль, см. is_global_admin).
Супервайзерам журнал НЕ показывается: расширить круг — отдельный вопрос
владельцу, а не «логичное» обобщение.
"""

# Роли, которые вообще существуют в портале.
_KNOWN_ROLES = ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee')

# Периметр раздела — три отдела. Списка id нет и не будет: отдел и есть периметр.
# Отдел продаж добавлен 16.09.2026 по просьбе владельца («у ОП операторов через
# QR») — тем же правилом, что и два первых: отдел целиком, QR у операторов.
SECTION_DEPARTMENT_CODES = ('szov', 'front_office', 'op')

# Тренер видит «всё» в других разделах, но ИИН живых водителей не его дело —
# то же решение, что закрыло ему «Обращения», «Посылки» и «Чаты водителей».
_SECTION_EXCLUDED_ROLES = ('trainer',)


def normalize_role(role):
    value = str(role or '').strip().lower()
    return value if value in _KNOWN_ROLES else 'operator'


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _own_code(ctx):
    return str(ctx.get('department_code') or '').strip().lower()


def is_department_head(ctx):
    return bool(ctx.get('headed_department_ids'))


def is_global_admin(ctx):
    """Глобальный админ — тот, кто не привязан к одному отделу.

    super_admin — всегда. admin — только пока он не назначен главой отдела:
    назначение главой заменяет базовую роль, иначе глава фронт-офисов читал бы
    журнал СЗоВ, а глава чужого отдела — весь раздел.
    """
    role = normalize_role(ctx.get('role'))
    if role == 'super_admin':
        return True
    return role == 'admin' and not is_department_head(ctx)


def _belongs_to(ctx, code):
    """Человек в этом отделе — своим членством или как его глава."""
    if code in _codes(ctx.get('headed_department_codes')):
        return True
    return _own_code(ctx) == code


def headed_section_codes(ctx):
    """Отделы раздела, которыми человек руководит."""
    return sorted(_codes(ctx.get('headed_department_codes')) & set(SECTION_DEPARTMENT_CODES))


def can_open_section(ctx):
    """Пускать ли пользователя в раздел вообще.

    Проверяется на КАЖДОМ роуте, а не только в меню: спрятанный пункт — это не
    доступ, раздел открывается и прямым адресом ?view=sign_links.
    """
    if is_global_admin(ctx):
        return True
    if normalize_role(ctx.get('role')) in _SECTION_EXCLUDED_ROLES:
        return False
    return any(_belongs_to(ctx, code) for code in SECTION_DEPARTMENT_CODES)


def can_generate(ctx):
    """Вводить ИИН и получать ссылку может каждый, кто вошёл в раздел.

    Отдельная функция, а не синоним can_open_section: если владелец решит
    выдать кому-то «только журнал», правило поменяется здесь, а не в роутах.
    """
    return can_open_section(ctx)


def requires_sensitive_qr(ctx):
    """Нужно ли подтвердить сессию QR-кодом, прежде чем раздел откроется.

    Оператору — да, обоим отделам: постановка требует «защищённый QR-доступом»,
    и ключ тот же, что у «Обращений», «Вики», «Посылок» и «Чатов водителей»
    (bot_schedule2: sensitive-access). Своего второго ключа раздел не заводит
    намеренно — два разных QR на один экран человек не различит.

    Кого гейт не касается: главы отдела и глобального админа — им подтверждать
    доступ не у кого, и супервайзера (он и подтверждает).

    Незнакомая роль подпадает под гейт: normalize_role сводит её к 'operator', и
    это правильная сторона ошибки — закрыто, а не открыто.
    """
    if is_global_admin(ctx) or is_department_head(ctx):
        return False
    return normalize_role(ctx.get('role')) == 'operator'


def journal_scope(ctx):
    """Какие отделы человек видит в журнале.

    None       — весь журнал (глобальный админ)
    [коды]     — только эти отделы (глава отдела раздела)
    []         — журнал не показывается
    """
    if is_global_admin(ctx):
        return None
    return headed_section_codes(ctx)


def can_view_journal(ctx):
    scope = journal_scope(ctx)
    return scope is None or bool(scope)


def capabilities(ctx):
    """Сводка для фронта: раздел рисует вкладки и кнопки по ней, а не по роли.

    Одно место правды: правило меняется здесь, а не в трёх местах интерфейса.
    """
    scope = journal_scope(ctx)
    return {
        'can_open': can_open_section(ctx),
        'can_generate': can_generate(ctx),
        'can_view_journal': can_view_journal(ctx),
        'journal_scope': scope,
        'requires_qr': requires_sensitive_qr(ctx),
        'is_global_admin': is_global_admin(ctx),
        'is_department_head': is_department_head(ctx),
    }
