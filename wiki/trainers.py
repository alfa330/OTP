"""Статистика прохождений тренажёров.

Тренажёр — учебный телефон с экранами чужого приложения; сценарии, экраны и
реплики помощника живут во фронте (src/components/wiki/trainers), и сервер о них
по-прежнему ничего не знает. Здесь ровно одна вещь, которой во фронте быть не
может: КТО за него садился, откуда и чем это кончилось.

Три решения, из которых складывается всё остальное:

  * попытка заводится на СТАРТЕ. Половина ценности статистики — брошенные
    попытки: «пятеро дошли до подписи в eGov и закрыли» говорит про инструкцию
    больше, чем «трое прошли до конца». Строка со статусом started и есть такая
    попытка, закрытие урока её дополняет;

  * агрегаты считаются в SQL, а не в питоне. Попыток со временем станет
    десятки тысяч (тренажёр проходят операторы всей линии), и вкладка, которая
    тянет их в питон ради среднего, начнёт грузиться секундами. Наружу уходит
    десяток чисел на тренажёр, а не таблица;

  * личные строки отдаются ТОЛЬКО тем, кто ведёт базу знаний. «Кто проходил» —
    персональные данные, и гейт у них тот же, что у вкладки «Тренажёры»
    (см. wiki_trainers в routes_articles.py). Запись попытки, наоборот, доступна
    любому читателю: он и есть тот, кто проходит.
"""

# Время в разделе везде алматинское, а не UTC: отчёт «кто прошёл сегодня»
# читают в Алматы, и смещение в пять часов сдвинуло бы половину смены на вчера.
# Берём то же выражение, которым проставляются DEFAULT'ы схемы, — второе
# определение «сейчас» разошлось бы с первым молча.
from datetime import datetime, timedelta

from .schema import _NOW as ALMATY_NOW

# Значения, которые фронт может прислать. Проверяем списком, а не полагаемся на
# CHECK в базе: неизвестный источник должен превратиться в 'article', а не
# уронить запись попытки — статистика не тот случай, ради которого стоит терять
# урок человека.
SOURCES = ('article', 'catalog')
STATUSES = ('started', 'finished', 'abandoned')


def _clamp(value, low, high, default=0):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


# ── Запись попытки ───────────────────────────────────────────────────────────

_START_SQL = """
INSERT INTO wiki_trainer_runs (
    trainer_key, user_id, article_id, article_title, source, stages_total,
    snapshot_department_id, snapshot_department_name, snapshot_group_name, snapshot_role)
SELECT %(key)s, u.id, %(article)s, a.title, %(source)s, %(stages)s,
       u.department_id, d.name, g.name, u.role
  FROM users u
  LEFT JOIN departments d ON d.id = u.department_id
  LEFT JOIN wiki_articles a ON a.id = %(article)s
  LEFT JOIN LATERAL (
        SELECT gr.name
          FROM group_operator_memberships gom
          JOIN groups gr ON gr.id = gom.group_id
         WHERE gom.operator_id = u.id
           AND gom.start_date <= CURRENT_DATE
           AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
         LIMIT 1
  ) g ON TRUE
 WHERE u.id = %(user)s
RETURNING id
"""


def start_run(cursor, *, trainer_key, user_id, article_id, source, stages_total):
    """Завести попытку. Возвращает id строки.

    Снимок отдела, группы и роли берётся ЗДЕСЬ же, одним запросом: операторы
    переходят между группами каждый месяц, и «кто из моей группы прошёл» без
    снимка врёт уже через месяц — ровно как это было бы у ознакомлений
    (см. wiki/ack.py).
    """
    cursor.execute(_START_SQL, {
        'key': str(trainer_key)[:64],
        'user': user_id,
        'article': article_id,
        'source': source if source in SOURCES else 'article',
        'stages': _clamp(stages_total, 0, 999),
    })
    row = cursor.fetchone()
    return row[0] if row else None


# Дополняем ТОЛЬКО свою попытку: id приходит из браузера, и без проверки
# владельца чужую попытку можно было бы закрыть как пройденную.
#
# finished_at и duration_ms ставим лишь при завершении: у брошенной попытки
# «сколько заняло» смысла не имеет — человек мог уйти пить чай с открытым
# уроком, и такое среднее время испортило бы отчёт целиком.
_FINISH_SQL = """
UPDATE wiki_trainer_runs
   SET status      = %(status)s,
       stages_done = GREATEST(stages_done, %(done)s),
       errors      = %(errors)s,
       hints       = %(hints)s,
       restarts    = %(restarts)s,
       duration_ms = CASE WHEN %(status)s = 'finished' THEN %(duration)s ELSE duration_ms END,
       finished_at = CASE WHEN %(status)s = 'finished' THEN %(now)s ELSE finished_at END
 WHERE id = %(id)s
   AND user_id = %(user)s
   AND status = 'started'
RETURNING id
"""


def finish_run(cursor, *, run_id, user_id, status, stages_done, errors, hints,
               restarts, duration_ms, now_sql=ALMATY_NOW):
    """Дополнить свою попытку. Возвращает True, если строка нашлась.

    Условие status = 'started' делает вызов идемпотентным: повторный «закрыл
    урок» (закрытие крестиком и следом pagehide-маячок браузера) не перезапишет
    уже проставленное «прошёл» на «бросил».
    """
    cursor.execute(_FINISH_SQL.replace('%(now)s', now_sql), {
        'id': run_id,
        'user': user_id,
        'status': status if status in STATUSES else 'abandoned',
        'done': _clamp(stages_done, 0, 999),
        'errors': _clamp(errors, 0, 9999),
        'hints': _clamp(hints, 0, 9999),
        'restarts': _clamp(restarts, 0, 9999),
        # Полчаса — потолок осмысленной попытки. Больше означает, что вкладку
        # оставили открытой, и такое число в среднем времени бесполезно.
        'duration': _clamp(duration_ms, 0, 30 * 60 * 1000, default=None),
    })
    return cursor.fetchone() is not None


# ── Сводка для витрины ───────────────────────────────────────────────────────

# Одна строка на тренажёр — ровно то, что рисуется на карточке вкладки. Median
# вместо среднего у длительности намеренно: одна попытка, оставленная открытой
# на двадцать минут, сдвигает среднее так, что цифра перестаёт что-либо значить.
_SUMMARY_SQL = """
SELECT trainer_key,
       count(*)                                             AS runs,
       count(*) FILTER (WHERE status = 'finished')          AS finished,
       count(DISTINCT user_id)                              AS people,
       count(DISTINCT user_id) FILTER (WHERE status = 'finished') AS people_done,
       max(started_at)                                      AS last_at,
       percentile_cont(0.5) WITHIN GROUP (
            ORDER BY duration_ms) FILTER (WHERE status = 'finished')  AS median_ms,
       avg(errors) FILTER (WHERE status = 'finished')       AS avg_errors,
       avg(hints)  FILTER (WHERE status = 'finished')       AS avg_hints
  FROM wiki_trainer_runs
 GROUP BY trainer_key
"""


def summary(cursor):
    """{ключ: сводка} по всем тренажёрам сразу.

    Без фильтра по ключам: тренажёров десяток, а лишний параметр заставил бы
    вкладку знать список ключей ДО того, как она его получила.
    """
    cursor.execute(_SUMMARY_SQL)
    out = {}
    for (key, runs, finished, people, people_done, last_at,
         median_ms, avg_errors, avg_hints) in cursor.fetchall():
        out[key] = {
            'runs': int(runs or 0),
            'finished': int(finished or 0),
            'people': int(people or 0),
            'people_done': int(people_done or 0),
            'last_at': last_at.isoformat() if last_at else None,
            'median_ms': int(median_ms) if median_ms is not None else None,
            'avg_errors': round(float(avg_errors), 1) if avg_errors is not None else None,
            'avg_hints': round(float(avg_hints), 1) if avg_hints is not None else None,
        }
    return out


# ── Подробности по одному тренажёру ──────────────────────────────────────────

_PERIOD = """
   AND (%(since)s::timestamp IS NULL OR r.started_at >= %(since)s)
   -- Приведение типа у ОБОИХ вхождений, а не только у проверки на NULL:
   -- «NULL + INTERVAL» Postgres вывести не может и падает с
   -- «operator does not exist: timestamp without time zone < interval».
   -- На моках такое не ловится — там любой SQL «проходит».
   AND (%(until)s::timestamp IS NULL
        OR r.started_at < %(until)s::timestamp + INTERVAL '1 day')
   AND (%(depts)s::int[] IS NULL OR r.snapshot_department_id = ANY(%(depts)s))
"""

# «Начал и не закрыл» — два разных состояния, и различает их время. Попытка
# младше получаса может идти прямо сейчас; всё, что старше, человек бросил, и
# закрытия мы уже не дождёмся: вкладку закрыли вместе с уроком, а маячок не
# дошёл (сеть, спящий телефон, аварийное закрытие браузера). Фоновых задач у
# вики нет, поэтому добиваем на лету — но в питоне, а не в SQL: тащить параметр
# «сейчас» во все шесть запросов ради одной подписи на экране дороже пользы.
STALE_MINUTES = 30

_TOTALS_SQL = """
SELECT count(*)                                            AS runs,
       count(*) FILTER (WHERE r.status = 'finished')       AS finished,
       count(DISTINCT r.user_id)                           AS people,
       count(DISTINCT r.user_id) FILTER (WHERE r.status = 'finished') AS people_done,
       min(r.started_at)                                   AS first_at,
       max(r.started_at)                                   AS last_at,
       percentile_cont(0.5) WITHIN GROUP (
            ORDER BY r.duration_ms) FILTER (WHERE r.status = 'finished') AS median_ms,
       avg(r.errors) FILTER (WHERE r.status = 'finished')  AS avg_errors,
       avg(r.hints)  FILTER (WHERE r.status = 'finished')  AS avg_hints,
       sum(r.restarts)                                     AS restarts
  FROM wiki_trainer_runs r
 WHERE r.trainer_key = %(key)s
""" + _PERIOD

# Разрез по статьям — то, о чём спрашивают первым: «в какой статье сколько раз».
# Статья, которую спрашивающему видеть нельзя, из разреза выпадает целиком, а не
# показывается заголовком: периметр вики и здесь тот же, что в каталоге.
_BY_ARTICLE_SQL = """
SELECT r.article_id,
       max(a.slug)                                         AS slug,
       max(COALESCE(r.article_title, a.title))             AS title,
       count(*)                                            AS runs,
       count(*) FILTER (WHERE r.status = 'finished')       AS finished,
       count(DISTINCT r.user_id)                           AS people,
       max(r.started_at)                                   AS last_at
  FROM wiki_trainer_runs r
  LEFT JOIN wiki_articles a ON a.id = r.article_id
 WHERE r.trainer_key = %(key)s
   AND (r.article_id IS NULL OR r.article_id = ANY(%(visible)s))
""" + _PERIOD + """
 GROUP BY r.article_id
 ORDER BY count(*) DESC, 3 NULLS LAST
"""

# По людям. Снимок отдела и группы берём с ПОСЛЕДНЕЙ попытки (max(started_at)):
# показывать самый старый снимок значило бы объяснять, почему человек числится в
# группе, из которой ушёл полгода назад.
_BY_PERSON_SQL = """
SELECT r.user_id,
       u.name,
       (array_agg(r.snapshot_department_name ORDER BY r.started_at DESC))[1] AS department,
       (array_agg(r.snapshot_group_name      ORDER BY r.started_at DESC))[1] AS grp,
       (array_agg(r.snapshot_role            ORDER BY r.started_at DESC))[1] AS role,
       count(*)                                            AS runs,
       count(*) FILTER (WHERE r.status = 'finished')       AS finished,
       sum(r.errors)                                       AS errors,
       sum(r.hints)                                        AS hints,
       min(r.duration_ms) FILTER (WHERE r.status = 'finished') AS best_ms,
       min(r.started_at)                                   AS first_at,
       max(r.started_at)                                   AS last_at
  FROM wiki_trainer_runs r
  LEFT JOIN users u ON u.id = r.user_id
 WHERE r.trainer_key = %(key)s
""" + _PERIOD + """
 GROUP BY r.user_id, u.name
 ORDER BY count(*) FILTER (WHERE r.status = 'finished') DESC, count(*) DESC, u.name
"""

# Лента попыток. Она же лист выгрузки, поэтому колонки одни и те же: расхождение
# между тем, что видно на экране, и тем, что уехало в файл, — самый дорогой сорт
# расхождения, его замечают уже в переписке с заказчиком.
_RUNS_SQL = """
SELECT r.id, r.started_at, r.finished_at, r.status, r.source,
       u.name, r.snapshot_department_name, r.snapshot_group_name, r.snapshot_role,
       COALESCE(r.article_title, a.title), a.slug,
       r.stages_done, r.stages_total, r.errors, r.hints, r.restarts, r.duration_ms
  FROM wiki_trainer_runs r
  LEFT JOIN users u ON u.id = r.user_id
  LEFT JOIN wiki_articles a ON a.id = r.article_id
 WHERE r.trainer_key = %(key)s
""" + _PERIOD + """
 ORDER BY r.started_at DESC
 LIMIT %(limit)s OFFSET %(offset)s
"""

_RUNS_COUNT_SQL = """
SELECT count(*) FROM wiki_trainer_runs r
 WHERE r.trainer_key = %(key)s
""" + _PERIOD


def _period(key, since, until, departments=None, extra=None):
    """Общие параметры всех запросов раздела.

    departments=None — без границы: так ходят директор и администратор вики (то
    же значение и тот же смысл, что у _grant_departments в routes_structure.py).
    Пустой СПИСОК — другое дело: он не откроет ничего, и это правильный ответ
    для того, у кого своего отдела нет.
    """
    params = {
        'key': key,
        'since': since or None,
        'until': until or None,
        'depts': None if departments is None else list(departments),
    }
    if extra:
        params.update(extra)
    return params


def _iso(value):
    return value.isoformat() if value else None


def totals(cursor, key, *, since=None, until=None, departments=None):
    cursor.execute(_TOTALS_SQL, _period(key, since, until, departments))
    (runs, finished, people, people_done, first_at, last_at,
     median_ms, avg_errors, avg_hints, restarts) = cursor.fetchone()
    return {
        'runs': int(runs or 0),
        'finished': int(finished or 0),
        'people': int(people or 0),
        'people_done': int(people_done or 0),
        'first_at': _iso(first_at),
        'last_at': _iso(last_at),
        'median_ms': int(median_ms) if median_ms is not None else None,
        'avg_errors': round(float(avg_errors), 1) if avg_errors is not None else None,
        'avg_hints': round(float(avg_hints), 1) if avg_hints is not None else None,
        'restarts': int(restarts or 0),
    }


def by_article(cursor, key, visible_ids, *, since=None, until=None, departments=None):
    cursor.execute(_BY_ARTICLE_SQL,
                   _period(key, since, until, departments,
                           {'visible': list(visible_ids or [])}))
    return [{
        'article_id': article_id,
        'slug': slug,
        # Запуск из вкладки «Тренажёры» статьи не имеет — и это не «нет данных»,
        # а законный второй источник. Подписываем его прямо здесь, чтобы ни
        # витрина, ни выгрузка не изобретали свою формулировку.
        'title': title or ('Вкладка «Тренажёры»' if article_id is None else 'Статья удалена'),
        'runs': int(runs or 0),
        'finished': int(finished or 0),
        'people': int(people or 0),
        'last_at': _iso(last_at),
    } for article_id, slug, title, runs, finished, people, last_at in cursor.fetchall()]


def by_person(cursor, key, *, since=None, until=None, departments=None):
    cursor.execute(_BY_PERSON_SQL, _period(key, since, until, departments))
    return [{
        'user_id': user_id,
        'name': name or 'Пользователь удалён',
        'department': department,
        'group': grp,
        'role': role,
        'runs': int(runs or 0),
        'finished': int(finished or 0),
        'errors': int(errors or 0),
        'hints': int(hints or 0),
        'best_ms': int(best_ms) if best_ms is not None else None,
        'first_at': _iso(first_at),
        'last_at': _iso(last_at),
    } for (user_id, name, department, grp, role, runs, finished,
           errors, hints, best_ms, first_at, last_at) in cursor.fetchall()]


def runs(cursor, key, *, since=None, until=None, departments=None,
         limit=100, offset=0, now=None):
    cursor.execute(_RUNS_COUNT_SQL, _period(key, since, until, departments))
    total = int(cursor.fetchone()[0] or 0)
    cursor.execute(_RUNS_SQL, _period(key, since, until, departments,
                                      {'limit': limit, 'offset': offset}))
    stale_before = (now or datetime.now()) - timedelta(minutes=STALE_MINUTES)
    items = [{
        'id': run_id,
        'started_at': _iso(started_at),
        'finished_at': _iso(finished_at),
        # Попытка, о закрытии которой никто не сказал и которая давно не могла
        # идти, — брошенная. Пересчитываем здесь, а не в базе: саму строку от
        # этого менять незачем, человек мог и вернуться к ней.
        'status': ('abandoned' if status == 'started' and started_at
                   and started_at < stale_before else status),
        'source': source,
        'name': name or 'Пользователь удалён',
        'department': department,
        'group': grp,
        'role': role,
        'article_title': article_title,
        'article_slug': slug,
        'stages_done': int(stages_done or 0),
        'stages_total': int(stages_total or 0),
        'errors': int(errors or 0),
        'hints': int(hints or 0),
        'restarts': int(restarts or 0),
        'duration_ms': int(duration_ms) if duration_ms is not None else None,
    } for (run_id, started_at, finished_at, status, source, name, department, grp, role,
           article_title, slug, stages_done, stages_total, errors, hints,
           restarts, duration_ms) in cursor.fetchall()]
    return {'total': total, 'items': items}
