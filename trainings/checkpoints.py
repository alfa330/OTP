# -*- coding: utf-8 -*-
"""Контрольные точки по сотруднику — вкладка «Контроль» раздела «Тренинги».

Что это. Супервайзер провёл обратную связь по оценке звонка и решил, что
человека нужно взять на контроль: назначает дату повторной проверки, пишет,
почему берёт, и что именно проверит. До этого срок контроля жил в голове и в
переписке — и терялся.

Почему модуль отдельный и без Flask. Здесь только разбор входных данных,
SQL и форма ответа: это ровно та часть, которая должна быть проверена
тестами без поднятого приложения и базы. Сами роуты остались плоскими
@app.route в bot_schedule2.py рядом с остальным «Журналом оценок» — точку
создаёт та же ручка, что сохраняет ОС, и разносить одну операцию по двум
слоям было бы хуже, а не лучше.

ГЛАВНОЕ ПРАВИЛО РАЗДЕЛА — разная видимость (требование постановки задачи #86):

    сотруднику видно      дата проверки и `focus` («что нужно проверить»)
    сотруднику НЕ видно   вид контроля (в том числе «испытательный срок»),
                          причина постановки и внутренний комментарий СВ

Держится оно не комментарием в коде, а двумя РАЗНЫМИ сборщиками ответа —
`payload_for_manager` и `payload_for_operator`. Общего словаря с последующим
удалением ключей здесь нет намеренно: новое служебное поле, добавленное в
таблицу, попало бы в такой словарь само и утекло бы сотруднику молча. При двух
сборщиках оно просто не появится в его ответе, пока кто-то не впишет его туда
руками.
"""

from datetime import date, datetime

from .schema import CHECKPOINT_KINDS, CHECKPOINT_STATUSES, checkpoint_kind_label

# Потолок на текстовые поля. Тот же порядок, что у полей ОС в
# bot_schedule2::upsert_call_feedback (4000): здесь короче, потому что это
# пометки для себя и для сменщика, а не разбор звонка.
MAX_TEXT_LENGTH = 2000

# Насколько далеко вперёд разрешено назначать проверку. Год — не техническое
# ограничение, а защита от опечатки в году (2027 вместо 2026): такая точка
# просто исчезла бы из поля зрения, и контроль потерялся бы ровно так же, как
# без неё.
MAX_DAYS_AHEAD = 366


class CheckpointError(ValueError):
    """Ошибка разбора блока «Контрольная точка». Текст — уже для пользователя."""


def _clean_text(value):
    return str(value or '').strip()


def _parse_due_date(raw):
    text = _clean_text(raw)
    if not text:
        raise CheckpointError('Укажите дату следующей проверки')
    try:
        return datetime.strptime(text[:10], '%Y-%m-%d').date()
    except ValueError:
        raise CheckpointError('Неверный формат даты проверки. Нужен ГГГГ-ММ-ДД')


def parse_checkpoint_input(raw, *, today=None):
    """Разбирает блок «Контрольная точка» из тела запроса на сохранение ОС.

    Возвращает словарь полей либо None, если тумблер выключен (или блока нет
    вовсе — так приходят старые клиенты и любой другой код, который про точки
    не знает). Ошибку бросает как CheckpointError с готовым русским текстом.

    Обязательность полей проверяется ТОЛЬКО при включённом тумблере — это
    прямой критерий приёмки задачи: выключенный блок не должен мешать сохранить
    обычную обратную связь.
    """
    if not isinstance(raw, dict):
        return None
    # 'enabled' отсутствует → считаем блок выключенным. Молчаливое «а вдруг он
    # имел в виду включить» здесь недопустимо: точка ставит человека на
    # контроль, и включать её за пользователя нельзя.
    if not bool(raw.get('enabled')):
        return None

    kind = _clean_text(raw.get('kind'))
    if kind not in CHECKPOINT_KINDS:
        raise CheckpointError('Выберите тип контрольной точки')

    reason = _clean_text(raw.get('reason'))
    if not reason:
        raise CheckpointError('Укажите причину постановки на контроль')
    if len(reason) > MAX_TEXT_LENGTH:
        raise CheckpointError('Причина слишком длинная (максимум %d символов)' % MAX_TEXT_LENGTH)

    focus = _clean_text(raw.get('focus'))
    if not focus:
        raise CheckpointError('Укажите, что нужно проверить повторно')
    if len(focus) > MAX_TEXT_LENGTH:
        raise CheckpointError('Поле «Что проверить» слишком длинное (максимум %d символов)'
                              % MAX_TEXT_LENGTH)

    internal_comment = _clean_text(raw.get('internal_comment'))
    if len(internal_comment) > MAX_TEXT_LENGTH:
        raise CheckpointError('Внутренний комментарий слишком длинный (максимум %d символов)'
                              % MAX_TEXT_LENGTH)

    due_date = _parse_due_date(raw.get('due_date'))
    today = today or date.today()
    if due_date < today:
        raise CheckpointError('Дата следующей проверки не может быть в прошлом')
    if (due_date - today).days > MAX_DAYS_AHEAD:
        raise CheckpointError('Дата следующей проверки слишком далеко — проверьте год')

    return {
        'kind': kind,
        'reason': reason,
        'due_date': due_date,
        'focus': focus,
        'internal_comment': internal_comment or None,
        # По умолчанию сотрудника предупреждаем: смысл контрольной точки в том,
        # чтобы человек знал, что исправить к проверке. Выключается осознанно —
        # тумблером в том же блоке.
        'notify_operator': bool(raw.get('notify_operator', True)),
    }


# ── Запись ──────────────────────────────────────────────────────────────────

_SELECT_COLUMNS = """
    c.id,
    c.operator_id,
    c.supervisor_id,
    c.feedback_id,
    c.call_id,
    c.kind,
    c.reason,
    c.due_date,
    c.focus,
    c.internal_comment,
    c.notify_operator,
    c.status,
    c.resolved_at,
    c.resolved_by,
    c.resolution_comment,
    TO_CHAR(c.created_at, 'YYYY-MM-DD HH24:MI') AS created_at_text,
    TO_CHAR(c.updated_at, 'YYYY-MM-DD HH24:MI') AS updated_at_text,
    op.name  AS operator_name,
    op.status AS operator_status,
    sv.name  AS supervisor_name,
    rb.name  AS resolved_by_name
"""

_FROM_JOINS = """
    FROM operator_checkpoints c
    JOIN users op ON op.id = c.operator_id
    LEFT JOIN users sv ON sv.id = c.supervisor_id
    LEFT JOIN users rb ON rb.id = c.resolved_by
"""


def _row_to_dict(row):
    due = row[7]
    resolved_at = row[12]
    return {
        'id': int(row[0]),
        'operator_id': int(row[1]) if row[1] is not None else None,
        'supervisor_id': int(row[2]) if row[2] is not None else None,
        'feedback_id': int(row[3]) if row[3] is not None else None,
        'call_id': int(row[4]) if row[4] is not None else None,
        'kind': row[5],
        'reason': row[6] or '',
        'due_date': due.strftime('%Y-%m-%d') if hasattr(due, 'strftime') else (due or None),
        'focus': row[8] or '',
        'internal_comment': row[9] or '',
        'notify_operator': bool(row[10]),
        'status': row[11] or 'open',
        'resolved_at': (resolved_at.strftime('%Y-%m-%d %H:%M')
                        if hasattr(resolved_at, 'strftime') else (resolved_at or None)),
        'resolved_by': int(row[13]) if row[13] is not None else None,
        'resolution_comment': row[14] or '',
        'created_at': row[15],
        'updated_at': row[16],
        'operator_name': row[17] or '',
        'operator_status': row[18],
        'supervisor_name': row[19] or '',
        'resolved_by_name': row[20] or '',
    }


def fetch_one(cursor, checkpoint_id):
    cursor.execute("SELECT %s %s WHERE c.id = %%s" % (_SELECT_COLUMNS, _FROM_JOINS),
                   (checkpoint_id,))
    row = cursor.fetchone()
    return _row_to_dict(row) if row else None


def fetch_by_feedback_ids(cursor, feedback_ids):
    """{feedback_id: точка} одним запросом на всю страницу журнала.

    Списком, а не запросом на строку: «Журнал оценок» рисует до сотни оценок за
    раз, и запрос на каждую превратил бы открытие месяца в сотню обращений к
    базе ради чипа в углу карточки.
    """
    ids = [int(value) for value in (feedback_ids or []) if value is not None]
    if not ids:
        return {}
    # DISTINCT ON: у одной обратной связи открытая точка одна, а закрытых
    # может накопиться несколько (проверили, не выправилось — назначили ещё).
    # Журналу нужна одна строка на оценку: живая, а если живой нет — последняя.
    cursor.execute(
        """
        SELECT DISTINCT ON (c.feedback_id) %s %s
         WHERE c.feedback_id = ANY(%%s)
         ORDER BY c.feedback_id, (c.status = 'open') DESC, c.id DESC
        """ % (_SELECT_COLUMNS, _FROM_JOINS),
        (ids,),
    )
    result = {}
    for row in cursor.fetchall():
        item = _row_to_dict(row)
        result[item['feedback_id']] = item
    return result


def open_checkpoint_for_operator(cursor, operator_id, *, exclude_feedback_id=None):
    """Открытая точка сотрудника, если она уже есть.

    Нужна не для запрета, а для подсказки в окне ОС: «сотрудник уже на контроле
    до 02.09». Запрещать второй контроль нельзя — оценки бывают у разных
    супервайзеров и по разным поводам, — но и ставить его вслепую незачем.
    """
    params = [int(operator_id)]
    extra = ''
    if exclude_feedback_id is not None:
        extra = ' AND (c.feedback_id IS NULL OR c.feedback_id <> %s)'
        params.append(int(exclude_feedback_id))
    cursor.execute(
        "SELECT %s %s WHERE c.operator_id = %%s AND c.status = 'open'%s"
        " ORDER BY c.due_date, c.id LIMIT 1" % (_SELECT_COLUMNS, _FROM_JOINS, extra),
        tuple(params),
    )
    row = cursor.fetchone()
    return _row_to_dict(row) if row else None


def upsert_for_feedback(cursor, *, feedback_id, call_id, operator_id, supervisor_id,
                        requester_id, data):
    """Создаёт или обновляет ОТКРЫТУЮ точку, привязанную к этой обратной связи.

    Возвращает id. Уже закрытую точку повторное сохранение ОС не воскрешает:
    проведённая проверка — состоявшийся факт, и её история остаётся. Если
    супервайзер снова включил тумблер на той же ОС, заводится НОВАЯ точка, а
    прежняя остаётся в истории сотрудника как проведённая.
    """
    # Правим только ОТКРЫТУЮ точку. Закрытая — состоявшийся факт: проверку
    # провели, и повторное открытие окна ОС не должно переписывать её задним
    # числом. Если открытой нет, ниже заводится новая, а история остаётся.
    cursor.execute(
        "SELECT id FROM operator_checkpoints"
        " WHERE feedback_id = %s AND status = 'open' LIMIT 1",
        (feedback_id,),
    )
    existing = cursor.fetchone()
    if existing:
        cursor.execute(
            """
            UPDATE operator_checkpoints
               SET operator_id = %s,
                   supervisor_id = %s,
                   call_id = %s,
                   kind = %s,
                   reason = %s,
                   due_date = %s,
                   focus = %s,
                   internal_comment = %s,
                   notify_operator = %s,
                   status = 'open',
                   resolved_at = NULL,
                   resolved_by = NULL,
                   resolution_comment = NULL,
                   updated_by = %s,
                   updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
             WHERE id = %s
            RETURNING id
            """,
            (operator_id, supervisor_id, call_id, data['kind'], data['reason'],
             data['due_date'], data['focus'], data['internal_comment'],
             data['notify_operator'], requester_id, int(existing[0])),
        )
        return int(cursor.fetchone()[0])

    cursor.execute(
        """
        INSERT INTO operator_checkpoints (
            operator_id, supervisor_id, feedback_id, call_id,
            kind, reason, due_date, focus, internal_comment, notify_operator,
            created_by, updated_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (operator_id, supervisor_id, feedback_id, call_id,
         data['kind'], data['reason'], data['due_date'], data['focus'],
         data['internal_comment'], data['notify_operator'],
         requester_id, requester_id),
    )
    return int(cursor.fetchone()[0])


def drop_for_feedback(cursor, feedback_id):
    """Снять контроль: тумблер в окне ОС выключили.

    Удаляем строку, а не помечаем 'cancelled'. Точка живёт внутри окна ОС и
    ничего, кроме плана проверки, не хранит: снятый контроль — это «передумали
    ещё до того, как что-то произошло», и держать такую запись в истории значит
    показывать супервайзеру собственные черновики. Отменённый статус остаётся
    для контроля, который сняли ПОСЛЕ — из вкладки «Контроль», отдельным
    действием, там за ним стоит решение.

    Закрытые точки (проверка проведена или контроль снят) здесь НЕ трогаются:
    это состоявшиеся факты, и правка старой ОС не должна стирать историю.
    """
    cursor.execute(
        "DELETE FROM operator_checkpoints WHERE feedback_id = %s AND status = 'open'",
        (feedback_id,),
    )
    return cursor.rowcount


def resolve(cursor, checkpoint_id, *, requester_id, status, comment=None):
    """Закрыть точку: 'done' — проверку провели, 'cancelled' — контроль сняли."""
    if status not in ('done', 'cancelled'):
        raise CheckpointError('Неизвестный итог контрольной точки')
    cursor.execute(
        """
        UPDATE operator_checkpoints
           SET status = %s,
               resolved_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
               resolved_by = %s,
               resolution_comment = %s,
               updated_by = %s,
               updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE id = %s AND status = 'open'
        RETURNING id
        """,
        (status, requester_id, _clean_text(comment) or None, requester_id, int(checkpoint_id)),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


def reopen(cursor, checkpoint_id, *, requester_id):
    """Вернуть закрытую точку в работу — на случай «отметил не ту»."""
    cursor.execute(
        """
        UPDATE operator_checkpoints
           SET status = 'open',
               resolved_at = NULL,
               resolved_by = NULL,
               resolution_comment = NULL,
               updated_by = %s,
               updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE id = %s AND status <> 'open'
        RETURNING id
        """,
        (requester_id, int(checkpoint_id)),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


# ── Чтение списка ───────────────────────────────────────────────────────────

def scope_clause(descriptor, params, *, alias='op'):
    """SQL-условие «кого этот человек видит» + параметры в params (dict).

    Одна функция на весь раздел. Правило «кого видит этот человек» считает
    bot_schedule2 (там же, где оно посчитано для самих оценок) и передаёт сюда
    ОПИСАНИЕМ, а не готовым SQL:

        {'scope': 'all'}                                  без границы
        {'scope': 'departments', 'department_ids': [...]}  глава отдела
        {'scope': 'supervisor',  'supervisor_id': 7}       СВ без отдела
        {'scope': 'self',        'user_id': 9}             сам сотрудник
        {'scope': 'none'}                                  никого

    Описанием — потому что условие нужно в двух местах с РАЗНЫМ синтаксисом
    параметров: список вкладки собирается здесь, а колокол подмешивает своё
    условие в общий запрос «когда сводка изменится сама», где параметры
    именованные, и смешать позиционные с именованными в одном запросе psycopg2
    не даст. Два рукописных условия разъехались бы молча — их тут одно.

    Возвращает пустую строку, когда границы нет: вызывающий добавляет её к
    своему WHERE как есть.
    """
    descriptor = descriptor or {}
    scope = str(descriptor.get('scope') or 'none')
    if scope == 'all':
        return ''
    if scope == 'departments':
        ids = [int(value) for value in (descriptor.get('department_ids') or [])]
        if not ids:
            return ' AND FALSE'
        params['cp_departments'] = ids
        return ' AND %s.department_id = ANY(%%(cp_departments)s)' % alias
    if scope == 'supervisor':
        params['cp_supervisor'] = int(descriptor.get('supervisor_id'))
        return ' AND %s.supervisor_id = %%(cp_supervisor)s' % alias
    if scope == 'self':
        params['cp_self'] = int(descriptor.get('user_id'))
        return ' AND %s.id = %%(cp_self)s' % alias
    return ' AND FALSE'


def list_for_scope(cursor, *, scope=None, statuses=('open',), operator_id=None, limit=500):
    """Список точек в границах видимости запросившего.

    Порядок — по сроку. Просроченное окажется сверху само: у него самая ранняя
    дата, и отдельной сортировки «сначала горящее» не нужно.
    """
    params = {'cp_limit': int(limit)}
    where = []
    wanted = tuple(s for s in (statuses or ()) if s in CHECKPOINT_STATUSES)
    if wanted:
        params['cp_statuses'] = list(wanted)
        where.append('c.status = ANY(%(cp_statuses)s)')
    if operator_id is not None:
        params['cp_operator'] = int(operator_id)
        where.append('c.operator_id = %(cp_operator)s')

    query = "SELECT %s %s" % (_SELECT_COLUMNS, _FROM_JOINS)
    query += ' WHERE TRUE'
    if where:
        query += ' AND ' + ' AND '.join(where)
    query += scope_clause(scope, params)
    # Открытые — по возрастанию срока (ближайшее и просроченное сверху),
    # закрытые — по убыванию, свежее сверху. Один ORDER BY на оба среза давал
    # бы либо «самое старое закрытое сверху», либо «самый дальний срок сверху».
    query += """
        ORDER BY (c.status = 'open') DESC,
                 CASE WHEN c.status = 'open' THEN c.due_date END ASC,
                 CASE WHEN c.status <> 'open' THEN c.due_date END DESC,
                 c.id DESC
        LIMIT %(cp_limit)s
    """
    cursor.execute(query, params)
    return [_row_to_dict(row) for row in cursor.fetchall()]


# ── Форма ответа ────────────────────────────────────────────────────────────

def payload_for_manager(item, *, today=None):
    """Полная карточка — супервайзеру, главе отдела, админу."""
    if not item:
        return None
    today = today or date.today()
    return {
        'id': item['id'],
        'operator_id': item['operator_id'],
        'operator_name': item['operator_name'],
        'operator_status': item['operator_status'],
        'supervisor_id': item['supervisor_id'],
        'supervisor_name': item['supervisor_name'],
        'call_id': item['call_id'],
        'feedback_id': item['feedback_id'],
        'kind': item['kind'],
        'kind_label': checkpoint_kind_label(item['kind']),
        'reason': item['reason'],
        'due_date': item['due_date'],
        'focus': item['focus'],
        'internal_comment': item['internal_comment'],
        'notify_operator': item['notify_operator'],
        'status': item['status'],
        'resolved_at': item['resolved_at'],
        'resolved_by_name': item['resolved_by_name'],
        'resolution_comment': item['resolution_comment'],
        'created_at': item['created_at'],
        'updated_at': item['updated_at'],
        'days_left': days_left(item['due_date'], today=today),
        'is_overdue': is_overdue(item, today=today),
    }


def payload_for_operator(item, *, today=None):
    """То же событие глазами сотрудника.

    Здесь СОЗНАТЕЛЬНО нет ни `kind`, ни `reason`, ни `internal_comment`, ни
    имени того, кто поставил на контроль: сотруднику полагается знать, что и к
    какому числу от него ждут, а решение о контроле — служебное (требование
    постановки). Если поле понадобится показать — его нужно вписать сюда
    руками, и это будет видно в ревью.

    Точка, по которой решили сотрудника не тревожить (`notify_operator` = false),
    не отдаётся ему вовсе.
    """
    if not item or not item.get('notify_operator'):
        return None
    today = today or date.today()
    return {
        'id': item['id'],
        'due_date': item['due_date'],
        'focus': item['focus'],
        'status': item['status'],
        'days_left': days_left(item['due_date'], today=today),
    }


def days_left(due_date, *, today=None):
    """Сколько дней до проверки: 0 — сегодня, отрицательное — просрочено."""
    if not due_date:
        return None
    today = today or date.today()
    if isinstance(due_date, str):
        try:
            due_date = datetime.strptime(due_date[:10], '%Y-%m-%d').date()
        except ValueError:
            return None
    return (due_date - today).days


def is_overdue(item, *, today=None):
    if not item or item.get('status') != 'open':
        return False
    remaining = days_left(item.get('due_date'), today=today)
    return remaining is not None and remaining < 0
