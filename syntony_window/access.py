"""Права окна Syntony. Чистая логика: ни базы, ни Flask.

Решения владельца 11.11.2026:

    супер-админ             ведёт список доступа (кнопка «Доступ» в разделе)
    сотрудник из списка     открывает окна: «Чаты», «Заявки», переписку водителя
    остальные               пунктов меню не видят, ручки отвечают отказом

Окно получает только тот, кто В СПИСКЕ, — и супер-админ тоже. Список и есть
«операторы iCORE» для Syntony: первый же запрос ссылки записывает человека у неё
оператором, и супер-админ, заглянувший в раздел, не должен там появляться сам собой.

Уволенный теряет всё сразу, даже если строка в списке осталась: статус сверяется
на каждом запросе.

QR. Рядовой сотрудник открывает окно только в сессии, подтверждённой обычным QR
портала (тем же, что «Чаты ОП», «Вики» и «Обращения»); руководителям и админам он
не нужен. «Рядовой» здесь — каждый, кому портал вообще выдаёт QR
(bot_schedule2.SENSITIVE_QR_GATED_ROLES); шире нельзя — человек упёрся бы в замок,
который ему нечем открыть. Зеркало в портале — sensitiveQrAvailableFor (App.jsx).
"""

_INACTIVE_STATUSES = ('fired', 'dismissal')

# Должности, которым портал выдаёт QR: объединение наборов «Вики» и «Чатов
# водителей». tests/test_syntony_window.py сверяет набор с обоими источниками.
QR_GATED_ROLES = frozenset({'operator', 'trainee', 'accounting_manager', 'marketing_manager'})


def normalize_role(role):
    value = str(role or '').strip().lower()
    if value == 'supervisor':
        return 'sv'
    if value in ('superadmin', 'super-admin', 'super admin'):
        return 'super_admin'
    return value


def is_working(ctx):
    return str(ctx.get('status') or 'working').strip().lower() not in _INACTIVE_STATUSES


def is_super_admin(ctx):
    return normalize_role(ctx.get('role')) == 'super_admin'


def can_manage(ctx):
    """Вести список доступа — супер-админ."""
    return is_working(ctx) and is_super_admin(ctx)


def can_request_window(ctx):
    """Запросить окно у Syntony — только тот, кто в списке доступа."""
    return is_working(ctx) and bool(ctx.get('granted'))


def can_open_section(ctx):
    """Показывать ли пункты меню. Тот же ответ портал получает флагом syntony_access."""
    return can_manage(ctx) or can_request_window(ctx)


def requires_qr(ctx):
    """Нужно ли подтвердить сессию QR, прежде чем окно откроется."""
    if ctx.get('is_head'):
        return False
    return normalize_role(ctx.get('role')) in QR_GATED_ROLES
