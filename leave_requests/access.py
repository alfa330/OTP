# -*- coding: utf-8 -*-
"""Кому открыты заявки на отпуск, Б/С и больничный.

Пилот (решение владельца 03.11.2026): подавать и разбирать заявки могут только
люди из PILOT_USER_IDS, главы отделов и суперадмины — у последних полный доступ.
Остальные сотрудники отдела заявок не видят: ни пункта в «Новом запросе», ни
вкладки в «Запросах», ни Telegram, ни колокола. Шаг супервайзера в пилоте ведёт
СВ-участник того же отдела: СВ группы вне пилота заявку бы не увидел.

Открыть всем — PILOT_USER_IDS = None: дальше решают настройки отделов.

ФИО в репозиторий не пишем — он публичный; id сверены по проду 03.11.2026.
"""

PILOT_USER_IDS = frozenset({55, 56})


def allowed(user_id, role, headed=False):
    """Открыты ли заявки человеку: подать, разбирать, настраивать."""
    if PILOT_USER_IDS is None:
        return True
    role = str(role or '').strip().lower()
    if role in ('super_admin', 'superadmin', 'super-admin', 'super admin'):
        return True
    if headed:
        return True
    return user_id is not None and int(user_id) in PILOT_USER_IDS


def viewer_allowed(viewer):
    return allowed(viewer.get('user_id'), viewer.get('role'), bool(viewer.get('headed_department_ids')))


def allowed_ids(cursor, user_ids):
    """Кому из списка заявки открыты — для Telegram, колокола и шага СВ."""
    ids = sorted({int(u) for u in user_ids or () if u})
    if PILOT_USER_IDS is None or not ids:
        return set(ids)
    cursor.execute(
        """
        SELECT u.id, u.role,
               EXISTS (SELECT 1 FROM departments d
                        WHERE d.head_user_id = u.id AND COALESCE(d.is_active, TRUE))
          FROM users u
         WHERE u.id = ANY(%s)
        """,
        (ids,),
    )
    return {int(row[0]) for row in cursor.fetchall() if allowed(row[0], row[1], bool(row[2]))}


def pilot_supervisor(cursor, department_id, supervisor_id):
    """Кто ведёт шаг СВ: СВ группы, а в пилоте, если он не участник, — первый
    работающий СВ-участник отдела заявки. Такого нет — None, заявка идёт главе."""
    if PILOT_USER_IDS is None or supervisor_id is None or allowed_ids(cursor, [supervisor_id]):
        return supervisor_id
    cursor.execute(
        """
        SELECT u.id
          FROM users u
         WHERE u.id = ANY(%s)
           AND u.department_id = %s
           AND LOWER(COALESCE(u.role, '')) IN ('sv', 'supervisor')
           AND COALESCE(u.status, 'working') NOT IN ('fired', 'dismissal')
         ORDER BY u.id
         LIMIT 1
        """,
        (sorted(PILOT_USER_IDS), department_id),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None
