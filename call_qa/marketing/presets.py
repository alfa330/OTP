# -*- coding: utf-8 -*-
"""Пресеты фильтров «ИИ-оценки» (ТЗ #317, раздел 3): личные и общие для отдела.

Пресет — это сохранённый ОТБОР целиком: и обычные оси раздела (период,
направление, сотрудник, балл), и маркетинговые. Сохранять только маркетинговую
часть было бы неправильно: «TikTok за прошлую неделю без оценки человека» —
одно намерение, и восстанавливать его надо целиком.

Хранится то, что прислал фронт, после ТОЙ ЖЕ проверки, что у списков
(`normalise_list_filters`): пресет с невалидным значением иначе восстанавливался
бы в панель и падал 400 на первом же запросе — уже без объяснения, откуда взялось
кривое значение.

Права
-----
* Свой пресет видит, правит и удаляет только его автор.
* Общий пресет (`is_shared`) видят все, кому открыт этот отдел в разделе; завести
  или снять его может руководитель маркетинга или админ. Аналитик общий пресет
  видит, но не трогает — так в ТЗ.
"""

import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

MAX_NAME = 80
MAX_PRESETS_PER_USER = 50


def _clean_name(name):
    text = ' '.join(str(name or '').split())
    if not text:
        raise ValueError("У пресета должно быть имя")
    if len(text) > MAX_NAME:
        raise ValueError(f"Имя пресета длиннее {MAX_NAME} символов")
    return text


def _row(record):
    (preset_id, owner_id, department, name, payload, is_shared,
     created_at, updated_at, owner_name) = record
    return {
        "id": int(preset_id), "owner_id": int(owner_id), "owner": owner_name or "",
        "department": department or "", "name": name,
        "filters": payload if isinstance(payload, dict) else {},
        "shared": bool(is_shared),
        "created_at": created_at.isoformat(timespec='minutes') if created_at else None,
        "updated_at": updated_at.isoformat(timespec='minutes') if updated_at else None,
    }


def list_presets(cur, *, user_id, department):
    """Свои + общие этого отдела. Сначала свои, внутри — по имени."""
    cur.execute(
        """
        SELECT p.id, p.owner_id, p.department, p.name, p.payload, p.is_shared,
               p.created_at, p.updated_at, u.name
          FROM qa_filter_presets p
          LEFT JOIN users u ON u.id = p.owner_id
         WHERE p.department = %s
           AND (p.owner_id = %s OR p.is_shared)
         ORDER BY (p.owner_id = %s) DESC, lower(p.name)
        """,
        (department or '', int(user_id), int(user_id)))
    return [_row(record) for record in cur.fetchall()]


def save_preset(cur, *, user_id, department, name, filters, shared, can_share):
    """Создать или перезаписать пресет с тем же именем. Возвращает строку.

    Перезапись по имени — намеренно: «Сохранить» с тем же названием обязано
    обновить отбор, а не плодить одноимённые строки в списке.
    """
    name = _clean_name(name)
    if shared and not can_share:
        raise PermissionError("Общие пресеты сохраняет руководитель маркетинга")
    if not isinstance(filters, dict):
        raise ValueError("filters: ожидается объект")

    cur.execute(
        "SELECT COUNT(*) FROM qa_filter_presets WHERE owner_id = %s AND department = %s",
        (int(user_id), department or ''))
    if int(cur.fetchone()[0] or 0) >= MAX_PRESETS_PER_USER:
        cur.execute(
            """SELECT 1 FROM qa_filter_presets
                WHERE owner_id = %s AND department = %s AND lower(name) = lower(%s)""",
            (int(user_id), department or '', name))
        if not cur.fetchone():
            raise ValueError(f"Больше {MAX_PRESETS_PER_USER} пресетов на человека не нужно")

    cur.execute(
        """
        INSERT INTO qa_filter_presets (owner_id, department, name, payload, is_shared)
        VALUES (%s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (owner_id, department, lower(name)) DO UPDATE
           SET payload = EXCLUDED.payload,
               is_shared = EXCLUDED.is_shared,
               name = EXCLUDED.name,
               updated_at = NOW()
        RETURNING id
        """,
        (int(user_id), department or '', name, json.dumps(filters, ensure_ascii=False),
         bool(shared)))
    preset_id = int(cur.fetchone()[0])
    return get_preset(cur, preset_id)


def get_preset(cur, preset_id):
    cur.execute(
        """
        SELECT p.id, p.owner_id, p.department, p.name, p.payload, p.is_shared,
               p.created_at, p.updated_at, u.name
          FROM qa_filter_presets p
          LEFT JOIN users u ON u.id = p.owner_id
         WHERE p.id = %s
        """, (int(preset_id),))
    record = cur.fetchone()
    return _row(record) if record else None


def delete_preset(cur, *, preset_id, user_id, can_share):
    """Удалить свой пресет; общий чужой — только тому, кто вправе делать общие."""
    current = get_preset(cur, preset_id)
    if not current:
        return False
    own = current["owner_id"] == int(user_id)
    if not own and not (current["shared"] and can_share):
        raise PermissionError("Чужой пресет удалить нельзя")
    cur.execute("DELETE FROM qa_filter_presets WHERE id = %s", (int(preset_id),))
    return True
