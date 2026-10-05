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
    """Назначение главой ЗАМЕНЯЕТ базовую роль: глава чужого отдела сюда не
    попадает, хотя роль у него admin. Та же граница, что у табло СЗоВ."""
    assert access.can_view_section(user(role="admin", is_department_head=True, department_code="tez")) is False


def test_op_head_sees_only_the_sales_part():
    """ТЗ 05.10.2026: у раздела появилась часть отдела продаж. Глава ОП видит
    её — и только её: СЗоВ (Oktell, операторы, выбросы) ему по-прежнему закрыт."""
    op_head = user(role="admin", is_department_head=True, department_code="op")
    assert access.can_view_section(op_head) is True
    assert access.visible_department_codes(op_head) == ["op"]
    assert access.can_view_department(op_head, "szov") is False
    assert access.visible_department_code(op_head) == ""   # часть Oktell — никого


def test_szov_supervisor_sees_section():
    """Решение владельца 31.08.2026. Обе формы роли обязательны: CHECK на
    users.role разрешает и 'sv', и 'supervisor', а normalize_role их не сводит —
    на одном литерале часть супервайзеров осталась бы за 403, и отличить это от
    «право не выдали» по симптому было бы нельзя."""
    assert access.can_view_section(user(role="sv")) is True
    assert access.can_view_section(user(role="supervisor")) is True


def test_supervisor_of_another_department_does_not():
    """Граница у СВ такая же строгая, как у главы: чужой отдел — ничего."""
    assert access.can_view_section(user(role="sv", department_code="tez")) is False
    assert access.can_view_section(user(role="supervisor", department_code="front")) is False


def test_op_supervisor_reads_only_the_sales_part():
    """СВ ОП читает часть ОП целиком (весь отдел, как СВ СЗоВ — свой) и не
    видит СЗоВ. Обе формы роли, по той же причине, что у СЗоВ."""
    for role in ("sv", "supervisor"):
        op_sv = user(role=role, department_code="op")
        assert access.visible_department_codes(op_sv) == ["op"]
        assert access.can_view_section(op_sv) is True
        assert access.can_view_department(op_sv, "szov") is False


def test_operator_and_trainer_do_not():
    assert access.can_view_section(user(role="operator")) is False
    assert access.can_view_section(user(role="trainer")) is False
    assert access.can_view_section(None) is False


def test_department_head_detected_by_id_field():
    assert access.is_department_head(user(head_of_department_id=3)) is True
    assert access.is_department_head(user()) is False


def test_camel_case_fields_from_frontend():
    assert access.can_view_section({"role": "admin", "isDepartmentHead": True, "departmentCode": "szov"}) is True


def test_visible_departments_by_circle():
    """Обе части видит только глобальный админ; порядок — СЗоВ, затем ОП:
    запрос без ?department= у главы и СВ СЗоВ обязан отдавать прежнее."""
    assert access.visible_department_codes(user(role="admin", department_code="")) == ["szov", "op"]
    assert access.visible_department_codes(user(role="super_admin", department_code="op")) == ["szov", "op"]
    assert access.visible_department_codes(user(role="admin", is_department_head=True)) == ["szov"]
    assert access.visible_department_codes(user(role="sv")) == ["szov"]
    assert access.visible_department_codes(user(role="operator")) == []
    assert access.visible_department_codes(user(role="operator", department_code="op")) == []
    assert access.visible_department_codes(None) == []


def test_head_of_both_departments_sees_both():
    """Глава двух отделов — две части, и не наугад: раньше access_context
    брал один отдел через LIMIT 1 без порядка."""
    both = user(role="admin", is_department_head=True, department_code="op",
                headed_department_codes=["op", "szov"])
    assert access.visible_department_codes(both) == ["szov", "op"]
    assert access.can_manage_settings(both) is True
    assert access.can_manage_phone_settings(both) is True


def test_head_of_a_department_without_code_is_not_the_head_of_his_own():
    """Ревью 05.10.2026 (G1). access_context всегда отдаёт список возглавляемых
    отделов, и он ПУСТ, когда человек руководит только отделом с пустым кодом.
    Раньше пустой список читался как «списка нет» и подменялся собственным
    отделом: СВ или оператор ОП, назначенный главой такого отдела, становился
    «главой ОП» и мог выключить автоофлайн всему отделу продаж. То же у СЗоВ —
    с общими настройками Oktell."""
    for role in ("sv", "supervisor", "operator"):
        for key in ("headed_department_codes", "headedDepartmentCodes"):
            op = user(role=role, is_department_head=True, department_code="op", **{key: []})
            assert access.headed_department_codes(op) == ()
            assert access.is_op_head(op) is False
            assert access.can_manage_phone_settings(op) is False
            szov = user(role=role, is_department_head=True, department_code="szov", **{key: []})
            assert access.is_szov_head(szov) is False
            assert access.can_manage_settings(szov) is False
    # Просмотр у СВ остаётся его собственным правом СВ, а не «правом главы».
    op_sv = user(role="sv", is_department_head=True, department_code="op",
                 headed_department_codes=[])
    assert access.visible_department_codes(op_sv) == ["op"]
    op_operator = user(role="operator", is_department_head=True, department_code="op",
                       headed_department_codes=[])
    assert access.can_view_section(op_operator) is False
    # Админ с таким назначением — не глобальный админ (назначение заменяет
    # роль) и не глава ни одной из частей раздела: раздел ему закрыт.
    admin = user(role="admin", is_department_head=True, department_code="op",
                 headed_department_codes=[])
    assert access.can_view_section(admin) is False
    assert access.can_manage_phone_settings(admin) is False


def test_own_department_is_used_only_when_the_context_has_no_list_at_all():
    """Обратная сторона G1: контекст БЕЗ поля (фронт, старые вызовы) по-прежнему
    читается по department_code — его access_context уже подменил возглавляемым."""
    head = {"role": "admin", "is_department_head": True, "department_code": "op"}
    assert access.headed_department_codes(head) == ("op",)
    assert access.can_manage_phone_settings(head) is True

    class Person:
        role = "admin"
        is_department_head = True
        department_code = "szov"

    assert access.headed_department_codes(Person()) == ("szov",)
    Person.headed_department_codes = []
    assert access.headed_department_codes(Person()) == ()
    assert access.can_manage_settings(Person()) is False


def test_unknown_department_is_never_visible():
    assert access.can_view_department(user(role="admin", department_code=""), "tez") is False
    assert access.can_view_department(user(role="admin", department_code=""), " OP ") is True


def test_sales_rule_is_managed_by_the_op_head_and_global_admins():
    """Правило ОП правят глава ОП и глобальные админы. СВ ОП только читает,
    глава СЗоВ в правило соседнего отдела не ходит — и наоборот."""
    for who in (user(role="admin", department_code=""),
                user(role="super_admin", department_code=""),
                user(role="admin", is_department_head=True, department_code="op")):
        assert access.can_manage_phone_settings(who) is True
    for who in (user(role="sv", department_code="op"),
                user(role="supervisor", department_code="op"),
                user(role="admin", is_department_head=True, department_code="szov"),
                user(role="sv"),
                user(role="operator", department_code="op"),
                None):
        assert access.can_manage_phone_settings(who) is False


def test_op_people_never_touch_oktell():
    """Глава и СВ ОП видят раздел, но Oktell-часть им закрыта целиком: ни
    общих настроек, ни агента (у ОП iCORE Phone, а не Oktell)."""
    for who in (user(role="admin", is_department_head=True, department_code="op"),
                user(role="sv", department_code="op"),
                user(role="supervisor", department_code="op")):
        assert access.can_manage_settings(who) is False
        assert access.can_download_agent(who) is False


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


# --------------------------------------------------------------------------- #
# Скачивание exe: третий круг, самый широкий
# --------------------------------------------------------------------------- #
#
# Решение владельца 07.09.2026: пункт «Скачать Oktell» стоял в меню у КАЖДОГО
# оператора СЗоВ — так же, как «Скачать iCore Phone» у ОП и Тез КЦ. 22.09.2026
# пункт убран (агент ставится MSI через групповую политику), круг ручки оставлен
# прежним — см. access.can_download_agent. Настройки и отчёт оператору
# по-прежнему не открыты.
#
# Порядок кругов: can_download_agent ⊇ can_view_section ⊇ can_manage_settings.


def test_szov_operator_downloads_but_does_not_see_the_section():
    operator = user(role="operator")
    assert access.can_download_agent(operator) is True
    assert access.can_view_section(operator) is False
    assert access.can_manage_settings(operator) is False


def test_trainee_of_szov_downloads_too():
    """Стажёр сидит на линии и в «Перезвоне» так же, как оператор, и в списке
    раздела он есть (EMPLOYEE_ROLES). Отказать ему в программе значило бы
    оставить его вне ограничителя по недоразумению."""
    assert access.can_download_agent(user(role="trainee")) is True


def test_roles_match_the_employee_list_of_the_section():
    """Свой, более узкий список ролей здесь означал бы «в разделе человек есть,
    а скачать не может». Сверяемся с тем же источником."""
    from oktell_guard import queries
    assert set(access.AGENT_USER_ROLES) == set(queries.EMPLOYEE_ROLES)


def test_operator_of_another_department_does_not_download():
    """Телефон ОП и Тез КЦ — iCORE Phone, у них своя кнопка и свой гейт."""
    assert access.can_download_agent(user(role="operator", department_code="op")) is False
    assert access.can_download_agent(user(role="operator", department_code="tez")) is False


def test_everybody_who_sees_the_section_can_download():
    """Круг раздела входит в круг скачивания целиком: СВ раздаёт установщик и
    сам ставит агента на свою машину."""
    for who in (user(role="admin", department_code=""),
                user(role="super_admin", department_code=""),
                user(role="admin", is_department_head=True),
                user(role="sv"),
                user(role="supervisor")):
        assert access.can_view_section(who) is True
        assert access.can_download_agent(who) is True


def test_nobody_else_downloads():
    assert access.can_download_agent(user(role="trainer")) is False
    assert access.can_download_agent(user(role="chat_operator")) is False
    assert access.can_download_agent(None) is False


def test_camel_case_department_from_the_frontend_also_counts():
    assert access.can_download_agent(
        {"role": "operator", "departmentCode": "szov"}) is True
