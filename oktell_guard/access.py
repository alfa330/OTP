"""Права раздела «Ограничитель Перезвона». Чистая логика: ни базы, ни Flask.

Модуль намеренно ничего не импортирует из database/flask — так его можно
дёргать в тестах напрямую (импорт database открывает пул к боевой базе, та же
причина, что в crm/access.py и wiki/__init__.py).

Решение владельца 18.08.2026: раздел правят **глава СЗоВ и админы**.
Решение владельца 31.08.2026: раздел ЧИТАЮТ ещё и **СВ СЗоВ**. Супервайзеру
нужно видеть, у кого агент не стоит, у кого молчит и кого сколько раз выкинуло;
общий порог, режим обкатки и версию exe он не трогает — эти три вещи действуют
на весь отдел сразу, а версия ещё и разъезжается по машинам автообновлением.
Отсюда can_view_section ШИРЕ can_manage_settings, чего раньше не было.

Граница «глава отдела ≠ глобальный админ» — действующая семантика портала:
назначение главой ЗАМЕНЯЕТ базовую роль и режет периметр отделом. Поэтому
глава чужого отдела (например ОП) сюда не попадает, хотя роль у него admin.
Ровно так же устроен доступ к табло СЗоВ.
"""

SECTION_DEPARTMENT_CODE = 'szov'

# Обе формы роли легальны: CHECK на users.role разрешает и 'sv', и 'supervisor',
# а normalize_role ниже, в отличие от src/utils/roles.js, их не сводит. Сравнение
# с одним литералом молча оставило бы часть СВ за 403, и симптом был бы не отличим
# от «право просто не выдали».
_SUPERVISOR_ROLES = ('sv', 'supervisor')


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


def is_szov_supervisor(user) -> bool:
    """СВ отдела СЗоВ. Сверяем по КОДУ отдела, а не по id: id засеян миграцией и
    в разных окружениях разный.

    Главу отдела здесь намеренно НЕ отсекаем, хотя соблазн есть: ровно так же
    устроены оба действующих образца этой ветки — _szov_wallboard_guard на
    бэкенде и canAccessSzovWallboardForUser на фронте. Отсеки — и бэкенд станет
    строже фронта, а это возвращает то самое «пункт меню виден, а раздел не
    открыт», из-за чего правка и понадобилась.
    """
    return (normalize_role(_field(user, 'role')) in _SUPERVISOR_ROLES
            and user_department_code(user) == SECTION_DEPARTMENT_CODE)


def can_view_section(user) -> bool:
    """Кто видит раздел: глобальные админы, глава СЗоВ и СВ СЗоВ."""
    return is_global_admin(user) or is_szov_head(user) or is_szov_supervisor(user)


def can_manage_settings(user) -> bool:
    """Кто правит настройки, пороги и загружает новую версию агента: глава СЗоВ
    и глобальные админы.

    Считается САМА, а не через can_view_section, как было до 31.08.2026: с
    приходом СВ просмотр стал шире правки. Именно то расхождение, которое
    прежний комментарий здесь и предсказывал.
    """
    return is_global_admin(user) or is_szov_head(user)


def visible_department_code(user):
    """Какой отдел показывать. Всегда СЗоВ — и админу, и главе отдела, и СВ.

    Раньше глобальный админ видел все отделы, и в списке оказывались люди,
    которых ограничитель вообще не касается. Это инструмент одного отдела:
    показывать в нём чужих — значит засорять список и путать отчёт.
    Понадобится другой отдел — это станет настройкой, а не расширением прав.

    Для СВ периметр здесь ШИРЕ его обычного: он видит операторов и выбросы
    всего отдела, а не только своих групп. Так же устроено табло СЗоВ, и фильтра
    по группе в запросах раздела нет вовсе — появится он отдельной задачей, а не
    попутно с выдачей права.
    """
    if can_view_section(user):
        return SECTION_DEPARTMENT_CODE
    return ''  # никого
