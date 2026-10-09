"""Права раздела «Оплата счетов». Чистая логика: ни базы, ни Flask.

Модуль намеренно ничего не импортирует из database/flask — так его можно
дёргать в тестах напрямую (импорт database открывает пул к боевой базе; та же
причина, что в parcels/access.py и driver_mailings/access.py).

ДВА СЛОЯ ПРАВ.

1. **Кому открыт раздел** — именной список. Раздел строится по задачам #179 и
   #381 и на время выката открыт только владельцу (users.id = 2, Ядигаров
   Руслан) — его прямое указание: «раздел доступный пока что только мне».
   02.10.2026 к пилоту добавлен соисполнитель задачи (id 448) с теми же
   правами. Не «всем super_admin»: супер-админов в портале пятеро, и
   согласование оплаты счетов никому из них ещё не выдавали. Пока в периметре
   только те, кто строит раздел, каждый играет все роли процесса сразу — для
   этого у него есть право действовать за любого исполнителя
   (`can_act_for_anyone`), и каждое такое действие подписывается в истории его
   именем. Когда периметр расширят — id добавляются в SECTION_ALLOWED_USER_IDS.

2. **Что человек видит и делает внутри** — по ролям процесса (ТЗ «Закуп и
   оплата», п. 3). Роли раздаются в самом разделе, вкладка «Участники», и
   хранятся в payment_role_members: «Утвердитель», «Бухгалтерия», «Финансовый
   отдел», «Ответственный за учёт имущества». Инициатор и руководитель — люди
   конкретной заявки. Главное правило ТЗ (п. 16): каждый видит только те
   задачи, по которым от него требуется действие.
"""

# Кому открыт раздел. 2 — Ядигаров Руслан (владелец), 448 — соисполнитель #179.
SECTION_ALLOWED_USER_IDS = (2, 448)

# Кто внутри раздела правит справочники, участников ролей, лимиты и маршруты
# согласования, переназначает подзадачи и удаляет заявки. На пилоте — те же
# люди; когда раздел откроют шире, сюда войдут те, кому владелец даст
# администрирование процесса.
SECTION_ADMIN_USER_IDS = (2, 448)

ROLE_APPROVER = 'approver'
ROLE_ACCOUNTING = 'accounting'
ROLE_FINANCE = 'finance'
ROLE_ASSET_KEEPER = 'asset_keeper'

# Роль, которой нужен реестр целиком, одна — бухгалтерия: она проверяет «историю
# предыдущих оплат» (п. 8) и ведёт закрывающие документы по всем заявкам, а
# «Реестр заявок на оплату» с фильтрами и выгрузкой — её же требование к первой
# версии раздела. Остальные (утверждающий, финансовый отдел, ответственный за
# имущество) видят в реестре только заявки, в которых участвуют: п. 16 — «только
# те задачи, по которым от него требуется действие».
FULL_REGISTRY_ROLES = (ROLE_ACCOUNTING,)

BOARD_APPROVAL = 'approval'
BOARD_ACCOUNTING = 'accounting'
BOARD_FINANCE = 'finance'

# Подзадачи согласования (workflow.APPROVAL_KINDS): этап руководителя и утверждение.
APPROVAL_KINDS = ('manager_approval', 'approval')


def user_id(ctx):
    raw = None
    if isinstance(ctx, dict):
        raw = ctx.get('user_id', ctx.get('id'))
    else:
        raw = getattr(ctx, 'user_id', None) or getattr(ctx, 'id', None)
    if raw in (None, ''):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _roles(roles):
    return {str(role) for role in (roles or ())}


def can_open_section(ctx):
    """Пускать ли в раздел вообще. Проверяется на КАЖДОМ роуте, не только в меню."""
    return user_id(ctx) in SECTION_ALLOWED_USER_IDS


def is_section_admin(ctx):
    return user_id(ctx) in SECTION_ADMIN_USER_IDS


def mark_section_access(members):
    """Каждому участнику роли — открыт ли ему раздел (`section_open`).

    Роль в «Участниках» и вход в раздел — разные вещи: участник вне периметра
    задач не увидит, и администратор должен узнать об этом из списка, а не из
    жалобы «мне ничего не приходит».
    """
    for people in (members or {}).values():
        for person in people:
            person['section_open'] = _int(person.get('user_id')) in SECTION_ALLOWED_USER_IDS
    return members


def can_act_for_anyone(ctx):
    """Выполнить подзадачу за любого исполнителя.

    Нужно ровно на пилоте: один человек проходит маршрут за все роли. Каждое
    такое действие пишется в историю его именем — подмены подписи нет.
    """
    return is_section_admin(ctx)


def can_create_request(ctx):
    """Заявку заводит любой, кому открыт раздел: инициатор — это тот, у кого
    возникла потребность в закупе, должность здесь не важна."""
    return can_open_section(ctx)


# ─── Что видит человек ───────────────────────────────────────────────────────

def sees_all_requests(ctx, roles=()):
    return is_section_admin(ctx) or bool(_roles(roles) & set(FULL_REGISTRY_ROLES))


def sees_department_people(ctx, roles=()):
    """Видит ли человек, КТО именно в бухгалтерии или финансовом отделе выполнил задачу.

    П. 9: исполнитель оплаты — подразделение, и «инициатор не должен знать, кто
    именно внутри бухгалтерии или финансового отдела выполняет платёж». Имена
    видят сами подразделения и администратор раздела; остальным вместо фамилии
    показывается название подразделения (payments/privacy.py).
    """
    return is_section_admin(ctx) or bool(_roles(roles) & {ROLE_ACCOUNTING, ROLE_FINANCE})


def can_see_request(ctx, request, roles=(), participant=False):
    """Открыть карточку заявки.

    `participant` — у человека есть (или была) своя подзадача в этой заявке,
    личная или по роли: считает запрос, сюда приходит готовым ответом.
    """
    if not request:
        return False
    if sees_all_requests(ctx, roles) or participant:
        return True
    me = user_id(ctx)
    if me is None:
        return False
    return me in (_int(request.get('initiator_id')), _int(request.get('manager_id')))


def boards_for(ctx, roles=(), is_manager=False):
    """Доски, которые человеку показываются (пп. 7–9).

    «Согласование» — утверждающим и тем, у кого есть заявки подчинённых на
    согласовании (`is_manager`); «Бухгалтерия» и «Финансовый отдел» — участникам
    этих ролей. Администратор раздела видит все три.
    """
    owned = _roles(roles)
    admin = is_section_admin(ctx)
    boards = []
    if admin or ROLE_APPROVER in owned or is_manager:
        boards.append(BOARD_APPROVAL)
    if admin or ROLE_ACCOUNTING in owned:
        boards.append(BOARD_ACCOUNTING)
    if admin or ROLE_FINANCE in owned:
        boards.append(BOARD_FINANCE)
    return boards


def can_open_board(ctx, board_code, roles=(), is_manager=False):
    return board_code in boards_for(ctx, roles, is_manager)


def board_scope(ctx, board_code, roles=()):
    """Чьи карточки показывать на доске: 'all' — все, 'own' — только свои.

    На досках подразделений (бухгалтерия, финансовый отдел) задача назначена
    отделу — её видит каждый участник (п. 9). На доске согласования утверждающий
    и руководитель видят СВОИ согласования (п. 16); администратор — все.
    """
    if is_section_admin(ctx):
        return 'all'
    if board_code in (BOARD_ACCOUNTING, BOARD_FINANCE):
        return 'all'
    return 'own'


# ─── Что человек делает ──────────────────────────────────────────────────────

def approves_own_request(ctx, subtask, request):
    """Подзадача — согласование заявки, которую человек сам и подал."""
    me = user_id(ctx)
    return bool(subtask and request and me is not None
                and subtask.get('kind') in APPROVAL_KINDS and _int(request.get('initiator_id')) == me)


def can_act_on_subtask(ctx, subtask, role_members, request=None):
    """Вправе ли человек выполнить подзадачу.

    subtask — строка payment_subtasks ({'kind', 'role_code', 'assignee_id',
    'state'}), role_members — {role_code: {user_id, ...}} из
    payment_role_members, request — заявка подзадачи ({'initiator_id'}).
    Подзадачу с конкретным исполнителем выполняет только он; подзадачу
    подразделения — любой его участник (п. 9). Администратор раздела — за
    любого (см. can_act_for_anyone). Действовать можно только по ОТКРЫТОЙ
    подзадаче: ждущую уточнения двигает ответ инициатора, а не исполнитель.

    Свою заявку не согласуют: утверждающий, подавший заявку сам, её не
    утверждает — она уходит остальным утверждающим (flow.submit).
    """
    me = user_id(ctx)
    if me is None or not subtask or subtask.get('state') != 'open':
        return False
    if can_act_for_anyone(ctx):
        return True
    if approves_own_request(ctx, subtask, request):
        return False
    assignee = subtask.get('assignee_id')
    if assignee is not None:
        return int(assignee) == me
    members = (role_members or {}).get(subtask.get('role_code')) or set()
    return me in {int(x) for x in members}


def acts_directly(ctx, subtask, role_members):
    """Человек — сам исполнитель подзадачи, а не администратор за него."""
    me = user_id(ctx)
    if me is None or not subtask:
        return False
    assignee = subtask.get('assignee_id')
    if assignee is not None:
        return int(assignee) == me
    members = (role_members or {}).get(subtask.get('role_code')) or set()
    return me in {int(x) for x in members}


def with_initiator(request):
    """Заявка сейчас у инициатора: черновик, доработка или уточнение."""
    return bool(request and request.get('status') == 'active' and request.get('stage') == 'initiation')


def can_edit_request(ctx, request):
    """Править поля заявки — только пока она у инициатора.

    Раньше инициатор мог править заявку вплоть до оплаты, и согласованную сумму
    можно было исправить уже после согласования. Теперь заявка, ушедшая на
    согласование или в оплату, закрыта для правки: чтобы её изменить, её
    возвращают инициатору («Вернуть на доработку», «Запросить информацию»), а
    смена существенных условий запускает согласование заново.
    """
    if not with_initiator(request):
        return False
    if is_section_admin(ctx):
        return True
    me = user_id(ctx)
    return me is not None and _int(request.get('initiator_id')) == me


def can_cancel_request(ctx, request):
    """Отменить заявку: инициатор или администратор — до оплаты.

    Оплаченную заявку не отменяют: деньги уже ушли, дальше только закрытие.
    Пока заявка в работе у бухгалтерии или финансового отдела, инициатор её не
    отменяет: платёж в банке мог уже уйти, а «Оплачено» ещё не нажато — отмена
    в этот момент оставила бы оплату без заявки. Отменяет администратор раздела;
    как только заявку вернули инициатору (уточнение), он снова вправе её отменить.
    """
    if not request or request.get('status') != 'active' or request.get('paid_on'):
        return False
    if is_section_admin(ctx):
        return True
    me = user_id(ctx)
    if me is None or _int(request.get('initiator_id')) != me:
        return False
    return request.get('current_role_code') not in (ROLE_ACCOUNTING, ROLE_FINANCE)


def can_delete_request(ctx):
    return is_section_admin(ctx)


def can_refund(ctx, request, roles=()):
    """Отметить возврат средств по оплаченной заявке — бухгалтерия."""
    if not request or not request.get('paid_on'):
        return False
    return is_section_admin(ctx) or ROLE_ACCOUNTING in _roles(roles)


def can_attach(ctx, request, roles=(), can_act=False):
    """Приложить файл к заявке: инициатор — пока заявка живая, исполнитель
    текущей подзадачи, бухгалтерия (закрывающие документы приходят и к ней)."""
    if not request or request.get('status') != 'active':
        return is_section_admin(ctx)
    if is_section_admin(ctx) or can_act or ROLE_ACCOUNTING in _roles(roles):
        return True
    me = user_id(ctx)
    return me is not None and _int(request.get('initiator_id')) == me


# ─── Номер карты (п. 5.2, п. 13) ─────────────────────────────────────────────

def can_view_card_number(ctx, request=None, roles=()):
    """Полный номер карты заявки: финансовый отдел (ему переводить),
    администратор раздела и инициатор — тот номер, что он сам ввёл в заявке.

    Карту, выбранную из справочника (`card_id`), инициатор видит маской: её
    номер вводил не он, а справочник карт ему закрыт — иначе чужой номер
    читался бы через собственный черновик. Согласующие, руководитель и
    бухгалтерия видят маску «•••• 1234».
    """
    if is_section_admin(ctx) or ROLE_FINANCE in _roles(roles):
        return True
    me = user_id(ctx)
    return bool(request and me is not None and _int(request.get('initiator_id')) == me
                and not request.get('card_id'))


def can_manage_cards(ctx, roles=()):
    """Справочник карт: вести его и видеть в нём полные номера."""
    return is_section_admin(ctx) or ROLE_FINANCE in _roles(roles)


# ─── Имущество (пп. 10–12) ───────────────────────────────────────────────────

def can_view_assets(ctx, roles=()):
    return is_section_admin(ctx) or bool(_roles(roles) & {ROLE_ASSET_KEEPER, ROLE_ACCOUNTING})


def can_manage_assets(ctx, roles=()):
    return is_section_admin(ctx) or ROLE_ASSET_KEEPER in _roles(roles)


# ─── Справочники ─────────────────────────────────────────────────────────────

def can_save_dictionary(ctx, dictionary, row_id=None, roles=()):
    """Записать строку справочника.

    Справочники ведёт администратор раздела. Исключения:
      * карты — финансовый отдел (он же единственный видит полные номера);
      * категории имущества — ответственный за учёт имущества.

    Нового поставщика инициатор заводит не здесь, а в самой заявке — одним
    названием (routes._parse_offers). Карточку целиком ему писать нельзя: в ней
    согласующий и лимит поставщика (п. 13.1), и человек назначил бы
    согласующим собственных заявок самого себя.
    """
    if is_section_admin(ctx):
        return True
    owned = _roles(roles)
    if dictionary == 'cards':
        return ROLE_FINANCE in owned
    return dictionary == 'asset_categories' and ROLE_ASSET_KEEPER in owned


def can_view_dictionary(ctx, dictionary, roles=()):
    """Открыть справочник целиком (вкладка «Справочники»).

    Карты — только тем, кто их ведёт: в этом справочнике лежат полные номера.
    Остальные справочники читает любой участник раздела — форма заявки и так
    выбирает из них.
    """
    if dictionary == 'cards':
        return can_manage_cards(ctx, roles)
    return can_open_section(ctx)


def capabilities(ctx, roles=(), is_manager=False):
    """Сводка для фронта: раздел рисует вкладки и кнопки по ней, а не по роли."""
    return {
        'can_create': can_create_request(ctx),
        'is_admin': is_section_admin(ctx),
        'sees_all_requests': sees_all_requests(ctx, roles),
        'boards': boards_for(ctx, roles, is_manager),
        'can_view_assets': can_view_assets(ctx, roles),
        'can_manage_assets': can_manage_assets(ctx, roles),
        'can_manage_cards': can_manage_cards(ctx, roles),
    }


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
