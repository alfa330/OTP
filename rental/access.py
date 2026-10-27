# -*- coding: utf-8 -*-
"""Права раздела «Аренда авто». Чистая логика: ни базы, ни Flask.

Ночной отчёт (#398) ведёт ООЗ — «Отдел обработки запросов» (код отдела
request_processing_department, глава отдела — постановщик задачи): ночная смена
собирает отчёт и ставит «Состояние». Отдел, а не список людей: придёт новый оператор в
ООЗ — увидит отчёт без правки кода. Плюс супер-админ.

Наличие авто и направления в офис (#391), роли из ТЗ (раздел 6):

    оператор ОП              смотрит машины и условия, направляет клиента в офис;
                             в реестре — свои направления
    сотрудник аренды         все направления: статус и комментарий; правит карточки
                             машин и «Условия работы»
    РОП / руководитель       всё то же и все направления — глава отдела продаж и глава
                             отдела аренды
    супер-админ              всё

Отдел продаж — код 'op' (операторы и СВ), отдел аренды — 'car_rental'. Тоже отделы, а не
списки людей.

Уволенный не видит ничего, даже если ещё числится в отделе.
"""

OOZ_DEPARTMENT_CODE = 'request_processing_department'
SALES_DEPARTMENT_CODE = 'op'
RENTAL_DEPARTMENT_CODE = 'car_rental'

_INACTIVE_STATUSES = ('fired', 'dismissal')


def is_working(ctx):
    return str(ctx.get('status') or 'working').strip().lower() not in _INACTIVE_STATUSES


def is_super_admin(ctx):
    return str(ctx.get('role') or '').strip().lower() == 'super_admin'


def _headed(ctx):
    return {str(code or '').strip().lower() for code in ctx.get('headed_codes') or ()}


def _department(ctx):
    return str(ctx.get('department_code') or '').strip().lower()


def _in_department(ctx, code):
    return _department(ctx) == code or code in _headed(ctx)


def in_ooz(ctx):
    return _in_department(ctx, OOZ_DEPARTMENT_CODE)


def in_sales(ctx):
    return _in_department(ctx, SALES_DEPARTMENT_CODE)


def in_rental(ctx):
    return _in_department(ctx, RENTAL_DEPARTMENT_CODE)


def is_rental_lead(ctx):
    """РОП / руководитель (ТЗ 6): глава отдела продаж или глава отдела аренды."""
    headed = _headed(ctx)
    return SALES_DEPARTMENT_CODE in headed or RENTAL_DEPARTMENT_CODE in headed


# ─── Ночной отчёт (#398) ────────────────────────────────────────────────────────

def can_open_night_report(ctx):
    return is_working(ctx) and (is_super_admin(ctx) or in_ooz(ctx))


def can_collect_any_time(ctx):
    """Собрать отчёт вне ночного окна (проверка после выкладки) — только супер-админ."""
    return can_open_night_report(ctx) and is_super_admin(ctx)


# ─── Наличие и направления (#391) ──────────────────────────────────────────────

def can_view_fleet(ctx):
    """Машины, условия, свои направления."""
    return is_working(ctx) and (is_super_admin(ctx) or in_sales(ctx) or in_rental(ctx))


def can_send(ctx):
    """«Направить в офис» — оператор ОП; сотрудник аренды и руководители тоже (ТЗ 6)."""
    return can_view_fleet(ctx)


def can_see_all_directions(ctx):
    return is_working(ctx) and (is_super_admin(ctx) or in_rental(ctx) or is_rental_lead(ctx))


def can_work_directions(ctx):
    """Итоговый статус и комментарий — сотрудник аренды и руководители, не оператор ОП (ТЗ 6)."""
    return can_see_all_directions(ctx)


def can_edit_cards(ctx):
    """Карточки машин — сотрудник аренды и руководители (ТЗ 10.1)."""
    return can_see_all_directions(ctx)


def can_edit_conditions(ctx):
    """«Условия работы» — сотрудники аренды и руководители (ТЗ 10.3)."""
    return can_see_all_directions(ctx)


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте; тот же ответ портал получает флагом
    rental_access в профиле и по нему рисует пункт меню."""
    return can_open_night_report(ctx) or can_view_fleet(ctx)


def capabilities(ctx):
    return {
        'night_report': can_open_night_report(ctx),
        'collect_any_time': can_collect_any_time(ctx),
        'fleet': can_view_fleet(ctx),
        'send': can_send(ctx),
        'all_directions': can_see_all_directions(ctx),
        'work_directions': can_work_directions(ctx),
        'edit_cards': can_edit_cards(ctx),
        'edit_conditions': can_edit_conditions(ctx),
    }
