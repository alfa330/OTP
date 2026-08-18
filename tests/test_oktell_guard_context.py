"""Контекст доступа: он строится запросом, а не разбором строки базы.

Из-за разбора строки раздел закрывался даже суперадмину: пользователь приходит
из _resolve_requester КОРТЕЖЕМ, обращение к полю по имени молча давало None,
и права считались от пустой роли.
"""

from oktell_guard import access


def test_row_like_tuple_is_not_a_valid_context():
    """Прямо тот случай, что сломался на проде: кортеж прав не даёт."""
    row = (7, 'Руслан', 'ruslan@example.com', 'super_admin')
    assert access.can_view_section(row) is False


def test_context_dict_from_query_works():
    ctx = {'id': 7, 'name': 'Руслан', 'role': 'super_admin',
           'department_code': '', 'is_department_head': False}
    assert access.can_view_section(ctx) is True


def test_department_head_context_uses_headed_department():
    """У главы отдела свой department_id может быть пустым или чужим — считаем
    по отделу, которым он руководит."""
    ctx = {'id': 9, 'role': 'admin', 'department_code': 'szov', 'is_department_head': True}
    assert access.can_view_section(ctx) is True
    assert access.visible_department_code(ctx) == 'szov'   # и админу тоже только СЗоВ


def test_head_of_other_department_still_denied():
    ctx = {'id': 9, 'role': 'admin', 'department_code': 'op', 'is_department_head': True}
    assert access.can_view_section(ctx) is False
