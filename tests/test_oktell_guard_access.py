"""Права раздела «Ограничитель Перезвона»: глава СЗоВ и глобальные админы."""

from oktell_guard import access


def user(**kwargs):
    base = {"role": "operator", "department_code": "szov", "is_department_head": False}
    base.update(kwargs)
    return base


def test_global_admin_sees_section():
    assert access.can_view_section(user(role="admin", department_code="")) is True
    assert access.can_view_section(user(role="super_admin", department_code="")) is True


def test_szov_head_sees_section():
    assert access.can_view_section(user(role="admin", is_department_head=True, department_code="szov")) is True


def test_head_of_another_department_does_not():
    """Назначение главой ЗАМЕНЯЕТ базовую роль: глава ОП сюда не попадает,
    хотя роль у него admin. Та же граница, что у табло СЗоВ."""
    assert access.can_view_section(user(role="admin", is_department_head=True, department_code="op")) is False


def test_supervisor_and_operator_do_not():
    assert access.can_view_section(user(role="sv")) is False
    assert access.can_view_section(user(role="operator")) is False
    assert access.can_view_section(user(role="trainer")) is False
    assert access.can_view_section(None) is False


def test_department_head_detected_by_id_field():
    assert access.is_department_head(user(head_of_department_id=3)) is True
    assert access.is_department_head(user()) is False


def test_camel_case_fields_from_frontend():
    assert access.can_view_section({"role": "admin", "isDepartmentHead": True, "departmentCode": "szov"}) is True


def test_scope_is_always_szov():
    """Раздел про один отдел: даже глобальный админ видит в нём только СЗоВ,
    иначе в списке оказываются люди, которых ограничитель не касается."""
    assert access.visible_department_code(user(role="admin", department_code="")) == "szov"
    assert access.visible_department_code(user(role="super_admin", department_code="")) == "szov"
    assert access.visible_department_code(user(role="admin", is_department_head=True)) == "szov"
    assert access.visible_department_code(user(role="operator")) == ""


def test_manage_matches_view_for_now():
    for candidate in (user(role="admin", department_code=""), user(role="sv"), user(role="operator")):
        assert access.can_manage_settings(candidate) == access.can_view_section(candidate)
