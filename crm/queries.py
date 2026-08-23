"""SQL-слой раздела «Обращения».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Так же устроены wiki и call_qa.

Почему это важно именно здесь: одно действие раздела почти всегда меняет три
таблицы сразу (тикет, сообщение, событие истории). Если бы каждая функция брала
своё соединение, ответ из Telegram мог бы лечь в переписку, а статус тикета —
нет. Один курсор = одна транзакция = нить и статус всегда согласованы.
"""

import json

from . import access

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"


# ─────────────────────────────────────────────────────────────────────────────
# Контекст доступа
# ─────────────────────────────────────────────────────────────────────────────

_ACCESS_CONTEXT_SQL = """
WITH me AS (
    SELECT id, name, role, department_id
      FROM users
     WHERE id = %(user_id)s
),
headed AS (
    SELECT d.id, d.code FROM departments d
     WHERE d.head_user_id = %(user_id)s AND d.is_active
),
my_groups AS (
    SELECT gsm.group_id
      FROM group_supervisor_memberships gsm
      JOIN groups g ON g.id = gsm.group_id AND g.status = 'active'
     WHERE gsm.supervisor_id = %(user_id)s
       AND gsm.start_date <= CURRENT_DATE
       AND (gsm.end_date IS NULL OR gsm.end_date >= CURRENT_DATE)
)
SELECT
    (SELECT name          FROM me),
    (SELECT role          FROM me),
    (SELECT department_id FROM me),
    (SELECT d.code FROM departments d WHERE d.id = (SELECT department_id FROM me)),
    COALESCE((SELECT array_agg(id)       FROM headed),    '{}'),
    COALESCE((SELECT array_agg(code)     FROM headed),    '{}'),
    COALESCE((SELECT array_agg(group_id) FROM my_groups), '{}')
"""


def load_access_context(cursor, user_id):
    """Профиль + периметр одним запросом.

    Группы берём только супервайзерские: раздел расширяет видимость СВ на
    операторов ЕГО групп, а собственное членство оператора в группе ничего
    к его правам не добавляет — свои обращения он и так видит по автору.
    """
    cursor.execute(_ACCESS_CONTEXT_SQL, {'user_id': int(user_id)})
    row = cursor.fetchone()
    if not row or row[1] is None:
        return None
    name, role, department_id, department_code, headed, headed_codes, groups = row
    return {
        'user_id': int(user_id),
        'name': name,
        'role': access.normalize_role(role),
        'department_id': department_id,
        'department_code': department_code,
        'headed_department_ids': list(headed or []),
        'headed_department_codes': list(headed_codes or []),
        'group_ids': list(groups or []),
    }


def visibility_sql(ctx):
    """Условие «этот тикет виден пользователю» для WHERE. Возвращает (sql, params).

    Ровно те же четыре правила, что и в access.can_view_ticket — но там по
    одному загруженному тикету, а здесь по списку. Две формы одного правила
    неизбежны (фильтровать список в Python значило бы вычитывать всю таблицу),
    поэтому они сверяются тестом test_crm_access.py.
    """
    scope = access.visibility_scope(ctx)
    params = {'viewer_id': ctx['user_id']}
    if scope == access.SCOPE_ALL:
        return 'TRUE', params

    # Автор видит своё при любом периметре.
    clauses = ['t.created_by = %(viewer_id)s']

    if scope == access.SCOPE_DEPARTMENT:
        params['headed_departments'] = list(ctx.get('headed_department_ids') or [])
        clauses.append('t.department_id = ANY(%(headed_departments)s)')
        clauses.append('q.department_id = ANY(%(headed_departments)s)')
    elif scope == access.SCOPE_GROUPS:
        params['viewer_groups'] = list(ctx.get('group_ids') or [])
        # Оператор «мой», если он состоит в моей группе СЕЙЧАС. Историческое
        # членство не расширяет доступ: ушедший из группы человек уносит с
        # собой и свою переписку.
        clauses.append("""EXISTS (
            SELECT 1 FROM group_operator_memberships gom
             WHERE gom.operator_id = t.created_by
               AND gom.group_id = ANY(%(viewer_groups)s)
               AND gom.start_date <= CURRENT_DATE
               AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
        )""")

    return '(%s)' % ' OR '.join(clauses), params


# ─────────────────────────────────────────────────────────────────────────────
# Очереди и тематики
# ─────────────────────────────────────────────────────────────────────────────

_QUEUE_COLUMNS = """
    q.id, q.title, q.description, q.chat_id, q.chat_title, q.department_id,
    q.sla_minutes, q.sort_order, q.is_active, q.created_at, q.code
"""


def _queue_row(row, expose_chat_id=False):
    item = {
        'id': row[0],
        'title': row[1],
        'description': row[2],
        'chat_title': row[4],
        'department_id': row[5],
        'sla_minutes': row[6],
        'sort_order': row[7],
        'is_active': bool(row[8]),
        # Очередь без привязанной группы не может принять обращение — оператору
        # она показывается недоступной, а не «работающей, но молча теряющей».
        'is_ready': row[3] is not None,
        # Код связывает очередь со сценарием; переименование очереди эту связь
        # не рвёт, поэтому он отдаётся всем, а не только настройщикам.
        'code': row[10],
    }
    if expose_chat_id:
        # chat_id — служебный идентификатор чужого чата, обычному сотруднику он
        # ни к чему; отдаём только тем, кто настраивает очереди.
        item['chat_id'] = row[3]
    return item


def list_queues(cursor, include_inactive=False, expose_chat_id=False):
    """Очереди с тематиками. Один запрос на очереди, один на тематики."""
    cursor.execute(
        """
        SELECT %s FROM crm_queues q
         WHERE (%%(include_inactive)s OR q.is_active)
         ORDER BY q.sort_order, q.title, q.id
        """ % _QUEUE_COLUMNS,
        {'include_inactive': bool(include_inactive)},
    )
    queues = [_queue_row(row, expose_chat_id) for row in cursor.fetchall()]
    if not queues:
        return []

    cursor.execute(
        """
        SELECT id, queue_id, title, sort_order, is_active
          FROM crm_topics
         WHERE queue_id = ANY(%(ids)s) AND (%(include_inactive)s OR is_active)
         ORDER BY sort_order, title, id
        """,
        {'ids': [q['id'] for q in queues], 'include_inactive': bool(include_inactive)},
    )
    by_queue = {}
    for row in cursor.fetchall():
        by_queue.setdefault(row[1], []).append({
            'id': row[0], 'queue_id': row[1], 'title': row[2],
            'sort_order': row[3], 'is_active': bool(row[4]),
        })
    for queue in queues:
        queue['topics'] = by_queue.get(queue['id'], [])
    return queues


def get_queue(cursor, queue_id):
    cursor.execute(
        'SELECT %s FROM crm_queues q WHERE q.id = %%s' % _QUEUE_COLUMNS,
        (int(queue_id),),
    )
    row = cursor.fetchone()
    return _queue_row(row, expose_chat_id=True) if row else None


def create_queue(cursor, *, title, description=None, chat_id=None, chat_title=None,
                 department_id=None, sla_minutes=None, sort_order=100, created_by=None):
    cursor.execute(
        """
        INSERT INTO crm_queues (title, description, chat_id, chat_title, department_id,
                                sla_minutes, sort_order, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (title, description, chat_id, chat_title, department_id,
         sla_minutes, sort_order, created_by),
    )
    return get_queue(cursor, cursor.fetchone()[0])


_QUEUE_EDITABLE = ('title', 'description', 'chat_id', 'chat_title', 'department_id',
                   'sla_minutes', 'sort_order', 'is_active')


def update_queue(cursor, queue_id, changes):
    fields = [f for f in _QUEUE_EDITABLE if f in changes]
    if not fields:
        return get_queue(cursor, queue_id)
    assignments = ', '.join('%s = %%(%s)s' % (f, f) for f in fields)
    params = {f: changes[f] for f in fields}
    params['id'] = int(queue_id)
    cursor.execute(
        'UPDATE crm_queues SET %s, updated_at = %s WHERE id = %%(id)s' % (assignments, _NOW),
        params,
    )
    return get_queue(cursor, queue_id)


def delete_queue(cursor, queue_id):
    """Удаляет очередь, если по ней нет обращений; иначе просит выключить.

    Обращения ссылаются на очередь через ON DELETE RESTRICT намеренно: очередь
    — часть истории («куда это уходило»), и стирать её вместе с перепиской
    нельзя. Пустую же держать незачем.
    """
    cursor.execute('SELECT 1 FROM crm_tickets WHERE queue_id = %s LIMIT 1', (int(queue_id),))
    if cursor.fetchone():
        return False
    cursor.execute('DELETE FROM crm_queues WHERE id = %s', (int(queue_id),))
    return True


def create_topic(cursor, *, queue_id, title, sort_order=100):
    cursor.execute(
        'INSERT INTO crm_topics (queue_id, title, sort_order) VALUES (%s, %s, %s) RETURNING id',
        (int(queue_id), title, sort_order),
    )
    return cursor.fetchone()[0]


def update_topic(cursor, topic_id, changes):
    fields = [f for f in ('title', 'sort_order', 'is_active') if f in changes]
    if not fields:
        return
    assignments = ', '.join('%s = %%(%s)s' % (f, f) for f in fields)
    params = {f: changes[f] for f in fields}
    params['id'] = int(topic_id)
    cursor.execute('UPDATE crm_topics SET %s WHERE id = %%(id)s' % assignments, params)


def delete_topic(cursor, topic_id):
    """Тематику с историей выключаем, а не стираем: на неё ссылаются тикеты."""
    cursor.execute('SELECT 1 FROM crm_tickets WHERE topic_id = %s LIMIT 1', (int(topic_id),))
    if cursor.fetchone():
        cursor.execute('UPDATE crm_topics SET is_active = FALSE WHERE id = %s', (int(topic_id),))
        return False
    cursor.execute('DELETE FROM crm_topics WHERE id = %s', (int(topic_id),))
    return True


# ─────────────────────────────────────────────────────────────────────────────
# МАРШРУТЫ ТЕМ: куда уходит конкретная тема
#
# По умолчанию тема уходит в группу своей тематики — очередь с кодом
# scenario['queue_code']. Маршрут перебивает этот адрес у одной темы.
#
# Считается это в ДВА шага и нарочно: сначала одним запросом берётся весь
# расклад (routing_context), потом адрес каждой темы выводится чистой функцией
# (resolve_route). Тем семь, каталог запрашивается при каждом открытии раздела,
# и поход в базу на каждую тему стоил бы ровно столько же, сколько один поход
# за всеми, — только семь раз. А чистая функция ещё и проверяется тестом без
# базы: правило «выключенная группа НЕ подменяется родной» — именно то, что
# нельзя проверить глазами.
# ─────────────────────────────────────────────────────────────────────────────

def routing_context(cursor):
    """Очереди и маршруты тем — всё, из чего считается адрес любой темы.

    Очереди берём ВСЕ, включая выключенные: выключенная очередь не адрес, но
    её название нужно, чтобы объяснить настройщику, куда указывает маршрут.
    """
    cursor.execute(
        'SELECT %s FROM crm_queues q ORDER BY q.sort_order, q.title, q.id' % _QUEUE_COLUMNS)
    queues = [_queue_row(row, expose_chat_id=True) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT scenario_key, queue_id, updated_by_name, updated_at
          FROM crm_topic_routes
        """
    )
    routes = {row[0]: {'queue_id': row[1], 'updated_by_name': row[2],
                       'updated_at': _iso(row[3])}
              for row in cursor.fetchall()}
    return {
        'queues': queues,
        'by_id': {q['id']: q for q in queues},
        # Код у очереди может быть пустым (её завели руками) — такая очередь
        # не бывает домом ни для одной темы, но быть адресом маршрута может.
        'by_code': {q['code']: q for q in queues if q['code']},
        'routes': routes,
    }


def queue_is_usable(queue):
    """Может ли очередь принять обращение прямо сейчас."""
    return bool(queue and queue.get('is_active') and queue.get('is_ready'))


def resolve_route(context, scenario_key, queue_code):
    """Адрес темы: {'home', 'queue', 'routed', 'is_ready', 'route'}.

    home  — группа тематики (нужна для подписи, даже если она выключена).
    queue — куда уйдёт обращение на самом деле; None, если адреса нет.

    Ключевое решение: маршрут, указывающий на выключенную или неготовую
    очередь, НЕ подменяется родной группой. Тему уводили как раз для того,
    чтобы её перестали получать в родной; молча вернуть её туда — отправить
    обращение не тем людям. Поэтому тема просто становится недоступной, а
    настройщик видит у неё предупреждение.
    """
    home = context['by_code'].get(queue_code)
    route = context['routes'].get(scenario_key)
    queue = context['by_id'].get(route['queue_id']) if route else home
    return {
        'home': home,
        'queue': queue,
        'routed': bool(route),
        'is_ready': queue_is_usable(queue),
        'route': route,
    }


def set_topic_route(cursor, *, scenario_key, queue_id, actor_user_id=None, actor_name=None):
    """Назначает теме группу. queue_id=None — вернуть в группу своей тематики.

    Возврат «в свою» стирает строку, а не пишет маршрут на родную очередь:
    иначе переименование или пересоздание очереди оставило бы маршрут,
    который смотрит в никуда и при этом выглядит настроенным.
    """
    if queue_id is None:
        cursor.execute('DELETE FROM crm_topic_routes WHERE scenario_key = %s',
                       (str(scenario_key),))
        return
    cursor.execute(
        """
        INSERT INTO crm_topic_routes (scenario_key, queue_id, updated_by, updated_by_name)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (scenario_key) DO UPDATE
           SET queue_id = EXCLUDED.queue_id,
               updated_by = EXCLUDED.updated_by,
               updated_by_name = EXCLUDED.updated_by_name,
               updated_at = {now}
        """.format(now=_NOW),
        (str(scenario_key), int(queue_id), actor_user_id, actor_name),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Обращения
# ─────────────────────────────────────────────────────────────────────────────

_TICKET_COLUMNS = """
    t.id, t.subject, t.body, t.status, t.priority, t.source,
    t.queue_id, q.title, t.topic_id, tp.title,
    t.client_name, t.client_phone,
    t.created_by, t.created_by_name, t.department_id,
    t.tg_chat_id, t.tg_message_id, t.delivery_status, t.delivery_error,
    t.due_at, t.first_reply_at, t.last_message_at, t.last_inbound_at,
    t.author_unread_at, t.author_unread_kind,
    t.resolved_at, t.resolved_by_name,
    t.created_at, t.updated_at, q.department_id,
    t.scenario_key, t.answers, t.flags, t.author_unread_count
"""


def _iso(value):
    return value.isoformat() if value is not None else None


def _ticket_row(row, viewer_id=None):
    author_id = row[12]
    is_author = viewer_id is not None and author_id is not None and int(author_id) == int(viewer_id)
    return {
        'id': row[0],
        'subject': row[1],
        'body': row[2],
        'status': row[3],
        'priority': row[4],
        'source': row[5],
        'queue_id': row[6],
        'queue_title': row[7],
        'topic_id': row[8],
        'topic_title': row[9],
        'client_name': row[10],
        'client_phone': row[11],
        'created_by': author_id,
        'created_by_name': row[13],
        'department_id': row[14],
        'tg_chat_id': row[15],
        'tg_message_id': row[16],
        'delivery_status': row[17],
        'delivery_error': row[18],
        'due_at': _iso(row[19]),
        'first_reply_at': _iso(row[20]),
        'last_message_at': _iso(row[21]),
        'last_inbound_at': _iso(row[22]),
        # Непрочитанное — свойство АВТОРА, а не тикета: супервайзер, открывший
        # чужое обращение, не должен видеть чужую «точку» и тем более гасить её.
        'unread': bool(row[23]) and is_author,
        'unread_kind': row[24] if (row[23] and is_author) else None,
        # Сколько уведомлений накопилось. Минимум единица, пока признак горит:
        # у обращений, заведённых до появления счётчика, он остался нулём, и
        # «непрочитано, но ноль штук» было бы неправдой в обе стороны.
        'unread_count': (int(row[33] or 0) or 1) if (row[23] and is_author) else 0,
        'resolved_at': _iso(row[25]),
        'resolved_by_name': row[26],
        'created_at': _iso(row[27]),
        'updated_at': _iso(row[28]),
        'queue_department_id': row[29],
        'scenario_key': row[30],
        'answers': row[31] or {},
        'flags': row[32] or [],
    }


def list_tickets(cursor, ctx, *, status=None, queue_id=None, mine=False, unread_only=False,
                 search=None, limit=50, offset=0):
    """Порция обращений в периметре + есть ли ещё. Возвращает (items, has_more).

    Точного «всего N» здесь нет намеренно. И отдельный COUNT(*), и COUNT(*) OVER ()
    в этом же запросе означают полный проход по периметру: для админа это вся
    таблица, и платить за неё пришлось бы на каждый фильтр, каждую догрузку и
    каждую букву в поиске. Признак has_more берётся из ОДНОЙ лишней запрошенной
    строки, то есть достаётся бесплатно. Так же устроены порции колокола и
    колонка «Готово» на доске задач.

    Порядок — last_message_at DESC, id DESC, ровно под индексом: без COALESCE и
    без выражений.

    Непрочитанное поднимается наверх (просьба владельца 20.08.2026), но ТОЛЬКО
    в выборке своих обращений, и на это две причины.

    Первая — смысл. «Непрочитано» есть у автора и ни у кого больше (см.
    _ticket_row), так что в списке «Все» поднимать нечего: признак там пуст у
    всех строк, и выражение в сортировке было бы платой за ничего.

    Вторая — план. Изначально «наверх» здесь не делали как раз потому, что
    выражение в ORDER BY отменяет чтение по индексу. Теперь под этот порядок
    есть свой индекс (idx_crm_tickets_author_attention), повторяющий его
    дословно вместе с выражением, — и база снова читает первые сорок строк и
    останавливается. Ведущий столбец индекса — created_by, поэтому порядок
    запрашивается только когда выборка сужена до одного автора.

    Довод «входящий ответ и так двигает last_message_at» на деле не работал:
    ответ двухдневной давности, который человек ещё не открыл, уезжал вниз под
    всё, что он завёл после.
    """
    where, params = visibility_sql(ctx)
    clauses = [where]

    # Поиск идёт по ВСЕМ обращениям, а не по «моим» (просьба СЗоВ 18.08.2026).
    # Смысл поиска здесь ровно один: проверить, не заведено ли обращение по
    # этому водителю кем-то ещё. Поиск, ограниченный своими, на этот вопрос
    # честно отвечает «нет» — и человек заводит дубль. Правило стоит на
    # сервере, а не только в интерфейсе: иначе оно держалось бы на том, что
    # клиент не прислал лишний параметр.
    if mine and not search:
        clauses.append('t.created_by = %(viewer_id)s')
    if status:
        params['statuses'] = list(status)
        clauses.append('t.status = ANY(%(statuses)s)')
    if queue_id:
        params['queue_id'] = int(queue_id)
        clauses.append('t.queue_id = %(queue_id)s')
    if unread_only:
        clauses.append('t.author_unread_at IS NOT NULL AND t.created_by = %(viewer_id)s')
    if search:
        # Поиск (ТЗ #29) разделён на две ветки, и это не украшательство, а
        # результат замера. Сначала было одно ИЛИ на пять условий сразу —
        # тема, текст, телефон, имя клиента и номер. Планировщик на таком ИЛИ
        # отказывается от индексов вовсе: он шёл по свежести и фильтровал, и
        # на 200 тыс. обращений редкое слово искалось 270 мс, а слово, которого
        # нет, — 187 мс (частое находилось за 0,6 мс просто потому, что 41
        # совпадение попадалось в первых же строках — то есть «быстро по
        # счастью»).
        #
        # Теперь ветки не смешиваются, и в каждой все условия проиндексированы,
        # так что база может собрать их через BitmapOr:
        #   цифры  → номер обращения, ИИН водителя или телефон клиента
        #   слова  → тема / текст / имя клиента по триграммным индексам
        #
        # Человек ищет ровно одно из двух: число или слово. Смешивать их в
        # одном условии смысла не было и до замера.
        needle = str(search).strip()
        digits = needle.replace(' ', '').replace('+', '').replace('-', '')
        if digits.isdigit():
            params['search'] = '%%%s%%' % digits
            # Номер приводим к int сами: t.id::text = ... не берёт индекс по id,
            # потому что сравнивается выражение, а не столбец.
            params['search_id'] = int(digits) if len(digits) < 10 else None
            # ИИН ищем в ОТВЕТАХ сценария, а не в теме обращения. В тему он
            # попадает уже отрендеренным («… · ИИН 060606202020»), и поиск по
            # ней держался бы на формате заголовка: сменится формулировка —
            # молча перестанет находить. В answers это отдельное поле, оно есть
            # у всех обращений, заведённых сценарием, поэтому старые обращения
            # находятся сразу и ничего не нужно перезаполнять.
            #
            # Выражение в условии и выражение в индексе — одно и то же, иначе
            # индекс не подойдёт запросу (уже наступали на это с client_name).
            clauses.append("""(
                (%(search_id)s IS NOT NULL AND t.id = %(search_id)s)
                OR t.client_phone ILIKE %(search)s
                OR (t.answers ->> 'iin') ILIKE %(search)s
            )""")
        else:
            params['search'] = '%%%s%%' % needle
            clauses.append("""(
                t.subject ILIKE %(search)s
                OR t.client_name ILIKE %(search)s
                OR t.body ILIKE %(search)s
            )""")

    # Выборка сужена до одного автора? Тогда «непрочитанное наверху» и имеет
    # смысл, и укладывается в индекс. mine без поиска — сегмент «Мои»; SCOPE_OWN —
    # оператор, который своё и видит.
    own_only = bool(mine and not search) or access.visibility_scope(ctx) == access.SCOPE_OWN
    order = ('(t.author_unread_at IS NULL), t.last_message_at DESC, t.id DESC'
             if own_only else 't.last_message_at DESC, t.id DESC')

    page = max(1, min(int(limit), 200))
    # Просим на одну строку больше, чем покажем: она и есть ответ на вопрос
    # «есть ли ещё», и стоит чтения одной строки, а не подсчёта всех.
    params['limit'] = page + 1
    params['offset'] = max(0, int(offset))

    cursor.execute(
        """
        SELECT %s
          FROM crm_tickets t
          JOIN crm_queues q ON q.id = t.queue_id
          LEFT JOIN crm_topics tp ON tp.id = t.topic_id
         WHERE %s
         ORDER BY %s
         LIMIT %%(limit)s OFFSET %%(offset)s
        """ % (_TICKET_COLUMNS, ' AND '.join(clauses), order),
        params,
    )
    rows = cursor.fetchall()
    has_more = len(rows) > page
    items = [_ticket_row(row, ctx['user_id']) for row in rows[:page]]
    attach_previews(cursor, items)
    return items, has_more


def attach_previews(cursor, items):
    """Дописывает в строки списка последнюю реплику нити — как в мессенджере.

    Отдельным запросом, а не LATERAL внутри списка, намеренно. LATERAL пришлось
    бы считать до сортировки: порядок «непрочитанное наверху» — выражение, и
    планировщик волен собрать соединение раньше, чем отсечёт лишние строки.
    Здесь же вопрос задаётся ровно про те сорок id, которые действительно
    поедут на экран, и отвечает на него DISTINCT ON по тому же индексу
    (ticket_id, created_at, id), которым читается сама переписка.

    Пустая нить бывает: обращение, которое ещё не ушло в Telegram. Такой строке
    превью просто не достанется, и лента покажет тему — без заглушек.
    """
    ids = [int(item['id']) for item in items]
    if not ids:
        return items
    cursor.execute(
        """
        SELECT DISTINCT ON (m.ticket_id)
               m.ticket_id, m.direction, m.author_name, m.tg_from_name,
               m.body, m.attachment_kind, m.attachment_name, m.created_at
          FROM crm_ticket_messages m
         WHERE m.ticket_id = ANY(%s)
         ORDER BY m.ticket_id, m.created_at DESC, m.id DESC
        """,
        (ids,),
    )
    preview = {}
    for row in cursor.fetchall():
        preview[row[0]] = {
            'direction': row[1],
            'author_name': row[2] or row[3],
            'body': row[4],
            'attachment': ({'kind': row[5], 'name': row[6]} if row[5] else None),
            'created_at': _iso(row[7]),
        }
    for item in items:
        item['last_message'] = preview.get(int(item['id']))
    return items


def get_ticket(cursor, ticket_id, viewer_id=None):
    """Одно обращение + группы его автора (нужны для проверки прав супервайзера)."""
    cursor.execute(
        """
        SELECT %s,
               COALESCE((
                   SELECT array_agg(gom.group_id)
                     FROM group_operator_memberships gom
                    WHERE gom.operator_id = t.created_by
                      AND gom.start_date <= CURRENT_DATE
                      AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
               ), '{}')
          FROM crm_tickets t
          JOIN crm_queues q ON q.id = t.queue_id
          LEFT JOIN crm_topics tp ON tp.id = t.topic_id
         WHERE t.id = %%s
        """ % _TICKET_COLUMNS,
        (int(ticket_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    ticket = _ticket_row(row, viewer_id)
    ticket['author_group_ids'] = list(row[-1] or [])
    return ticket


def create_ticket(cursor, *, queue_id, topic_id, subject, body, priority, source,
                  client_name, client_phone, created_by, created_by_name,
                  department_id, due_at=None, scenario_key=None, answers=None, flags=None):
    cursor.execute(
        """
        INSERT INTO crm_tickets (queue_id, topic_id, subject, body, priority, source,
                                 client_name, client_phone, created_by, created_by_name,
                                 department_id, due_at, last_message_at,
                                 scenario_key, answers, flags)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {now},
                %s, %s::jsonb, %s::jsonb)
        RETURNING id
        """.format(now=_NOW),
        (int(queue_id), topic_id, subject, body, priority, source,
         client_name, client_phone, created_by, created_by_name, department_id, due_at,
         scenario_key,
         json.dumps(answers or {}, ensure_ascii=False),
         json.dumps(list(flags or []), ensure_ascii=False)),
    )
    return cursor.fetchone()[0]


def scenario_breakdown(cursor, ctx, days=30):
    """Разбивка обращений по тематикам за период — отчёт из ТЗ #29.

    Считается по частичному индексу idx_crm_tickets_scenario и только по
    периметру зрителя.
    """
    where, params = visibility_sql(ctx)
    params['days'] = int(days)
    cursor.execute(
        """
        SELECT t.scenario_key,
               COUNT(*)                                                   AS total,
               COUNT(*) FILTER (WHERE t.status = 'resolved')              AS resolved,
               COUNT(*) FILTER (WHERE t.flags @> '["mass_outage"]'::jsonb) AS mass_outage
          FROM crm_tickets t
          JOIN crm_queues q ON q.id = t.queue_id
         WHERE t.scenario_key IS NOT NULL
           AND t.created_at >= (%s - make_interval(days => %%(days)s))
           AND %s
         GROUP BY t.scenario_key
         ORDER BY total DESC
        """ % (_NOW, where),
        params,
    )
    return [{'scenario_key': r[0], 'total': r[1], 'resolved': r[2], 'mass_outage': r[3]}
            for r in cursor.fetchall()]


def set_delivery(cursor, ticket_id, *, status, chat_id=None, message_id=None, error=None):
    """Фиксирует судьбу отправки в Telegram отдельно от сути обращения."""
    cursor.execute(
        """
        UPDATE crm_tickets
           SET delivery_status = %s,
               tg_chat_id = COALESCE(%s, tg_chat_id),
               tg_message_id = COALESCE(%s, tg_message_id),
               delivery_error = %s,
               updated_at = {now}
         WHERE id = %s
        """.format(now=_NOW),
        (status, chat_id, message_id, error, int(ticket_id)),
    )


def add_message(cursor, *, ticket_id, direction, body=None, author_user_id=None,
                author_name=None, tg_chat_id=None, tg_message_id=None, tg_from_id=None,
                tg_from_name=None, tg_username=None, attachment=None,
                reply_to_tg_message_id=None):
    """Добавляет сообщение в нить. Возвращает id или None, если это дубль.

    Дубль — не ошибка: Telegram штатно повторяет апдейт, если не получил
    подтверждения. ON CONFLICT DO NOTHING делает приём ответов идемпотентным.
    """
    attachment = attachment or {}
    cursor.execute(
        """
        INSERT INTO crm_ticket_messages
            (ticket_id, direction, body, author_user_id, author_name,
             tg_chat_id, tg_message_id, tg_from_id, tg_from_name, tg_username,
             attachment_kind, attachment_file_id, attachment_name,
             attachment_mime, attachment_size, reply_to_tg_message_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        RETURNING id
        """,
        (int(ticket_id), direction, body, author_user_id, author_name,
         tg_chat_id, tg_message_id, tg_from_id, tg_from_name, tg_username,
         attachment.get('kind'), attachment.get('file_id'), attachment.get('name'),
         attachment.get('mime'), attachment.get('size'), reply_to_tg_message_id),
    )
    row = cursor.fetchone()
    return row[0] if row else None


# Потолок на одну нить. Обращение — это несколько реплик, а не чат на тысячу
# сообщений; но группа может «уйти в обсуждение», и тогда карточка тянула бы всю
# переписку в браузер целиком. Берём последние MESSAGES_LIMIT и переворачиваем:
# свежее важнее, а полная история всегда есть в самом Telegram.
MESSAGES_LIMIT = 300


def message_of_ticket(cursor, ticket_id, message_id):
    """Строка нити ЭТОГО обращения — для ответа на конкретное сообщение.

    Проверка обязательна и делается запросом, а не доверием клиенту: иначе
    оператор смог бы заставить бота ответить на любое сообщение в рабочей
    группе, подставив чужой номер.
    """
    cursor.execute(
        """
        SELECT id, tg_message_id, body, author_name, tg_from_name
          FROM crm_ticket_messages
         WHERE id = %s AND ticket_id = %s
        """,
        (int(message_id), int(ticket_id)),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {'id': row[0], 'tg_message_id': row[1], 'body': row[2],
            'author_name': row[3] or row[4]}


def list_messages(cursor, ticket_id, limit=MESSAGES_LIMIT):
    cursor.execute(
        """
        SELECT id, direction, body, author_user_id, author_name,
               tg_from_name, tg_username, attachment_kind, attachment_name,
               attachment_mime, attachment_size, created_at, tg_message_id,
               tg_from_id, reply_to_tg_message_id
          FROM (
            SELECT * FROM crm_ticket_messages
             WHERE ticket_id = %s
             ORDER BY created_at DESC, id DESC
             LIMIT %s
          ) recent
         ORDER BY created_at, id
        """,
        (int(ticket_id), int(limit)),
    )
    return [{
        'id': row[0],
        'direction': row[1],
        'body': row[2],
        'author_user_id': row[3],
        # Для входящих автор — сотрудник из Telegram, для исходящих — наш
        # пользователь. Фронту важно одно поле «кто», а не два.
        'author_name': row[4] or row[5] or (('@' + row[6]) if row[6] else None),
        'telegram_username': row[6],
        'attachment': {
            'kind': row[7], 'name': row[8], 'mime': row[9], 'size': row[10],
        } if row[7] else None,
        'created_at': _iso(row[11]),
        'tg_message_id': row[12],
        # Кто написал — для окраски имени: у сотрудника из Telegram id
        # устойчив, а имя он может сменить.
        'telegram_user_id': row[13],
        # На какое сообщение это ответ. Саму цитату собирает интерфейс: вся нить
        # уже у него на руках, и второй запрос за текстом соседней строки был бы
        # платой за ничего.
        'reply_to_tg_message_id': row[14],
    } for row in cursor.fetchall()]


def add_event(cursor, *, ticket_id, kind, actor_user_id=None, actor_name=None, payload=None):
    cursor.execute(
        """
        INSERT INTO crm_ticket_events (ticket_id, kind, actor_user_id, actor_name, payload)
        VALUES (%s, %s, %s, %s, %s::jsonb)
        """,
        (int(ticket_id), kind, actor_user_id, actor_name, json.dumps(payload or {}, ensure_ascii=False)),
    )


def list_events(cursor, ticket_id, limit=100):
    cursor.execute(
        """
        SELECT id, kind, actor_user_id, actor_name, payload, created_at
          FROM crm_ticket_events
         WHERE ticket_id = %s
         ORDER BY created_at DESC, id DESC
         LIMIT %s
        """,
        (int(ticket_id), int(limit)),
    )
    return [{
        'id': row[0], 'kind': row[1], 'actor_user_id': row[2],
        'actor_name': row[3], 'payload': row[4] or {}, 'created_at': _iso(row[5]),
    } for row in cursor.fetchall()]


# Что именно ждёт автора. Значение уходит и в колокол, и в карточку раздела.
UNREAD_REPLY = 'reply'
UNREAD_DONE = 'done'
UNREAD_PROGRESS = 'progress'


def touch_inbound(cursor, ticket_id, *, unread_kind=UNREAD_REPLY, mark_answered=True):
    """Пришло что-то из Telegram: обновляем нить и зажигаем уведомление автору.

    first_reply_at ставится один раз (COALESCE) — это метрика скорости ответа
    группы, и второй ответ не должен её улучшать.

    Статус переводим в 'answered' только из рабочих: у решённого обращения
    дописка в чате не должна воскрешать его в списке «ждут ответа».

    Счётчик непрочитанного увеличивается на каждое входящее — он и есть то
    число, которое лента показывает пузырьком у обращения. Обнуляется не по
    таймеру и не колоколом, а открытием карточки (mark_seen_by_author).
    """
    cursor.execute(
        """
        UPDATE crm_tickets
           SET last_message_at = {now},
               last_inbound_at = {now},
               first_reply_at = COALESCE(first_reply_at, {now}),
               status = CASE
                   WHEN %(mark_answered)s AND status IN ('open', 'in_progress') THEN 'answered'
                   ELSE status END,
               author_unread_at = {now},
               author_unread_kind = %(kind)s,
               author_unread_count = author_unread_count + 1,
               updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': int(ticket_id), 'kind': unread_kind, 'mark_answered': bool(mark_answered)},
    )


def touch_outbound(cursor, ticket_id):
    """Оператор дописал в нить — сдвигаем время последнего сообщения.

    Уведомление автору не зажигаем: он и есть тот, кто написал.
    """
    cursor.execute(
        'UPDATE crm_tickets SET last_message_at = {now}, updated_at = {now} WHERE id = %s'.format(now=_NOW),
        (int(ticket_id),),
    )


def set_status(cursor, ticket_id, status, *, actor_user_id=None, actor_name=None,
               notify_author=False, unread_kind=UNREAD_DONE):
    """Меняет статус. notify_author — когда статус сменил не автор, а группа.

    Возвращает True, если статус действительно изменился: повторное нажатие
    «Выполнено» в Telegram не должно писать вторую строку в историю и второй
    раз звонить автору.
    """
    cursor.execute('SELECT status FROM crm_tickets WHERE id = %s FOR UPDATE', (int(ticket_id),))
    row = cursor.fetchone()
    if not row or row[0] == status:
        return False

    resolved = status == 'resolved'
    cursor.execute(
        """
        UPDATE crm_tickets
           SET status = %(status)s,
               resolved_at = CASE WHEN %(resolved)s THEN {now} ELSE NULL END,
               resolved_by = CASE WHEN %(resolved)s THEN %(actor_id)s ELSE NULL END,
               resolved_by_name = CASE WHEN %(resolved)s THEN %(actor_name)s ELSE NULL END,
               author_unread_at = CASE WHEN %(notify)s THEN {now} ELSE author_unread_at END,
               author_unread_kind = CASE WHEN %(notify)s THEN %(kind)s ELSE author_unread_kind END,
               author_unread_count = author_unread_count + CASE WHEN %(notify)s THEN 1 ELSE 0 END,
               updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW),
        {'id': int(ticket_id), 'status': status, 'resolved': resolved,
         'actor_id': actor_user_id, 'actor_name': actor_name,
         'notify': bool(notify_author), 'kind': unread_kind},
    )
    return True


def mark_seen_by_author(cursor, ticket_id, user_id):
    """Гасит «непрочитано» — только у автора и только открытием карточки.

    Именно поэтому у раздела нет общего «прочитать всё»: уведомление «вам
    ответили» снимается тем, что человек прочитал ответ, а не тем, что он
    заглянул в колокол.
    """
    cursor.execute(
        """
        UPDATE crm_tickets
           SET author_unread_at = NULL, author_unread_kind = NULL,
               author_unread_count = 0
         WHERE id = %s AND created_by = %s
           AND (author_unread_at IS NOT NULL OR author_unread_count > 0)
        """,
        (int(ticket_id), int(user_id)),
    )
    return cursor.rowcount > 0


def find_ticket_by_tg_message(cursor, chat_id, message_id):
    """По сообщению, на которое ответили в группе, найти обращение.

    Ищем по таблице переписки, а не по crm_tickets: сотрудник может ответить
    и на исходную заявку, и на уточнение оператора, и на реплику коллеги —
    все три лежат здесь строками одной нити.
    """
    cursor.execute(
        """
        SELECT m.ticket_id, t.status, t.created_by, t.subject
          FROM crm_ticket_messages m
          JOIN crm_tickets t ON t.id = m.ticket_id
         WHERE m.tg_chat_id = %s AND m.tg_message_id = %s
         LIMIT 1
        """,
        (int(chat_id), int(message_id)),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {'ticket_id': row[0], 'status': row[1], 'created_by': row[2], 'subject': row[3]}


def find_message_attachment(cursor, ticket_id, message_id):
    cursor.execute(
        """
        SELECT attachment_file_id, attachment_name, attachment_mime, attachment_kind
          FROM crm_ticket_messages
         WHERE id = %s AND ticket_id = %s
        """,
        (int(message_id), int(ticket_id)),
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    return {'file_id': row[0], 'name': row[1], 'mime': row[2], 'kind': row[3]}


# ─────────────────────────────────────────────────────────────────────────────
# Сводки
# ─────────────────────────────────────────────────────────────────────────────

def unread_for_bell(cursor, user_id, limit):
    """Непрочитанное автором — для колокола. Возвращает (всего, элементы)."""
    cursor.execute(
        """
        SELECT t.id, t.subject, t.author_unread_kind, t.author_unread_at, q.title,
               COUNT(*) OVER () AS total
          FROM crm_tickets t
          JOIN crm_queues q ON q.id = t.queue_id
         WHERE t.created_by = %(user_id)s AND t.author_unread_at IS NOT NULL
         ORDER BY t.author_unread_at DESC, t.id DESC
         LIMIT %(limit)s
        """,
        {'user_id': int(user_id), 'limit': int(limit)},
    )
    rows = cursor.fetchall()
    total = rows[0][5] if rows else 0
    return int(total), rows


def counters(cursor, ctx):
    """Одно число для раздела: сколько обращений ждут ЛИЧНО этого человека.

    История этой функции — иллюстрация того, как счётчики дорожают незаметно.
    Сначала здесь считалось шесть агрегатов (активные, ответили, непрочитано,
    недоставленные, просроченные, всего) — четыре из них интерфейс не показывал
    вовсе. Замер на 200 тыс. обращений: 60 мс проходом по таблице у админа.
    Дальше осталось два, вынесенных в отдельные подзапросы под частичные
    индексы, — уже index-only, но всё равно 13 мс: «сколько всего обращений с
    ответом» приходится честно пересчитать по 33 тыс. записей.

    А украшало это число подпись сегмента «Ответили», при том что сам список
    обращений с ответом — один клик и он же показывает их точно. Поэтому число
    убрано, а не оптимизировано дальше: то, что не показывают, считать не надо.

    Осталось непрочитанное — оно нужно бейджу раздела и читается по частичному
    индексу за десятые доли миллисекунды при любом объёме, потому что условие
    «мой автор» отсекает всё остальное сразу.
    """
    cursor.execute(
        """
        SELECT COUNT(*)
          FROM crm_tickets t
         WHERE t.created_by = %(viewer_id)s
           AND t.author_unread_at IS NOT NULL
        """,
        {'viewer_id': ctx['user_id']},
    )
    return {'unread': cursor.fetchone()[0]}


def delivery_payload(cursor, ticket_id):
    """Всё, что нужно, чтобы собрать сообщение для Telegram, одним запросом.

    Отдельно от get_ticket: отправке не нужны ни группы автора, ни история, а
    нужны две вещи, которых нет в карточке, — chat_id очереди и название отдела
    для подписи.
    """
    cursor.execute(
        """
        SELECT t.subject, t.body, t.priority, t.status, t.due_at,
               t.client_name, t.client_phone,
               t.created_by, t.created_by_name,
               t.delivery_status, t.tg_message_id,
               q.chat_id, q.title, tp.title, d.name,
               t.answers ->> 'iin', t.scenario_key, t.answers, t.flags
          FROM crm_tickets t
          JOIN crm_queues q ON q.id = t.queue_id
          LEFT JOIN crm_topics tp ON tp.id = t.topic_id
          LEFT JOIN departments d ON d.id = t.department_id
         WHERE t.id = %s
        """,
        (int(ticket_id),),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        'subject': row[0], 'body': row[1], 'priority': row[2], 'status': row[3],
        'due_at': row[4], 'client_name': row[5], 'client_phone': row[6],
        'created_by': row[7], 'created_by_name': row[8],
        'delivery_status': row[9], 'tg_message_id': row[10],
        'chat_id': row[11], 'queue_title': row[12], 'topic_title': row[13],
        # ИИН нужен уточнению в группе: специалист видит номер обращения, но
        # без ИИН ему всё равно приходится открывать исходное сообщение.
        'iin': row[15],
        # Нужен, чтобы понять, сама ли тематика сформулировала сообщение.
        'scenario_key': row[16],
        # Ответы и метки — чтобы собрать карточку заново, а не разбирать
        # обратно уже готовый текст: разбор собственного вывода это второе
        # место, где живёт формат, и оно всегда отстаёт от первого.
        'answers': row[17] or {},
        'flags': row[18] or [],
        'department_name': row[14],
    }


def taxi_parks(cursor):
    """Таксопарки для выбора в обращении.

    Источник тот же, что у раздела «Парки» в вики (wiki_taxi_parks): второй
    список парков означал бы, что оператор выбирает из одного набора, а
    справочник компании живёт другим. Читаем только активные и только имя —
    в обращении хранится название парка, а не ссылка на строку: обращение
    остаётся читаемым, даже если парк потом переименуют или уберут.

    Своя таблица здесь не нужна и вредна: парки заводит вики, и заводит их
    один раз. Точно так же раздел берёт реестр чатов у заявок в IT (bot_chats).
    """
    cursor.execute(
        """
        SELECT p.name
          FROM wiki_taxi_parks p
         WHERE p.status = 'active'
         ORDER BY p.position, p.name
        """
    )
    return [row[0] for row in cursor.fetchall()]


def bot_chats(cursor):
    """Чаты, куда бот уже добавлен, — для привязки очереди к группе.

    Источник тот же, что у заявок в IT: it_ticket_channels наполняет обработчик
    my_chat_member при добавлении бота в чат. Заводить второй реестр чатов
    значило бы иметь два ответа на вопрос «где бот состоит».
    """
    cursor.execute(
        """
        SELECT c.chat_id, c.title, c.chat_type, c.username,
               (SELECT q.title FROM crm_queues q WHERE q.chat_id = c.chat_id LIMIT 1)
          FROM it_ticket_channels c
         WHERE c.is_active AND c.chat_type IN ('group', 'supergroup')
         ORDER BY c.title NULLS LAST, c.chat_id
        """
    )
    return [{
        'chat_id': row[0],
        'title': row[1] or ('Чат %s' % row[0]),
        'chat_type': row[2],
        'username': row[3],
        # Занятый чат показываем, но не даём выбрать повторно: одна группа —
        # одна очередь (уникальный индекс это же и стережёт в базе).
        'used_by_queue': row[4],
    } for row in cursor.fetchall()]
