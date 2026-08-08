"""SQL таксопарков и акций.

Фича автономная: к статьям не привязана, живёт своей вкладкой раздела.
В проде вики её данными не пользовались (16 парков — ровно захардкоженный сид,
акций ноль), поэтому переносится механика, а не содержимое.
"""

_PARK_KEYS = ('id', 'slug', 'name', 'description', 'city', 'phone', 'address',
              'website', 'commission', 'logo_file_id', 'status', 'position',
              'promotions_count')


def list_parks(cursor, include_archived=False, query=None):
    cursor.execute(
        """
        SELECT p.id, p.slug, p.name, p.description, p.city, p.phone, p.address,
               p.website, p.commission, p.logo_file_id, p.status, p.position,
               (SELECT count(*) FROM wiki_promotion_taxi_parks pp
                 JOIN wiki_promotions pr ON pr.id = pp.promotion_id
                WHERE pp.park_id = p.id AND pr.status = 'active')
          FROM wiki_taxi_parks p
         WHERE (%(archived)s OR p.status = 'active')
           AND (%(query)s::text IS NULL
                OR p.name ILIKE '%%' || %(query)s::text || '%%'
                OR p.city ILIKE '%%' || %(query)s::text || '%%')
         ORDER BY p.position, p.name
        """,
        {'archived': include_archived, 'query': query or None},
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(zip(_PARK_KEYS, row))
        item['commission'] = float(item['commission']) if item['commission'] is not None else None
        item['logo_url'] = ('/api/wiki/file/%s' % item['logo_file_id']) if item['logo_file_id'] else None
        rows.append(item)
    return rows


def get_park(cursor, slug):
    cursor.execute(
        """
        SELECT p.id, p.slug, p.name, p.description, p.city, p.phone, p.address,
               p.website, p.commission, p.logo_file_id, p.status, p.position, 0
          FROM wiki_taxi_parks p WHERE p.slug = %s
        """,
        (slug,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    park = dict(zip(_PARK_KEYS, row))
    park['commission'] = float(park['commission']) if park['commission'] is not None else None
    park['logo_url'] = ('/api/wiki/file/%s' % park['logo_file_id']) if park['logo_file_id'] else None

    cursor.execute(
        """
        SELECT pr.id, pr.title, pr.description, pr.starts_at, pr.ends_at
          FROM wiki_promotions pr
          JOIN wiki_promotion_taxi_parks pp ON pp.promotion_id = pr.id
         WHERE pp.park_id = %s AND pr.status = 'active'
         ORDER BY pr.ends_at NULLS LAST, pr.id DESC
        """,
        (park['id'],),
    )
    park['promotions'] = [dict(zip(('id', 'title', 'description', 'starts_at', 'ends_at'), r))
                          for r in cursor.fetchall()]
    return park


def create_park(cursor, *, slug, name, fields, created_by):
    cursor.execute(
        """
        INSERT INTO wiki_taxi_parks (slug, name, description, city, phone, address,
                                     website, commission, logo_file_id, position, created_by)
        VALUES (%(slug)s, %(name)s, %(description)s, %(city)s, %(phone)s, %(address)s,
                %(website)s, %(commission)s, %(logo)s,
                COALESCE((SELECT max(position) + 1 FROM wiki_taxi_parks), 0), %(by)s)
        RETURNING id
        """,
        {'slug': slug, 'name': name, 'by': created_by,
         'description': fields.get('description'), 'city': fields.get('city'),
         'phone': fields.get('phone'), 'address': fields.get('address'),
         'website': fields.get('website'), 'commission': fields.get('commission'),
         'logo': fields.get('logo_file_id')},
    )
    return cursor.fetchone()[0]


_PARK_UPDATABLE = ('name', 'description', 'city', 'phone', 'address', 'website',
                   'commission', 'logo_file_id', 'status', 'position', 'slug')


def update_park(cursor, park_id, fields):
    sets, values = [], []
    for key in _PARK_UPDATABLE:
        if key in fields:
            sets.append(key + ' = %s')
            values.append(fields[key])
    if not sets:
        return False
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.append(park_id)
    cursor.execute('UPDATE wiki_taxi_parks SET ' + ', '.join(sets) + ' WHERE id = %s', values)
    return cursor.rowcount > 0


_PROMO_KEYS = ('id', 'title', 'description', 'content', 'banner_file_id',
               'starts_at', 'ends_at', 'status', 'park_ids', 'is_running')


def list_promotions(cursor, include_archived=False):
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
         WHERE (%s OR pr.status = 'active')
         ORDER BY pr.ends_at NULLS LAST, pr.id DESC
        """,
        (include_archived,),
    )
    rows = []
    for row in cursor.fetchall():
        item = dict(zip(_PROMO_KEYS, row))
        item['park_ids'] = list(item['park_ids'] or [])
        item['banner_url'] = ('/api/wiki/file/%s' % item['banner_file_id']) if item['banner_file_id'] else None
        rows.append(item)
    return rows


def create_promotion(cursor, *, title, fields, park_ids, created_by):
    cursor.execute(
        """
        INSERT INTO wiki_promotions (title, description, content, banner_file_id,
                                     starts_at, ends_at, created_by)
        VALUES (%(title)s, %(description)s, %(content)s, %(banner)s,
                %(starts)s, %(ends)s, %(by)s)
        RETURNING id
        """,
        {'title': title, 'description': fields.get('description'),
         'content': fields.get('content') or '', 'banner': fields.get('banner_file_id'),
         'starts': fields.get('starts_at'), 'ends': fields.get('ends_at'),
         'by': created_by},
    )
    promotion_id = cursor.fetchone()[0]
    set_promotion_parks(cursor, promotion_id, park_ids)
    return promotion_id


_PROMO_UPDATABLE = ('title', 'description', 'content', 'banner_file_id',
                    'starts_at', 'ends_at', 'status')


def update_promotion(cursor, promotion_id, fields):
    sets, values = [], []
    for key in _PROMO_UPDATABLE:
        if key in fields:
            sets.append(key + ' = %s')
            values.append(fields[key])
    if not sets:
        return False
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.append(promotion_id)
    cursor.execute('UPDATE wiki_promotions SET ' + ', '.join(sets) + ' WHERE id = %s', values)
    return cursor.rowcount > 0


def set_promotion_parks(cursor, promotion_id, park_ids):
    cursor.execute('DELETE FROM wiki_promotion_taxi_parks WHERE promotion_id = %s',
                   (promotion_id,))
    for park_id in {int(p) for p in (park_ids or []) if p}:
        cursor.execute(
            'INSERT INTO wiki_promotion_taxi_parks (promotion_id, park_id) VALUES (%s, %s) '
            'ON CONFLICT DO NOTHING',
            (promotion_id, park_id),
        )


def slug_is_free(cursor, slug, exclude_id=None):
    cursor.execute(
        'SELECT 1 FROM wiki_taxi_parks WHERE slug = %s AND (%s::int IS NULL OR id <> %s::int)',
        (slug, exclude_id, exclude_id),
    )
    return cursor.fetchone() is None
