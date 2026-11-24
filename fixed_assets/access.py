"""Права раздела «Основные средства». Чистая логика: ни базы, ни Flask.

Контекст зрителя (queries.load_context):
    user_id, name, is_super            кто это в портале;
    department_id, department_code     его отдел;
    headed_department_ids / _codes     отделы, которые он возглавляет;
    member_roles                       роли из «Участников» раздела (поимённые добавки);
    has_assets, involved               числится ли за ним ОС, участвует ли он в запросах.

Роли ТЗ (раздел 2) ложатся на портал так:

    Бухгалтерия              отдел «Бухгалтерия» (и его глава) — ведёт учёт: принимает ОС,
                             отмечает оригиналы, закрывает и отклоняет запросы, видит всё
    HR                       отдел HR — запускает передачу при увольнении, участвует в
                             перемещениях, видит всю историю
    IT-специалист            отдел IT — видит реестр («наличие на складе ОС и МОЛ»),
                             проверяет оргтехнику перед списанием, заводит «Новое ОС»
    Руководитель аналитики   глава «Отдела аналитики» — видит всё, только чтение
    Руководитель отдела      глава любого отдела — ОС и запросы своего отдела, проверка
                             мебели и прочего перед списанием
    МОЛ                      любой сотрудник — ТОЛЬКО СМОТРИТ: ОС на своё имя и историю, в
                             которой он сам участвует. Запросы он не заводит и не отклоняет,
                             скан акта не прикладывает (решение владельца 17.11.2026); акт
                             он подписывает на бумаге, запрос ведёт тот, кто его завёл
    суперадмин               всё, что у бухгалтерии, плюс «Участники»

Права складываются: руководитель отдела, за которым числится ОС, остаётся руководителем.

Видимость повторяет SQL (queries.asset_scope_sql, queries.request_scope_sql) — правило
меняется в обоих местах, расхождение ловит tests/test_fixed_assets_postgres.py.
"""

from . import rules

ACCOUNTING_DEPARTMENT_CODE = 'accounting'
HR_DEPARTMENT_CODE = 'hr'
IT_DEPARTMENT_CODE = 'it'
# «Руководитель аналитики» из ТЗ — глава этого отдела; рядовой аналитик ролью не становится.
ANALYTICS_DEPARTMENT_CODE = 'analytik'

TAB_ORDER = ('my', 'registry', 'requests', 'dismissal', 'history', 'settings')

# Пилот: пока флаг стоит, раздел открыт только суперадминам — и пункт меню (флаг профиля,
# queries.section_open_for), и каждая ручка; увольнение при этом раздел не держит
# (bot_schedule2: _fixed_assets_dismissal_block). Снимает его владелец, когда раздел
# проверен; на стенде флаг снят переменной FIXED_ASSETS_PILOT=off.
PILOT_SUPER_ADMIN_ONLY = True

LEVEL_FULL = 'full'       # всё: стоимость, история, акты
LEVEL_SCOPED = 'scoped'   # ОС своего отдела (руководитель): карточка, история, акты; без стоимости
LEVEL_OWN = 'own'         # своё ОС (МОЛ): карточка и только то, в чём он сам участвовал
LEVEL_BRIEF = 'brief'     # IT: что это, где и за кем — «наличие на складе ОС и МОЛ»


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _in_department(ctx, code):
    """Человек в этом отделе — своим членством или как его глава."""
    if str(ctx.get('department_code') or '').strip().lower() == code:
        return True
    return code in _codes(ctx.get('headed_department_codes'))


def _member(ctx, role):
    return role in (ctx.get('member_roles') or ())


def is_super(ctx):
    return bool(ctx and ctx.get('is_super'))


def is_accounting(ctx):
    return _member(ctx, rules.MEMBER_ACCOUNTING) or _in_department(ctx, ACCOUNTING_DEPARTMENT_CODE)


def is_hr(ctx):
    return _member(ctx, rules.MEMBER_HR) or _in_department(ctx, HR_DEPARTMENT_CODE)


def is_it(ctx):
    return _member(ctx, rules.MEMBER_IT) or _in_department(ctx, IT_DEPARTMENT_CODE)


def is_observer(ctx):
    return _member(ctx, rules.MEMBER_OBSERVER) or ANALYTICS_DEPARTMENT_CODE in _codes(
        ctx.get('headed_department_codes'))


def headed_ids(ctx):
    return {int(value) for value in (ctx.get('headed_department_ids') or [])}


def is_head(ctx):
    return bool(headed_ids(ctx))


def is_keeper(ctx):
    """Ведёт учёт: бухгалтерия и суперадмин."""
    return is_super(ctx) or is_accounting(ctx)


def sees_all(ctx):
    """«Бухгалтерия, руководитель аналитики, HR видят всё» (ТЗ, «Доступ к истории»)."""
    return is_super(ctx) or is_accounting(ctx) or is_hr(ctx) or is_observer(ctx)


def sees_registry(ctx):
    """Реестр целиком: кто видит всё, и IT («наличие на складе ОС и МОЛ»)."""
    return sees_all(ctx) or is_it(ctx)


def can_open(ctx):
    """Пускать ли в раздел. Проверяется на каждой ручке: спрятанный пункт меню — не доступ."""
    if not ctx:
        return False
    if PILOT_SUPER_ADMIN_ONLY:
        return is_super(ctx)
    return bool(sees_registry(ctx) or is_head(ctx) or ctx.get('has_assets') or ctx.get('involved'))


def can_manage_members(ctx):
    return is_super(ctx)


def can_edit_settings(ctx):
    return is_keeper(ctx)


def can_run_dismissal(ctx):
    """«HR запускает передачу» (ТЗ, процесс 4)."""
    return is_super(ctx) or is_hr(ctx)


def works_with_requests(ctx):
    """Ведёт запросы или следит за ними по роли: бухгалтерия, HR, IT, руководитель аналитики,
    руководитель отдела. У сотрудника с ОС (МОЛ) такой работы нет — он только смотрит."""
    return sees_registry(ctx) or is_head(ctx)


def tabs(ctx):
    result = []
    if ctx.get('has_assets') or not works_with_requests(ctx):
        result.append('my')
    if works_with_requests(ctx):
        result += ['registry', 'requests']
    if can_run_dismissal(ctx):
        result.append('dismissal')
    # История есть у каждого: кто видит всё — общая, остальным — та, где они участвуют.
    result.append('history')
    if can_edit_settings(ctx):
        result.append('settings')
    return [tab for tab in TAB_ORDER if tab in result]


# ─── Основные средства ───────────────────────────────────────────────────────

def asset_level(ctx, asset):
    """Что зритель видит в карточке ОС: full / scoped / brief / None."""
    if not ctx or not asset:
        return None
    if sees_all(ctx):
        return LEVEL_FULL
    if asset.get('department_id') and int(asset['department_id']) in headed_ids(ctx):
        return LEVEL_SCOPED
    if asset.get('mol_user_id') == ctx.get('user_id'):
        return LEVEL_OWN
    if is_it(ctx):
        return LEVEL_BRIEF
    return None


def can_intake(ctx):
    """«Бухгалтерия создаёт карточку ОС и присваивает инв. номер. Другим способом ОС в
    систему не попадает» (ТЗ, процесс 1)."""
    return is_keeper(ctx)


def can_correct(ctx, asset):
    """Исправить описание карточки — бухгалтерия и только пока открыт запрос приёма."""
    return bool(is_keeper(ctx) and asset and asset.get('intake_open'))


def can_set_barcode(ctx, asset):
    """Привязать штрихкод с наклейки — бухгалтерия, в любой момент, пока ОС на учёте: наклейку клеят
    и меняют не только при приёме, а ОС, принятые раньше, надо связать с их наклейками."""
    return bool(is_keeper(ctx) and asset and asset.get('status') == rules.ASSET_ACTIVE)


def can_create(ctx, kind):
    """Кто заводит запрос этого вида. МОЛ запросов не заводит: в ТЗ он в списке был
    («Сотрудник IT, HR, руководители отделов, МОЛ создаёт запрос на акт перемещения»),
    владелец 17.11.2026 решил иначе — МОЛ только смотрит."""
    if not can_open(ctx):
        return False
    if kind == rules.KIND_TRANSFER:
        return bool(is_keeper(ctx) or is_it(ctx) or is_hr(ctx) or is_head(ctx))
    if kind == rules.KIND_WRITEOFF:
        return bool(is_keeper(ctx) or is_it(ctx) or is_head(ctx))
    if kind == rules.KIND_DISMISSAL:
        return can_run_dismissal(ctx)
    if kind == rules.KIND_NEED:
        # «IT-специалист формирует запрос на новое ОС».
        return bool(is_super(ctx) or is_it(ctx))
    return False


def creatable_kinds(ctx):
    return [kind for kind in rules.REQUESTABLE_KINDS if can_create(ctx, kind)]


def can_pick_asset(ctx, asset, kind):
    """Вправе ли человек включить это ОС в свой запрос: бухгалтерия, IT и HR — любое,
    руководитель — ОС своего отдела. То, что ОС числится за человеком, права не даёт."""
    if not asset or asset.get('status') != rules.ASSET_ACTIVE:
        return False
    of_department = bool(asset.get('department_id')) and int(asset['department_id']) in headed_ids(ctx)
    if kind == rules.KIND_TRANSFER:
        return bool(is_keeper(ctx) or is_it(ctx) or is_hr(ctx) or of_department)
    if kind == rules.KIND_WRITEOFF:
        return bool(is_keeper(ctx) or is_it(ctx) or of_department)
    if kind == rules.KIND_DISMISSAL:
        return can_run_dismissal(ctx)
    return False


# ─── Запросы ─────────────────────────────────────────────────────────────────

def _is_party(ctx, request):
    user_id = ctx.get('user_id')
    return user_id is not None and user_id in (
        request.get('created_by'), request.get('from_user_id'), request.get('to_user_id'),
        request.get('need_for_user_id'))


def _heads_side(ctx, request):
    headed = headed_ids(ctx)
    return bool(headed) and any(
        request.get(key) and int(request[key]) in headed
        for key in ('from_department_id', 'to_department_id', 'review_department_id'))


def can_view_request(ctx, request):
    if not can_open(ctx) or not request:
        return False
    if request.get('status') == rules.STATUS_DRAFT:
        # Черновик — рабочий лист автора: остальные увидят запрос, когда он отправлен.
        return request.get('created_by') == ctx.get('user_id') or is_super(ctx)
    if sees_all(ctx) or _is_party(ctx, request) or _heads_side(ctx, request):
        return True
    if is_it(ctx):
        return request.get('review_by') == rules.REVIEW_IT or request.get('kind') == rules.KIND_NEED
    return False


def reviews_by_role(ctx, request):
    """Проверяющий этого запроса по своей роли: IT, бухгалтерия или глава отдела ОС."""
    group = request.get('review_by')
    if group == rules.REVIEW_IT:
        return is_it(ctx)
    if group == rules.REVIEW_ACCOUNTING:
        return is_accounting(ctx)
    if group == rules.REVIEW_HEAD:
        department_id = request.get('review_department_id')
        return bool(department_id) and int(department_id) in headed_ids(ctx)
    return False


def is_reviewer(ctx, request):
    """Может ли человек провести шаг «На проверке»: проверяющий по роли или суперадмин."""
    return is_super(ctx) or reviews_by_role(ctx, request)


def is_signer(ctx, request):
    """Руководитель стороны акта — глава отдела сдающего или принимающего. Сами сдающий и
    принимающий (МОЛ) подписывают бумагу, а в системе шагов не делают."""
    headed = headed_ids(ctx)
    return bool(headed) and any(
        request.get(key) and int(request[key]) in headed for key in ('from_department_id', 'to_department_id'))


def can_attach_scan(ctx, request):
    """Приложить скан подписанного акта: автор запроса, руководители сторон, HR (увольнение),
    бухгалтерия."""
    if request.get('status') != rules.STATUS_SIGNING:
        return False
    if is_keeper(ctx) or is_signer(ctx, request) or request.get('created_by') == ctx.get('user_id'):
        return True
    return request.get('kind') == rules.KIND_DISMISSAL and is_hr(ctx)


def can_replace_scan(ctx, request):
    """Заменить скан, пока запрос ждёт оригинал: бухгалтерия и тот, кто его приложил (ошибся
    файлом). Прежний скан остаётся в истории — отклонять из-за него весь запрос незачем."""
    if request.get('status') != rules.STATUS_ORIGINAL:
        return False
    return bool(is_keeper(ctx) or (ctx.get('user_id') is not None and request.get('scan_by') == ctx.get('user_id')))


def allowed_actions(ctx, request):
    """Что человек может сделать с запросом сейчас (экран рисует кнопки по этому списку)."""
    if not can_view_request(ctx, request):
        return []
    status = request.get('status')
    kind = request.get('kind')
    author = request.get('created_by') == ctx.get('user_id')
    actions = []
    if status == rules.STATUS_DRAFT:
        if author or is_super(ctx):
            actions += ['edit', 'submit', 'delete']
        return actions
    if status in (rules.STATUS_SIGNING, rules.STATUS_ORIGINAL, rules.STATUS_CLOSED) and kind in rules.ACT_TITLES:
        actions.append('act')
    if status == rules.STATUS_REVIEW:
        if is_reviewer(ctx, request):
            actions.append('accept' if kind == rules.KIND_NEED else 'check')
            actions.append('reject')
        elif author or is_accounting(ctx):
            actions.append('reject')
    elif status == rules.STATUS_SIGNING:
        if can_attach_scan(ctx, request):
            actions.append('scan')
        # Приём в учёт не отклоняют: карточка уже заведена, акт к ней доводят до конца.
        if kind != rules.KIND_INTAKE and (is_keeper(ctx) or is_signer(ctx, request) or author):
            actions.append('reject')
    elif status == rules.STATUS_ORIGINAL:
        if is_keeper(ctx):
            actions.append('receive')
            if kind != rules.KIND_INTAKE:
                actions.append('reject')
        if can_replace_scan(ctx, request):
            actions.append('rescan')
    return actions


def is_waiting(ctx, request):
    """Ждёт ли запрос действия этого человека — для отбора «Ждут меня»."""
    status = request.get('status')
    author = request.get('created_by') == ctx.get('user_id')
    if status == rules.STATUS_DRAFT:
        return author
    if status == rules.STATUS_REVIEW:
        # По роли, а не по праву суперадмина: чужие проверки ему в «Ждут меня» не сыплются.
        return reviews_by_role(ctx, request)
    if status == rules.STATUS_SIGNING:
        return bool(author or is_signer(ctx, request))
    if status == rules.STATUS_ORIGINAL:
        return is_accounting(ctx)
    return False


def can_see_cost(ctx):
    return sees_all(ctx)


def capabilities(ctx):
    """Сводка для экрана: вкладки и кнопки рисуются по ней, а не по роли."""
    return {
        'tabs': tabs(ctx),
        'can_intake': can_intake(ctx),
        'create_kinds': creatable_kinds(ctx),
        'can_run_dismissal': can_run_dismissal(ctx),
        'can_edit_settings': can_edit_settings(ctx),
        'can_manage_members': can_manage_members(ctx),
        'sees_all': sees_all(ctx),
        'sees_registry': sees_registry(ctx),
        'sees_cost': can_see_cost(ctx),
        'is_keeper': is_keeper(ctx),
        'works_with_requests': works_with_requests(ctx),
        'roles': role_labels(ctx),
    }


def role_labels(ctx):
    """Кем человек является в разделе — словами (подпись под заголовком)."""
    labels = []
    if is_super(ctx):
        labels.append('Суперадмин')
    if is_accounting(ctx):
        labels.append('Бухгалтерия')
    if is_hr(ctx):
        labels.append('HR')
    if is_it(ctx):
        labels.append('IT-специалист')
    if is_observer(ctx):
        labels.append('Просмотр всей истории')
    if is_head(ctx):
        labels.append('Руководитель отдела')
    if ctx.get('has_assets'):
        labels.append('МОЛ')
    return labels
