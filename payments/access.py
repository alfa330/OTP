"""Права раздела «Оплата счетов». Чистая логика: ни базы, ни Flask.

Модуль намеренно ничего не импортирует из database/flask — так его можно
дёргать в тестах напрямую (импорт database открывает пул к боевой базе; та же
причина, что в parcels/access.py и driver_mailings/access.py).

ПОЧЕМУ ПОКА ИМЕННОЙ СПИСОК ИЗ ОДНОГО ЧЕЛОВЕКА.

Раздел строится по задаче #179 и на время выката открыт только владельцу
(users.id = 2, Ядигаров Руслан) — его прямое указание: «раздел доступный пока
что только мне». Не «всем super_admin»: супер-админов в портале пятеро, и
согласование оплаты счетов никому из них ещё не выдавали. Пока периметр один
человек, он играет все роли процесса сразу (инициатор, руководитель,
Учредитель, Бухгалтерия) — для этого у него есть право действовать за любого
ответственного (`can_act_for_anyone`), и каждое такое действие подписывается
в истории его именем.

Когда периметр расширят — id добавляются в SECTION_ALLOWED_USER_IDS, а роли
процесса (кто Учредитель, кто Бухгалтерия) раздаются в самом разделе, вкладка
«Участники», и хранятся в payment_role_members. Права ВНУТРИ раздела уже
считаются по ролям процесса, а не по этому списку, — расширение не потребует
переписывать проверки шагов.
"""

# Кому открыт раздел. 2 — Ядигаров Руслан (владелец), единственный на пилоте.
SECTION_ALLOWED_USER_IDS = (2,)

# Кто внутри раздела правит справочники, участников ролей, Приказы и договоры,
# переназначает шаги и удаляет заявки. На пилоте — тот же человек; когда раздел
# откроют шире, сюда войдут те, кому владелец даст администрирование процесса.
SECTION_ADMIN_USER_IDS = (2,)


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


def can_open_section(ctx):
    """Пускать ли в раздел вообще. Проверяется на КАЖДОМ роуте, не только в меню."""
    return user_id(ctx) in SECTION_ALLOWED_USER_IDS


def is_section_admin(ctx):
    return user_id(ctx) in SECTION_ADMIN_USER_IDS


def can_act_for_anyone(ctx):
    """Отписаться за любого ответственного шага.

    Нужно ровно на пилоте: один человек проходит маршрут за все роли. Каждое
    такое действие пишется в историю его именем — подмены подписи нет.
    """
    return is_section_admin(ctx)


def can_create_request(ctx):
    """Заявку заводит любой, кому открыт раздел: инициатор — это тот, у кого
    возникла потребность в закупе, должность здесь не важна."""
    return can_open_section(ctx)


def can_act_on_step(ctx, step_entry, role_members):
    """Вправе ли человек отписаться на шаге.

    step_entry — строка payment_request_steps ({'role_code', 'assignee_id'}),
    role_members — {role_code: {user_id, ...}} из payment_role_members.
    Шаг с конкретным ответственным закрывает только он; шаг роли — любой её
    участник. Администратор раздела — за любого (см. can_act_for_anyone).
    """
    me = user_id(ctx)
    if me is None or not step_entry:
        return False
    if can_act_for_anyone(ctx):
        return True
    assignee = step_entry.get('assignee_id')
    if assignee is not None:
        return int(assignee) == me
    members = (role_members or {}).get(step_entry.get('role_code')) or set()
    return me in {int(x) for x in members}


def can_edit_request(ctx, request):
    """Править поля заявки: инициатор — пока заявка живая, администратор — всегда.

    После оплаты (шаг 10 отписан) поля заявки — документ, по которому платили;
    инициатору править их уже нельзя, только администратору с записью в истории.
    """
    if not request or request.get('status') != 'active':
        return is_section_admin(ctx)
    if is_section_admin(ctx):
        return True
    me = user_id(ctx)
    return (me is not None and int(request.get('initiator_id') or 0) == me
            and int(request.get('current_step') or 0) <= 10)


def can_cancel_request(ctx, request):
    """Отменить заявку: инициатор — до оплаты, администратор — до оплаты тоже.
    Оплаченную заявку не отменяют: деньги уже ушли, дальше только закрытие."""
    if not request or request.get('status') != 'active':
        return False
    if int(request.get('current_step') or 0) > 10:
        return False
    me = user_id(ctx)
    return is_section_admin(ctx) or (me is not None and int(request.get('initiator_id') or 0) == me)


def can_delete_request(ctx):
    return is_section_admin(ctx)


def capabilities(ctx):
    """Сводка для фронта: раздел рисует кнопки по ней, а не по роли."""
    return {
        'can_open': can_open_section(ctx),
        'can_create': can_create_request(ctx),
        'is_admin': is_section_admin(ctx),
        'can_act_for_anyone': can_act_for_anyone(ctx),
        'can_delete': can_delete_request(ctx),
    }
