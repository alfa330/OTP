"""SQL «Моих данных».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют
транзакцией — её держит вызывающий, как в sign_links и parcels. Имена колонок
подставляются только из fields.FIELDS — строкового ввода в SQL нет.
"""

from .fields import FIELDS

_COLUMNS = ', '.join(FIELDS)


def load(cursor, user_id, *, for_update=False):
    """Шесть полей сотрудника из users или None, если такого нет.

    users — источник истины: «Учет сотрудников» пишет поле в обе таблицы, но
    читает отсюда (Database.update_user), и расхождений между ними на проде ноль.
    FOR UPDATE — при сохранении: иначе две вкладки, сохранённые разом, записали
    бы в историю одно и то же «было» дважды.
    """
    cursor.execute(
        f"SELECT {_COLUMNS} FROM users WHERE id = %s" + (" FOR UPDATE" if for_update else ""),
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(FIELDS, row))


def _as_history_text(value):
    return None if value is None else str(value)


def save(cursor, user_id, changes):
    """Записывает правку оператора. Возвращает (итоговые поля, изменённые) или None.

    - пишет только то, что действительно поменялось, — повторное «Сохранить»
      не плодит пустых строк истории;
    - обе копии поля (users и user_hr_profiles) — одним заходом, как
      Database.update_user, иначе «Учет сотрудников» разошёлся бы с профилем;
    - специальность живёт только вместе с местом учёбы (задача #279): стёрли
      университет — стирается и специальность, с отдельной строкой истории;
    - автор правки в истории — сам оператор.
    """
    current = load(cursor, user_id, for_update=True)
    if current is None:
        return None

    final = dict(current)
    for field in FIELDS:
        if field in changes:
            final[field] = changes[field]
    if not final['study_place']:
        final['study_specialty'] = None

    changed = [field for field in FIELDS if final[field] != current[field]]
    if not changed:
        return final, []

    assignments = ', '.join(f"{field} = %s" for field in changed)
    values = [final[field] for field in changed]
    cursor.execute(f"UPDATE users SET {assignments} WHERE id = %s", (*values, user_id))
    # Строка профиля есть у всех (бэкофилл на старте), но правка не должна
    # зависеть от того, успел ли он пройти для только что заведённого человека.
    cursor.execute(
        "INSERT INTO user_hr_profiles (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
        (user_id,),
    )
    cursor.execute(f"UPDATE user_hr_profiles SET {assignments} WHERE user_id = %s", (*values, user_id))

    rows_sql = ', '.join(['(%s, %s, %s, %s, %s)'] * len(changed))
    params = []
    for field in changed:
        params.extend((
            user_id, user_id, field,
            _as_history_text(current[field]), _as_history_text(final[field]),
        ))
    cursor.execute(
        "INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value) "
        f"VALUES {rows_sql}",
        params,
    )
    return final, changed
