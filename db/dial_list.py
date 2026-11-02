"""База данных: обзвон.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    logging,
    Optional,
)


class DialListMixin:
    def find_dial_list_line_holder(self, sip_login='', number='', domain='',
                                   exclude_user_ids=None) -> Optional[dict]:
        """Кто из сотрудников сидит на линии Binotel по привязке раздела «Удаленный КЦ».

        В том разделе на линию сажают и сотрудника ДРУГОГО отдела; учётка линии тогда
        лежит не в его SIP-настройках, а в dial_list_line_members, и проверки
        find_sip_number_owners / find_sip_login_owner такой занятости не видят.
        Ищем по SIP-логину линии либо по паре «номер + сервер отдела линии». Уволенный
        линию не держит. Любой сбой (раздел не развёрнут) — None: сохранение
        SIP-настроек из-за соседнего раздела останавливаться не должно."""
        sip_login = str(sip_login or '').strip()
        number = str(number or '').strip()
        domain = str(domain or '').strip().lower()
        if not sip_login and not (number and domain):
            return None
        exclude = sorted({int(u) for u in (exclude_user_ids or []) if u is not None})
        try:
            with self._get_cursor() as cur:
                cur.execute("""
                    SELECT u.id, u.name, m.internal_number
                    FROM dial_list_line_members m
                    JOIN users u ON u.id = m.user_id
                    LEFT JOIN sip_department_config dc ON dc.department_id = m.department_id
                    WHERE m.released_at IS NULL
                      AND LOWER(COALESCE(u.status, '')) <> ALL(%s)
                      AND u.id <> ALL(%s)
                      AND ((%s <> '' AND LOWER(TRIM(m.sip_login)) = LOWER(%s))
                           OR (%s <> '' AND TRIM(m.internal_number) = %s
                               AND LOWER(TRIM(COALESCE(dc.sip_server, ''))) = %s))
                    LIMIT 1
                """, (list(self._SIP_INACTIVE_STATUSES), exclude, sip_login, sip_login,
                      number if domain else '', number, domain))
                row = cur.fetchone()
        except Exception:
            logging.exception("SIP: не удалось проверить привязки к линиям раздела «Удаленный КЦ»")
            return None
        if not row:
            return None
        return {"user_id": row[0], "name": row[1] or "", "internal_number": (row[2] or "").strip()}
