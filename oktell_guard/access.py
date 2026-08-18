"""Права раздела «Ограничитель Перезвона». Чистая логика: ни базы, ни Flask.

Модуль намеренно ничего не импортирует из database/flask — так его можно
дёргать в тестах напрямую (импорт database открывает пул к боевой базе, та же
причина, что в crm/access.py и wiki/__init__.py).

Решение владельца 18.08.2026: раздел правят **глава СЗоВ и админы**.
Граница «глава отдела ≠ глобальный админ» — действующая семантика портала:
назначение главой ЗАМЕНЯЕТ базовую роль и режет периметр отделом. Поэтому
глава чужого отдела (например ОП) сюда не попадает, хотя роль у него admin.
Ровно так же устроен доступ к табло СЗоВ.
"""

SECTION_DEPARTMENT_CODE = 'szov'

_ADMIN_ROLES = ('super_admin', 'admin')


def normalize_role(role) -> str:
    return str(role or '').strip().lower()


def normalize_department_code(code) -> str:
    return str(code or '').strip().lower()


def _field(user, *names):
    """Пользователь приходит и как dict (из БД), и как объект — берём первое,
    что есть. Имена дублируются в snake_case и camelCase, потому что фронт и
    бэкенд отдают разные варианты одного и того же поля."""
    if user is None:
        return None
    for name in names:
        if isinstance(user, dict):
            if name in user and user[name] not in (None, ''):
                return user[name]
        else:
            value = getattr(user, name, None)
            if value not in (None, ''):
                return value
    return None


def is_department_head(user) -> bool:
    """Глава отдела — это назначение, а не роль: у человека остаётся его
    базовая роль (часто admin), но появляется отдел, которым он руководит."""
    flag = _field(user, 'is_department_head', 'isDepartmentHead')
    if flag is True:
        return True
    if isinstance(flag, str) and flag.strip().lower() in ('1', 'true', 'yes', 'да'):
        return True
    # Признак берётся ИЛИ из флага, ИЛИ из ссылки на возглавляемый отдел:
    # разные источники (БД, фронт, кэш) отдают разный набор полей, и явный
    # False в одном из них не должен перебивать заполненный id в другом.
    head_of = _field(user, 'head_of_department_id', 'headOfDepartmentId',
                     'department_head_id', 'departmentHeadId')
    return head_of is not None


def user_department_code(user) -> str:
    return normalize_department_code(
        _field(user, 'department_code', 'departmentCode', 'department')
    )


def is_global_admin(user) -> bool:
    """Глобальный админ = админская роль БЕЗ назначения главой отдела."""
    role = normalize_role(_field(user, 'role'))
    if role == 'super_admin':
        return True
    return role == 'admin' and not is_department_head(user)


def is_szov_head(user) -> bool:
    return is_department_head(user) and user_department_code(user) == SECTION_DEPARTMENT_CODE


def can_view_section(user) -> bool:
    """Кто видит раздел: глава СЗоВ и глобальные админы."""
    return is_global_admin(user) or is_szov_head(user)


def can_manage_settings(user) -> bool:
    """Кто правит настройки и загружает новую версию агента.

    Совпадает с правом просмотра осознанно: раздел маленький и целиком
    настроечный, отдельного «смотрю, но не трогаю» уровня здесь нет. Если
    когда-нибудь понадобится — расходятся именно эти две функции.
    """
    return can_view_section(user)


def visible_department_code(user):
    """Какой отдел человек видит в отчёте.

    None = все отделы (глобальный админ). Глава СЗоВ видит только СЗоВ: это не
    ограничение ради ограничения, а та же граница периметра, что и везде.
    """
    if is_global_admin(user):
        return None
    if is_szov_head(user):
        return SECTION_DEPARTMENT_CODE
    return ''  # никого
