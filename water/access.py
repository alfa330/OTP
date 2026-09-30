"""Права раздела «Учёт воды». Чистая логика: ни базы, ни Flask.

Роли ТЗ ложатся на портал так:

    региональный руководитель       глава отдела «Фронт офисы»; он же
    (он же «ответственный»)         «ответственный сотрудник компании» —
                                    ответ постановщика 29.09.2026. Заводит офисы
                                    в учёт, проводит поступления и пересчёты,
                                    меняет условия программы и пороги
    фронт-офисы (front_office)      выдают воду, видят остатки и журнал
    колл-центр СЗоВ (szov)          проверяют право водителя, видят остатки и
                                    журнал; воду не выдают — её выдают в офисе
    глобальный админ                всё
    тренер                          не входит (раздел не просил)

Периметр — ОТДЕЛ, а не роль, как у «Посылок»: выдаёт тот, кто сидит в офисе,
проверяет тот, кому позвонил водитель. Роль внутри отдела значения не имеет.
Сотрудники к офисам в iCore не привязаны, поэтому граница «только свой офис»
здесь не проводится: офис выбирается при выдаче, свой город подставляется сам.

Хелперы ролей — общие с «Посылками»: семантика «глава отдела ≠ глобальный
админ» у портала одна, и второй её копии здесь быть не должно.
"""

from parcels.access import is_department_head, is_global_admin, normalize_role

# ПИЛОТ (30.09.2026): владелец проверяет раздел сам — «задеплой для меня, чтобы
# я мог проверить». Пока флаг стоит, раздел открыт только супер-админу; всё, что
# ниже, — периметр ПОСЛЕ пилота. Снимать флаг вместе с зеркалом во фронте
# (WATER_PILOT_SUPER_ADMIN_ONLY в App.jsx) — тест сверяет их.
PILOT_SUPER_ADMIN_ONLY = True

ISSUE_DEPARTMENT_CODE = 'front_office'   # выдают воду
CHECK_DEPARTMENT_CODE = 'szov'           # колл-центр: проверяют и смотрят

SECTION_DEPARTMENT_CODES = (ISSUE_DEPARTMENT_CODE, CHECK_DEPARTMENT_CODE)


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _belongs_to(ctx, code):
    """Человек в этом отделе — своим членством или как его глава."""
    if code in _codes(ctx.get('headed_department_codes')):
        return True
    return str(ctx.get('department_code') or '').strip().lower() == code


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте: спрятанный пункт меню
    доступом не является, а ручки /api/water зовут и напрямую.

    Тренер не входит ни в каком отделе: раздел он не просил, а правило
    владельца — реализовывать буквально (тренеру «Посылки» открыли только по
    его отдельной задаче #360). Зеркало — canAccessWaterSectionForUser.
    """
    if PILOT_SUPER_ADMIN_ONLY:
        return normalize_role(ctx.get('role')) == 'super_admin'
    if is_global_admin(ctx):
        return True
    if normalize_role(ctx.get('role')) == 'trainer':
        return False
    return any(_belongs_to(ctx, code) for code in SECTION_DEPARTMENT_CODES)


def can_issue(ctx):
    """Выдать воду водителю — фронт-офисы и глобальный админ.

    СЗоВ сюда не попадает ни в какой роли: по ТЗ колл-центр «проверяет право и
    смотрит информацию о выдачах», а вода физически выдаётся в офисе.
    """
    if is_global_admin(ctx):
        return True
    return _belongs_to(ctx, ISSUE_DEPARTMENT_CODE)


def can_manage(ctx):
    """Учёт целиком: офисы, поступления, пересчёт, условия программы, пороги.

    Глава фронт-офисов и глобальный админ. Глава СЗоВ сюда не входит: его отдел
    воду не выдаёт и не получает, и менять ей счёт ему незачем.
    """
    if is_global_admin(ctx):
        return True
    return is_department_head(ctx) and ISSUE_DEPARTMENT_CODE in _codes(
        ctx.get('headed_department_codes'))


def requires_sensitive_qr(ctx):
    """QR-подтверждение сессии — оператору, как в «Посылках».

    На экране ФИО и телефон живого водителя из CRM, его парк и машина — те же
    персональные данные, ради которых закрыты «Посылки» и «Обращения». Ключ
    общий с ними: второй QR на один экран человек не различит.
    """
    if is_global_admin(ctx) or is_department_head(ctx):
        return False
    return normalize_role(ctx.get('role')) == 'operator'


def default_city(ctx):
    """Город сотрудника (`users.city`) — чтобы подставить его офис самому.

    Не ограничение: подменять коллегу из соседнего города приходится каждый
    отпуск, поэтому выдать воду можно в любом офисе учёта.
    """
    city = str(ctx.get('city') or '').strip()
    return city or None


def capabilities(ctx):
    """Сводка для фронта: раздел рисует кнопки по ней, а не по роли."""
    return {
        'can_open': can_open_section(ctx),
        'can_issue': can_issue(ctx),
        'can_manage': can_manage(ctx),
        'requires_qr': requires_sensitive_qr(ctx),
        'is_global_admin': is_global_admin(ctx),
        'default_city': default_city(ctx),
    }
