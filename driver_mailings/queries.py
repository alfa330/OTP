"""SQL раздела «Рассылки».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
транзакцией, ни соединением — как в fleet_edm/queries.py и crm/queries.py.
Транзакцией владеет вызывающий: отправка складывает в одну транзакцию карточку
рассылки и все её цели, и разорвать её на пять самостоятельных коммитов значило
бы получить рассылку без целей при первой же ошибке связи.

Куки кабинета здесь не читаются и не пишутся: сессия общая с «Провайдером ЭДО»,
за ней ходят в fleet_edm.queries.load_session (см. докстроку пакета).

Все запросы параметризованные. Там, где список полей собирается на лету
(частичное обновление шаблона), в SQL уходят ТОЛЬКО имена колонок из
зашитого в модуль кортежа, а значения — всегда через %s.
"""

from psycopg2.extras import Json, execute_values

# Колонки рассылки, которые уходят в интерфейс.
#
# idempotency_key здесь нет намеренно: это внутренний токен защиты от двойного
# клика, человеку он ничего не говорит, а в журнале и в карточке лишний.
MAILING_COLUMNS = (
    'id', 'created_at', 'created_by', 'created_by_name', 'title', 'message',
    'message_kk', 'message_ru', 'filters', 'recipients_estimate', 'status',
    'finished_at', 'error',
)

TARGET_COLUMNS = (
    'mailing_id', 'park_id', 'park_name', 'park_city', 'recipients_estimate',
    'status', 'fleet_mailing_id', 'sent_at', 'revoked_at', 'error', 'error_code',
)

TEMPLATE_COLUMNS = (
    'id', 'name', 'title', 'message_kk', 'message_ru', 'message', 'filters',
    'park_ids', 'created_by', 'created_by_name', 'created_at', 'updated_at',
)

PARK_COLUMNS = (
    'park_id', 'name', 'city', 'is_enabled', 'status', 'max_title', 'max_message',
    'per_day', 'revoke_seconds', 'checked_at',
)

# Текстовые поля шаблона, которые разрешено менять через PATCH, и поля-JSONB.
# Список зашит здесь, потому что имена колонок попадают в SQL напрямую: всё, что
# пришло от клиента и не встретилось в этих кортежах, просто игнорируется.
TEMPLATE_TEXT_FIELDS = ('name', 'title', 'message_kk', 'message_ru', 'message')
TEMPLATE_JSON_FIELDS = ('filters', 'park_ids')


def _columns(cursor):
    return [column[0] for column in (cursor.description or [])]


def _row_to_dict(cursor, row):
    if row is None:
        return None
    return dict(zip(_columns(cursor), row))


def _rows_to_dicts(cursor):
    columns = _columns(cursor)
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _text(value):
    """Пустую строку кладём как NULL: в карточке рассылки «не заполнено» и
    «заполнено пустотой» — одно и то же, а два разных представления одного
    состояния потом ловятся в интерфейсе как два разных случая."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _int(value):
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Кто спрашивает ───────────────────────────────────────────────────────────

def access_context(cursor, user_id):
    """Роль, отдел и возглавляет ли человек отдел.

    Отдельным запросом, а не разбором кортежа из _resolve_requester: там
    пользователь приходит СТРОКОЙ базы, обращение к ней по имени поля молча даёт
    None, а порядок столбцов меняется вместе с чужими правками. На этом уже
    обжигался «Ограничитель Перезвона» — раздел закрывался даже суперадмину.
    """
    if not user_id:
        return None
    cursor.execute(
        """
        SELECT u.id,
               u.name,
               u.role,
               COALESCE(d.code, '')  AS department_code,
               EXISTS (
                   SELECT 1 FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
               )                     AS is_department_head,
               COALESCE((
                   SELECT h.code FROM departments h
                    WHERE h.head_user_id = u.id AND h.is_active
                    LIMIT 1
               ), '')                AS headed_department_code
          FROM users u
          LEFT JOIN departments d ON d.id = u.department_id
         WHERE u.id = %(user_id)s
        """,
        {'user_id': int(user_id)},
    )
    return _row_to_dict(cursor, cursor.fetchone())


# ── Кэш диспетчерских ────────────────────────────────────────────────────────

def parks_cache(cursor):
    """Что раздел показывает в мультиселекте.

    Отдаются ВСЕ проверенные парки, включая запрещённые (is_enabled = FALSE):
    интерфейс должен уметь объяснить, почему из 90 диспетчерских аккаунта в
    списке пять, — иначе это выглядит как потеря половины данных.
    """
    cursor.execute(
        """
        SELECT {columns}
          FROM driver_mailing_parks
         ORDER BY is_enabled DESC, COALESCE(city, ''), COALESCE(name, ''), park_id
        """.format(columns=', '.join(PARK_COLUMNS))
    )
    return _rows_to_dicts(cursor)


def save_parks_cache(cursor, rows):
    """Записать результат опроса 90 парков.

    Пишем пачкой одним execute_values: по строке на парк — это 90 обращений к
    базе там, где хватает одного.

    Парки, которых в новом опросе не оказалось, удаляем: у аккаунта отобрали
    диспетчерскую — она не должна остаться в мультиселекте навсегда. Пустой
    список при этом НИЧЕГО не чистит: пустым он приходит только когда опрос
    сорвался, и стереть по нему рабочий кэш означало бы обезглавить раздел из-за
    одной сетевой ошибки.
    """
    rows = [row for row in (rows or []) if _text(row.get('park_id'))]
    if not rows:
        return 0
    values = [
        (
            _text(row.get('park_id')),
            _text(row.get('name')),
            _text(row.get('city')),
            bool(row.get('is_enabled')),
            _text(row.get('status')),
            _int(row.get('max_title')),
            _int(row.get('max_message')),
            _int(row.get('per_day')),
            _int(row.get('revoke_seconds')),
        )
        for row in rows
    ]
    execute_values(
        cursor,
        """
        INSERT INTO driver_mailing_parks (park_id, name, city, is_enabled, status,
                                          max_title, max_message, per_day,
                                          revoke_seconds, checked_at)
        VALUES %s
        ON CONFLICT (park_id) DO UPDATE
           SET name           = EXCLUDED.name,
               city           = EXCLUDED.city,
               is_enabled     = EXCLUDED.is_enabled,
               status         = EXCLUDED.status,
               max_title      = EXCLUDED.max_title,
               max_message    = EXCLUDED.max_message,
               per_day        = EXCLUDED.per_day,
               revoke_seconds = EXCLUDED.revoke_seconds,
               checked_at     = NOW()
        """,
        values,
        template='(%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())',
    )
    cursor.execute(
        "DELETE FROM driver_mailing_parks WHERE park_id <> ALL(%s)",
        ([value[0] for value in values],),
    )
    return len(values)


def parks_cache_age(cursor):
    """Возраст кэша в секундах или None, если кэш пуст.

    Берём САМУЮ СТАРУЮ отметку, а не самую свежую: если прошлый скан оборвался на
    середине, половина строк осталась вчерашней, и честный ответ про такой кэш —
    «ему сутки», а не «ему минута».
    """
    cursor.execute(
        "SELECT EXTRACT(EPOCH FROM (NOW() - MIN(checked_at))) FROM driver_mailing_parks"
    )
    row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


# ── Рассылка и её цели ───────────────────────────────────────────────────────

def create_mailing(cursor, *, created_by, created_by_name, title, message,
                   message_kk, message_ru, filters, recipients_estimate,
                   idempotency_key):
    """Завести карточку рассылки и вернуть её id.

    ЭТО И ЕСТЬ ЗАЩИТА ОТ ДВОЙНОГО КЛИКА. Токен идемпотентности приходит с фронта
    и генерируется один раз на попытку отправки; колонка UNIQUE, поэтому второе
    нажатие не создаёт вторую рассылку, а ON CONFLICT DO NOTHING оставляет нас без
    RETURNING — тогда мы просто находим уже существующую запись и возвращаем ТОТ
    ЖЕ id. Вызывающий видит рассылку, у которой цели уже заведены, и по их статусу
    понимает, что отправка идёт (или прошла), а не начинает её заново.

    Проверкой «нет ли похожей рассылки за последнюю минуту» это не заменяется:
    два одинаковых текста в разные наборы парков — законный сценарий, а вот два
    одинаковых токена бывают только у повторно отправленной формы.
    """
    cursor.execute(
        """
        INSERT INTO driver_mailings (created_by, created_by_name, title, message,
                                     message_kk, message_ru, filters,
                                     recipients_estimate, idempotency_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        (
            _int(created_by),
            _text(created_by_name),
            str(title or ''),
            str(message or ''),
            _text(message_kk),
            _text(message_ru),
            Json(filters or {}),
            _int(recipients_estimate),
            _text(idempotency_key),
        ),
    )
    row = cursor.fetchone()
    if row:
        return int(row[0])

    cursor.execute(
        "SELECT id FROM driver_mailings WHERE idempotency_key = %s",
        (_text(idempotency_key),),
    )
    row = cursor.fetchone()
    if row:
        return int(row[0])
    # Сюда попасть нельзя: вставка либо прошла, либо упёрлась в UNIQUE по токену,
    # и тогда запись с этим токеном есть. Молчать всё равно нельзя — иначе
    # отправка пойдёт с mailing_id = None и журнал останется без рассылки.
    raise RuntimeError('Не удалось создать рассылку: запись не появилась в базе')


def find_mailing_by_key(cursor, idempotency_key):
    """Найти рассылку по токену идемпотентности.

    Нужна роуту, который хочет отличить повторное нажатие ДО вставки и вернуть
    человеку прежний результат, не трогая кабинет.
    """
    key = _text(idempotency_key)
    if not key:
        return None
    cursor.execute(
        "SELECT {columns} FROM driver_mailings WHERE idempotency_key = %s".format(
            columns=', '.join(MAILING_COLUMNS)
        ),
        (key,),
    )
    return _row_to_dict(cursor, cursor.fetchone())


def add_target(cursor, mailing_id, *, park_id, park_name, park_city,
               recipients_estimate):
    """Строка «эта рассылка в этой диспетчерской», пока в статусе pending.

    Название и город парка КОПИРУЮТСЯ в строку, а не берутся ссылкой на кэш:
    кэш пересобирается опросом и парк из него может исчезнуть совсем, а журнал
    обязан и через год показывать, куда именно ушло сообщение.

    ON CONFLICT — на случай повторной попытки отправки по той же карточке: цель
    уже заведена, и падать на этом незачем.
    """
    cursor.execute(
        """
        INSERT INTO driver_mailing_targets (mailing_id, park_id, park_name, park_city,
                                            recipients_estimate, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        ON CONFLICT (mailing_id, park_id) DO UPDATE
           SET park_name           = EXCLUDED.park_name,
               park_city           = EXCLUDED.park_city,
               recipients_estimate = EXCLUDED.recipients_estimate
        """,
        (
            int(mailing_id),
            str(park_id or ''),
            _text(park_name),
            _text(park_city),
            _int(recipients_estimate),
        ),
    )


def mark_target_sent(cursor, mailing_id, park_id, *, fleet_mailing_id=None):
    """Ушло. fleet_mailing_id может быть None — кабинет отвечает на отправку 204
    без тела, и если разыскать свою рассылку в его журнале не удалось, честнее
    записать «отправлено, но не связано», чем не записать отправку вовсе.

    sent_at ставим здесь и только здесь: от него отсчитывается окно отзыва в 300
    секунд, и брать это время из кода приложения нельзя — часы инстанса и часы
    базы расходятся, а ошибка в пару секунд у пятиминутного окна дорого стоит.
    """
    cursor.execute(
        """
        UPDATE driver_mailing_targets
           SET status = 'sent',
               sent_at = NOW(),
               fleet_mailing_id = COALESCE(%s, fleet_mailing_id),
               error = NULL,
               error_code = NULL
         WHERE mailing_id = %s AND park_id = %s
        """,
        (_text(fleet_mailing_id), int(mailing_id), str(park_id or '')),
    )


def mark_target_failed(cursor, mailing_id, park_id, *, error=None, error_code=None):
    """Кабинет отказал. error_code храним отдельно от текста: человеку показывается
    русская фраза, а машинный код (limit_drivers, limit_time, no_permissions)
    нужен, чтобы потом отличить «слишком много получателей» от «нет прав в этом
    парке» — при разборе жалоб это первый вопрос."""
    cursor.execute(
        """
        UPDATE driver_mailing_targets
           SET status = 'failed',
               error = %s,
               error_code = %s
         WHERE mailing_id = %s AND park_id = %s
        """,
        (_text(error), _text(error_code), int(mailing_id), str(park_id or '')),
    )


def abandon_pending_targets(cursor, mailing_id):
    """Закрыть цели, до которых отправка так и не дошла.

    Вызывается, когда цикл отправки прервался посреди работы — упал пул
    соединений, кабинет отдал неожиданную форму ответа, процесс перезапустили.
    Без этого такие строки остаются в статусе «В очереди» НАВСЕГДА: фонового
    доводчика у раздела нет, а повторная отправка с тем же токеном отвечает
    «уже отправлено» и цикл не возобновляет.

    «В очереди» в журнале читается как «вот-вот уйдёт» — то есть врёт: не уйдёт
    уже никогда. Честнее сказать «отправка прервалась», и человек решит, слать
    ли заново. Возвращает число закрытых строк.
    """
    cursor.execute(
        """
        UPDATE driver_mailing_targets
           SET status = 'failed',
               error = 'Отправка прервалась и до этой диспетчерской не дошла',
               error_code = 'interrupted'
         WHERE mailing_id = %s AND status = 'pending'
        """,
        (int(mailing_id),),
    )
    return cursor.rowcount or 0


def mark_target_revoked(cursor, mailing_id, park_id):
    """Отозвано в кабинете.

    Только для строк, которые уже уходили: отзывать pending или failed нечего, а
    условие по статусу защищает от гонки «отзыв прилетел раньше, чем отправка
    записалась» — иначе рассылка осталась бы навсегда отозванной, продолжая
    висеть у водителей.
    """
    cursor.execute(
        """
        UPDATE driver_mailing_targets
           SET status = 'revoked',
               revoked_at = NOW()
         WHERE mailing_id = %s AND park_id = %s AND status = 'sent'
        """,
        (int(mailing_id), str(park_id or '')),
    )


def finish_mailing(cursor, mailing_id):
    """Пересчитать сводный статус рассылки по её целям и закрыть карточку.

    Считаем в Python, а не одним UPDATE с CASE, потому что правило читается
    людьми чаще, чем исполняется базой:

        целей нет            → failed   (не дошли даже до первой отправки)
        все отозваны         → revoked
        ушло ноль            → failed
        ушло везде           → sent
        иначе                → partial

    'partial' здесь не аварийное состояние, а обычный исход: в одном парке
    рассылка разрешена, в другом суточный лимит уже исчерпан. Смесь sent и
    revoked тоже даёт partial — и это честно: часть водителей сообщение видела.

    Функция вызывается и после отправки, и после отзыва, поэтому finished_at
    переписывается: это «когда с рассылкой в последний раз что-то сделали», а не
    «когда её создали».
    """
    cursor.execute(
        """
        SELECT COUNT(*)                                          AS total,
               COUNT(*) FILTER (WHERE status = 'sent')           AS sent,
               COUNT(*) FILTER (WHERE status = 'revoked')        AS revoked
          FROM driver_mailing_targets
         WHERE mailing_id = %s
        """,
        (int(mailing_id),),
    )
    row = cursor.fetchone() or (0, 0, 0)
    total, sent, revoked = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)

    if total == 0:
        status = 'failed'
    elif revoked == total:
        status = 'revoked'
    elif sent == 0:
        status = 'failed'
    elif sent == total:
        status = 'sent'
    else:
        status = 'partial'

    cursor.execute(
        "UPDATE driver_mailings SET status = %s, finished_at = NOW() WHERE id = %s",
        (status, int(mailing_id)),
    )
    return status


def _attach_targets(cursor, mailings):
    """Дописать каждой рассылке её цели ОДНОЙ выборкой на всю страницу.

    Не в цикле по рассылкам: журнал открывают на 20 строк, а у каждой до пяти
    целей — это был бы 21 запрос вместо двух. И не через json_agg в основном
    запросе: тогда даты внутри целей приехали бы строками, а снаружи — datetime,
    и сериализация ответа стала бы зависеть от того, откуда взялась строка.
    """
    if not mailings:
        return mailings
    ids = [int(item['id']) for item in mailings]
    cursor.execute(
        """
        SELECT {columns}
          FROM driver_mailing_targets
         WHERE mailing_id = ANY(%s)
         ORDER BY COALESCE(park_city, ''), COALESCE(park_name, ''), park_id
        """.format(columns=', '.join(TARGET_COLUMNS)),
        (ids,),
    )
    by_mailing = {}
    for target in _rows_to_dicts(cursor):
        by_mailing.setdefault(int(target['mailing_id']), []).append(target)
    for item in mailings:
        item['targets'] = by_mailing.get(int(item['id']), [])
    return mailings


def journal(cursor, limit=20, offset=0):
    """Страница журнала: (список рассылок с вложенными целями, всего записей).

    total считается отдельным COUNT(*), а не окном в основном запросе: с ним
    пришлось бы тащить лишнюю колонку в каждую строку страницы, а таблица растёт
    на десяток строк в сутки — считать её целиком дёшево.
    """
    limit = max(1, min(int(limit or 20), 200))
    offset = max(0, int(offset or 0))

    cursor.execute("SELECT COUNT(*) FROM driver_mailings")
    total = int((cursor.fetchone() or (0,))[0] or 0)

    cursor.execute(
        """
        SELECT {columns}
          FROM driver_mailings
         ORDER BY created_at DESC, id DESC
         LIMIT %s OFFSET %s
        """.format(columns=', '.join(MAILING_COLUMNS)),
        (limit, offset),
    )
    items = _attach_targets(cursor, _rows_to_dicts(cursor))
    return items, total


def mailing_detail(cursor, mailing_id):
    """Карточка одной рассылки с её целями или None, если такой нет."""
    cursor.execute(
        "SELECT {columns} FROM driver_mailings WHERE id = %s".format(
            columns=', '.join(MAILING_COLUMNS)
        ),
        (int(mailing_id),),
    )
    mailing = _row_to_dict(cursor, cursor.fetchone())
    if mailing is None:
        return None
    return _attach_targets(cursor, [mailing])[0]


def claimed_fleet_ids(cursor, park_id, since=None):
    """Идентификаторы рассылок кабинета, уже закреплённые за нашими записями.

    Нужно при сопоставлении после отправки. Кабинет на отправку отвечает 204 без
    тела, и свой id мы разыскиваем в его журнале по совпадению заголовка. Если в
    один парк ушли две рассылки с одинаковым заголовком (а это ровно тот случай,
    когда человек отправляет повторно), верхняя запись журнала подойдёт обеим — и
    вторая заберёт себе чужой id. Тогда отзыв первой отозвал бы вторую.

    Поэтому перед выбором забираем занятое и пропускаем его. since — нижняя
    граница по времени отправки, чтобы не тащить всю историю парка; строки, у
    которых sent_at ещё не проставлен, попадают в ответ всегда: они закреплены
    прямо сейчас, и именно с ними столкнётся параллельная отправка.
    """
    cursor.execute(
        """
        SELECT fleet_mailing_id
          FROM driver_mailing_targets
         WHERE park_id = %s
           AND fleet_mailing_id IS NOT NULL
           AND (%s::timestamptz IS NULL OR sent_at IS NULL OR sent_at >= %s::timestamptz)
        """,
        (str(park_id or ''), since, since),
    )
    return {str(row[0]) for row in cursor.fetchall() if row[0]}


# ── Шаблоны ──────────────────────────────────────────────────────────────────

def templates(cursor):
    """Все шаблоны раздела. Не по автору: периметр раздела — два-три человека,
    и прятать заготовки друг от друга внутри такой группы незачем."""
    cursor.execute(
        """
        SELECT {columns}
          FROM driver_mailing_templates
         ORDER BY updated_at DESC, id DESC
        """.format(columns=', '.join(TEMPLATE_COLUMNS))
    )
    return _rows_to_dicts(cursor)


def get_template(cursor, template_id):
    cursor.execute(
        "SELECT {columns} FROM driver_mailing_templates WHERE id = %s".format(
            columns=', '.join(TEMPLATE_COLUMNS)
        ),
        (int(template_id),),
    )
    return _row_to_dict(cursor, cursor.fetchone())


def create_template(cursor, *, name, title=None, message_kk=None, message_ru=None,
                    message=None, filters=None, park_ids=None, created_by=None,
                    created_by_name=None):
    """Завести шаблон и вернуть его целиком (роут отдаёт созданную запись).

    park_ids складываем в шаблон вместе с текстом: половина заготовки — это не
    слова, а «кому», и шаблон без набора диспетчерских пришлось бы каждый раз
    досбирать руками, то есть он не экономил бы ничего.
    """
    cursor.execute(
        """
        INSERT INTO driver_mailing_templates (name, title, message_kk, message_ru,
                                              message, filters, park_ids,
                                              created_by, created_by_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {columns}
        """.format(columns=', '.join(TEMPLATE_COLUMNS)),
        (
            str(name or '').strip(),
            str(title or ''),
            _text(message_kk),
            _text(message_ru),
            str(message or ''),
            Json(filters or {}),
            Json(list(park_ids or [])),
            _int(created_by),
            _text(created_by_name),
        ),
    )
    return _row_to_dict(cursor, cursor.fetchone())


def update_template(cursor, template_id, **fields):
    """Частичное обновление: меняем ровно то, что прислали.

    Имена колонок берутся из TEMPLATE_TEXT_FIELDS/TEMPLATE_JSON_FIELDS, всё
    остальное из fields молча отбрасывается — в SQL не должно попадать ни одного
    имени, пришедшего снаружи. Значения уходят только через %s.

    Пустой набор полей — не ошибка: PATCH без изменений возвращает текущую
    запись, а не 400.
    """
    sets, params = [], []
    for column in TEMPLATE_TEXT_FIELDS:
        if column not in fields:
            continue
        sets.append('{} = %s'.format(column))
        value = fields[column]
        if column == 'name':
            params.append(str(value or '').strip())
        elif column in ('title', 'message'):
            # В схеме NOT NULL DEFAULT '': пусто здесь — пустая строка, не NULL.
            params.append(str(value or ''))
        else:
            # Языковые части двуязычного составителя: пусто = не заполнено.
            params.append(_text(value))
    for column in TEMPLATE_JSON_FIELDS:
        if column in fields:
            sets.append('{} = %s'.format(column))
            default = [] if column == 'park_ids' else {}
            params.append(Json(fields[column] if fields[column] is not None else default))

    if not sets:
        return get_template(cursor, template_id)

    sets.append('updated_at = NOW()')
    params.append(int(template_id))
    cursor.execute(
        """
        UPDATE driver_mailing_templates
           SET {sets}
         WHERE id = %s
        RETURNING {columns}
        """.format(sets=', '.join(sets), columns=', '.join(TEMPLATE_COLUMNS)),
        params,
    )
    return _row_to_dict(cursor, cursor.fetchone())


def delete_template(cursor, template_id):
    """Удалить шаблон. True — удалили, False — такого не было.

    Удаление настоящее, а не пометкой: шаблон никуда не ссылается и ни в чём не
    участвует после отправки — рассылка хранит свой текст и свои фильтры копией.
    """
    cursor.execute(
        "DELETE FROM driver_mailing_templates WHERE id = %s",
        (int(template_id),),
    )
    return bool(cursor.rowcount)
