"""Права раздела «Почта». Чистая логика: ни базы, ни Flask.

Контекст зрителя (queries.load_context):
    user_id, name, portal_role, is_super — кто это в портале;
    role       — роль в «Участниках» (rules.ROLES) или None;
    office_ids — его офисы из «Участников».

Роли ТЗ п. 11 ложатся так (вкладка «Участники» выдаёт только office, admin — постановщику
схемой; остальные заложены на будущее, см. clean_participant):

    office       свой офис: создаёт отправления из него, видит его исходящие (черновики —
                 только свои: «свои черновики»), подтверждает получение входящих
    coordinator  офисы из списка: всё, что у сотрудника офиса, плюс черновики коллег
                 этих офисов, проблемы и возвраты
    regional     офисы из списка: только чтение («чтение/контроль своих офисов»)
    central      все офисы, все действия — «сквозной реестр и эскалации»
    accounting   все офисы, только чтение; вложения отправлений с конфиденциальными
                 строками не открываются («без доступа к закрытым вложениям»)
    admin        всё, что у центрального координатора, плюс «Участники» и «Справочники»
    суперадмин   портала — как admin, даже без строки в «Участниках»

Видимость повторяет SQL (queries._visibility_clause) — правило меняется в обоих местах,
сверяет их tests/test_mail_postgres.py.
"""

from . import rules

ACTING_ROLES = ('office', 'coordinator')
FULL_ROLES = ('central', 'admin')
READ_ONLY_ROLES = ('regional', 'accounting')

TAB_ORDER = ('overview', 'outgoing', 'incoming', 'registry', 'problems', 'participants', 'settings')

# Пилот (владелец 13.11.2026: «деплой, чтобы раздел был доступен пока для суперадминов»): раздел
# открывается только суперадминам — и пункт меню (флаг профиля, queries.section_open_for), и каждая
# ручка. «Участники» при этом заводятся и хранятся; снять флаг — и раздел откроется им без переделок.
PILOT_SUPER_ADMIN_ONLY = True


def can_open(ctx):
    if not ctx:
        return False
    if PILOT_SUPER_ADMIN_ONLY:
        return bool(ctx.get('is_super'))
    return bool(ctx.get('is_super') or ctx.get('role') in rules.ROLES)


def is_admin(ctx):
    return bool(ctx.get('is_super') or ctx.get('role') == 'admin')


def is_full(ctx):
    """Все офисы и все действия: центральный координатор, администратор, суперадмин."""
    return bool(ctx.get('is_super') or ctx.get('role') in FULL_ROLES)


def sees_all(ctx):
    return bool(ctx.get('is_super') or ctx.get('role') in rules.ROLES_ALL_OFFICES)


def own_offices(ctx):
    return {int(x) for x in (ctx.get('office_ids') or [])}


def can_create(ctx):
    return bool(ctx.get('is_super') or ctx.get('role') in ACTING_ROLES + FULL_ROLES)


def sender_office_ids(ctx, all_office_ids):
    """Из каких офисов человек может отправлять. Сотрудник и координатор — только из
    своих; центр, админ и суперадмин — из любого (свой ставится по умолчанию)."""
    all_office_ids = [int(x) for x in all_office_ids]
    if is_full(ctx):
        return all_office_ids
    if ctx.get('role') in ACTING_ROLES:
        own = own_offices(ctx)
        return [x for x in all_office_ids if x in own]
    return []


def default_sender_office(ctx, all_office_ids):
    allowed = sender_office_ids(ctx, all_office_ids)
    own = [x for x in allowed if x in own_offices(ctx)]
    if own:
        return own[0]
    return allowed[0] if len(allowed) == 1 else None


def can_view(ctx, shipment):
    if not can_open(ctx):
        return False
    if is_full(ctx):
        return True
    status = shipment.get('status')
    if ctx.get('role') == 'accounting':
        return status != 'draft'
    if shipment.get('created_by') == ctx.get('user_id'):
        return True
    own = own_offices(ctx)
    if shipment.get('sender_office_id') in own:
        # «Свои черновики»: чужой черновик сотрудник офиса не видит, координатор — видит.
        return status != 'draft' or ctx.get('role') == 'coordinator'
    if shipment.get('recipient_office_id') in own:
        return status in rules.INCOMING_VISIBLE
    return False


def sides(ctx, shipment):
    """Чья сторона у зрителя в этом отправлении: sender / recipient / coordinator."""
    result = set()
    full = is_full(ctx)
    acting = ctx.get('role') in ACTING_ROLES
    own = own_offices(ctx)
    if full or (acting and shipment.get('sender_office_id') in own):
        result.add('sender')
    # Своё отправление не принимает никто — даже админ (ТЗ п. 11).
    if (full or (acting and shipment.get('recipient_office_id') in own)) \
            and shipment.get('created_by') != ctx.get('user_id'):
        result.add('recipient')
    if full or (ctx.get('role') == 'coordinator'
                and (shipment.get('sender_office_id') in own or shipment.get('recipient_office_id') in own)):
        result.add('coordinator')
    return result


def _side_allows(side, have):
    if side == 'courier':
        return bool(have)
    return side in have


# Шаги, которые ставят «Принято и сверено». Автору отправления недоступны ни в какой роли
# (ТЗ п. 11: «самостоятельно подтверждать получение собственного отправления нельзя») —
# ни сверка, ни «Проблема решена» у координатора, создавшего отправление сам.
ACCEPTING_ACTIONS = ('reconcile', 'resolve')


def allowed_actions(ctx, shipment):
    if not can_view(ctx, shipment):
        return []
    have = sides(ctx, shipment)
    status = shipment.get('status')
    own = shipment.get('created_by') == ctx.get('user_id')
    return [code for code, spec in rules.ACTIONS.items()
            if status in spec['from'] and _side_allows(spec['side'], have)
            and not (own and code in ACCEPTING_ACTIONS)]


def edit_rights(ctx, shipment):
    """(можно ли править, нужна ли причина, можно ли трогать строки содержимого).

    До передачи курьеру — свободно, стороне отправки. После — «существенные поля
    изменяются только с записью причины и истории» (ТЗ п. 11): стороне отправки и
    координатору, с причиной. Строки содержимого — то, с чем получатель сверяет посылку,
    поэтому после передачи их правит только сторона отправки (координатор офиса получения —
    нет: иначе он подогнал бы заявленное под пришедшее перед своей же сверкой), а с
    «Доставлено курьером» и дальше — никто. Закрытое отправление правит только администратор.
    """
    if not can_view(ctx, shipment):
        return False, False, False
    status = shipment.get('status')
    have = sides(ctx, shipment)
    if status in rules.BEFORE_HANDOVER:
        allowed = 'sender' in have
        return allowed, False, allowed
    if status in rules.FINAL or status in ('problem', 'return_initiated'):
        allowed = is_admin(ctx)
        return allowed, True, False
    allowed = bool(have & {'sender', 'coordinator'})
    items = allowed and 'sender' in have and status != 'delivered'
    return allowed, True, items


def can_attach(ctx, shipment):
    if not can_view(ctx, shipment) or shipment.get('status') == 'cancelled':
        return False
    return bool(sides(ctx, shipment))


def can_open_files(ctx, shipment):
    if not can_view(ctx, shipment):
        return False
    if ctx.get('role') == 'accounting' and not ctx.get('is_super') and shipment.get('is_confidential'):
        return False
    return True


def tabs(ctx):
    """Вкладки ТЗ п. 2 (названия — как в макете). «Общий реестр» — у всех: это всё, что
    человек видит, и туда ведут плитки «Обзора»; «Исходящие» и «Входящие» — тем, у кого
    есть свой офис."""
    if not can_open(ctx):
        return []
    result = ['overview']
    if own_offices(ctx):
        result += ['outgoing', 'incoming']
    result += ['registry', 'problems']
    if is_admin(ctx):
        result += ['participants', 'settings']
    return result


def capabilities(ctx):
    return {
        'can_open': can_open(ctx),
        'role': ctx.get('role'),
        'role_label': rules.ROLE_LABELS.get(ctx.get('role')) or ('Суперадмин' if ctx.get('is_super') else ''),
        'is_admin': is_admin(ctx),
        'is_full': is_full(ctx),
        'sees_all': sees_all(ctx),
        'can_create': can_create(ctx),
        'office_ids': sorted(own_offices(ctx)),
        'tabs': tabs(ctx),
    }


def clean_participant(payload, known_office_ids, current_role=None):
    """Строка «Участников» (решение владельца 13.11.2026): «нам нужны именно сотрудники офисов,
    которые будут отправлять и принимать посылки — в участниках мы привязываем их к офисам».

    Поэтому вкладка заводит одну роль — сотрудник офиса, ровно с одним офисом. Остальные роли ТЗ
    п. 11 (координатор, региональный руководитель, центральный координатор, бухгалтерия) в правах
    заложены, но ни окно, ни прямой запрос их не выдают: роль из запроса не читается вовсе — так
    администратор раздела не раздаст и права администратора. Администратор раздела (постановщик ТЗ,
    schema.SEED_ADMIN_USER_IDS) остаётся администратором; ему офис — по желанию, если он сам
    отправляет почту.
    """
    payload = payload if isinstance(payload, dict) else {}
    role = 'admin' if current_role == 'admin' else 'office'
    errors = {}
    raw = payload.get('office_ids')
    if raw is None:
        raw = [payload.get('office_id')] if payload.get('office_id') is not None else []
    if not isinstance(raw, list):
        raw = [raw]
    office_ids = []
    for value in raw:
        try:
            office_id = rules.int_or_none(value)
        except (TypeError, ValueError):
            errors['office_ids'] = 'Неверный офис'
            continue
        if office_id is None:
            continue
        if office_id not in known_office_ids:
            errors['office_ids'] = 'Офиса нет в справочнике'
            continue
        if office_id not in office_ids:
            office_ids.append(office_id)
    if 'office_ids' not in errors:
        if role == 'office' and not office_ids:
            errors['office_ids'] = 'Выберите офис'
        elif len(office_ids) > 1:
            errors['office_ids'] = 'Офис у сотрудника один'
    if errors:
        raise rules.MailError('Проверьте офис', code='MAIL_FIELDS', fields=errors)
    return role, office_ids
