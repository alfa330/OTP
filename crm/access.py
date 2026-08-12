"""Права раздела «Обращения». Чистая логика: ни базы, ни Flask.

Модуль намеренно не импортирует ни database, ни flask — так его можно
импортировать в тестах напрямую (импорт database открывает пул к боевой базе,
см. wiki/__init__.py, там та же причина).

Роли берутся из ТЗ #29 («Оператор / Супервайзер / Руководитель») и ложатся на
роли портала так:

    оператор, стажёр, тренер   создаёт обращения, видит ТОЛЬКО свои
    супервайзер                 + обращения операторов своих групп
    глава отдела                + все обращения своего отдела
    глобальный админ            всё, плюс настройка очередей и тематик

Граница «глава отдела ≠ глобальный админ» — не новая выдумка, а действующая
семантика портала: назначение главой ЗАМЕНЯЕТ базовую роль и жёстко режет
периметр отделом (так же считают задачи, «Бот опозданий» и ИИ-оценка).
"""

# Роли, которые вообще существуют в портале.
_KNOWN_ROLES = ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee')

# ─── Кто пускается в раздел ───────────────────────────────────────────────────
#
# Раздел выкатывается не на всю компанию, а на СЗоВ: глава отдела, супервайзеры
# отдела, глобальные админы — и один оператор пилотом (решение владельца
# 2026-08-12). Пилот именно один человек, а не «роль оператор в СЗоВ»: смысл в
# том, чтобы обкатать механику на живых обращениях, не выдавая раздел сотне
# людей до того, как очереди настроены.
#
# Список id, а не флаг в БД, — сознательно: это временное состояние выката, и
# отдельная таблица под него переживёт сам пилот. Расширять его придётся
# правкой кода, что для выката правильно (видно в истории, проходит ревью).
SECTION_DEPARTMENT_CODE = 'szov'
PILOT_USER_IDS = frozenset({20})  # Хайрихан Шерзад Зуритдинулы, оператор СЗоВ

_ADMIN_ROLES = ('super_admin', 'admin')
_SUPERVISOR_ROLES = ('sv', 'supervisor')

# Видимость: чем шире, тем больше видно. Значения используются и в SQL-слое.
SCOPE_ALL = 'all'
SCOPE_DEPARTMENT = 'department'
SCOPE_GROUPS = 'groups'
SCOPE_OWN = 'own'


def normalize_role(role):
    value = str(role or '').strip().lower()
    return value if value in _KNOWN_ROLES else 'operator'


def is_department_head(ctx):
    return bool(ctx.get('headed_department_ids'))


def is_global_admin(ctx):
    """Глобальный админ — тот, кто не привязан к одному отделу.

    super_admin — всегда. admin — только пока он не назначен главой отдела:
    назначение главой заменяет базовую роль (иначе глава одного отдела читал бы
    обращения всех остальных).
    """
    role = normalize_role(ctx.get('role'))
    if role == 'super_admin':
        return True
    return role == 'admin' and not is_department_head(ctx)


def is_supervisor(ctx):
    return normalize_role(ctx.get('role')) in _SUPERVISOR_ROLES


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def can_open_section(ctx):
    """Пускать ли пользователя в раздел вообще.

    Проверяется на КАЖДОМ роуте, а не только в меню: спрятанный пункт — это не
    доступ, раздел открывается и прямым адресом ?view=crm_tickets.

    Границу отдела держим строго, как «Табло СЗоВ» и переписка ТЭЗ: главе и СВ
    чужого отдела обращения СЗоВ ни к чему.
    """
    if is_global_admin(ctx):
        return True
    if int(ctx.get('user_id') or 0) in PILOT_USER_IDS:
        return True
    if SECTION_DEPARTMENT_CODE in _codes(ctx.get('headed_department_codes')):
        return True
    own_code = str(ctx.get('department_code') or '').strip().lower()
    return is_supervisor(ctx) and own_code == SECTION_DEPARTMENT_CODE


def visibility_scope(ctx):
    """Насколько широкий список обращений положен пользователю."""
    if is_global_admin(ctx):
        return SCOPE_ALL
    if is_department_head(ctx):
        return SCOPE_DEPARTMENT
    if is_supervisor(ctx):
        return SCOPE_GROUPS
    return SCOPE_OWN


def can_manage_queues(ctx):
    """Заводить очереди, привязывать Telegram-группы, править тематики.

    Только глобальный админ: очередь — это адрес в чужой Telegram-группе, и
    ошибочная привязка отправит обращения не туда. Точка входа одна, как у
    закреплённого канала заявок в IT.
    """
    return is_global_admin(ctx)


def can_create_ticket(ctx):
    """Создавать обращения может каждый, кого пустили в раздел.

    Отдельной роли под это нет намеренно: смысл раздела в том, чтобы обращение
    заводил тот, у кого возник вопрос, а не «ответственный за обращения».
    """
    return can_open_section(ctx)


def is_author(ctx, ticket):
    author = ticket.get('created_by')
    return author is not None and int(author) == int(ctx.get('user_id') or 0)


def can_view_ticket(ctx, ticket):
    """Видит ли пользователь конкретное обращение.

    Дублирует условия SQL-фильтра списка (queries.visibility_sql) — но нужен
    отдельно: карточку открывают по прямой ссылке, минуя список.
    """
    if is_author(ctx, ticket):
        return True
    scope = visibility_scope(ctx)
    if scope == SCOPE_ALL:
        return True
    if scope == SCOPE_DEPARTMENT:
        dept = ticket.get('department_id')
        queue_dept = ticket.get('queue_department_id')
        headed = {int(x) for x in (ctx.get('headed_department_ids') or [])}
        return (dept is not None and int(dept) in headed) or \
               (queue_dept is not None and int(queue_dept) in headed)
    if scope == SCOPE_GROUPS:
        groups = {int(x) for x in (ctx.get('group_ids') or [])}
        author_groups = {int(x) for x in (ticket.get('author_group_ids') or [])}
        return bool(groups & author_groups)
    return False


def can_reply(ctx, ticket):
    """Писать в нить обращения (сообщение уходит в Telegram-группу).

    Автор — всегда: это его диалог. Остальные — только если обращение вообще в
    их периметре и они управленческого круга; посторонний зритель не должен
    писать от имени системы в чужую группу.
    """
    if not can_view_ticket(ctx, ticket):
        return False
    if ticket.get('status') in ('resolved', 'cancelled'):
        return False
    if is_author(ctx, ticket):
        return True
    return is_global_admin(ctx) or is_department_head(ctx) or is_supervisor(ctx)


def can_change_status(ctx, ticket):
    """Закрыть обращение или вернуть в работу.

    То же правило, что у ответа, но без запрета на закрытые: снять «решено»
    (вернуть в работу) — это как раз действие над закрытым обращением.
    """
    if not can_view_ticket(ctx, ticket):
        return False
    if is_author(ctx, ticket):
        return True
    return is_global_admin(ctx) or is_department_head(ctx) or is_supervisor(ctx)


def can_delete_ticket(ctx, ticket):
    """Удаление — только глобальный админ (ТЗ #29: у оператора «без удаления»)."""
    return can_view_ticket(ctx, ticket) and is_global_admin(ctx)


def capabilities(ctx):
    """Сводка для фронта: раздел рисует кнопки по ней, а не по роли.

    Одно место правды: правило меняется здесь, а не в трёх местах интерфейса.
    """
    return {
        'scope': visibility_scope(ctx),
        'can_open': can_open_section(ctx),
        'can_create': can_create_ticket(ctx),
        'can_manage_queues': can_manage_queues(ctx),
        'is_global_admin': is_global_admin(ctx),
        'is_department_head': is_department_head(ctx),
        'is_supervisor': is_supervisor(ctx),
    }
