# -*- coding: utf-8 -*-
"""Контекст доступа «оператор отдела» без учётки — для партнёрского ключа.

Ключ действует от имени должности в отделе, а не от имени человека. Контекст
собирается по той же форме, что wiki.routes.wiki_route строит для сотрудника
(queries.load_access_context → access.collect_subjects → queries.load_capabilities),
только без строки users: user_id = 0 — такого человека нет, поэтому к периметру
не примешиваются ни авторство, ни личные правила, ни гостевые выдачи. Остаются
ровно правила отдела и должности — то есть то, что видит ЛЮБОЙ оператор СЗоВ.

Периметр считает wiki.perimeter.assistant_perimeter: мастер-ключа там нет по
построению, черновики, строгие статьи и статьи с рубильником ai_opt_out не
попадают. Второго вычислителя для партнёра не заводится намеренно — на таком
раздвоении исходная вика уже ломалась (шапка wiki/perimeter.py).
"""

from wiki import access as wiki_access
from wiki import perimeter as wiki_perimeter
from wiki import queries as wiki_queries

# Нет такого пользователя: авторство, гостевые выдачи и личные правила
# сравниваются с ним и не совпадают никогда.
SERVICE_USER_ID = 0


def service_context(cursor, *, otp_role, department_id):
    """Контекст доступа должности в отделе — той же формы, что у сотрудника."""
    role = wiki_access.normalize_role(otp_role) or 'operator'
    department = int(department_id) if department_id else None
    ctx = {
        'user_id': SERVICE_USER_ID,
        'otp_role': role,
        'department_id': department,
        'direction_id': None,
        'job_title': None,
        'wiki_enabled': True,
        'headed_department_ids': [],
        'group_ids': [],
        'wiki_roles': [],
        'access_mode': 'auto',
        'has_guest_access': False,
    }
    ctx['subjects'] = wiki_access.collect_subjects(
        user_id=SERVICE_USER_ID, otp_role=role, department_id=department,
        headed_department_ids=(), direction_id=None, group_ids=(),
        wiki_role_ids=(), job_title=None)
    wiki_queries.load_capabilities(cursor, ctx, ctx['subjects'])
    return ctx


def spaces(cursor, ctx):
    """Пространства вики, выданные отделу ключа. Гостевые выдачи не считаются."""
    return [int(value) for value in wiki_queries.spaces_for_user(cursor, ctx, include_guest=False)]


def scope(cursor, ctx, space_id):
    """Периметр партнёра в одном пространстве: {article_ids, section_ids, hash, read_count}."""
    return wiki_perimeter.assistant_perimeter(cursor, ctx, space_id)
