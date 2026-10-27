"""Права раздела «Коды 2FA». Чистая логика: ни базы, ни Flask.

Постановка владельца 27.10.2026: «уведомление приходит главе маркетинга, и он просто
подтверждает»; в тот же день: «сделай чтобы мог глава маркетинга и суперадмины, но уведомления
будут приходить тем, кто включил переключатель». На портал это ложится так:

    глава маркетинга      подтверждает и отклоняет; ведёт аккаунты, доступы, журнал
    супер-админ           то же самое
    сотрудник с доступом  запрашивает коды тех аккаунтов, что ему выданы

Подтверждать может любой из подтверждающих, а уведомление о запросе (колокол и Telegram с
кнопками) получает только включивший тумблер в разделе — по умолчанию он выключен.

Глава — `departments.head_user_id` отдела 'marketing', а не id в коде: сменится глава —
подтверждать и вести раздел будет новый, без правки кода.

Сотрудник — любой работающий: доступ выдаёт глава в самом разделе, а не отдел. Уволенный
теряет всё сразу, даже если выдачи остались в таблице: статус проверяется на каждом шаге.

Пока не задан ключ шифрования (crypto.key_status), раздел виден только супер-админу —
с подсказкой, что задать. Остальным пустой раздел без возможности что-то сделать был бы
только шумом в меню.
"""

APPROVER_DEPARTMENT_CODE = 'marketing'

_INACTIVE_STATUSES = ('fired', 'dismissal')


def is_working(ctx):
    return str(ctx.get('status') or 'working').strip().lower() not in _INACTIVE_STATUSES


def is_super_admin(ctx):
    return str(ctx.get('role') or '').strip().lower() == 'super_admin'


def is_approver(ctx):
    approver = ctx.get('approver_id')
    return approver is not None and int(approver) == int(ctx.get('user_id') or 0)


def key_ready(ctx):
    return ctx.get('key_status') == 'ok'


def can_approve(ctx):
    """Подтвердить или отклонить запрос — глава маркетинга и супер-админ."""
    return is_working(ctx) and (is_approver(ctx) or is_super_admin(ctx)) and key_ready(ctx)


def can_manage(ctx):
    """Аккаунты, доступы и журнал — глава маркетинга и супер-админ."""
    if not is_working(ctx):
        return False
    if is_super_admin(ctx):
        return True
    return is_approver(ctx) and key_ready(ctx)


def granted_ids(ctx):
    return {int(x) for x in ctx.get('granted_account_ids') or ()}


def can_request(ctx, account_id=None):
    """Запросить код: работает, ключ задан, аккаунт выдан ему."""
    if not (is_working(ctx) and key_ready(ctx)):
        return False
    granted = granted_ids(ctx)
    if account_id is None:
        return bool(granted)
    return int(account_id) in granted


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте. Тот же ответ портал получает
    флагом twofa_access в профиле — по нему рисует пункт меню."""
    return can_manage(ctx) or can_request(ctx)


def capabilities(ctx):
    opened = can_open_section(ctx)
    return {
        'can_open': opened,
        'can_manage': opened and can_manage(ctx),
        'can_approve': opened and can_approve(ctx),
        'can_request': opened and can_request(ctx),
    }
