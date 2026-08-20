"""Права раздела «Провайдер ЭДО». Чистая логика: ни базы, ни Flask.

Модуль намеренно ничего не импортирует из database/flask — так его можно дёргать
в тестах напрямую (импорт database открывает пул к боевой базе; та же причина,
что в oktell_guard/access.py и crm/access.py).

Кому открыт раздел: **глобальные админы и глава СЗоВ**. Постановщик задачи #176 —
Жагалтаева Салтанат Маратовна, у неё роль admin без назначения главой, то есть
она проходит первым правилом.

Почему НЕ выдан супервайзерам, хотя у табло СЗоВ они есть: выгрузка отдаёт
персональные данные водителей — ФИО и телефоны десятков тысяч человек, и
единичной строкой, а целым файлом. Круг тех, кто может унести такой файл, должен
быть уже круга тех, кто смотрит нагрузку на линию. Понадобится шире — это
отдельное решение владельца, а не «раз уж похоже на табло».

Граница «глава отдела ≠ глобальный админ» — действующая семантика портала:
назначение главой ЗАМЕНЯЕТ базовую роль и режет периметр отделом. Поэтому глава
чужого отдела (например ОП) сюда не попадает, хотя роль у него admin.
"""

SECTION_DEPARTMENT_CODE = 'szov'


def normalize_role(role) -> str:
    return str(role or '').strip().lower()


def normalize_department_code(code) -> str:
    return str(code or '').strip().lower()


def _field(user, *names):
    """Пользователь приходит и как dict (из БД), и как объект — берём первое, что
    есть. Имена дублируются в snake_case и camelCase, потому что фронт и бэкенд
    отдают разные варианты одного и того же поля."""
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
    """Глава отдела — это назначение, а не роль: у человека остаётся его базовая
    роль (часто admin), но появляется отдел, которым он руководит."""
    flag = _field(user, 'is_department_head', 'isDepartmentHead')
    if flag is True:
        return True
    if isinstance(flag, str) and flag.strip().lower() in ('1', 'true', 'yes', 'да'):
        return True
    # Признак берётся ИЛИ из флага, ИЛИ из ссылки на возглавляемый отдел: разные
    # источники (БД, фронт, кэш) отдают разный набор полей, и явный False в одном
    # из них не должен перебивать заполненный код отдела в другом.
    if normalize_department_code(_field(user, 'headed_department_code', 'headedDepartmentCode')):
        return True
    head_of = _field(user, 'head_of_department_id', 'headOfDepartmentId',
                     'department_head_id', 'departmentHeadId')
    return head_of is not None


def headed_department_code(user) -> str:
    return normalize_department_code(
        _field(user, 'headed_department_code', 'headedDepartmentCode')
    )


def is_global_admin(user) -> bool:
    """Глобальный админ = админская роль БЕЗ назначения главой отдела."""
    role = normalize_role(_field(user, 'role'))
    if role == 'super_admin':
        return True
    return role == 'admin' and not is_department_head(user)


def is_section_head(user) -> bool:
    return is_department_head(user) and headed_department_code(user) == SECTION_DEPARTMENT_CODE


def can_view_section(user) -> bool:
    """Кто открывает раздел и скачивает готовые файлы."""
    return is_global_admin(user) or is_section_head(user)


def can_run_job(user) -> bool:
    """Кто запускает выгрузку.

    Совпадает с правом просмотра: раздел целиком про «загрузить и получить», и
    отдельного «смотрю чужие выгрузки, но своих не делаю» уровня здесь нет.
    """
    return can_view_section(user)


def can_manage_session(user) -> bool:
    """Кто обновляет сессию кабинета Fleet.

    Это самое опасное действие раздела: куки дают доступ ко всем 86 диспетчерским
    от имени живого сотрудника. Поэтому только глобальные админы — глава отдела
    сюда не входит намеренно.
    """
    return is_global_admin(user)
