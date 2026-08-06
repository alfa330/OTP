"""Правила тишины одним срезом.

Опрос сверяет каждое нарушение с каждым чатом, поэтому правила читаем один раз
за прогон и дальше сопоставляем в памяти. Совпадение — по вхождению строки:
«Иванов» гасит и «Иванов Иван Иванович», как было в прежнем сервисе.
"""


def _matches(value: str, muted_values: set[str]) -> bool:
    if not value:
        return False
    value_lower = value.strip().lower()
    return any(muted.lower() in value_lower for muted in muted_values)


class MuteSnapshot:
    def __init__(self, rows: list[dict]):
        self.global_all = False
        self.global_users: set[str] = set()
        self.global_depts: set[str] = set()
        self.by_chat: dict[str, dict] = {}

        for row in rows or []:
            chat_id = row.get("chat_id")
            kind = row.get("mute_kind")
            value = str(row.get("mute_value") or "")
            if chat_id:
                scope = self.by_chat.setdefault(
                    str(chat_id), {"all": False, "users": set(), "depts": set()}
                )
                if kind == "all":
                    scope["all"] = True
                elif kind == "user":
                    scope["users"].add(value)
                elif kind == "dept":
                    scope["depts"].add(value)
            else:
                if kind == "all":
                    self.global_all = True
                elif kind == "user":
                    self.global_users.add(value)
                elif kind == "dept":
                    self.global_depts.add(value)

    def is_globally_muted(self, user_name: str, department_name: str) -> bool:
        return _matches(user_name, self.global_users) or _matches(department_name, self.global_depts)

    def is_event_muted_for_chat(self, chat_id: str, user_name: str, department_name: str) -> bool:
        if self.global_all:
            return True
        scope = self.by_chat.get(str(chat_id))
        if not scope:
            return False
        return (
            scope["all"]
            or _matches(user_name, scope["users"])
            or _matches(department_name, scope["depts"])
        )
