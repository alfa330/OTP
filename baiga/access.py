"""Права раздела «Списки Байги». Чистая логика: ни базы, ни Flask.

Кому раздел открыт — решение владельца 05.10.2026:

    супер-админ                     всё
    глава «Маркетинга»              всё («доступ полный»): загрузка, замена и
                                    удаление недели, выгрузка, журнал
    аналитик, поимённо              то же и без QR — список ANALYST_USER_IDS
                                    (06.10.2026: «без qr, полный доступ к разделу»)
    главы ОП и СЗоВ                 поиск и просмотр
    супервайзеры ОП и СЗоВ          поиск и просмотр
    операторы ОП и СЗоВ,
    сотрудники «Маркетинга»         поиск и просмотр — после QR
    остальные                       раздел закрыт

«Остальные» — это и те, кого постановка #356 (п. 2) называла шире: главы прочих
отделов, админ, не возглавляющий отдел, тренер. Владелец перечислил круг сам, и
расширять его «по смыслу» нельзя: в строках ФИО и номер ВУ водителя.

Рядовой сотрудник входит ТОЛЬКО через QR-замок — правило и ключ те же, что у
«Посылок» (parcels.access.requires_sensitive_qr): двух разных QR на один экран
человек не различит. Кого замок не спрашивает и кто не назван выше (стажёр,
тренер), тому раздел закрыт: открыть его без замка значило бы показать ФИО и
номера ВУ без подтверждения.

Аналитик — единственный, кого замок не спрашивает вопреки должности: он назван
поимённо, и раздел ему выдан целиком. Числится он оператором, и общий замок
запер бы от него тот самый раздел, который он ведёт (requires_sensitive_qr).

Главы названы отделом, а не id: назначение главой живёт в
departments.head_user_id и переживает и смену человека, и его вторую учётку.
"""

# Роль и QR-замок считают хелперы «Посылок»: правило у портала одно.
from parcels.access import normalize_role
from parcels.access import requires_sensitive_qr as _staff_requires_qr

# Выключатель: пока флаг стоит, раздел открыт только супер-админу — так он
# выкладывался 02.10.2026 и так же закрывается обратно одной строкой. Зеркало во
# фронте — BAIGA_PILOT_SUPER_ADMIN_ONLY в App.jsx, тест сверяет их: переключать
# в обоих местах.
PILOT_SUPER_ADMIN_ONLY = False

# Аналитики поимённо — только id, ФИО в публичный репозиторий не кладём.
# Зеркало — BAIGA_ANALYST_USER_IDS в App.jsx (аналитику нужен пункт меню).
# 540 — сотрудник отдела аналитики, назван владельцем 06.10.2026.
ANALYST_USER_IDS = frozenset({540})

MANAGE_DEPARTMENT_CODE = 'marketing'     # глава ведёт раздел, сотрудники читают после QR
READ_DEPARTMENT_CODES = ('op', 'szov')   # глава и СВ читают, операторы — после QR

SECTION_DEPARTMENT_CODES = (MANAGE_DEPARTMENT_CODE,) + READ_DEPARTMENT_CODES

_SUPERVISOR_ROLES = ('sv', 'supervisor')


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _own_code(ctx):
    return str(ctx.get('department_code') or '').strip().lower()


def _heads(ctx, codes):
    """Глава хотя бы одного из отделов."""
    return bool(_codes(ctx.get('headed_department_codes')) & set(codes))


def is_analyst(ctx):
    try:
        return int(ctx.get('user_id')) in ANALYST_USER_IDS
    except (TypeError, ValueError):
        return False


def requires_sensitive_qr(ctx):
    """Нужно ли подтвердить сессию QR-кодом, прежде чем раздел отдаст данные.

    Правило общее с «Посылками», кроме аналитика из именного списка: ему раздел
    открыт без замка. Спрашивают это и ручки раздела, и ИИ-помощник
    (baiga/assistant.py) — второй путь к тем же строкам. У помощника перед этим
    стоит ещё и замок самой вики (wiki_route): его это правило не снимает.
    Зеркало — baigaQrRequiredFor в App.jsx.
    """
    if is_analyst(ctx):
        return False
    return _staff_requires_qr(ctx)


def can_manage(ctx):
    """Загрузить, заменить и удалить неделю, журнал, исходник — супер-админ,
    глава «Маркетинга» и аналитик."""
    if normalize_role(ctx.get('role')) == 'super_admin' or is_analyst(ctx):
        return True
    return _heads(ctx, (MANAGE_DEPARTMENT_CODE,))


def can_export(ctx):
    """Выгрузить выборку в Excel — тот, кому раздел открыт полностью.

    Остальным владелец дал чтение: главы и супервайзеры ОП и СЗоВ, операторы и
    сотрудники «Маркетинга» ищут и смотрят, файл со всеми ФИО и номерами ВУ не
    уносят.
    """
    return can_manage(ctx)


def _can_read(ctx):
    """Искать и смотреть — без выгрузки и загрузки."""
    if _heads(ctx, READ_DEPARTMENT_CODES):
        return True
    own = _own_code(ctx)
    if normalize_role(ctx.get('role')) in _SUPERVISOR_ROLES:
        return own in READ_DEPARTMENT_CODES
    # Рядовой — только тот, кого спросит QR-замок: одно условие и пускает в
    # раздел, и закрывает данные, разъехаться им негде.
    return _staff_requires_qr(ctx) and own in SECTION_DEPARTMENT_CODES


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте: спрятанный пункт меню
    доступом не является. Зеркало — canAccessBaigaSectionForUser в App.jsx."""
    if PILOT_SUPER_ADMIN_ONLY:
        return normalize_role(ctx.get('role')) == 'super_admin'
    return can_manage(ctx) or _can_read(ctx)


def capabilities(ctx):
    """Сводка для фронта: экран рисует кнопки по ней, а не по роли.

    Кто в раздел не пущен (выключатель, чужой отдел), не получает и прав внутри:
    иначе сводка обещала бы кнопки, на которые каждая ручка ответит 403.
    """
    opened = can_open_section(ctx)
    return {
        'can_open': opened,
        'can_export': opened and can_export(ctx),
        'can_manage': opened and can_manage(ctx),
    }
