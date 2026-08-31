"""Права раздела «Ограничитель Перезвона».

Читают: глобальные админы, глава СЗоВ и СВ СЗоВ. Правят: только первые двое.
Расхождение просмотра и правки — решение владельца 31.08.2026, до него обе
функции совпадали, и тесты ниже это совпадение закрепляли.
"""

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


def test_szov_supervisor_sees_section():
    """Решение владельца 31.08.2026. Обе формы роли обязательны: CHECK на
    users.role разрешает и 'sv', и 'supervisor', а normalize_role их не сводит —
    на одном литерале часть супервайзеров осталась бы за 403, и отличить это от
    «право не выдали» по симптому было бы нельзя."""
    assert access.can_view_section(user(role="sv")) is True
    assert access.can_view_section(user(role="supervisor")) is True


def test_supervisor_of_another_department_does_not():
    """Раздел про один отдел, и граница у СВ такая же строгая, как у главы."""
    assert access.can_view_section(user(role="sv", department_code="op")) is False
    assert access.can_view_section(user(role="supervisor", department_code="front")) is False


def test_operator_and_trainer_do_not():
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
    # У СВ периметр тот же: раздел показывает весь отдел, а не его группы.
    assert access.visible_department_code(user(role="sv")) == "szov"
    assert access.visible_department_code(user(role="operator")) == ""


def test_supervisor_reads_but_does_not_manage():
    """Главное в правке: у СВ просмотр без правки. Общий порог, режим обкатки и
    версия exe действуют на весь отдел сразу — это не уровень супервайзера."""
    supervisor = user(role="sv")
    assert access.can_view_section(supervisor) is True
    assert access.can_manage_settings(supervisor) is False


def test_manage_stays_with_the_head_and_global_admins():
    for candidate in (user(role="admin", department_code=""),
                      user(role="super_admin", department_code=""),
                      user(role="admin", is_department_head=True, department_code="szov")):
        assert access.can_manage_settings(candidate) is True
    for candidate in (user(role="sv"), user(role="supervisor"), user(role="operator"),
                      user(role="admin", is_department_head=True, department_code="op")):
        assert access.can_manage_settings(candidate) is False
