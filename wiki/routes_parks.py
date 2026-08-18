"""Эндпоинты таксопарков и акций.

В отличие от оригинала, публичных среди них нет: там GET по паркам, акциям и
классификатору отдавались вообще без авторизации.
"""

from flask import jsonify, request

from . import offices as wiki_offices
from . import parks as wiki_parks
from . import queries
from .routes_structure import _clean, _int_or_none, _slugify


def _body():
    return request.get_json(silent=True) or {}


def _decimal_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if 0 <= number <= 100 else None


def register(bp, wiki_route, db, log_ip):

    # ── Парки ────────────────────────────────────────────────────────────
    @wiki_route('/parks', methods=('GET', 'POST'))
    def wiki_parks_list(cursor, ctx):
        if request.method == 'GET':
            can_manage = bool(ctx['capabilities'].get('can_manage_structure')
                              or ctx['capabilities'].get('can_manage_access'))
            items = wiki_parks.list_parks(cursor, include_archived=can_manage,
                                          query=request.args.get('q'))
            # Офисы парка едут вместе со списком: связью управляют из карточки
            # парка, и форма обязана открыться с уже проставленными галочками.
            by_park = wiki_offices.offices_by_park(cursor, [p['id'] for p in items])
            for park in items:
                park['offices'] = by_park.get(park['id'], [])
            return jsonify({'items': items, 'can_manage': can_manage})

        if not ctx['capabilities'].get('can_manage_structure'):
            return jsonify({"error": "Нет права управлять справочником",
                            "code": "WIKI_FORBIDDEN"}), 403

        data = _body()
        name = _clean(data.get('name'))
        if not name:
            return jsonify({"error": "Укажите название парка"}), 400

        slug = _clean(data.get('slug'), 120) or _slugify(name)
        base, suffix = slug, 2
        while not wiki_parks.slug_is_free(cursor, slug):
            slug = '%s-%d' % (base, suffix)
            suffix += 1

        park_id = wiki_parks.create_park(cursor, slug=slug, name=name, created_by=ctx['user_id'],
                                         fields={
                                             'description': _clean(data.get('description'), 2000),
                                             'city': _clean(data.get('city'), 120),
                                             'phone': _clean(data.get('phone'), 64),
                                             'address': _clean(data.get('address'), 500),
                                             'website': _clean(data.get('website'), 500),
                                             'commission': _decimal_or_none(data.get('commission')),
                                             'logo_file_id': data.get('logo_file_id') or None,
                                         })
        if isinstance(data.get('offices'), list):
            wiki_offices.set_park_offices(cursor, park_id, data['offices'])

        queries.log_action(cursor, actor_id=ctx['user_id'], action='park.create',
                           entity_type='park', entity_id=park_id,
                           details={'name': name}, ip_address=log_ip())
        return jsonify({"id": park_id, "slug": slug}), 201

    @wiki_route('/parks/<slug>')
    def wiki_park_detail(cursor, ctx, slug):
        park = wiki_parks.get_park(cursor, slug)
        if not park or (park['status'] == 'archived'
                        and not ctx['capabilities'].get('can_manage_structure')):
            return jsonify({"error": "Парк не найден"}), 404
        return jsonify(park)

    @wiki_route('/parks/<int:park_id>', methods=('PATCH', 'DELETE'),
                capability='can_manage_structure')
    def wiki_park_item(cursor, ctx, park_id):
        if request.method == 'DELETE':
            # Архивируем: за парком могут стоять акции, а физическое удаление
            # снесло бы их связи каскадом.
            if not wiki_parks.update_park(cursor, park_id, {'status': 'archived'}):
                return jsonify({"error": "Парк не найден"}), 404
            queries.log_action(cursor, actor_id=ctx['user_id'], action='park.archive',
                               entity_type='park', entity_id=park_id, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        fields = {}
        for key, limit in (('name', 255), ('description', 2000), ('city', 120),
                           ('phone', 64), ('address', 500), ('website', 500)):
            if key in data:
                fields[key] = _clean(data[key], limit)
        if 'commission' in data:
            fields['commission'] = _decimal_or_none(data['commission'])
        if 'logo_file_id' in data:
            fields['logo_file_id'] = data['logo_file_id'] or None
        if data.get('status') in ('active', 'archived'):
            fields['status'] = data['status']
        if 'position' in data:
            fields['position'] = _int_or_none(data['position']) or 0

        changed = wiki_parks.update_park(cursor, park_id, fields) if fields else False
        if isinstance(data.get('offices'), list):
            wiki_offices.set_park_offices(cursor, park_id, data['offices'])
            changed = True

        if not changed:
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='park.update',
                           entity_type='park', entity_id=park_id,
                           details=fields, ip_address=log_ip())
        return jsonify({"status": "ok"})

    # ── Акции ────────────────────────────────────────────────────────────
    @wiki_route('/promotions', methods=('GET', 'POST'))
    def wiki_promotions(cursor, ctx):
        if request.method == 'GET':
            can_manage = bool(ctx['capabilities'].get('can_manage_structure'))
            return jsonify({
                'items': wiki_parks.list_promotions(cursor, include_archived=can_manage),
                'can_manage': can_manage,
            })

        if not ctx['capabilities'].get('can_manage_structure'):
            return jsonify({"error": "Нет права управлять справочником",
                            "code": "WIKI_FORBIDDEN"}), 403

        data = _body()
        title = _clean(data.get('title'))
        if not title:
            return jsonify({"error": "Укажите название акции"}), 400

        promotion_id = wiki_parks.create_promotion(
            cursor, title=title, created_by=ctx['user_id'],
            park_ids=[_int_or_none(p) for p in (data.get('park_ids') or [])],
            fields={
                'description': _clean(data.get('description'), 2000),
                'content': data.get('content') or '',
                'banner_file_id': data.get('banner_file_id') or None,
                'starts_at': data.get('starts_at') or None,
                'ends_at': data.get('ends_at') or None,
            })
        queries.log_action(cursor, actor_id=ctx['user_id'], action='promotion.create',
                           entity_type='promotion', entity_id=promotion_id,
                           details={'title': title}, ip_address=log_ip())
        return jsonify({"id": promotion_id}), 201

    @wiki_route('/promotions/<int:promotion_id>', methods=('PATCH', 'DELETE'),
                capability='can_manage_structure')
    def wiki_promotion_item(cursor, ctx, promotion_id):
        if request.method == 'DELETE':
            if not wiki_parks.update_promotion(cursor, promotion_id, {'status': 'archived'}):
                return jsonify({"error": "Акция не найдена"}), 404
            queries.log_action(cursor, actor_id=ctx['user_id'], action='promotion.archive',
                               entity_type='promotion', entity_id=promotion_id,
                               ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        fields = {}
        for key, limit in (('title', 255), ('description', 2000)):
            if key in data:
                fields[key] = _clean(data[key], limit)
        if 'content' in data:
            # Содержимое акции проходит тот же санитайзер, что и статьи.
            from .sanitize import sanitize_html
            fields['content'] = sanitize_html(data['content'])
        for key in ('starts_at', 'ends_at'):
            if key in data:
                fields[key] = data[key] or None
        if 'banner_file_id' in data:
            fields['banner_file_id'] = data['banner_file_id'] or None
        if data.get('status') in ('active', 'archived'):
            fields['status'] = data['status']

        changed = wiki_parks.update_promotion(cursor, promotion_id, fields)
        if 'park_ids' in data:
            wiki_parks.set_promotion_parks(
                cursor, promotion_id,
                [_int_or_none(p) for p in (data.get('park_ids') or [])])
            changed = True

        if not changed:
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='promotion.update',
                           entity_type='promotion', entity_id=promotion_id,
                           details={'fields': sorted(fields.keys())}, ip_address=log_ip())
        return jsonify({"status": "ok"})
