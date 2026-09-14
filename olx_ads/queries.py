# -*- coding: utf-8 -*-
"""SQL-слой раздела «Объявления OLX».

Функции принимают ГОТОВЫЙ курсор (из Database._get_cursor) и не управляют ни
пулом, ни транзакцией — их держит вызывающий. Так же устроены olx_amo, wiki,
crm, parcels.

Почему это важно именно здесь: применение правки трогает три таблицы — снимок
объявления, историю и счётчики прогона. Если бы каждая функция брала своё
соединение, объявление могло бы оказаться помеченным обновлённым, а строки
истории по нему не появиться — и правка, ушедшая в чужую систему, не осталась
бы записанной нигде. Один курсор = одна транзакция = снимок и история всегда
согласованы.

Отдельное правило этого раздела: **сетевой вызов НИКОГДА не идёт внутри
транзакции.** Поход в OLX занимает секунды, а прогон по 281 объявлению — минуты;
держать соединение к базе всё это время значит съесть пул (та же ошибка была
разобрана в olx_amo: таймаут amoCRM 60 с × 9 кабинетов). Поэтому service.py
пишет короткими транзакциями между запросами, а не одной длинной.
"""

import json
from datetime import datetime, timedelta

_NOW = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')"

# Смещение Алматы от UTC — тем же приёмом, что в olx_amo/queries.py и
# parcels/queries.py: у Казахстана с 01.03.2024 одна зона без перевода часов, а
# tzdata на контейнере Render может и отсутствовать.
_ALMATY_OFFSET = timedelta(hours=5)


def now_almaty():
    return datetime.utcnow() + _ALMATY_OFFSET


# ─────────────────────────────────────────────────────────────────────────────
# Строки в словари
# ─────────────────────────────────────────────────────────────────────────────
# Пул отдаёт обычный курсор psycopg2 — строки приходят кортежами. Имена берём из
# cursor.description, а не раскладываем руками по фиксированному списку: колонок
# много, и они растут вместе с историей.

def _one(cursor):
    row = cursor.fetchone()
    return _as_dict(cursor, row) if row is not None else None


def _all(cursor):
    rows = cursor.fetchall() or []
    names = [c[0] for c in (cursor.description or [])]
    return [dict(zip(names, row)) for row in rows]


def _as_dict(cursor, row):
    if isinstance(row, dict):
        return row
    names = [c[0] for c in (cursor.description or [])]
    return dict(zip(names, row))


def _scalar(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return list(row.values())[0]
    return row[0]


# ─────────────────────────────────────────────────────────────────────────────
# Снимок объявлений
# ─────────────────────────────────────────────────────────────────────────────

def upsert_advert(cursor, cabinet_code, row):
    """Сложить объявление в снимок. `row` — уже разобранный словарь из service."""
    cursor.execute(
        """
        INSERT INTO olx_ads_adverts (
            cabinet_code, advert_id, status, title, description, url,
            category_id, category_name, city_id, city_name,
            company_name, phone, activated_at, valid_to, auto_extend,
            payload, synced_at
        ) VALUES (
            %(cabinet)s, %(advert_id)s, %(status)s, %(title)s, %(description)s, %(url)s,
            %(category_id)s, %(category_name)s, %(city_id)s, %(city_name)s,
            %(company_name)s, %(phone)s, %(activated_at)s, %(valid_to)s, %(auto_extend)s,
            %(payload)s::jsonb, {now}
        )
        ON CONFLICT (cabinet_code, advert_id) DO UPDATE SET
            status = EXCLUDED.status,
            title = EXCLUDED.title,
            description = EXCLUDED.description,
            url = EXCLUDED.url,
            category_id = EXCLUDED.category_id,
            category_name = EXCLUDED.category_name,
            city_id = EXCLUDED.city_id,
            city_name = EXCLUDED.city_name,
            company_name = EXCLUDED.company_name,
            phone = EXCLUDED.phone,
            activated_at = EXCLUDED.activated_at,
            valid_to = EXCLUDED.valid_to,
            auto_extend = EXCLUDED.auto_extend,
            payload = EXCLUDED.payload,
            synced_at = {now}
        """.format(now=_NOW),
        dict(row, cabinet=cabinet_code, payload=json.dumps(row.get('payload') or {},
                                                           ensure_ascii=False)))


def drop_missing_adverts(cursor, cabinet_code, keep_ids):
    """Убрать из снимка то, чего в кабинете больше нет.

    Объявление могли удалить прямо в OLX. Оставив его в снимке, мы показывали бы
    в разделе то, чего не существует, и предлагали бы это править.
    """
    if keep_ids:
        cursor.execute(
            "DELETE FROM olx_ads_adverts "
            " WHERE cabinet_code = %s AND NOT (advert_id = ANY(%s))",
            (cabinet_code, [str(x) for x in keep_ids]))
    else:
        cursor.execute("DELETE FROM olx_ads_adverts WHERE cabinet_code = %s",
                       (cabinet_code,))
    return cursor.rowcount or 0


def get_advert(cursor, cabinet_code, advert_id):
    cursor.execute(
        "SELECT * FROM olx_ads_adverts WHERE cabinet_code = %s AND advert_id = %s",
        (cabinet_code, str(advert_id)))
    return _one(cursor)


def _advert_filters(cabinet=None, status=None, city_id=None, category_id=None,
                    search=None, has_draft=None):
    """Общий WHERE для списка и для счётчика — один, чтобы они не разъехались."""
    where, params = [], {}
    if cabinet:
        where.append("a.cabinet_code = %(cabinet)s")
        params['cabinet'] = cabinet
    if status:
        where.append("a.status = %(status)s")
        params['status'] = status
    if city_id:
        where.append("a.city_id = %(city_id)s")
        params['city_id'] = int(city_id)
    if category_id:
        where.append("a.category_id = %(category_id)s")
        params['category_id'] = int(category_id)
    if search:
        where.append("(a.title ILIKE %(search)s OR a.description ILIKE %(search)s"
                     " OR a.advert_id = %(search_exact)s)")
        params['search'] = '%%%s%%' % (search,)
        params['search_exact'] = str(search).strip()
    if has_draft is True:
        where.append("d.id IS NOT NULL")
    elif has_draft is False:
        where.append("d.id IS NULL")
    return (' AND '.join(where) if where else 'TRUE'), params


# Черновик приклеивается к объявлению здесь, а не вторым запросом с фронта:
# список из 281 строки иначе превращался бы в 281 запрос за черновиком.
_LIST_SQL = """
    -- Колонки перечислены явно: полный объект OLX (~2 КБ на строку) списку не
    -- нужен, он нужен только сборке тела PUT, а та читает объявление из OLX
    -- заново. Тащить его в каждую страницу списка значило бы гонять в браузер
    -- сотни килобайт, которых экран не показывает.
    SELECT a.cabinet_code, a.advert_id, a.status, a.title, a.description, a.url,
           a.category_id, a.category_name, a.city_id, a.city_name,
           a.company_name, a.phone, a.activated_at, a.valid_to, a.auto_extend,
           a.synced_at,
           d.id            AS draft_id,
           d.title         AS draft_title,
           d.description   AS draft_description,
           d.model         AS draft_model,
           d.created_at    AS draft_created_at,
           d.created_by_name AS draft_author,
           h.created_at    AS last_change_at,
           h.actor_name    AS last_change_actor,
           h.result        AS last_change_result
      FROM olx_ads_adverts a
      LEFT JOIN olx_ads_drafts d
             ON d.cabinet_code = a.cabinet_code
            AND d.advert_id = a.advert_id
            AND d.status = 'draft'
      LEFT JOIN LATERAL (
            SELECT created_at, actor_name, result
              FROM olx_ads_history
             WHERE cabinet_code = a.cabinet_code AND advert_id = a.advert_id
             ORDER BY created_at DESC
             LIMIT 1
      ) h ON TRUE
     WHERE {where}
     ORDER BY a.cabinet_code, a.city_name NULLS LAST, a.advert_id
     LIMIT %(limit)s OFFSET %(offset)s
"""


def list_adverts(cursor, limit=100, offset=0, **filters):
    where, params = _advert_filters(**filters)
    params.update({'limit': int(limit), 'offset': int(offset)})
    cursor.execute(_LIST_SQL.format(where=where), params)
    return _all(cursor)


def count_adverts(cursor, **filters):
    where, params = _advert_filters(**filters)
    cursor.execute(
        """
        SELECT COUNT(*)
          FROM olx_ads_adverts a
          LEFT JOIN olx_ads_drafts d
                 ON d.cabinet_code = a.cabinet_code
                AND d.advert_id = a.advert_id
                AND d.status = 'draft'
         WHERE %s
        """ % (where,), params)
    return int(_scalar(cursor) or 0)


def advert_facets(cursor):
    """Значения для выпадашек фильтра — из самих данных, а не из справочника.

    Списывать города и категории из кода нельзя: они приходят из OLX и меняются
    без нас. Пустой фильтр с несуществующим значением хуже отсутствующего.
    """
    cursor.execute(
        """
        SELECT cabinet_code,
               COUNT(*)                                   AS total,
               COUNT(*) FILTER (WHERE status = 'active')  AS active
          FROM olx_ads_adverts
         GROUP BY cabinet_code
         ORDER BY cabinet_code
        """)
    cabinets_rows = _all(cursor)

    cursor.execute(
        "SELECT city_id, MAX(city_name) AS city_name, COUNT(*) AS total "
        "  FROM olx_ads_adverts WHERE city_id IS NOT NULL "
        " GROUP BY city_id ORDER BY MAX(city_name)")
    cities = _all(cursor)

    cursor.execute(
        "SELECT category_id, MAX(category_name) AS category_name, COUNT(*) AS total "
        "  FROM olx_ads_adverts WHERE category_id IS NOT NULL "
        " GROUP BY category_id ORDER BY COUNT(*) DESC")
    categories = _all(cursor)

    cursor.execute(
        "SELECT status, COUNT(*) AS total FROM olx_ads_adverts "
        " GROUP BY status ORDER BY COUNT(*) DESC")
    statuses = _all(cursor)

    cursor.execute("SELECT MAX(synced_at) FROM olx_ads_adverts")
    synced_at = _scalar(cursor)

    cursor.execute("SELECT COUNT(*) FROM olx_ads_drafts WHERE status = 'draft'")
    drafts = int(_scalar(cursor) or 0)

    return {'cabinets': cabinets_rows, 'cities': cities, 'categories': categories,
            'statuses': statuses, 'synced_at': synced_at, 'drafts': drafts}


# ─────────────────────────────────────────────────────────────────────────────
# Прогоны и история
# ─────────────────────────────────────────────────────────────────────────────

def start_run(cursor, kind, actor_id, actor_name, total):
    cursor.execute(
        """
        INSERT INTO olx_ads_runs (kind, status, actor_id, actor_name, total)
        VALUES (%s, 'running', %s, %s, %s)
        RETURNING id
        """, (kind, actor_id, actor_name, int(total)))
    return _scalar(cursor)


def finish_run(cursor, run_id, applied, failed, status='done', error_text=None):
    cursor.execute(
        """
        UPDATE olx_ads_runs
           SET applied = %s, failed = %s, status = %s,
               error_text = %s, finished_at = {now}
         WHERE id = %s
        """.format(now=_NOW),
        (int(applied), int(failed), status, error_text, int(run_id)))


def get_run(cursor, run_id):
    cursor.execute("SELECT * FROM olx_ads_runs WHERE id = %s", (int(run_id),))
    return _one(cursor)


def list_runs(cursor, limit=20):
    cursor.execute("SELECT * FROM olx_ads_runs ORDER BY started_at DESC LIMIT %s",
                   (int(limit),))
    return _all(cursor)


def add_history(cursor, **row):
    """Записать попытку правки. Пишется ВСЕГДА — и на удачу, и на отказ OLX.

    Строка с result='failed' это не мусор: в самом OLX после отказа не остаётся
    ничего, и без неё расхождение «применили 281, в кабинете 279» не
    расследуется.
    """
    cursor.execute(
        """
        INSERT INTO olx_ads_history (
            run_id, cabinet_code, advert_id, advert_url,
            actor_id, actor_name, source, origin,
            old_title, new_title, old_description, new_description,
            result, error_text
        ) VALUES (
            %(run_id)s, %(cabinet_code)s, %(advert_id)s, %(advert_url)s,
            %(actor_id)s, %(actor_name)s, %(source)s, %(origin)s,
            %(old_title)s, %(new_title)s, %(old_description)s, %(new_description)s,
            %(result)s, %(error_text)s
        )
        RETURNING id
        """, {
            'run_id': row.get('run_id'),
            'cabinet_code': row['cabinet_code'],
            'advert_id': str(row['advert_id']),
            'advert_url': row.get('advert_url'),
            'actor_id': row.get('actor_id'),
            'actor_name': row.get('actor_name'),
            'source': row.get('source') or 'single',
            'origin': row.get('origin') or 'human',
            'old_title': row.get('old_title'),
            'new_title': row.get('new_title'),
            'old_description': row.get('old_description'),
            'new_description': row.get('new_description'),
            'result': row['result'],
            'error_text': row.get('error_text'),
        })
    return _scalar(cursor)


def list_history(cursor, limit=100, offset=0, cabinet=None, advert_id=None,
                 result=None, run_id=None, origin=None):
    where, params = ['TRUE'], {'limit': int(limit), 'offset': int(offset)}
    if cabinet:
        where.append("cabinet_code = %(cabinet)s")
        params['cabinet'] = cabinet
    if advert_id:
        where.append("advert_id = %(advert_id)s")
        params['advert_id'] = str(advert_id)
    if result:
        where.append("result = %(result)s")
        params['result'] = result
    if origin:
        where.append("origin = %(origin)s")
        params['origin'] = origin
    if run_id:
        where.append("run_id = %(run_id)s")
        params['run_id'] = int(run_id)
    cursor.execute(
        "SELECT * FROM olx_ads_history WHERE %s "
        " ORDER BY created_at DESC, id DESC LIMIT %%(limit)s OFFSET %%(offset)s"
        % (' AND '.join(where),), params)
    return _all(cursor)


def count_history(cursor, cabinet=None, advert_id=None, result=None, run_id=None,
                  origin=None):
    where, params = ['TRUE'], {}
    if cabinet:
        where.append("cabinet_code = %(cabinet)s")
        params['cabinet'] = cabinet
    if advert_id:
        where.append("advert_id = %(advert_id)s")
        params['advert_id'] = str(advert_id)
    if result:
        where.append("result = %(result)s")
        params['result'] = result
    if origin:
        where.append("origin = %(origin)s")
        params['origin'] = origin
    if run_id:
        where.append("run_id = %(run_id)s")
        params['run_id'] = int(run_id)
    cursor.execute("SELECT COUNT(*) FROM olx_ads_history WHERE %s"
                   % (' AND '.join(where),), params)
    return int(_scalar(cursor) or 0)


def get_history_entry(cursor, entry_id):
    cursor.execute("SELECT * FROM olx_ads_history WHERE id = %s", (int(entry_id),))
    return _one(cursor)


# ─────────────────────────────────────────────────────────────────────────────
# Бриф месяца
# ─────────────────────────────────────────────────────────────────────────────

_BRIEF_FIELDS = ('title', 'offer', 'bonus', 'promo', 'raffle', 'commission',
                 'income', 'extra')


# Кабинеты брифа приклеиваются к нему здесь, одним запросом: экран брифов
# показывает у каждого брифа, для кого он написан, и N+1 запросов на список не
# нужен.
_BRIEF_SELECT = """
    SELECT b.*,
           COALESCE(ARRAY(SELECT c.cabinet_code
                            FROM olx_ads_brief_cabinets c
                           WHERE c.brief_id = b.id
                           ORDER BY c.cabinet_code), '{}') AS cabinets
      FROM olx_ads_briefs b
"""


def list_briefs(cursor, limit=50):
    cursor.execute(_BRIEF_SELECT + " ORDER BY b.is_active DESC, b.updated_at DESC LIMIT %s",
                   (int(limit),))
    return _all(cursor)


def get_brief(cursor, brief_id):
    cursor.execute(_BRIEF_SELECT + " WHERE b.id = %s", (int(brief_id),))
    return _one(cursor)


def briefs_by_cabinet(cursor):
    """Карта «кабинет → действующий бриф» (только id и название).

    Нужна экрану: какой бриф возьмёт ИИ у объявления этого кабинета, и у каких
    кабинетов брифа нет вовсе. Кабинета без брифа в карте просто нет.
    """
    cursor.execute(
        """
        SELECT c.cabinet_code, b.id, b.title
          FROM olx_ads_brief_cabinets c
          JOIN olx_ads_briefs b ON b.id = c.brief_id
         WHERE c.is_active
        """)
    return {row['cabinet_code']: {'id': row['id'], 'title': row['title']}
            for row in _all(cursor)}


def active_briefs_for(cursor, cabinet_codes):
    """Действующие брифы целиком для перечисленных кабинетов: {кабинет: бриф}.

    Для генерации ИИ: у каждого объявления свой кабинет, а значит и свой бриф.
    """
    codes = sorted({str(code) for code in (cabinet_codes or []) if code})
    if not codes:
        return {}
    cursor.execute(
        """
        SELECT c.cabinet_code AS covered_cabinet, b.*
          FROM olx_ads_brief_cabinets c
          JOIN olx_ads_briefs b ON b.id = c.brief_id
         WHERE c.is_active AND c.cabinet_code = ANY(%s)
        """, (codes,))
    return {row['covered_cabinet']: row for row in _all(cursor)}


def create_brief(cursor, values, actor_id=None, actor_name=None):
    payload = {field: (values.get(field) or None) for field in _BRIEF_FIELDS}
    payload.update({'actor_id': actor_id, 'actor_name': actor_name})
    cursor.execute(
        """
        INSERT INTO olx_ads_briefs (title, offer, bonus, promo, raffle,
                                    commission, income, extra,
                                    created_by, created_by_name)
        VALUES (%(title)s, %(offer)s, %(bonus)s, %(promo)s, %(raffle)s,
                %(commission)s, %(income)s, %(extra)s, %(actor_id)s, %(actor_name)s)
        RETURNING id
        """, payload)
    return _scalar(cursor)


def update_brief(cursor, brief_id, values):
    payload = {field: (values.get(field) or None) for field in _BRIEF_FIELDS}
    payload['id'] = int(brief_id)
    cursor.execute(
        """
        UPDATE olx_ads_briefs
           SET title = %(title)s, offer = %(offer)s, bonus = %(bonus)s,
               promo = %(promo)s, raffle = %(raffle)s, commission = %(commission)s,
               income = %(income)s, extra = %(extra)s, updated_at = {now}
         WHERE id = %(id)s
        """.format(now=_NOW), payload)
    return cursor.rowcount or 0


def brief_cabinet_codes(cursor, brief_id):
    cursor.execute(
        "SELECT cabinet_code FROM olx_ads_brief_cabinets WHERE brief_id = %s "
        " ORDER BY cabinet_code", (int(brief_id),))
    return [row['cabinet_code'] for row in _all(cursor)]


def _claim_cabinets(cursor, brief_id, codes):
    """Забрать кабинеты у других ДЕЙСТВУЮЩИХ брифов. Возвращает, что откуда перешло.

    Правило раздела: у кабинета один действующий бриф, и кабинет забирает тот,
    который включили (или сохранили включённым) последним. У прежнего брифа
    кабинет СНИМАЕТСЯ из списка, а не просто гасится: иначе в экране у брифа
    стоял бы кабинет, на котором он не действует, — и «почему ИИ взял не этот
    бриф» стало бы загадкой. Бриф, у которого не осталось ни одного кабинета,
    выключается сам.

    Выключенные брифы не трогаем: их список кабинетов — это заготовка, и пока
    её не включили, она никому не мешает.
    """
    codes = sorted({str(code) for code in (codes or []) if code})
    if not codes:
        return []
    cursor.execute(
        """
        SELECT c.cabinet_code AS cabinet, b.id AS from_brief_id, b.title AS from_title
          FROM olx_ads_brief_cabinets c
          JOIN olx_ads_briefs b ON b.id = c.brief_id
         WHERE c.is_active AND c.brief_id <> %s AND c.cabinet_code = ANY(%s)
         ORDER BY c.cabinet_code
        """, (int(brief_id), codes))
    moved = _all(cursor)
    if not moved:
        return []
    cursor.execute(
        "DELETE FROM olx_ads_brief_cabinets "
        " WHERE is_active AND brief_id <> %s AND cabinet_code = ANY(%s)",
        (int(brief_id), codes))
    cursor.execute(
        """
        UPDATE olx_ads_briefs b
           SET is_active = FALSE, updated_at = {now}
         WHERE b.is_active AND b.id <> %s
           AND NOT EXISTS (SELECT 1 FROM olx_ads_brief_cabinets c WHERE c.brief_id = b.id)
        """.format(now=_NOW), (int(brief_id),))
    return moved


def set_brief_cabinets(cursor, brief_id, cabinet_codes):
    """Заменить список кабинетов брифа. Возвращает, какие кабинеты перешли от других.

    Если бриф включён, новые кабинеты он забирает у других действующих брифов
    сразу (см. `_claim_cabinets`); выключенный просто запоминает выбор.
    """
    codes = sorted({str(code) for code in (cabinet_codes or []) if code})
    cursor.execute("SELECT is_active FROM olx_ads_briefs WHERE id = %s", (int(brief_id),))
    active = bool(_scalar(cursor))

    cursor.execute(
        "DELETE FROM olx_ads_brief_cabinets "
        " WHERE brief_id = %s AND NOT (cabinet_code = ANY(%s))",
        (int(brief_id), codes))

    # Сначала забрать у других, потом зажечь у себя: частичный уникальный индекс
    # uniq_olx_ads_brief_cabinet_active не даст двум действующим строкам по
    # одному кабинету существовать даже на миг.
    moved = _claim_cabinets(cursor, brief_id, codes) if active else []
    for code in codes:
        cursor.execute(
            """
            INSERT INTO olx_ads_brief_cabinets (brief_id, cabinet_code, is_active)
            VALUES (%s, %s, %s)
            ON CONFLICT (brief_id, cabinet_code) DO UPDATE SET is_active = EXCLUDED.is_active
            """, (int(brief_id), code, active))
    return moved


def activate_brief(cursor, brief_id):
    """Включить бриф для его кабинетов. Одна транзакция.

    Возвращает список перешедших кабинетов, а None — если кабинетов у брифа нет:
    включать бриф «ни для кого» бессмысленно, и вызывающий должен сказать это
    человеку, а не молча зажечь флаг.

    Порядок обязателен: сначала кабинеты забираются у других брифов, потом
    зажигаются у этого — иначе уникальный индекс по действующему кабинету
    отвергнет запись.
    """
    codes = brief_cabinet_codes(cursor, brief_id)
    if not codes:
        return None
    moved = _claim_cabinets(cursor, brief_id, codes)
    cursor.execute(
        "UPDATE olx_ads_brief_cabinets SET is_active = TRUE WHERE brief_id = %s",
        (int(brief_id),))
    cursor.execute(
        "UPDATE olx_ads_briefs SET is_active = TRUE, updated_at = {now} WHERE id = %s"
        .format(now=_NOW), (int(brief_id),))
    return moved


def deactivate_brief(cursor, brief_id):
    """Выключить бриф: его кабинеты остаются без брифа, пока не включат другой.

    Сами кабинеты из списка брифа не удаляются — выключенный бриф можно включить
    обратно тем же составом.
    """
    cursor.execute(
        "UPDATE olx_ads_brief_cabinets SET is_active = FALSE WHERE brief_id = %s",
        (int(brief_id),))
    cursor.execute(
        "UPDATE olx_ads_briefs SET is_active = FALSE, updated_at = {now} WHERE id = %s"
        .format(now=_NOW), (int(brief_id),))
    return cursor.rowcount or 0


# ─────────────────────────────────────────────────────────────────────────────
# Черновики текста
# ─────────────────────────────────────────────────────────────────────────────

def upsert_draft(cursor, cabinet_code, advert_id, title, description,
                 brief_id=None, model=None, origin='ai',
                 actor_id=None, actor_name=None):
    """Положить черновик. Живой черновик на объявление ровно один — перезаписываем."""
    cursor.execute(
        """
        INSERT INTO olx_ads_drafts (cabinet_code, advert_id, brief_id, title,
                                    description, model, origin, status,
                                    created_by, created_by_name)
        VALUES (%(cabinet)s, %(advert_id)s, %(brief_id)s, %(title)s,
                %(description)s, %(model)s, %(origin)s, 'draft',
                %(actor_id)s, %(actor_name)s)
        ON CONFLICT (cabinet_code, advert_id) WHERE status = 'draft'
        DO UPDATE SET title = EXCLUDED.title,
                      description = EXCLUDED.description,
                      brief_id = EXCLUDED.brief_id,
                      model = EXCLUDED.model,
                      origin = EXCLUDED.origin,
                      created_by = EXCLUDED.created_by,
                      created_by_name = EXCLUDED.created_by_name,
                      updated_at = {now}
        RETURNING id
        """.format(now=_NOW),
        {'cabinet': cabinet_code, 'advert_id': str(advert_id), 'brief_id': brief_id,
         'title': title, 'description': description, 'model': model,
         'origin': origin, 'actor_id': actor_id, 'actor_name': actor_name})
    return _scalar(cursor)


def get_draft(cursor, cabinet_code, advert_id):
    cursor.execute(
        "SELECT * FROM olx_ads_drafts "
        " WHERE cabinet_code = %s AND advert_id = %s AND status = 'draft'",
        (cabinet_code, str(advert_id)))
    return _one(cursor)


def list_live_drafts(cursor, limit=500):
    cursor.execute(
        """
        SELECT d.*, a.title AS current_title, a.description AS current_description,
               a.city_name, a.category_id, a.url, a.status AS advert_status
          FROM olx_ads_drafts d
          JOIN olx_ads_adverts a
            ON a.cabinet_code = d.cabinet_code AND a.advert_id = d.advert_id
         WHERE d.status = 'draft'
         ORDER BY d.cabinet_code, a.city_name NULLS LAST
         LIMIT %s
        """, (int(limit),))
    return _all(cursor)


def set_draft_status(cursor, draft_id, status):
    cursor.execute(
        "UPDATE olx_ads_drafts SET status = %s, updated_at = {now}, "
        "       applied_at = CASE WHEN %s = 'applied' THEN {now} ELSE applied_at END "
        " WHERE id = %s".format(now=_NOW), (status, status, int(draft_id)))
    return cursor.rowcount or 0


def discard_drafts(cursor, pairs):
    """Выбросить черновики пачкой. `pairs` — [(кабинет, id объявления), ...]."""
    dropped = 0
    for cabinet_code, advert_id in pairs or []:
        cursor.execute(
            "UPDATE olx_ads_drafts SET status = 'discarded', updated_at = {now} "
            " WHERE cabinet_code = %s AND advert_id = %s AND status = 'draft'"
            .format(now=_NOW), (cabinet_code, str(advert_id)))
        dropped += cursor.rowcount or 0
    return dropped
