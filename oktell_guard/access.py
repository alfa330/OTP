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

Решение владельца 07.09.2026: пункт «Скачать Oktell» стоит в меню у **каждого
оператора СЗоВ**, как «Скачать iCore Phone» у ОП и Тез КЦ. Поэтому появился
третий, самый широкий круг — can_download_agent. Порядок такой:
can_download_agent ⊇ can_view_section ⊇ can_manage_settings (с 05.10.2026 —
не весь раздел, а его часть СЗоВ: см. ниже про отдел продаж).

22.09.2026 пункт из меню убран (решение владельца): им не пользуются, агент
ставится на машины централизованно — MSI через групповую политику, — а не
скачиванием из меню. Ручка /download и её круг оставлены как были: файл
по-прежнему качают из самого раздела (кнопка «Скачать агента»), а сужать
круг ради снятого пункта незачем — см. can_download_agent.

Граница «глава отдела ≠ глобальный админ» — действующая семантика портала:
назначение главой ЗАМЕНЯЕТ базовую роль и режет периметр отделом. Поэтому
глава чужого отдела сюда не попадает, хотя роль у него admin. Ровно так же
устроен доступ к табло СЗоВ.

ТЗ от 05.10.2026: у раздела появился второй отдел — отдел продаж. Там нет
Oktell и агента: «выбросы» в «Офлайн» делает сам iCORE Phone (oktell_guard/
phone.py), раздел показывает, кого сколько раз выкинуло, и держит правило.
Отделы разведены, а не смешаны: глава и СВ ОП видят ТОЛЬКО часть ОП (глава
правит её правило, СВ читает), глава и СВ СЗоВ — только СЗоВ, как и раньше;
обе части видит лишь глобальный админ. Всё про Oktell — агент, общий порог,
персональные правила, версии — остаётся за кругом СЗоВ: SECTION_DEPARTMENT_CODE
по-прежнему 'szov' (на нём же держится канал Oktell в «Новостях»).
"""

from .phone import PHONE_DEPARTMENT_CODE

SECTION_DEPARTMENT_CODE = 'szov'

# Отделы раздела в порядке показа: первым идёт тот, что был всегда, — тогда
# запрос без ?department= у главы и СВ СЗоВ отдаёт ровно то же, что до правки.
SECTION_DEPARTMENTS = (
    (SECTION_DEPARTMENT_CODE, 'СЗоВ'),
    (PHONE_DEPARTMENT_CODE, 'Отдел продаж'),
)
SECTION_DEPARTMENT_CODES = tuple(code for code, _name in SECTION_DEPARTMENTS)

# Обе формы роли легальны: CHECK на users.role разрешает и 'sv', и 'supervisor',
# а normalize_role ниже, в отличие от src/utils/roles.js, их не сводит. Сравнение
# с одним литералом молча оставило бы часть СВ за 403, и симптом был бы не отличим
# от «право просто не выдали».
_SUPERVISOR_ROLES = ('sv', 'supervisor')

# Кого ограничитель касается — те же роли, что показывает вкладка «Сотрудники»
# (EMPLOYEE_ROLES в queries.py). Держать здесь свой, более узкий список значило
# бы «в разделе человек есть, а скачать программу он не может».
AGENT_USER_ROLES = ('operator', 'trainee')


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


def headed_department_codes(user) -> tuple:
    """Коды ВСЕХ отделов, которыми человек руководит.

    access_context отдаёт их списком: глава двух отделов раньше получал один из
    них наугад (LIMIT 1 без порядка), и раздел то открывался, то нет. Без списка
    (контекст с фронта, старые вызовы) — отдел главы из department_code, его
    access_context уже подменил возглавляемым.

    «Без списка» — это когда поля в контексте НЕТ ВОВСЕ. Пустой список — ответ
    базы «известных нам отделов он не возглавляет» (глава отдела с пустым кодом):
    подставь тут его собственный отдел — и СВ или оператор ОП, назначенный
    главой такого отдела, стал бы «главой ОП» и правил бы правило всего отдела.
    """
    if not is_department_head(user):
        return ()
    names = ('headed_department_codes', 'headedDepartmentCodes')
    has_list = any((name in user) if isinstance(user, dict) else hasattr(user, name)
                   for name in names)
    raw = _field(user, *names)
    if isinstance(raw, str):
        raw = raw.replace(';', ',').split(',')
    codes = []
    for code in (raw or ()):
        code = normalize_department_code(code)
        if code and code not in codes:
            codes.append(code)
    if not codes and not has_list:
        own = user_department_code(user)
        if own:
            codes.append(own)
    return tuple(codes)


def is_szov_head(user) -> bool:
    return SECTION_DEPARTMENT_CODE in headed_department_codes(user)


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


def is_op_head(user) -> bool:
    """Глава отдела продаж: правит правило автоофлайна своего отдела."""
    return PHONE_DEPARTMENT_CODE in headed_department_codes(user)


def is_op_supervisor(user) -> bool:
    """СВ отдела продаж: читает часть ОП целиком, как СВ СЗоВ — свою.

    Периметр — весь отдел, а не его группы (решение по ТЗ 05.10.2026): выбросы
    ЯР и Потока СВ разбирает вместе, а фильтра по группе в разделе нет и у СЗоВ.
    """
    return (normalize_role(_field(user, 'role')) in _SUPERVISOR_ROLES
            and user_department_code(user) == PHONE_DEPARTMENT_CODE)


def visible_department_codes(user) -> list:
    """Части раздела, которые человеку открыты, в порядке SECTION_DEPARTMENTS.

    Глобальный админ видит обе. Глава/СВ СЗоВ — только СЗоВ, глава/СВ ОП —
    только ОП: данные соседнего отдела (операторы, номера, выбросы) им не
    показываются ни во вкладках, ни прямым запросом с ?department=.
    """
    if is_global_admin(user):
        return list(SECTION_DEPARTMENT_CODES)
    visible = []
    if is_szov_head(user) or is_szov_supervisor(user):
        visible.append(SECTION_DEPARTMENT_CODE)
    if is_op_head(user) or is_op_supervisor(user):
        visible.append(PHONE_DEPARTMENT_CODE)
    return visible


def can_view_department(user, code) -> bool:
    return normalize_department_code(code) in visible_department_codes(user)


def can_view_section(user) -> bool:
    """Кто видит раздел: глобальные админы, глава и СВ СЗоВ, глава и СВ ОП —
    каждый свою часть (visible_department_codes)."""
    return bool(visible_department_codes(user))


def can_manage_phone_settings(user) -> bool:
    """Кто правит правило автоофлайна ОП: глава ОП и глобальные админы.

    Отдельный предикат, а не can_manage_settings: тот пускает к общему порогу
    Oktell и версии агента, и главе ОП там делать нечего — как и главе СЗоВ
    в правиле отдела продаж.
    """
    return is_global_admin(user) or is_op_head(user)


def is_szov_operator(user) -> bool:
    """Оператор (или стажёр) СЗоВ — тот, кого ограничитель и касается.

    Тот же круг, что показывает вкладка «Сотрудники» (EMPLOYEE_ROLES в
    queries.py): сравнивать надо с тем же списком ролей, иначе в разделе человек
    есть, а скачать программу он не может.
    """
    return (normalize_role(_field(user, 'role')) in AGENT_USER_ROLES
            and user_department_code(user) == SECTION_DEPARTMENT_CODE)


def can_download_agent(user) -> bool:
    """Кто может скачать сам exe.

    Шире, чем раздел. С 07.09 по 22.09.2026 пункт «Скачать Oktell» стоял в меню
    у КАЖДОГО оператора СЗоВ, а не только у тех, кто видит настройки; пункт
    убран (агент ставится MSI через групповую политику), круг оставлен. Сужать
    его ради снятого пункта незачем: ничего секретного этим не открывается —
    сам файл и так отдаёт публичная /version, она нужна автообновлению на
    каждой машине, — а кнопка «Скачать агента» в разделе ходит сюда же.

    С 05.10.2026 раздел видят и глава/СВ ОП, но агент Oktell им не нужен (в ОП
    нет Oktell, у них iCORE Phone), поэтому круг считается от части СЗоВ, а не
    от раздела целиком. Порядок кругов теперь такой:
    can_download_agent ⊇ «видит СЗоВ» ⊇ can_manage_settings.
    """
    return can_view_department(user, SECTION_DEPARTMENT_CODE) or is_szov_operator(user)


def can_manage_settings(user) -> bool:
    """Кто правит настройки, пороги и загружает новую версию агента: глава СЗоВ
    и глобальные админы.

    Считается САМА, а не через can_view_section, как было до 31.08.2026: с
    приходом СВ просмотр стал шире правки. Именно то расхождение, которое
    прежний комментарий здесь и предсказывал.
    """
    return is_global_admin(user) or is_szov_head(user)


def visible_department_code(user):
    """Какой отдел показывать в части Oktell. Всегда СЗоВ — и админу, и главе
    отдела, и СВ; тем, кому открыта только часть ОП, — никого.

    Часть ОП сюда не входит: какой отдел открыт в запросе, решают
    visible_department_codes и ?department= (routes.requested_department).

    Раньше глобальный админ видел все отделы, и в списке оказывались люди,
    которых ограничитель вообще не касается. Это инструмент одного отдела:
    показывать в нём чужих — значит засорять список и путать отчёт.
    Понадобится другой отдел — это станет настройкой, а не расширением прав.

    Для СВ периметр здесь ШИРЕ его обычного: он видит операторов и выбросы
    всего отдела, а не только своих групп. Так же устроено табло СЗоВ, и фильтра
    по группе в запросах раздела нет вовсе — появится он отдельной задачей, а не
    попутно с выдачей права.
    """
    if can_view_department(user, SECTION_DEPARTMENT_CODE):
        return SECTION_DEPARTMENT_CODE
    return ''  # никого
