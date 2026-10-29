# -*- coding: utf-8 -*-
"""Права раздела «Воронка найма» (ТЗ #404, раздел 2). Чистая логика: ни базы, ни Flask.

Пять ролей ТЗ и откуда они берутся в портале:

    руководитель рекрутинга / HR   супер-админ; глава отдела HR (код hr) и его сотрудники;
                                   плюс те, кому в разделе выдали «Руководитель рекрутинга»
    штатный рекрутер               выдача в разделе («Штатный рекрутер»)
    удалённый рекрутер             выдача в разделе («Удалённый рекрутер»): телефоны видит
                                   только в своих карточках
    тренер                         роль «тренер», ведёт группу на обучение — видит её кандидатов
    руководитель отдела            глава отдела, куда был найм: кандидаты своего отдела,
                                   отметки смен и подтверждение «Отработал 7 дней»

Выдачи — данные (hiring_people), не код: новый удалённый рекрутер появляется без правки
кода, как и в «Кодах 2FA». Уволенный не видит ничего.
"""

from . import rules

HR_DEPARTMENT_CODE = 'hr'
_INACTIVE_STATUSES = ('fired', 'dismissal')


def is_working(ctx):
    return str(ctx.get('status') or 'working').strip().lower() not in _INACTIVE_STATUSES


def is_super_admin(ctx):
    return str(ctx.get('role') or '').strip().lower() == 'super_admin'


def _headed_codes(ctx):
    return {str(code or '').strip().lower() for code in ctx.get('headed_codes') or ()}


def _headed_ids(ctx):
    return {int(x) for x in (ctx.get('headed_ids') or ()) if x}


def is_lead(ctx):
    """Руководитель рекрутинга / HR."""
    if is_super_admin(ctx):
        return True
    if HR_DEPARTMENT_CODE in _headed_codes(ctx):
        return True
    if str(ctx.get('department_code') or '').strip().lower() == HR_DEPARTMENT_CODE:
        return True
    return ctx.get('grant') == rules.GRANT_LEAD


def is_recruiter(ctx):
    """Рекрутер — штатный или удалённый. Руководитель тоже берёт карточки в работу (ТЗ 2)."""
    return ctx.get('grant') in (rules.GRANT_STAFF, rules.GRANT_REMOTE) or is_lead(ctx)


def is_remote(ctx):
    """Удалённый рекрутер: в общем пуле телефонов не видит (ТЗ 2)."""
    return ctx.get('grant') == rules.GRANT_REMOTE and not is_lead(ctx)


def is_trainer(ctx):
    """Тренер с хотя бы одной группой на обучение."""
    return bool(ctx.get('trainer_group_ids'))


def is_dept_head(ctx):
    """Глава отдела, в который ведётся найм (есть вакансия с его отделом)."""
    headed = _headed_ids(ctx)
    wanted = {int(x) for x in (ctx.get('vacancy_department_ids') or ()) if x}
    return bool(headed & wanted)


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте; тот же ответ портал получает
    флагом hiring_access в профиле и по нему рисует пункт меню."""
    if not is_working(ctx):
        return False
    return is_lead(ctx) or is_recruiter(ctx) or is_trainer(ctx) or is_dept_head(ctx)


def can_see_pool(ctx):
    return is_recruiter(ctx)


def can_take(ctx):
    return is_recruiter(ctx)


def can_upload(ctx):
    return is_lead(ctx)


def can_add_response(ctx):
    return is_recruiter(ctx)


def can_manage(ctx):
    """Справочники, выдачи, группы, планы, переназначение и возврат в пул."""
    return is_lead(ctx)


def can_see_all_motivation(ctx):
    return is_lead(ctx)


def can_see_reports(ctx):
    """Отчёты и дашборд целиком — руководитель и HR; рекрутер видит их только по себе."""
    return is_lead(ctx)


def sees_phone(ctx, card):
    """Удалённый рекрутер видит телефон только в карточках, которые взял (ТЗ 2)."""
    if not is_remote(ctx):
        return True
    recruiter_id = card.get('recruiter_id')
    return bool(recruiter_id) and int(recruiter_id) == int(ctx['user_id'])


def capabilities(ctx):
    return {
        'lead': is_lead(ctx),
        'recruiter': is_recruiter(ctx),
        'remote': is_remote(ctx),
        'trainer': is_trainer(ctx),
        'dept_head': is_dept_head(ctx),
        'super_admin': is_super_admin(ctx),
        'pool': can_see_pool(ctx),
        'take': can_take(ctx),
        'upload': can_upload(ctx),
        'add_response': can_add_response(ctx),
        'manage': can_manage(ctx),
        'reports': can_see_reports(ctx),
        'headed_ids': sorted(_headed_ids(ctx)),
        'trainer_group_ids': sorted({int(x) for x in (ctx.get('trainer_group_ids') or ())}),
    }


def scope_label(ctx):
    """Кем человек входит в раздел — для подписи на рабочем столе."""
    if is_lead(ctx):
        return 'Руководитель рекрутинга'
    if ctx.get('grant') == rules.GRANT_REMOTE:
        return 'Удалённый рекрутер'
    if ctx.get('grant') == rules.GRANT_STAFF:
        return 'Штатный рекрутер'
    if is_trainer(ctx):
        return 'Тренер'
    if is_dept_head(ctx):
        return 'Руководитель отдела'
    return ''
