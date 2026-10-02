"""Права раздела «Списки Байги». Чистая логика: ни базы, ни Flask.

Роли из постановки #356 (п. 2) ложатся на портал так:

    аналитик                  загрузка, замена и удаление недели, поиск,
                              выгрузка, журнал — поимённо (ANALYST_USER_IDS)
    маркетинг (marketing)     поиск и выгрузка
    руководители              поиск и выгрузка — глава любого отдела
    поддержка: СЗоВ (szov)    только поиск и просмотр
    глобальный админ          всё
    тренер                    не входит: раздел он не просил

«Аналитика» как роли или отдела в портале нет, поэтому он задаётся списком id —
тот же приём, что у ведущих «Библиотеки». Пока список пуст, загружает
глобальный админ; кого вписать, ответит постановщик.

В строках ФИО и номер ВУ, поэтому у операторов раздел закрыт QR-замком —
правило то же, что у «Посылок» (parcels.access.requires_sensitive_qr): гейт
общий, ключ один, двух разных QR на один экран человек не различит.

Хелперы ролей общие с «Посылками»: семантика «глава отдела ≠ глобальный админ»
у портала одна.
"""

from parcels.access import (  # noqa: F401 — requires_sensitive_qr отдаётся дальше
    is_department_head, is_global_admin, normalize_role, requires_sensitive_qr,
)

# ПИЛОТ (02.10.2026, так же выкладывались «Термокороба»): пока флаг стоит, раздел
# открыт только супер-админу; всё, что ниже, — периметр ПОСЛЕ пилота, его
# утверждает постановщик. Снимать флаг вместе с зеркалом во фронте
# (BAIGA_PILOT_SUPER_ADMIN_ONLY в App.jsx) — тест сверяет их.
PILOT_SUPER_ADMIN_ONLY = True

# Аналитики поимённо — только id, ФИО в публичный репозиторий не кладём.
# Зеркало — BAIGA_ANALYST_USER_IDS в App.jsx (аналитику нужен пункт меню).
ANALYST_USER_IDS = frozenset()

EXPORT_DEPARTMENT_CODES = ('marketing',)   # ищут и выгружают
VIEW_DEPARTMENT_CODES = ('szov',)          # поддержка: ищут и смотрят

SECTION_DEPARTMENT_CODES = EXPORT_DEPARTMENT_CODES + VIEW_DEPARTMENT_CODES


def _codes(values):
    return {str(code).strip().lower() for code in (values or []) if code}


def _belongs_to(ctx, codes):
    """Человек в одном из отделов — своим членством или как его глава."""
    if _codes(ctx.get('headed_department_codes')) & set(codes):
        return True
    return str(ctx.get('department_code') or '').strip().lower() in codes


def is_analyst(ctx):
    try:
        return int(ctx.get('user_id')) in ANALYST_USER_IDS
    except (TypeError, ValueError):
        return False


def can_open_section(ctx):
    """Пускать ли в раздел. Проверяется на КАЖДОМ роуте: спрятанный пункт меню
    доступом не является. Зеркало — canAccessBaigaSectionForUser в App.jsx."""
    if PILOT_SUPER_ADMIN_ONLY:
        return normalize_role(ctx.get('role')) == 'super_admin'
    if is_global_admin(ctx) or is_analyst(ctx):
        return True
    if normalize_role(ctx.get('role')) == 'trainer':
        return False
    # «Руководители» — глава любого отдела: итоги акции смотрят все руководители,
    # а не только маркетинга.
    if is_department_head(ctx):
        return True
    return _belongs_to(ctx, SECTION_DEPARTMENT_CODES)


def can_export(ctx):
    """Выгрузить выборку в Excel — маркетинг, руководители, аналитик, админ.

    Поддержка сюда не входит: «операторы поддержки — только поиск и просмотр».
    """
    if is_global_admin(ctx) or is_analyst(ctx) or is_department_head(ctx):
        return True
    return _belongs_to(ctx, EXPORT_DEPARTMENT_CODES)


def can_manage(ctx):
    """Загрузить, заменить и удалить неделю, журнал, исходник — аналитик и
    глобальный админ."""
    return is_global_admin(ctx) or is_analyst(ctx)


def capabilities(ctx):
    """Сводка для фронта: экран рисует кнопки по ней, а не по роли.

    Кто в раздел не пущен (пилот, чужой отдел), не получает и прав внутри: иначе
    сводка обещала бы кнопки, на которые каждая ручка ответит 403.
    """
    opened = can_open_section(ctx)
    return {
        'can_open': opened,
        'can_export': opened and can_export(ctx),
        'can_manage': opened and can_manage(ctx),
    }
