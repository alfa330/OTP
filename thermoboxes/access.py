"""Права раздела «Термокороба». Чистая логика: ни базы, ни Flask.

Постановка #363: «Данные заполняют сотрудники фронт-офисов, КЦ их только
просматривает», памятку «редактирует только Супервайзер». На портал это
ложится так:

    фронт-офисы (front_office)   правят остатки и условия — любой роли
    колл-центр СЗоВ (szov)       смотрят («КЦ» — это СЗоВ, решение владельца
                                 по соседнему «Учёту воды», 29.09.2026)
    супервайзер (sv)             правит памятку — СВ любого из двух отделов
    глава фронт-офисов           правит данные и заводит офисы в таблицу
    глобальный админ             всё
    тренер                       не входит: раздел он не просил

Граница «только свой офис» не проводится, как в «Посылках» и «Учёте воды»:
сотрудники к офисам в iCore не привязаны, а подменять коллегу из соседнего
города приходится каждый отпуск.

QR-подтверждения у раздела нет: на экране адреса офисов и числа, ни одного
водителя — закрывать нечего, а лишний замок только задержит оператора КЦ,
которому курьер звонит прямо сейчас.

Хелперы ролей общие с «Посылками»: семантика «глава отдела ≠ глобальный админ»
у портала одна.
"""

from parcels.access import is_department_head, is_global_admin, normalize_role

# ПИЛОТ (02.10.2026): «сделай деплой и открой доступ для суперадминов» — пока
# флаг стоит, раздел открыт только супер-админу; всё, что ниже, — периметр
# ПОСЛЕ пилота. Снимать флаг вместе с зеркалом во фронте
# (THERMOBOXES_PILOT_SUPER_ADMIN_ONLY в App.jsx) — тест сверяет их.
PILOT_SUPER_ADMIN_ONLY = True

EDIT_DEPARTMENT_CODE = 'front_office'   # заполняют данные
READ_DEPARTMENT_CODE = 'szov'           # колл-центр: смотрят

SECTION_DEPARTMENT_CODES = (EDIT_DEPARTMENT_CODE, READ_DEPARTMENT_CODE)

_SUPERVISOR_ROLES = ('sv', 'supervisor')


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _belongs_to(ctx, code):
    """Человек в этом отделе — своим членством или как его глава."""
    if code in _codes(ctx.get('headed_department_codes')):
        return True
    return str(ctx.get('department_code') or '').strip().lower() == code


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте: спрятанный пункт меню
    доступом не является. Зеркало — canAccessThermoboxesSectionForUser."""
    if PILOT_SUPER_ADMIN_ONLY:
        return normalize_role(ctx.get('role')) == 'super_admin'
    if is_global_admin(ctx):
        return True
    if normalize_role(ctx.get('role')) == 'trainer':
        return False
    return any(_belongs_to(ctx, code) for code in SECTION_DEPARTMENT_CODES)


def can_edit(ctx):
    """Править остатки и условия выдачи — фронт-офисы и глобальный админ.

    СЗоВ сюда не попадает ни в какой роли, включая СВ и главу: «КЦ только
    просматривает».
    """
    if is_global_admin(ctx):
        return True
    return _belongs_to(ctx, EDIT_DEPARTMENT_CODE)


def can_manage(ctx):
    """Завести офис в таблицу или убрать его — глава фронт-офисов и
    глобальный админ. Сотрудник офиса правит строки, но состав таблицы — это
    решение руководителя."""
    if is_global_admin(ctx):
        return True
    return is_department_head(ctx) and EDIT_DEPARTMENT_CODE in _codes(
        ctx.get('headed_department_codes'))


def can_edit_memo(ctx):
    """Памятка — «редактирует блок только Супервайзер».

    СВ любого из двух отделов раздела (у фронт-офисов СВ пока нет, но роль
    предусмотрена) и глобальный админ. Глава отдела сюда не входит: постановка
    называет именно супервайзера.
    """
    if is_global_admin(ctx):
        return True
    if normalize_role(ctx.get('role')) not in _SUPERVISOR_ROLES:
        return False
    return any(_belongs_to(ctx, code) for code in SECTION_DEPARTMENT_CODES)


def capabilities(ctx):
    """Сводка для фронта: экран рисует кнопки по ней, а не по роли.

    Кто в раздел не пущен (пилот, чужой отдел), не получает и прав внутри: иначе
    сводка обещала бы кнопки, на которые каждая ручка ответит 403.
    """
    opened = can_open_section(ctx)
    return {
        'can_open': opened,
        'can_edit': opened and can_edit(ctx),
        'can_manage': opened and can_manage(ctx),
        'can_edit_memo': opened and can_edit_memo(ctx),
    }
