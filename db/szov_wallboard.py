"""База данных: табло и отбивки СЗоВ.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    _UNSET_GROUP,
    Json,
    json,
)


class SzovWallboardMixin:
    # --- Последний снимок «Табло СЗоВ» -----------------------------------------------------

    def get_szov_wallboard_snapshot(self, direction: str = 'osnova'):
        """(payload, captured_at) последнего удачного снимка табло или (None, None).

        Нужен только на холодном старте процесса: пока источник не ответил, экран показывает
        этот снимок с пометкой «данные устарели» вместо пустоты.

        direction — какое табло: 'osnova' (линия СЗоВ, Oktell) или 'tez' (ТП и ОП, Binotel).
        Дефолт сохраняет прежние вызовы: у линии направление подразумевалось молчаливо."""
        with self._get_cursor() as cur:
            cur.execute("SELECT payload, captured_at FROM szov_wallboard_snapshot WHERE direction = %s",
                        (str(direction or 'osnova'),))
            row = cur.fetchone()
        if not row or not row[0]:
            return None, None
        return row[0], float(row[1] or 0.0)

    def save_szov_wallboard_snapshot(self, payload: dict, captured_at: float,
                                     direction: str = 'osnova') -> None:
        """Перезаписать снимок табло. На направление ровно одна строка — история табло не нужна."""
        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO szov_wallboard_snapshot (direction, payload, captured_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (direction) DO UPDATE
                SET payload = EXCLUDED.payload,
                    captured_at = EXCLUDED.captured_at
            """, (str(direction or 'osnova'), Json(payload or {}), float(captured_at or 0.0)))

    def _szov_broadcast_direction(self, value) -> str:
        """Направление отбивки. Пусто = «Линия».

        Пустое значение допускаем намеренно: фронт деплоится отдельно от бэкенда
        (GitHub Pages против Render), и старый бандл, ничего не знающий о втором
        направлении, обязан продолжать настраивать «Линию», а не падать."""
        direction = str(value or self.SZOV_BROADCAST_DIRECTION_DEFAULT).strip()
        if direction not in self.SZOV_BROADCAST_DIRECTIONS:
            raise ValueError("Неизвестное направление табло")
        return direction

    def get_szov_broadcast_chats(self, direction=None) -> list:
        """Получатели отбивки табло: куда, в каком режиме и включён ли чат."""
        direction = self._szov_broadcast_direction(direction)
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT c.chat_id, c.chat_title, c.mode, c.is_enabled,
                       c.updated_by, c.updated_at, u.name, c.group_id
                FROM szov_wallboard_broadcast_chats c
                LEFT JOIN users u ON u.id = c.updated_by
                WHERE c.direction = %s
                ORDER BY COALESCE(NULLIF(c.chat_title, ''), c.chat_id::text)
            """, (direction,))
            rows = cur.fetchall()
        return [{
            "chat_id": r[0],
            "chat_title": r[1] or "",
            "mode": r[2] or "always",
            "is_enabled": bool(r[3]),
            "updated_by": r[4],
            "updated_by_name": r[6],
            "updated_at": r[5].isoformat() if r[5] else None,
            # Группа отбивки «Табло ОП»; None — весь отдел (и всегда None у других табло).
            "group_id": r[7],
        } for r in rows]

    def save_szov_broadcast_chat(self, payload: dict, user_id=None, direction=None) -> list:
        """Добавить получателя или изменить его режим / включённость.

        Один чат — одна строка НА НАПРАВЛЕНИЕ: повторное добавление того же чата не
        плодит дубли, иначе отбивка приходила бы в него дважды. А вот один и тот же чат
        в «Линии» и в «Чате» — это две разные строки: отбивки разные."""
        payload = payload or {}
        direction = self._szov_broadcast_direction(direction or payload.get('direction'))
        raw = payload.get('chat_id')
        if raw in (None, '', 'null'):
            raise ValueError("Не выбран чат для отбивки")
        try:
            chat_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError("Не выбран чат для отбивки")

        existing = {row['chat_id']: row
                    for row in self.get_szov_broadcast_chats(direction)}.get(chat_id)
        # Не переданное поле означает «оставить как было»: форма шлёт точечные патчи
        # (переключили режим / щёлкнули тумблером), а не всю карточку получателя.
        mode = payload.get('mode')
        if mode is None:
            mode = (existing or {}).get('mode', 'always')
        mode = str(mode).strip()
        if mode not in self.SZOV_BROADCAST_MODES:
            raise ValueError("Неизвестный режим отправки")
        title = payload.get('chat_title')
        chat_title = str(title).strip()[:255] if title is not None else (existing or {}).get('chat_title', '')
        is_enabled = payload.get('is_enabled')
        is_enabled = bool(is_enabled) if is_enabled is not None else bool((existing or {}).get('is_enabled', True))
        # Группа отбивки: форма шлёт точечные патчи, поэтому отсутствие ключа — «не трогать».
        group_id = self._szov_broadcast_group(
            payload['group_id'] if 'group_id' in payload else _UNSET_GROUP, direction, existing)

        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO szov_wallboard_broadcast_chats
                    (direction, chat_id, chat_title, mode, is_enabled, group_id, updated_by, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                ON CONFLICT (direction, chat_id) DO UPDATE SET
                    chat_title = EXCLUDED.chat_title,
                    mode = EXCLUDED.mode,
                    is_enabled = EXCLUDED.is_enabled,
                    group_id = EXCLUDED.group_id,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = EXCLUDED.updated_at
            """, (direction, chat_id, chat_title, mode, is_enabled, group_id, user_id))
        chats = self.get_szov_broadcast_chats(direction)
        self._log_szov_broadcast_change(
            user_id,
            'added' if existing is None else 'changed',
            {"chat_id": chat_id, "chat_title": chat_title, "mode": mode, "is_enabled": is_enabled,
             "group_id": group_id},
            chats,
            direction,
        )
        return chats

    def delete_szov_broadcast_chat(self, chat_id, user_id=None, direction=None) -> list:
        """Убрать чат из получателей одного направления."""
        direction = self._szov_broadcast_direction(direction)
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            raise ValueError("Не выбран чат для отбивки")
        with self._get_cursor() as cur:
            cur.execute("""
                DELETE FROM szov_wallboard_broadcast_chats
                WHERE direction = %s AND chat_id = %s
                RETURNING chat_title, mode
            """, (direction, chat_id))
            removed = cur.fetchone()
        chats = self.get_szov_broadcast_chats(direction)
        if removed:
            self._log_szov_broadcast_change(
                user_id, 'removed',
                {"chat_id": chat_id, "chat_title": removed[0] or "", "mode": removed[1] or "always",
                 "is_enabled": False},
                chats,
                direction,
            )
        return chats

    def _log_szov_broadcast_change(self, user_id, action: str, target: dict, chats: list,
                                   direction=None) -> None:
        """Аудит: что именно сделали и каким после этого стал весь список получателей.

        Снимок целиком, а не diff: строк мало, зато видно, кому уходила отбивка на
        момент каждого изменения."""
        snapshot = json.dumps({
            "action": action,
            **target,
            "chats": [{"chat_id": c["chat_id"], "chat_title": c["chat_title"],
                       "mode": c["mode"], "is_enabled": c["is_enabled"],
                       "group_id": c.get("group_id")} for c in chats],
        })
        with self._get_cursor() as cur:
            cur.execute("""
                INSERT INTO szov_wallboard_broadcast_history (changed_by, settings, direction)
                VALUES (%s, %s::jsonb, %s)
            """, (user_id, snapshot, self._szov_broadcast_direction(direction)))

    def get_szov_broadcast_history(self, limit: int = 50, direction=None) -> list:
        """Кто и когда менял получателей отбивки этого направления."""
        limit = max(1, min(int(limit or 50), 200))
        direction = self._szov_broadcast_direction(direction)
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT h.changed_at, h.changed_by, u.name, h.settings
                FROM szov_wallboard_broadcast_history h
                LEFT JOIN users u ON u.id = h.changed_by
                WHERE h.direction = %s
                ORDER BY h.changed_at DESC, h.id DESC
                LIMIT %s
            """, (direction, limit))
            rows = cur.fetchall()
        return [{
            "changed_at": r[0].isoformat() if r[0] else None,
            "changed_by": r[1],
            "changed_by_name": r[2],
            "settings": r[3] or {},
        } for r in rows]

    def _broadcast_personal_columns(self, direction):
        columns = self.BROADCAST_PERSONAL_COLUMNS.get(str(direction or '').strip())
        if columns is None:
            raise ValueError("У этого табло нет отбивки лично себе")
        return columns
