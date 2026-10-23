"""Права раздела «Учёт воды». Чистая логика: ни базы, ни Flask.

Кто что видит — решение владельца 01.10.2026 (поверх ТЗ):

    супер-админ                     всё: выдача, остатки, журнал, настройки
    руководитель регионов /         то же — он и ведёт учёт: офисы, поступления,
    фронт-офисов                    пересчёт, отмена выдач, условия программы и
    (глава отдела «Фронт офисы»)    пороги, доступ к выдаче по офисам
    офисники из списка              выдают воду — только в офисах, к которым им
    (ISSUER_USER_IDS)               выдан доступ, — и видят остатки; журнала и
                                    настроек у них нет
    колл-центр СЗоВ (отдел szov,    видят остатки и условия получения воды,
    любая роль)                     проверяют право водителя; воду не выдают
    отдел продаж (отдел op,         то же, что колл-центр СЗоВ («для ОП нужно
    любая роль)                     открыть доступ», 02.10.2026)
    тренер                          как операторы своего отдела («тренеру можно
                                    выдать доступ как и операторам», 01.10.2026):
                                    тренер СЗоВ — права СЗоВ, тренер ОП — права
                                    ОП, тренер ТЭЗ закрыт
    ТЭЗ КЦ (отдел tez)              раздел закрыт целиком — и главе, и админу
    остальные: офисники не из       раздел закрыт: им его не выдавали
    списка, другие отделы и их
    админы

«Журнал и настройки — супер-админам и руководителю» означает именно супер-
админов: админ-сотрудник СЗоВ, глобальный по семантике портала, входит сюда
своим отделом и получает права СЗоВ, а не руководителя.

Выдача — поимённо, а не всему отделу «Фронт офисы»: программа идёт в офисах
Алматы и Астаны, и владелец назвал выдающих по именам («на данный момент выдай
доступы по офисникам»). Список — только id: ФИО сотрудников в публичный
репозиторий не кладём. Зеркало во фронте — WATER_ISSUER_USER_IDS и
canAccessWaterSectionForUser в App.jsx; тест сверяет их, дополнять — в обоих
местах.

Где именно офисник выдаёт — доступ по офисам (владелец 23.10.2026: «из фронт-
офисников выбираем людей, которым открыт данный раздел, и выдаём доступ, чтобы
они могли выдавать воду только по доступным им офисам»). Список выше решает,
открыт ли человеку раздел; в каких офисах он выдаёт — строки
`water_issuer_offices`, их раздаёт руководитель в «Настройках». Сами строки
читает queries.issue_office_ids и кладёт в контекст (`issue_office_ids`);
здесь — только правило.

Хелперы ролей — общие с «Посылками»: семантика «глава отдела ≠ глобальный
админ» у портала одна, и второй её копии здесь быть не должно.
"""

from parcels.access import is_department_head, is_global_admin, normalize_role

ISSUE_DEPARTMENT_CODE = 'front_office'   # выдают воду (поимённо) и ведут учёт (глава)
CHECK_DEPARTMENT_CODES = ('szov', 'op')  # колл-центр СЗоВ и ОП: остатки, условия, проверка
CLOSED_DEPARTMENT_CODES = ('tez',)       # ТЭЗ КЦ — закрыт целиком

SECTION_DEPARTMENT_CODES = (ISSUE_DEPARTMENT_CODE,) + CHECK_DEPARTMENT_CODES

# Офисники, которые выдают воду: шесть в Алматы, двое в Астане (01.10.2026).
ISSUER_USER_IDS = (419, 421, 422, 423, 424, 509, 425, 426)


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _own(ctx):
    return str(ctx.get('department_code') or '').strip().lower()


def _role(ctx):
    return normalize_role(ctx.get('role'))


def _belongs_to(ctx, code):
    """Человек в этом отделе — своим членством или как его глава."""
    if code in _codes(ctx.get('headed_department_codes')):
        return True
    return _own(ctx) == code


def _user_id(ctx):
    try:
        return int(ctx.get('user_id'))
    except (TypeError, ValueError):
        return None


def is_super_admin(ctx):
    return _role(ctx) == 'super_admin'


def is_regional_manager(ctx):
    """Руководитель регионов / фронт-офисов — глава отдела «Фронт офисы»."""
    return is_department_head(ctx) and ISSUE_DEPARTMENT_CODE in _codes(
        ctx.get('headed_department_codes'))


def _in_closed_department(ctx):
    return any(_belongs_to(ctx, code) for code in CLOSED_DEPARTMENT_CODES)


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте: спрятанный пункт меню
    доступом не является, а ручки /api/water зовут и напрямую.

    Порядок значим: супер-админ проходит всегда; ТЭЗ закрыт раньше любых других
    оснований — его админ не должен пройти «как админ». Тренер проходит своим
    отделом, как операторы. Зеркало — canAccessWaterSectionForUser.
    """
    if is_super_admin(ctx):
        return True
    if _in_closed_department(ctx):
        return False
    if is_regional_manager(ctx):
        return True
    if _own(ctx) == ISSUE_DEPARTMENT_CODE:
        return _user_id(ctx) in ISSUER_USER_IDS
    return any(_belongs_to(ctx, code) for code in CHECK_DEPARTMENT_CODES)


def can_issue(ctx):
    """Выдаёт ли человек воду вообще: супер-админ, руководитель и офисники из
    списка. В КАКОМ офисе — can_issue_in_office: офисник выдаёт только там,
    где ему выдан доступ.

    СЗоВ и ОП сюда не попадают ни в какой роли: они проверяют право и
    смотрят остатки, а вода физически выдаётся в офисе.
    """
    if not can_open_section(ctx):
        return False
    if is_super_admin(ctx) or is_regional_manager(ctx):
        return True
    return _own(ctx) == ISSUE_DEPARTMENT_CODE and _user_id(ctx) in ISSUER_USER_IDS


def issues_by_office_grant(ctx):
    """Выдаёт только в офисах, к которым выдан доступ, — офисник из списка.
    Руководителю и супер-админу доступ по офисам не нужен: учёт ведут они, и
    выдают они в любом офисе. Им же раздавать доступ (can_manage)."""
    return can_issue(ctx) and not (is_super_admin(ctx) or is_regional_manager(ctx))


def issue_office_ids(ctx):
    """Офисы учёта (`water_offices.id`), где человек выдаёт воду.

    None — любой офис (руководитель, супер-админ). Список — офисник: строки
    доступа из контекста; их там нет (не прочитались, раздел развернулся не
    до конца) — пустой список, а не «любой»: правило «только по доступным
    офисам» без строк доступа ничего не открывает. Кто воду не выдаёт вовсе —
    тоже пустой список.
    """
    if not can_issue(ctx):
        return []
    if not issues_by_office_grant(ctx):
        return None
    ids = ctx.get('issue_office_ids') or []
    return sorted({int(value) for value in ids})


def can_issue_in_office(ctx, water_office_id):
    """Выдать воду именно в этом офисе."""
    allowed = issue_office_ids(ctx)
    if allowed is None:
        return can_issue(ctx)
    try:
        return int(water_office_id) in allowed
    except (TypeError, ValueError):
        return False


def can_manage(ctx):
    """Учёт целиком: офисы, поступления, пересчёт, отмена выдач, условия
    программы и пороги — супер-админ и руководитель регионов / фронт-офисов."""
    if not can_open_section(ctx):
        return False
    return is_super_admin(ctx) or is_regional_manager(ctx)


def can_view_journal(ctx):
    """Журнал выдач и его выгрузка — тем же, кому настройки (01.10.2026)."""
    return can_manage(ctx)


def requires_sensitive_qr(ctx):
    """QR-подтверждение сессии — оператору, как в «Посылках».

    На экране ФИО и телефон живого водителя из CRM, его парк и машина — те же
    персональные данные, ради которых закрыты «Посылки» и «Обращения». Ключ
    общий с ними: второй QR на один экран человек не различит.
    """
    if is_global_admin(ctx) or is_department_head(ctx):
        return False
    return _role(ctx) == 'operator'


def default_city(ctx):
    """Город сотрудника (`users.city`) — чтобы подставить его офис самому.

    Не ограничение: где офисник выдаёт воду, решает доступ по офисам
    (issue_office_ids) — подменить коллегу из соседнего города в отпуск
    руководитель открывает ему и тот офис.
    """
    city = str(ctx.get('city') or '').strip()
    return city or None


def capabilities(ctx):
    """Сводка для фронта: раздел рисует вкладки и кнопки по ней, а не по роли.

    issue_office_ids — офисы, которые офиснику показывать в «Выдаче»; None —
    все офисы учёта (руководитель, супер-админ).
    """
    return {
        'can_open': can_open_section(ctx),
        'can_issue': can_issue(ctx),
        'issue_office_ids': issue_office_ids(ctx),
        'can_manage': can_manage(ctx),
        'can_view_journal': can_view_journal(ctx),
        'requires_qr': requires_sensitive_qr(ctx),
        'is_global_admin': is_global_admin(ctx),
        'default_city': default_city(ctx),
    }
