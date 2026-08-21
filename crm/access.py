"""Права раздела «Обращения». Чистая логика: ни базы, ни Flask.

Модуль намеренно не импортирует ни database, ни flask — так его можно
импортировать в тестах напрямую (импорт database открывает пул к боевой базе,
см. wiki/__init__.py, там та же причина).

Роли берутся из ТЗ #29 («Оператор / Супервайзер / Руководитель») и ложатся на
роли портала так:

    оператор, стажёр            создаёт обращения, отвечает, закрывает
    супервайзер                 то же
    глава отдела                то же
    глобальный админ            + настройка очередей и тематик, удаление
    тренер                      в раздел не пускается вовсе

Список обращений при этом ОБЩИЙ: кого пустили в раздел, тот видит все
обращения. Так было не всегда — сначала каждый видел только свои, а СВ и глава
отдела чуть шире. Практика показала, что это ровно наоборот: по одному водителю
несколько сотрудников заводили несколько одинаковых обращений, потому что не
могли увидеть уже открытое (просьба СЗоВ 18.08.2026). Периметр держит вход в
раздел (can_open_section), а не второй фильтр внутри него.

Граница «глава отдела ≠ глобальный админ» — не новая выдумка, а действующая
семантика портала: назначение главой ЗАМЕНЯЕТ базовую роль и жёстко режет
периметр отделом (так же считают задачи, «Бот опозданий» и ИИ-оценка).
"""

# Роли, которые вообще существуют в портале.
_KNOWN_ROLES = ('super_admin', 'admin', 'sv', 'supervisor', 'trainer', 'operator', 'trainee')

# ─── Кто пускается в раздел ───────────────────────────────────────────────────
#
# Весь отдел СЗоВ и никто больше (решение владельца 19.08.2026). Пилот на одном
# операторе закончился: очереди настроены, механика обкатана, и держать список
# id больше незачем — он ушёл вместе с самим понятием пилота.
#
# Граница отдела строгая, роль внутри отдела значения не имеет: обращение
# заводит тот, у кого возник вопрос, а не «ответственный за обращения». Глава
# отдела попадает сюда по своему отделу, глобальный админ — потому что он вне
# отделов вовсе.
#
# Единственное исключение — тренер: он видит «всё» в других разделах, но
# переписка с рабочими группами не его дело (то же решение, что 2026-08-12).
# Оно же закреплено в TRAINER_ALLOWED_VIEWS во фронте.
SECTION_DEPARTMENT_CODE = 'szov'
_SECTION_EXCLUDED_ROLES = ('trainer',)

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
    if normalize_role(ctx.get('role')) in _SECTION_EXCLUDED_ROLES:
        return False
    if SECTION_DEPARTMENT_CODE in _codes(ctx.get('headed_department_codes')):
        return True
    own_code = str(ctx.get('department_code') or '').strip().lower()
    return own_code == SECTION_DEPARTMENT_CODE


def requires_sensitive_qr(ctx):
    """Нужно ли подтвердить сессию QR-кодом, прежде чем раздел откроется.

    Оператору — да. «Обращения» — это переписка с рабочими группами по живым
    водителям: телефоны, адреса, суммы. Открывать её должен человек за своим
    рабочим местом и с ведома старшего, а не любая забытая открытой сессия.

    Ключ тот же, что у «Моих оценок» (bot_schedule2: sensitive-access): QR
    генерирует сам оператор, подтверждает админ или супервайзер, доступ живёт
    до конца ЭТОЙ сессии. Своего второго ключа раздел не заводит намеренно —
    два разных QR на один и тот же экран человек не различит.

    Кого гейт не касается: главы отдела и глобального админа — им подтверждать
    доступ не у кого, а также супервайзера и тренера (тренера сюда и так не
    пускает can_open_section).

    Незнакомая роль подпадает под гейт: normalize_role сводит её к 'operator',
    и это правильная сторона ошибки — закрыто, а не открыто.
    """
    if is_global_admin(ctx) or is_department_head(ctx):
        return False
    return normalize_role(ctx.get('role')) == 'operator'


def visibility_scope(ctx):
    """Насколько широкий список обращений положен пользователю.

    Пустили в раздел — видно всё. Смысл раздела в том, чтобы обращение по
    водителю было ОДНО: найти уже открытое можно только если оно видно, а
    невидимое коллеге обращение он заведёт заново.

    Более узкие периметры (отдел, свои группы, только своё) никуда не делись —
    они остаются в SQL-слое и в can_view_ticket и понадобятся, когда раздел
    выйдет за пределы одного отдела. Сейчас же вход в раздел и есть периметр.
    """
    if can_open_section(ctx):
        return SCOPE_ALL
    if is_department_head(ctx):
        return SCOPE_DEPARTMENT
    if is_supervisor(ctx):
        return SCOPE_GROUPS
    return SCOPE_OWN


def can_manage_queues(ctx):
    """Заводить очереди, привязывать Telegram-группы, править тематики.

    Глобальный админ, глава отдела и супервайзер (просьба владельца
    21.08.2026). До этого — только глобальный админ, из соображения «очередь это
    адрес в чужой рабочей группе, ошибочная привязка отправит обращения не
    туда». Соображение никуда не делось, просто владелец решил, что ждать
    админа ради привязки чата дороже: очередями пользуется СЗоВ, а заводит их
    сейчас не СЗоВ.

    Границей остаётся сам раздел: `can_manage_queues` проверяется ПОСЛЕ
    `can_open_section` (декоратор в routes.py), а он открыт узкому кругу.
    Отдельного периметра «чьи очереди» нет и быть не может — очередь одна на
    компанию, а не на отдел.

    Глава отдела здесь не по просьбе, а по здравому смыслу: иначе супервайзер
    может то, чего не может его руководитель.
    """
    return is_global_admin(ctx) or is_department_head(ctx) or is_supervisor(ctx)


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

    Видит обращение — может и продолжить по нему работу. Это прямое требование
    СЗоВ: сотрудник должен подхватить уже открытое обращение коллеги, а не
    заводить второе такое же. Читатель, которому нельзя ответить, дубль как раз
    и создаёт.

    Закрытое обращение не отвечают: сначала вернуть в работу.
    """
    if not can_view_ticket(ctx, ticket):
        return False
    if ticket.get('status') in ('resolved', 'cancelled'):
        return False
    return True


def can_change_status(ctx, ticket):
    """Закрыть обращение или вернуть в работу.

    То же правило, что у ответа, но без запрета на закрытые: снять «решено»
    (вернуть в работу) — это как раз действие над закрытым обращением.
    """
    return can_view_ticket(ctx, ticket)


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
        'requires_qr': requires_sensitive_qr(ctx),
    }
