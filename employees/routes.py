# -*- coding: utf-8 -*-
"""HTTP «Учета сотрудников»: GET /api/admin/employees/page — список по страницам.

    ?list=users        «Сотрудники», «Тренеры», «Админы», «Операторы» — кого и
                       в каких границах отдаёт /api/admin/users;
                       roles=operator,trainee,…  сужает набор ролей списка (шире
                       ручки он не становится — берётся пересечение);
                       group=operators  группы «Мои операторы» и направления
    ?list=supervisors  «Супервайзеры» — как полный профиль /api/admin/sv_list
    status=active|working|bs|sick_leave|annual_leave|fired, department=<id>,
    q=<поиск>, sort=name|direction|supervisor|status|hire_date|rate,
    dir=asc|desc, limit (≤200), offset, ids=1 — id всех найденных («Выбрать всех»).

Ответ: employees (строки в форме полной ручки), total, list_total, offset,
limit, status_counts, groups (у group=operators), ids (при ids=1).

Фабрика получает зависимости аргументами: обратный импорт из bot_schedule2 был
бы циклом. Границы видимости повторяют get_admin_users и get_sv_list
дословно — правило «кого видно» в двух местах сторожат
tests/test_employees_page.py и tests/test_employees_page_postgres.py.
"""

import logging

from flask import Blueprint, jsonify, request

from . import page, rows


def users_scope(*, role, requester_id, headed, hr, back_office_roles, roles,
                is_admin_role, is_super_admin_role, department_scope_id_for_requester):
    """Кого видно в «users» — как get_admin_users. None — раздел закрыт."""
    if not (is_admin_role(role) or role in ('sv', 'trainer') or headed or hr):
        return None
    visible = ['operator', 'trainee', 'trainer', *sorted(back_office_roles)]
    if headed or hr:
        visible.extend(['sv', 'supervisor'])
    # Админы — тому, у кого есть пункт «Админы»: супер-админу и кадровику.
    if role == 'super_admin' or hr:
        visible.append('admin')
    if roles is not None:
        wanted = set(roles)
        visible = [item for item in visible if item in wanted]
    headed_filter = None
    sv_department = None
    # Изоляция отделов: СВ и глава отдела видят только свой отдел; кадровика
    # граница не касается — у него учёт по всей фирме.
    if (role == 'sv' or headed) and not is_super_admin_role(role) and not hr:
        if headed:
            headed_filter = tuple(sorted(int(item) for item in headed))
        else:
            sv_department = department_scope_id_for_requester(requester_id)
    return page.Scope(list='users', roles=tuple(visible), headed=headed_filter,
                      sv_department=sv_department, requester_id=int(requester_id))


def supervisors_scope(*, role, requester_id, headed, hr, is_global_admin_requester):
    """Кого видно в «supervisors» — как полный профиль get_sv_list.

    Список «Супервайзеры» раздела открыт админу, главе отдела и кадровику; СВ и
    наблюдатель «Маркетинга» берут свою урезанную проекцию из /api/admin/sv_list
    для других разделов — страница им не нужна."""
    is_global_admin = is_global_admin_requester(role, requester_id)
    if not (is_global_admin or headed or hr):
        return None
    headed_filter = None
    if headed and not is_global_admin and not hr:
        headed_filter = tuple(sorted(int(item) for item in headed))
    return page.Scope(list='supervisors', headed=headed_filter, requester_id=int(requester_id))


def build_employees_blueprint(*, db, require_api_key, get_authenticated_requester, normalize_role,
                              is_admin_role, is_super_admin_role, is_global_admin_requester,
                              headed_department_ids, department_scope_id_for_requester,
                              is_employee_accounting_manager, build_avatar_signed_url,
                              back_office_roles, proxy_status_labels):
    bp = Blueprint('employees_page', __name__)
    load_rows = rows.build_row_loader(avatar_url=build_avatar_signed_url, proxy_labels=proxy_status_labels,
                                      normalize_role=normalize_role)

    @bp.route('/api/admin/employees/page', methods=['GET', 'OPTIONS'])
    @require_api_key
    def employees_page():
        try:
            requester_id, requester, auth_error = get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code
            try:
                query = page.parse_query(request.args)
            except page.PageError as exc:
                return jsonify({"error": str(exc)}), 400

            role = normalize_role(requester[3])
            headed = headed_department_ids(requester_id)
            hr = is_employee_accounting_manager(requester_id)
            if query.list == 'supervisors':
                scope = supervisors_scope(role=role, requester_id=requester_id, headed=headed, hr=hr,
                                          is_global_admin_requester=is_global_admin_requester)
            else:
                scope = users_scope(role=role, requester_id=requester_id, headed=headed, hr=hr,
                                    back_office_roles=back_office_roles, roles=query.roles,
                                    is_admin_role=is_admin_role, is_super_admin_role=is_super_admin_role,
                                    department_scope_id_for_requester=department_scope_id_for_requester)
            if scope is None:
                return jsonify({"error": "Forbidden"}), 403

            with db._get_cursor() as cursor:
                result = page.fetch_page(cursor, query, scope, me=requester_id, load_rows=load_rows)
            return jsonify({"status": "success", **result}), 200
        except Exception as exc:  # noqa: BLE001 — как у соседних ручек: 500 без подробностей
            logging.error("Error in employees_page: %s", exc, exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    return bp
