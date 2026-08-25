# -*- coding: utf-8 -*-
"""Права раздела «Касания». Чистая логика: ни базы, ни Flask.

Модуль не импортирует ни `database`, ни `flask` — так его можно взять в тест
напрямую (импорт `database` открывает пул к боевой базе; та же причина у
parcels/access.py и crm/access.py).

Периметр — отдел продаж
-----------------------
Эта АТС обслуживает только отдел продаж, и это проверено, а не предположено: за
24.06–12.08.2026 в CDR встретилось 98 внутренних номеров, из них 55 совпали с
ОП-номерами из базы, а три «СЗоВ-номера» (6605, 6635, 6692) станция держит за
сотрудниками ОП. СЗоВ и Тез КЦ сидят на Oktell и в этот CDR не попадают.
Практический вывод: любой звонок отсюда — касание ОП, отдельного фильтра по
отделу внутри раздела не нужно, а сам раздел принадлежит ОП.

Кому открыт
-----------
    глобальный админ        весь раздел
    глава отдела продаж     весь раздел (в границах своего отдела — он и есть)
    супервайзер ОП          весь раздел
    оператор, тренер        нет

Почему оператору нет. Выгрузка — это телефоны клиентов за период целиком, а не
свои звонки: инструмент разбора работы отдела, а не личный кабинет. Тренеру
закрыто по той же причине, по которой ему закрыты «Обращения» и «Посылки», —
живые персональные данные не его дело.

Отдельного QR-гейта раздел не заводит осознанно. Ключ портала (sensitive-access)
закрывает раздел от РОЛИ «оператор», а её здесь нет вовсе: супервайзер — это
как раз тот, кто QR подтверждает, а главе отдела и админу подтверждать не у
кого. Второй гейт был бы декорацией, а лишний QR на экране человек не различит.

Граница «глава отдела ≠ глобальный админ» — действующая семантика портала:
назначение главой ЗАМЕНЯЕТ базовую роль и режет периметр своим отделом.
"""

_KNOWN_ROLES = ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee')

# Отдел, которому принадлежит станция. Один код, а не список: см. шапку.
SECTION_DEPARTMENT_CODE = 'op'

# Роли внутри отдела, которым раздел открыт.
_SECTION_ROLES = ('sv', 'supervisor', 'admin', 'super_admin')


def normalize_role(role):
    value = str(role or '').strip().lower()
    if value == 'supervisor':
        return 'sv'
    if value == 'superadmin':
        return 'super_admin'
    return value if value in _KNOWN_ROLES else 'operator'


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def is_department_head(ctx):
    return bool(ctx.get('headed_department_ids'))


def is_global_admin(ctx):
    """Админ, не привязанный к одному отделу.

    super_admin — всегда; admin — пока он не назначен главой отдела, иначе глава
    СЗоВ читал бы звонки отдела продаж.
    """
    role = normalize_role(ctx.get('role'))
    if role == 'super_admin':
        return True
    return role == 'admin' and not is_department_head(ctx)


def belongs_to_sales(ctx):
    """Человек в отделе продаж — своим членством или как его глава."""
    if SECTION_DEPARTMENT_CODE in _codes(ctx.get('headed_department_codes')):
        return True
    return str(ctx.get('department_code') or '').strip().lower() == SECTION_DEPARTMENT_CODE


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте, а не только в меню:
    спрятанный пункт доступом не является, раздел открывается прямым адресом."""
    if is_global_admin(ctx):
        return True
    if not belongs_to_sales(ctx):
        return False
    if is_department_head(ctx):
        return True
    return normalize_role(ctx.get('role')) in _SECTION_ROLES


def can_sync(ctx):
    """Запускать выкачку новых суток со станции.

    Право то же, что на чтение: выкачка — не привилегия, а способ увидеть
    данные, которых ещё нет. Отдельная функция всё же нужна: она называет
    действие, у которого своя цена (минуты работы и нагрузка на станцию), и
    сузить его завтра можно будет здесь, а не по всем роутам.
    """
    return can_open_section(ctx)


def capabilities(ctx):
    """Сводка для фронта: кнопки рисуются по ней, а не по роли. Одно место
    правды — правило меняется здесь, а не в трёх местах интерфейса."""
    return {
        'can_open': can_open_section(ctx),
        'can_sync': can_sync(ctx),
        'is_global_admin': is_global_admin(ctx),
        'is_department_head': is_department_head(ctx),
    }
