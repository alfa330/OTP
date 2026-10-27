# -*- coding: utf-8 -*-
"""Права раздела «Аренда авто». Чистая логика: ни базы, ни Flask.

Ночной отчёт (#398) ведёт ООЗ — «Отдел обработки запросов» (код отдела
request_processing_department, глава отдела — постановщик задачи): ночная смена
собирает отчёт и ставит «Состояние». Отдел, а не список людей: придёт новый оператор в
ООЗ — увидит отчёт без правки кода. Плюс супер-админ.

Уволенный не видит ничего, даже если ещё числится в отделе.
"""

OOZ_DEPARTMENT_CODE = 'request_processing_department'

_INACTIVE_STATUSES = ('fired', 'dismissal')


def is_working(ctx):
    return str(ctx.get('status') or 'working').strip().lower() not in _INACTIVE_STATUSES


def is_super_admin(ctx):
    return str(ctx.get('role') or '').strip().lower() == 'super_admin'


def in_ooz(ctx):
    if str(ctx.get('department_code') or '').strip().lower() == OOZ_DEPARTMENT_CODE:
        return True
    return OOZ_DEPARTMENT_CODE in {str(code or '').strip().lower() for code in ctx.get('headed_codes') or ()}


def can_open_night_report(ctx):
    return is_working(ctx) and (is_super_admin(ctx) or in_ooz(ctx))


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте; тот же ответ портал получает флагом
    rental_access в профиле и по нему рисует пункт меню."""
    return can_open_night_report(ctx)


def can_collect_any_time(ctx):
    """Собрать отчёт вне ночного окна (проверка после выкладки) — только супер-админ."""
    return can_open_night_report(ctx) and is_super_admin(ctx)


def capabilities(ctx):
    return {
        'night_report': can_open_night_report(ctx),
        'collect_any_time': can_collect_any_time(ctx),
    }
