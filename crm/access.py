"""Права раздела «Обращения». Чистая логика: ни базы, ни Flask.

Модуль намеренно не импортирует ни database, ни flask — так его можно
импортировать в тестах напрямую (импорт database открывает пул к боевой базе,
см. wiki/__init__.py, там та же причина).

Роли берутся из ТЗ #29 («Оператор / Супервайзер / Руководитель») и ложатся на
роли портала так:

    оператор, стажёр            создаёт обращения, отвечает, закрывает
    тренер                      то же (с 25.09.2026, задача #360)
    супервайзер                 то же
    глава отдела                то же
    глобальный админ            + настройка очередей и тематик, удаление

Список обращений при этом ОБЩИЙ: кого пустили в раздел, тот видит все
обращения. Так было не всегда — сначала каждый видел только свои, а СВ и глава
отдела чуть шире. Практика показала, что это ровно наоборот: по одному водителю
несколько сотрудников заводили несколько одинаковых обращений, потому что не
могли увидеть уже открытое (просьба СЗоВ 18.08.2026). Периметр держит вход в
раздел (can_open_section), а не второй фильтр внутри него.

С 05.11.2026 у раздела есть отделы-гости (ОП и фронт-офисы, см.
PARTNER_DEPARTMENT_SCENARIOS): они заводят одну тематику — «Сотрудничество», —
и видят обращения своего отдела плюс эту тематику всех отделов (SCOPE_PARTNER).

Граница «глава отдела ≠ глобальный админ» — не новая выдумка, а действующая
семантика портала: назначение главой ЗАМЕНЯЕТ базовую роль и жёстко режет
периметр отделом (так же считают задачи, «Бот опозданий» и ИИ-оценка).
"""

# schema — такой же чистый модуль (одни константы и DDL строками), поэтому
# правило «ни базы, ни Flask» импорт не нарушает.
from .schema import REVIEW_PENDING, REVIEW_RESOLVED

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
# Исключений по роли больше нет. До 25.09.2026 им был тренер («переписка с
# рабочими группами не его дело», решение 2026-08-12); снято по задаче #360 —
# тренер учит операторов работе в разделе и просил доступ сам. Проходит он,
# как все, своим отделом: тренер чужого отдела сюда не попадёт. Во фронте раздел
# вписан и в TRAINER_ALLOWED_VIEWS — без этого гард вида выкидывал бы тренера
# обратно в «Опросы».
SECTION_DEPARTMENT_CODE = 'szov'

# Отделы-гости: раздел открыт им ради одной работы — завести обращение своей
# тематики и дождаться ответа группы (владелец, 05.11.2026: «Сотрудничество» —
# отделу продаж и фронт-офисам, «чтобы они могли заполнять и отправлять»).
#
# Тематики СЗоВ (Sapar, посылки, офисы, термокороба, жалобы) гостю не
# показываются и сервером не принимаются: за ними стоят проверки по ИИН,
# реестру посылок и справочнику офисов, то есть данные водителей. Видит гость
# обращения своего отдела и обращения СВОИХ тематик во всех отделах, СЗоВ
# включительно (тот же владелец, тот же день: «ОП может видеть обращения,
# оставленные СЗоВ, так же и с фронт-офисами»): правило «кого пустили — видит
# всё, чтобы не заводить дубли» действует и здесь, но по тематике — одну
# компанию не заведут дважды, а ИИН и телефоны водителей из обращений СЗоВ
# других тематик к гостю не попадают. Очереди и выгрузка остаются у СЗоВ:
# очередь — адрес в рабочей группе на всю компанию.
#
# Роль внутри отдела-гостя значения не имеет, как и у СЗоВ: заводит тот, кому
# позвонили. Глава проходит по возглавляемому отделу. Зеркало пункта меню —
# CRM_PARTNER_DEPARTMENT_CODES в App.jsx; тест сверяет оба места.
PARTNER_DEPARTMENT_SCENARIOS = {
    'op': frozenset({'cooperation'}),
    'front_office': frozenset({'cooperation'}),
}

_ADMIN_ROLES = ('super_admin', 'admin')
_SUPERVISOR_ROLES = ('sv', 'supervisor')

# Видимость: чем шире, тем больше видно. Значения используются и в SQL-слое.
SCOPE_ALL = 'all'
# Отдел-гость: свой отдел (и возглавляемые) плюс свои тематики всех отделов.
SCOPE_PARTNER = 'partner'
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


def _own_code(ctx):
    return str(ctx.get('department_code') or '').strip().lower()


def is_section_home(ctx):
    """Сотрудник (любая роль) или глава отдела-хозяина раздела — СЗоВ."""
    if SECTION_DEPARTMENT_CODE in _codes(ctx.get('headed_department_codes')):
        return True
    return _own_code(ctx) == SECTION_DEPARTMENT_CODE


def _sees_whole_section(ctx):
    return is_global_admin(ctx) or is_section_home(ctx)


def partner_codes(ctx):
    """Отделы-гости, через которые человек входит в раздел: свой и возглавляемые."""
    codes = _codes(ctx.get('headed_department_codes')) | {_own_code(ctx)}
    return codes & set(PARTNER_DEPARTMENT_SCENARIOS)


def can_open_section(ctx):
    """Пускать ли пользователя в раздел вообще.

    Проверяется на КАЖДОМ роуте, а не только в меню: спрятанный пункт — это не
    доступ, раздел открывается и прямым адресом ?view=crm_tickets.

    Границу отдела держим строго, как «Табло СЗоВ» и переписка ТЭЗ: главе и СВ
    чужого отдела обращения СЗоВ ни к чему. Отдел-гость входит, но видит и
    заводит в разделе только своё (allowed_scenarios, visibility_scope).
    """
    return _sees_whole_section(ctx) or bool(partner_codes(ctx))


def allowed_scenarios(ctx):
    """Тематики, которые человек может заводить и проверять: frozenset ключей
    из crm/scenarios.py, или None — все (глобальный админ и СЗоВ).

    Гостю — тематики его отделов. Тот, кого в раздел не пустили, получает
    пустой набор: закрыто, а не «без ограничений».
    """
    if _sees_whole_section(ctx):
        return None
    allowed = set()
    for code in partner_codes(ctx):
        allowed |= PARTNER_DEPARTMENT_SCENARIOS[code]
    return frozenset(allowed)


def can_use_scenario(ctx, scenario_key):
    """Можно ли заводить обращение этой тематики и звать её проверки.

    Не только каталог: проверки по ИИН, реестру посылок и справочнику офисов
    отвечают данными водителей, и запрос к ним собирается руками в обход
    спрятанной в каталоге тематики.
    """
    allowed = allowed_scenarios(ctx)
    return allowed is None or str(scenario_key or '') in allowed


def can_use_entry(ctx, entry):
    """Вход в тематику (проверка по ИИН до выбора категории) — только тому, кому
    открыто ВСЁ, куда он ведёт: каждая категория и «документов нет».

    Вход с частью закрытых категорий проверял бы водителя по Sapar ради выбора,
    которого человеку всё равно не дадут.
    """
    allowed = allowed_scenarios(ctx)
    if allowed is None:
        return True
    keys = [key for key in list(entry.get('categories') or []) + [entry.get('no_documents')]
            if key]
    return bool(keys) and all(key in allowed for key in keys)


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
    доступ не у кого, а также супервайзера и тренера. Тренер с 25.09.2026 в
    раздел входит (задача #360), и QR ему по-прежнему не нужен — ровно как в
    «Вики», где он ходит без подтверждения давно: гейт стоит на рядовых.

    Незнакомая роль подпадает под гейт: normalize_role сводит её к 'operator',
    и это правильная сторона ошибки — закрыто, а не открыто.
    """
    if is_global_admin(ctx) or is_department_head(ctx):
        return False
    return normalize_role(ctx.get('role')) == 'operator'


def visibility_scope(ctx):
    """Насколько широкий список обращений положен пользователю.

    Глобальному админу и СЗоВ — всё. Смысл раздела в том, чтобы обращение по
    водителю было ОДНО: найти уже открытое можно только если оно видно, а
    невидимое коллеге обращение он заведёт заново.

    Отделу-гостю — SCOPE_PARTNER: обращения своего отдела (partner_department_ids)
    и обращения своих тематик во всех отделах. Обращения СЗоВ других тематик, с
    ИИН и телефонами водителей, — нет. Супервайзеру гостя свой отдел целиком
    нужен ещё и потому, что «С Яндексом» проверяет любой СВ отдела автора
    (can_review), и узкий периметр «свои группы» прятал бы от него его же задачу.

    Остальные периметры (отдел, свои группы, только своё) — для тех, кого в
    раздел не пустили: роуты их до сюда не доводят, но правило и SQL держат их
    честно.
    """
    if _sees_whole_section(ctx):
        return SCOPE_ALL
    if partner_codes(ctx):
        return SCOPE_PARTNER
    if is_department_head(ctx):
        return SCOPE_DEPARTMENT
    if is_supervisor(ctx):
        return SCOPE_GROUPS
    return SCOPE_OWN


def partner_department_ids(ctx):
    """Отделы, обращения которых гость видит целиком: свой и возглавляемые — и
    только отделы-гости.

    Свой — потому что оператор ОП ничего не возглавляет, а обращения коллег по
    отделу видеть должен. Только гостевые — потому что глава бывает и не из
    своего отдела, и нескольких отделов сразу: глава фронт-офисов, числящаяся
    в IT, или глава ОП, заодно возглавляющий Тез КЦ, иначе получили бы ВСЕ
    обращения IT или Тез КЦ любой тематики — Sapar с ИИН водителя в том числе
    (разбор правки 05.11.2026). Чужой отдел гостю открыт только тематикой.

    Возглавляемые — парами (id, код): load_access_context отдаёт их в одном
    порядке. Порядок результата — чтобы параметр SQL не зависел от порядка, в
    котором отделы пришли из базы.
    """
    partners = set(PARTNER_DEPARTMENT_SCENARIOS)
    ids = set()
    for department_id, code in zip(ctx.get('headed_department_ids') or [],
                                   ctx.get('headed_department_codes') or []):
        if str(code or '').strip().lower() in partners:
            ids.add(int(department_id))
    if _own_code(ctx) in partners and ctx.get('department_id') is not None:
        ids.add(int(ctx['department_id']))
    return sorted(ids)


def can_manage_queues(ctx):
    """Заводить очереди, привязывать Telegram-группы, править тематики.

    Глобальный админ, глава отдела и супервайзер (просьба владельца
    21.08.2026). До этого — только глобальный админ, из соображения «очередь это
    адрес в чужой рабочей группе, ошибочная привязка отправит обращения не
    туда». Соображение никуда не делось, просто владелец решил, что ждать
    админа ради привязки чата дороже: очередями пользуется СЗоВ, а заводит их
    сейчас не СЗоВ.

    Глава и СВ — только СЗоВ, отдела-хозяина: отделу-гостю раздел открыт ради
    его тематики (05.11.2026), а очередь одна на компанию, не на отдел.
    Отдельного периметра «чьи очереди» нет и быть не может.

    Глава отдела здесь не по просьбе, а по здравому смыслу: иначе супервайзер
    может то, чего не может его руководитель.
    """
    if is_global_admin(ctx):
        return True
    return is_section_home(ctx) and (is_department_head(ctx) or is_supervisor(ctx))


def can_export(ctx):
    """Выгрузить обращения в Excel за период (владелец, 30.09.2026).

    Тем же, кто настраивает очереди: глобальному админу, главе и супервайзеру
    СЗоВ. Выгрузка — это все обращения периода с ИИН и телефонами водителей
    одним файлом; оператору в разделе они видны по одному, а файл уносится с
    рабочего места целиком. Отделу-гостю (ОП, фронт-офисы) выгрузку не открыли:
    просили завести и отправить «Сотрудничество», не отчёт по нему.
    """
    return can_manage_queues(ctx)


def can_create_ticket(ctx):
    """Создавать обращения может каждый, кого пустили в раздел.

    Отдельной роли под это нет намеренно: смысл раздела в том, чтобы обращение
    заводил тот, у кого возник вопрос, а не «ответственный за обращения».
    """
    return can_open_section(ctx)


def is_author(ctx, ticket):
    author = ticket.get('created_by')
    return author is not None and int(author) == int(ctx.get('user_id') or 0)


def _belongs_to(ticket, department_ids):
    """Обращение заведено в одном из отделов — или пришло в очередь одного из них."""
    departments = {int(x) for x in (department_ids or [])}
    dept = ticket.get('department_id')
    queue_dept = ticket.get('queue_department_id')
    return (dept is not None and int(dept) in departments) or \
           (queue_dept is not None and int(queue_dept) in departments)


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
    if scope == SCOPE_PARTNER:
        if ticket.get('scenario_key') in allowed_scenarios(ctx):
            return True
        return _belongs_to(ticket, partner_department_ids(ctx))
    if scope == SCOPE_DEPARTMENT:
        return _belongs_to(ticket, ctx.get('headed_department_ids'))
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

    В обращение на проверке у супервайзера писать некуда: в группу оно ещё не
    ушло, и нити в Telegram у него нет.
    """
    if not can_view_ticket(ctx, ticket):
        return False
    if ticket.get('status') in ('resolved', 'cancelled'):
        return False
    if ticket.get('review_state') == REVIEW_PENDING:
        return False
    return True


def can_change_status(ctx, ticket):
    """Закрыть обращение или вернуть в работу.

    То же правило, что у ответа, но без запрета на закрытые: снять «решено»
    (вернуть в работу) — это как раз действие над закрытым обращением.

    Проверка супервайзером эти кнопки снимает. Пока обращение её ждёт,
    «Вопрос решён» закрыл бы его в обход проверки — решает проверяющий
    (can_review). А решённое им в группу не уходило вовсе: «Вернуть в работу»
    сделало бы из него открытое обращение, которое никто не получал и на
    которое некому ответить.
    """
    if ticket.get('review_state') in (REVIEW_PENDING, REVIEW_RESOLVED):
        return False
    return can_view_ticket(ctx, ticket)


def can_review(ctx, ticket):
    """Решить по обращению на проверке: «Решено» с итогом или «Отправить в
    группу» (возврат задачи #297; владелец, 07.10.2026 — как у жалоб на Яндекс).

    Проверяет любой супервайзер отдела автора (владелец, 09.10.2026), а когда
    других супервайзеров в отделе нет — глава отдела: по этому правилу
    считаются счётчик раздела и колокол (queries.reviewer_sql). Нажать может и
    тот, кто стоит над ними, — глава отдела автора и глобальный админ. То же
    деление у жалоб (complaints/access.can_review).

    Оператор не проверяет никогда — ни своё, ни чужое. Супервайзер своё
    обращение тоже не проверяет: его решает коллега.
    """
    if ticket.get('review_state') != REVIEW_PENDING:
        return False
    if not can_view_ticket(ctx, ticket):
        return False
    if is_global_admin(ctx):
        return True
    department = ticket.get('department_id')
    headed = {int(x) for x in (ctx.get('headed_department_ids') or [])}
    if department is not None and int(department) in headed:
        return True
    if is_supervisor(ctx) and not is_author(ctx, ticket):
        own = ctx.get('department_id')
        return department is not None and own is not None and int(department) == int(own)
    return False


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
        'can_export': can_export(ctx),
        'is_global_admin': is_global_admin(ctx),
        'is_department_head': is_department_head(ctx),
        'is_supervisor': is_supervisor(ctx),
        'requires_qr': requires_sensitive_qr(ctx),
    }
