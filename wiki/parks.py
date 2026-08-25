"""SQL таксопарков и акций.

Фича автономная: к статьям не привязана, живёт своей вкладкой раздела.
В проде вики её данными не пользовались (16 парков — ровно захардкоженный сид,
акций ноль), поэтому переносится механика, а не содержимое.

Парки и акции принадлежат ПРОСТРАНСТВУ (space_id, см. schema.
_scope_directories_to_space), поэтому space_id — обязательный аргумент каждой
функции: у справочника нет второй границы, и забытый параметр показал бы вику
одного клиента сотрудникам другого. То же в wiki/offices.py — офисы и парки
это один справочник с двух сторон.
"""

# Телефона среди полей нет: номера парка живут в wiki_park_phones — по одному
# на точку (офис или «онлайн»), потому что в одном офисе их бывает несколько.
_PARK_KEYS = ('id', 'slug', 'name', 'description', 'city', 'address',
              'website', 'commission', 'logo_file_id', 'status', 'position',
              'promotions_count', 'head_office_id', 'head_office_name',
              'head_office_city', 'head_office_address')

# Адрес парка — ссылка на офис, а не текст: свободное поле повторяло адрес,
# который уже записан в справочнике офисов, и повторы расходятся — ровно та
# болезнь, от которой ушла статья «Адреса офисов». Поэтому оба запроса ниже
# джойнят wiki_offices как ho.


def _park_row(row):
    """Строка парка в словарь.

    Собственный address парка остаётся как след старого свободного поля: его
    показывают, только если офис не выбран, — чтобы введённый ранее текст не
    исчез с экрана молча.
    """
    park = dict(zip(_PARK_KEYS, row))
    park['commission'] = float(park['commission']) if park['commission'] is not None else None
    park['logo_url'] = ('/api/wiki/file/%s' % park['logo_file_id']) if park['logo_file_id'] else None
    name = park.pop('head_office_name', None)
    city = park.pop('head_office_city', None)
    address = park.pop('head_office_address', None)
    park['head_office'] = ({'id': park['head_office_id'], 'name': name,
                            'city': city, 'address': address}
                           if park['head_office_id'] else None)
    return park


def list_parks(cursor, include_archived=False, query=None, *, space_id):
    cursor.execute(
        """
        SELECT p.id, p.slug, p.name, p.description, p.city, p.address,
               p.website, p.commission, p.logo_file_id, p.status, p.position,
               (SELECT count(*) FROM wiki_promotion_taxi_parks pp
                 JOIN wiki_promotions pr ON pr.id = pp.promotion_id
                WHERE pp.park_id = p.id AND pr.status = 'active'),
               p.head_office_id, ho.name, ho.city, ho.address
          FROM wiki_taxi_parks p
          -- Адрес головного офиса — только свой: связь границу не пересекает,
          -- но условие тут стоит дешевле, чем доверие к тому, что не пересечёт.
          LEFT JOIN wiki_offices ho ON ho.id = p.head_office_id
                                   AND ho.space_id = p.space_id
         WHERE p.space_id = %(space)s
           AND (%(archived)s OR p.status = 'active')
           AND (%(query)s::text IS NULL
                OR p.name ILIKE '%%' || %(query)s::text || '%%'
                OR p.city ILIKE '%%' || %(query)s::text || '%%')
         ORDER BY p.position, p.name
        """,
        {'archived': include_archived, 'query': query or None, 'space': space_id},
    )
    return [_park_row(row) for row in cursor.fetchall()]


def get_park(cursor, slug, *, space_id):
    """Парк по слагу В ПРОСТРАНСТВЕ: слаг уникален там же (см. схему)."""
    cursor.execute(
        """
        SELECT p.id, p.slug, p.name, p.description, p.city, p.address,
               p.website, p.commission, p.logo_file_id, p.status, p.position, 0,
               p.head_office_id, ho.name, ho.city, ho.address
          FROM wiki_taxi_parks p
          LEFT JOIN wiki_offices ho ON ho.id = p.head_office_id
                                   AND ho.space_id = p.space_id
         WHERE p.slug = %s AND p.space_id = %s
        """,
        (slug, space_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    park = _park_row(row)

    cursor.execute(
        """
        SELECT pr.id, pr.title, pr.description, pr.starts_at, pr.ends_at
          FROM wiki_promotions pr
          JOIN wiki_promotion_taxi_parks pp ON pp.promotion_id = pr.id
         WHERE pp.park_id = %s AND pr.status = 'active' AND pr.space_id = %s
         ORDER BY pr.ends_at NULLS LAST, pr.id DESC
        """,
        (park['id'], space_id),
    )
    park['promotions'] = [dict(zip(('id', 'title', 'description', 'starts_at', 'ends_at'), r))
                          for r in cursor.fetchall()]
    return park


def create_park(cursor, *, slug, name, fields, created_by, space_id):
    cursor.execute(
        """
        INSERT INTO wiki_taxi_parks (space_id, slug, name, description, city, address,
                                     website, commission, logo_file_id, head_office_id,
                                     position, created_by)
        VALUES (%(space)s, %(slug)s, %(name)s, %(description)s, %(city)s, %(address)s,
                %(website)s, %(commission)s, %(logo)s, %(head_office)s,
                -- Позиция — внутри пространства: общий max сдвигал бы первый
                -- парк новой вики за все чужие.
                COALESCE((SELECT max(position) + 1 FROM wiki_taxi_parks
                           WHERE space_id = %(space)s), 0), %(by)s)
        RETURNING id
        """,
        {'slug': slug, 'name': name, 'by': created_by, 'space': space_id,
         'description': fields.get('description'), 'city': fields.get('city'),
         'address': fields.get('address'),
         'website': fields.get('website'), 'commission': fields.get('commission'),
         'logo': fields.get('logo_file_id'), 'head_office': fields.get('head_office_id')},
    )
    return cursor.fetchone()[0]


_PARK_UPDATABLE = ('name', 'description', 'city', 'address', 'website',
                   'commission', 'logo_file_id', 'status', 'position', 'slug',
                   'head_office_id')


def update_park(cursor, park_id, fields, *, space_id):
    """False — парка нет ИЛИ он из другого пространства. Разницы нет намеренно:
    роут отвечает «Парк не найден», и для чужого парка это правда."""
    sets, values = [], []
    for key in _PARK_UPDATABLE:
        if key in fields:
            sets.append(key + ' = %s')
            values.append(fields[key])
    if not sets:
        return False
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.extend((park_id, space_id))
    cursor.execute('UPDATE wiki_taxi_parks SET ' + ', '.join(sets) +
                   ' WHERE id = %s AND space_id = %s', values)
    return cursor.rowcount > 0


_PROMO_KEYS = ('id', 'title', 'description', 'content', 'banner_file_id',
               'starts_at', 'ends_at', 'status', 'park_ids', 'is_running')


def list_promotions(cursor, include_archived=False, *, space_id):
    cursor.execute(
        """
        SELECT pr.id, pr.title, pr.description, pr.content, pr.banner_file_id,
               pr.starts_at, pr.ends_at, pr.status,
               COALESCE((SELECT array_agg(pp.park_id) FROM wiki_promotion_taxi_parks pp
                          WHERE pp.promotion_id = pr.id), '{}'),
               (pr.status = 'active'
                AND (pr.starts_at IS NULL
                     OR pr.starts_at <= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                AND (pr.ends_at IS NULL
                     OR pr.ends_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')))
          FROM wiki_promotions pr
         WHERE pr.space_id = %s AND (%s OR pr.status = 'active')
         ORDER BY pr.ends_at NULLS LAST, pr.id DESC
        """,
        (space_id, include_archived),
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(zip(_PROMO_KEYS, row))
        item['park_ids'] = list(item['park_ids'] or [])
        item['banner_url'] = ('/api/wiki/file/%s' % item['banner_file_id']) if item['banner_file_id'] else None
        rows.append(item)
    return rows


def create_promotion(cursor, *, title, fields, park_ids, created_by, space_id):
    cursor.execute(
        """
        INSERT INTO wiki_promotions (space_id, title, description, content, banner_file_id,
                                     starts_at, ends_at, created_by)
        VALUES (%(space)s, %(title)s, %(description)s, %(content)s, %(banner)s,
                %(starts)s, %(ends)s, %(by)s)
        RETURNING id
        """,
        {'title': title, 'description': fields.get('description'),
         'content': fields.get('content') or '', 'banner': fields.get('banner_file_id'),
         'starts': fields.get('starts_at'), 'ends': fields.get('ends_at'),
         'by': created_by, 'space': space_id},
    )
    promotion_id = cursor.fetchone()[0]
    set_promotion_parks(cursor, promotion_id, park_ids, space_id=space_id)
    return promotion_id


_PROMO_UPDATABLE = ('title', 'description', 'content', 'banner_file_id',
                    'starts_at', 'ends_at', 'status')


def update_promotion(cursor, promotion_id, fields, *, space_id):
    sets, values = [], []
    for key in _PROMO_UPDATABLE:
        if key in fields:
            sets.append(key + ' = %s')
            values.append(fields[key])
    if not sets:
        return False
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.extend((promotion_id, space_id))
    cursor.execute('UPDATE wiki_promotions SET ' + ', '.join(sets) +
                   ' WHERE id = %s AND space_id = %s', values)
    return cursor.rowcount > 0


def set_promotion_parks(cursor, promotion_id, park_ids, *, space_id):
    """Парки акции. Чужой park_id в теле запроса связи не создаёт.

    Парк выбирается запросом, а не подставляется значением: акция пространства
    «Тез», привязанная к парку Таксопарков, показала бы этот парк в её карточке
    — то есть утечка в обход самой вкладки «Парки».
    """
    cursor.execute('DELETE FROM wiki_promotion_taxi_parks WHERE promotion_id = %s',
                   (promotion_id,))
    for park_id in sorted({int(p) for p in (park_ids or []) if p}):
        cursor.execute(
            """
            INSERT INTO wiki_promotion_taxi_parks (promotion_id, park_id)
            SELECT pr.id, p.id
              FROM wiki_promotions pr
              JOIN wiki_taxi_parks p ON p.space_id = pr.space_id
             WHERE pr.id = %s AND p.id = %s AND pr.space_id = %s
            ON CONFLICT DO NOTHING
            """,
            (promotion_id, park_id, space_id),
        )


def logo_space_ids(cursor, file_id):
    """Пространства, в которых этот файл — логотип парка или баннер акции.

    Пусто — файл справочнику не принадлежит.

    Единственная функция модуля без аргумента space_id, и это не забывчивость:
    пространство здесь ОТВЕТ, а не условие выборки. Спрашивают ровно затем,
    чтобы роут /file/<id> сверил его с пространствами читателя. Без этого
    логотип видел бы один загрузивший: непривязанный к статье файл роут отдаёт
    только автору загрузки, и в рельсе витрины у всех остальных стояла бы
    битая картинка.

    Баннер акции здесь же, хотя грузить его пока неоткуда: колонка
    banner_file_id живёт в схеме с самого начала, и первая же форма для неё
    наступила бы на ту же яму.
    """
    cursor.execute(
        """
        SELECT space_id FROM wiki_taxi_parks WHERE logo_file_id = %(file)s
         UNION
        SELECT space_id FROM wiki_promotions WHERE banner_file_id = %(file)s
        """,
        {'file': file_id},
    )
    return {row[0] for row in cursor.fetchall()}


def slug_is_free(cursor, slug, exclude_id=None, *, space_id):
    """Свободен ли слаг В ЭТОМ пространстве (уникальность там же — см. схему)."""
    cursor.execute(
        'SELECT 1 FROM wiki_taxi_parks '
        ' WHERE space_id = %s AND slug = %s AND (%s::int IS NULL OR id <> %s::int)',
        (space_id, slug, exclude_id, exclude_id),
    )
    return cursor.fetchone() is None
