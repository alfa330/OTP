"""Эндпоинты таксопарков и акций.

В отличие от оригинала, публичных среди них нет: там GET по паркам, акциям и
классификатору отдавались вообще без авторизации.
"""

from flask import jsonify, request

from . import access as wiki_access
from . import offices as wiki_offices
from . import parks as wiki_parks
from . import queries
from .routes_structure import _clean, _int_or_none, _slugify


def _body():
    return request.get_json(silent=True) or {}


def _office_links(data):
    """Офисы парка из тела запроса. (links, error)

    Номер у офиса обязателен — решение владельца 19.08.2026: строка без номера
    в новой форме не создаётся, и молча её пропустить значит потерять офис,
    который человек только что выбрал.
    """
    if not isinstance(data.get('offices'), list):
        return None, None

    links, seen = [], set()
    for item in data['offices']:
        if not isinstance(item, dict):
            continue
        office_id = _int_or_none(item.get('office_id'))
        if not office_id or office_id in seen:
            continue
        seen.add(office_id)
        phones = wiki_offices.link_phones(item) or []
        if not phones:
            return None, 'У каждого офиса должен быть хотя бы один номер'
        links.append({'office_id': office_id, 'phones': phones,
                      'schedule': item.get('schedule'),
                      'note': _clean(item.get('note'), 500)})
    return links, None


def _decimal_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if 0 <= number <= 100 else None


def register(bp, wiki_route, db, log_ip):

    def _may_edit(ctx):
        """Справочники правит всякий, у кого есть что-то сверх чтения.

        Решение владельца 19.08.2026. Прежний гейт can_manage_structure
        оставлял снаружи супервайзера и тренера: статью они завести могли, а
        поправить телефон парка — нет, хотя это тот же справочный контент и
        следить за ним, кроме них, некому.
        """
        return wiki_access.has_write_capability(ctx['capabilities'])

    def _forbidden():
        return jsonify({"error": "Справочник правит тот, у кого есть права сверх чтения",
                        "code": "WIKI_FORBIDDEN"}), 403

    # ── Парки ────────────────────────────────────────────────────────────
    @wiki_route('/parks', methods=('GET', 'POST'))
    def wiki_parks_list(cursor, ctx):
        if request.method == 'GET':
            can_manage = _may_edit(ctx)
            items = wiki_parks.list_parks(cursor, include_archived=can_manage,
                                          query=request.args.get('q'))
            # Офисы парка едут вместе со списком: связью управляют из карточки
            # парка, и форма обязана открыться с уже проставленными галочками.
            by_park = wiki_offices.offices_by_park(cursor, [p['id'] for p in items])
            phones = wiki_offices.phones_by_park(cursor, [p['id'] for p in items])
            for park in items:
                park['offices'] = by_park.get(park['id'], [])
                # Номера без офиса — «онлайн»: парк принимает только по телефону.
                park['phones'] = phones.get(park['id'], {}).get(None, [])
            return jsonify({'items': items, 'can_manage': can_manage})

        if not _may_edit(ctx):
            return _forbidden()

        data = _body()
        name = _clean(data.get('name'))
        if not name:
            return jsonify({"error": "Укажите название парка"}), 400

        links, error = _office_links(data)
        if error:
            return jsonify({"error": error}), 400

        slug = _clean(data.get('slug'), 120) or _slugify(name)
        base, suffix = slug, 2
        while not wiki_parks.slug_is_free(cursor, slug):
            slug = '%s-%d' % (base, suffix)
            suffix += 1

        park_id = wiki_parks.create_park(cursor, slug=slug, name=name, created_by=ctx['user_id'],
                                         fields={
                                             'description': _clean(data.get('description'), 2000),
                                             'city': _clean(data.get('city'), 120),
                                             'address': _clean(data.get('address'), 500),
                                             'website': _clean(data.get('website'), 500),
                                             'commission': _decimal_or_none(data.get('commission')),
                                             'logo_file_id': data.get('logo_file_id') or None,
                                             'head_office_id': _int_or_none(data.get('head_office_id')),
                                         })
        if links is not None:
            wiki_offices.set_park_offices(cursor, park_id, links)
        online = wiki_offices.link_phones(data)
        if online is not None:
            wiki_offices.set_park_online_phones(cursor, park_id, online)

        queries.log_action(cursor, actor_id=ctx['user_id'], action='park.create',
                           entity_type='park', entity_id=park_id,
                           details={'name': name}, ip_address=log_ip())
        return jsonify({"id": park_id, "slug": slug}), 201

    @wiki_route('/parks/<slug>')
    def wiki_park_detail(cursor, ctx, slug):
        park = wiki_parks.get_park(cursor, slug)
        if not park or (park['status'] == 'archived'
                        and not _may_edit(ctx)):
            return jsonify({"error": "Парк не найден"}), 404
        park['offices'] = wiki_offices.offices_by_park(cursor, [park['id']]).get(park['id'], [])
        park['phones'] = (wiki_offices.phones_by_park(cursor, [park['id']])
                          .get(park['id'], {}).get(None, []))
        return jsonify(park)

    @wiki_route('/parks/<int:park_id>', methods=('PATCH', 'DELETE'))
    def wiki_park_item(cursor, ctx, park_id):
        if not _may_edit(ctx):
            return _forbidden()
        if request.method == 'DELETE':
            # Архивируем: за парком могут стоять акции, а физическое удаление
            # снесло бы их связи каскадом.
            if not wiki_parks.update_park(cursor, park_id, {'status': 'archived'}):
                return jsonify({"error": "Парк не найден"}), 404
            queries.log_action(cursor, actor_id=ctx['user_id'], action='park.archive',
                               entity_type='park', entity_id=park_id, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        links, error = _office_links(data)
        if error:
            return jsonify({"error": error}), 400

        fields = {}
        for key, limit in (('name', 255), ('description', 2000), ('city', 120),
                           ('address', 500), ('website', 500)):
            if key in data:
                fields[key] = _clean(data[key], limit)
        if 'commission' in data:
            fields['commission'] = _decimal_or_none(data['commission'])
        if 'logo_file_id' in data:
            fields['logo_file_id'] = data['logo_file_id'] or None
        # Ключ есть, а значение пустое — «адрес снят», а не «поле не прислали».
        if 'head_office_id' in data:
            fields['head_office_id'] = _int_or_none(data['head_office_id'])
        if data.get('status') in ('active', 'archived'):
            fields['status'] = data['status']
        if 'position' in data:
            fields['position'] = _int_or_none(data['position']) or 0

        changed = wiki_parks.update_park(cursor, park_id, fields) if fields else False
        if links is not None:
            wiki_offices.set_park_offices(cursor, park_id, links)
            changed = True
        online = wiki_offices.link_phones(data)
        if online is not None:
            wiki_offices.set_park_online_phones(cursor, park_id, online)
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
            can_manage = _may_edit(ctx)
            return jsonify({
                'items': wiki_parks.list_promotions(cursor, include_archived=can_manage),
                'can_manage': can_manage,
            })

        if not _may_edit(ctx):
            return _forbidden()

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

    @wiki_route('/promotions/<int:promotion_id>', methods=('PATCH', 'DELETE'))
    def wiki_promotion_item(cursor, ctx, promotion_id):
        if not _may_edit(ctx):
            return _forbidden()
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
