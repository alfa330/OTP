# -*- coding: utf-8 -*-
"""Права раздела «АТС». Чистая логика: ни базы, ни Flask (как cdr/access.py, откуда берётся
периметр раздела).

Кому что
--------
    смотреть живые звонки      тот же периметр, что у «Касаний» (cdr.access.can_open_section):
                               админ, глава ОП, СВ ОП, поимённые допуски
    позвонить клиенту          любой из них, у кого в карточке есть внутренний номер станции —
                               звонок идёт с ЕГО телефона, чужим телефоном не позвонить
    положить трубку            свой звонок — всегда; чужой — руководитель (см. ниже)
    подключиться к разговору   руководители и СВ отдела продаж, глобальные админы. Поимённым
                               допускам (маркетинг) — нет: раздел им выдан для разбора звонков,
                               а слушать живой разговор оператора — другое решение, его не было.
"""

from cdr import access as cdr_access

_SUPERVISOR_ROLES = ('sv', 'supervisor')


def can_open_section(ctx):
    return cdr_access.can_open_section(ctx)


def can_call(ctx, my_ext):
    """Позвонить можно только со своего телефона: без внутреннего номера звонить нечем."""
    return can_open_section(ctx) and bool(my_ext)


def can_spy(ctx):
    """Подключение к чужому разговору — только руководство: глобальный админ, глава ОП, СВ ОП."""
    if not can_open_section(ctx):
        return False
    if cdr_access.is_global_admin(ctx):
        return True
    if not cdr_access.belongs_to_sales(ctx):
        return False
    if cdr_access.is_department_head(ctx):
        return True
    return cdr_access.normalize_role(ctx.get('role')) in _SUPERVISOR_ROLES


def is_own_call(call, my_ext):
    """Звонок — мой: я в разговоре, мне звонит телефон или я его набрал."""
    if not my_ext or not call:
        return False
    return my_ext == str(call.get('ext') or '') or my_ext in (call.get('ringing') or [])


def can_hangup(ctx, call, my_ext):
    return can_open_section(ctx) and (is_own_call(call, my_ext) or can_spy(ctx))


def capabilities(ctx, my_ext):
    """Сводка для фронта: кнопки рисуются по ней, а не по роли."""
    return {
        'ext': my_ext or '',
        'can_call': can_call(ctx, my_ext),
        'can_spy': can_spy(ctx),
    }
