"""Эндпоинты таксопарков и акций.

В отличие от оригинала, публичных среди них нет: там GET по паркам, акциям и
классификатору отдавались вообще без авторизации.

Каждый роут начинается с request_space: справочник принадлежит пространству
(см. шапку wiki/parks.py).
"""

import json
import uuid

from flask import jsonify, request

from . import access as wiki_access
from . import offices as wiki_offices
from . import parks as wiki_parks
from . import queries
from . import storage as wiki_storage
from .routes_structure import _clean, _int_or_none, _slugify, request_space

# Логотип парка. Пять мегабайт, а не двадцать пять, как у импорта документа:
# это аватарка 38×38 в рельсе витрины, и всё, что больше, — недоразумение,
# которое лучше отклонить сразу. Форма к тому же уменьшает картинку до 512 px
# ещё в браузере (src/components/wiki/parkLogo.js).
_LOGO_MAX_BYTES = 5 * 1024 * 1024
# SVG в списке нет намеренно: это исполняемый документ, а не картинка, и
# отдавать его по ссылке справочника, который правит половина отдела, незачем.
_LOGO_TYPES = ('image/png', 'image/jpeg', 'image/webp')


def _body():
    return request.get_json(silent=True) or {}


def _numbers(data):
    """Плоский список номеров из тела запроса. None — ключа не было.

    [{office_id: int | None, phone, note}]. Номер без телефона отбрасывается: в
    справочнике он выглядел бы рабочей строкой, по которой не позвонить.
    """
    if not isinstance(data.get('numbers'), list):
        return None
    result = []
    for item in data['numbers']:
        if not isinstance(item, dict):
            continue
        phone = _clean(item.get('phone'), 64)
        if not phone:
            continue
        result.append({'office_id': _int_or_none(item.get('office_id')),
                       'phone': phone,
                       'note': _clean(item.get('note'), 200) or None})
    return result


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


def _head_office(cursor, data, space_id):
    """head_office_id из тела запроса — только свой офис, иначе None.

    Проверка нужна именно здесь: это единственное место, где id офиса попадает
    в КОЛОНКУ парка, а не в таблицу связи, где чужую сторону отсекает SQL. Без
    неё чужой офис записался бы в head_office_id и не показывался бы только
    потому, что LEFT JOIN в parks.list_parks его отфильтрует — то есть утечка
    ждала бы первого запроса, который джойн забудет.
    """
    office_id = _int_or_none(data.get('head_office_id'))
    if office_id is None:
        return None
    return office_id if office_id in wiki_offices.own_office_ids(
        cursor, [office_id], space_id=space_id) else None


def _logo_file(cursor, value, *, user_id, space_id):
    """uuid логотипа из тела запроса или None (в том числе на мусор).

    Проверка нужна, потому что id файла — это ключ доступа: роут /file/<id>
    открывает логотип каждому, кому видно пространство. Без неё редактор
    справочника вписал бы сюда uuid картинки из чужой статьи и раздал бы её
    своему пространству, ничего не загружая.

    Свой файл — тот, что человек только что загрузил (/parks/logo), либо тот,
    что УЖЕ стоит логотипом в этом пространстве: правку телефона у чужого парка
    делают чаще, чем смену логотипа, и форма присылает картинку обратно как
    есть — не признать её значило бы стирать логотип чужой правкой.

    Привязанный к статье файл не подходит никогда: у него своя граница
    (периметр разделов), и справочник её не воспроизведёт.
    """
    try:
        file_id = str(uuid.UUID(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        return None

    cursor.execute(
        """
        SELECT 1 FROM wiki_files f
         WHERE f.id = %(file)s AND f.article_id IS NULL
           AND (f.uploaded_by = %(user)s
                OR EXISTS (SELECT 1 FROM wiki_taxi_parks p
                            WHERE p.logo_file_id = f.id AND p.space_id = %(space)s))
        """,
        {'file': file_id, 'user': user_id, 'space': space_id},
    )
    return file_id if cursor.fetchone() else None


# Ракурс логотипа. Увеличение до четырёх крат: дальше видна одна буква
# вывески, а плитка в рельсе всего 38 px — там это уже не логотип, а пятно.
_LOGO_ZOOM_MAX = 4.0
_LOGO_FRAME_DEFAULT = {'zoom': 1.0, 'x': 0.5, 'y': 0.5, 'ratio': 1.0}


def _logo_frame(value):
    """Ракурс из тела запроса → словарь для JSONB или None.

    Четыре числа: zoom — во сколько раз картинка крупнее «вписанной» (1 — как
    было, целиком по короткой стороне), x и y — какая доля лишнего срезана
    слева и сверху, ratio — соотношение сторон исходника. Ratio хранится
    вместе с остальными, а не выводится при показе: без него плитка не знает
    геометрии, пока картинка не загрузилась, и логотип на долю секунды прыгал
    бы из центра в выбранное место при каждом заходе.

    Чужие ключи и мусор отбрасываются молча, числа зажимаются в границы: сюда
    приходит тело запроса, а не свои данные, и «ракурс» с zoom=1e9 нарисовал бы
    в плитке один пиксель.
    """
    if not isinstance(value, dict):
        return None

    def number(key, default, low, high):
        try:
            result = float(value.get(key, default))
        except (TypeError, ValueError):
            return default
        if result != result or result in (float('inf'), float('-inf')):
            return default
        return round(min(high, max(low, result)), 4)

    frame = {'zoom': number('zoom', 1.0, 1.0, _LOGO_ZOOM_MAX),
             'x': number('x', 0.5, 0.0, 1.0),
             'y': number('y', 0.5, 0.0, 1.0),
             'ratio': number('ratio', 1.0, 0.05, 20.0)}
    # «Середина без увеличения» — это и есть отсутствие ракурса. Хранить его
    # значением значило бы держать в базе две записи одного и того же.
    return None if frame == _LOGO_FRAME_DEFAULT else frame


def _frame_json(frame):
    """Ракурс в параметр для JSONB-колонки: psycopg словарь сам не адаптирует."""
    return json.dumps(frame) if frame else None


def _decimal_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if 0 <= number <= 100 else None


def _write_numbers(cursor, park_id, numbers, links, data, *, space_id):
    """Пишет номера парка. True, если что-то записали.

    Форма шлёт плоский список numbers; старая форма и скрипт переноса — offices
    со списками телефонов плюс phones для номеров без офиса. Поддерживаются оба:
    ломать внешние вызовы ради формы незачем.
    """
    if numbers is not None:
        wiki_offices.set_park_numbers(cursor, park_id, numbers, space_id=space_id)
        return True

    wrote = False
    if links is not None:
        wiki_offices.set_park_offices(cursor, park_id, links, space_id=space_id)
        wrote = True
    online = wiki_offices.link_phones(data)
    if online is not None:
        wiki_offices.set_park_online_phones(cursor, park_id, online)
        wrote = True
    return wrote


def register(bp, wiki_route, db, log_ip, gcs):

    def _may_edit(ctx):
        """Справочники правит всякий, у кого есть что-то сверх чтения.

        Решение владельца 19.08.2026. Прежний гейт can_manage_structure
        оставлял снаружи супервайзера и тренера: статью они завести могли, а
        поправить телефон парка — нет, хотя это тот же справочный контент и
        следить за ним, кроме них, некому.
        """
        # Способности ИТОГОВЫЕ, а не только должностные: право, выписанное
        # правилом раздела, тоже открывает справочник — решение владельца
        # подтверждено 21.08.2026, когда способности стали суммой должности и
        # выписанного (queries.load_capabilities). Порог остался прежним —
        # «хоть что-то сверх чтения»; изменилось лишь то, что теперь это
        # «что-то» может прийти не только от должности.
        #
        # Да, справочник шире одного раздела, а правило выписано на раздел.
        # Владелец выбрал это осознанно: следить за телефонами парков некому,
        # кроме тех, кто вообще ведёт содержимое, а поимённая выдача прав в
        # разделе — такое же решение руководителя, как и назначение на должность.
        # Границей при этом остаётся ПРОСТРАНСТВО: способность отвечает «вправе
        # ли править», request_space — «что именно правит».
        return wiki_access.has_write_capability(ctx['capabilities'])

    def _forbidden():
        return jsonify({"error": "Справочник правит тот, у кого есть права сверх чтения",
                        "code": "WIKI_FORBIDDEN"}), 403

    # ── Парки ────────────────────────────────────────────────────────────
    @wiki_route('/parks', methods=('GET', 'POST'))
    def wiki_parks_list(cursor, ctx):
        space_id, space_error = request_space(cursor, ctx)
        if space_error:
            return space_error

        if request.method == 'GET':
            can_manage = _may_edit(ctx)
            items = wiki_parks.list_parks(cursor, include_archived=can_manage,
                                          query=request.args.get('q'),
                                          space_id=space_id)
            # Офисы парка едут вместе со списком: связью управляют из карточки
            # парка, и форма обязана открыться с уже проставленными галочками.
            by_park = wiki_offices.offices_by_park(cursor, [p['id'] for p in items],
                                                   space_id=space_id)
            phones = wiki_offices.phones_by_park(cursor, [p['id'] for p in items],
                                                 space_id=space_id)
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

        numbers = _numbers(data)
        links, error = (None, None) if numbers is not None else _office_links(data)
        if error:
            return jsonify({"error": error}), 400

        # Ракурс без логотипа не имеет смысла: показывать нечего.
        logo_file_id = _logo_file(cursor, data.get('logo_file_id'),
                                  user_id=ctx['user_id'], space_id=space_id)
        logo_frame = _frame_json(_logo_frame(data.get('logo_frame'))) if logo_file_id else None

        slug = _clean(data.get('slug'), 120) or _slugify(name)
        base, suffix = slug, 2
        while not wiki_parks.slug_is_free(cursor, slug, space_id=space_id):
            slug = '%s-%d' % (base, suffix)
            suffix += 1

        park_id = wiki_parks.create_park(cursor, slug=slug, name=name, created_by=ctx['user_id'],
                                         space_id=space_id,
                                         fields={
                                             'description': _clean(data.get('description'), 2000),
                                             'city': _clean(data.get('city'), 120),
                                             'address': _clean(data.get('address'), 500),
                                             'website': _clean(data.get('website'), 500),
                                             'commission': _decimal_or_none(data.get('commission')),
                                             'logo_file_id': logo_file_id,
                                             'logo_frame': logo_frame,
                                             'head_office_id': _head_office(cursor, data, space_id),
                                         })
        _write_numbers(cursor, park_id, numbers, links, data, space_id=space_id)

        queries.log_action(cursor, actor_id=ctx['user_id'], action='park.create',
                           entity_type='park', entity_id=park_id,
                           details={'name': name, 'space_id': space_id},
                           ip_address=log_ip())
        return jsonify({"id": park_id, "slug": slug}), 201

    @wiki_route('/parks/<slug>')
    def wiki_park_detail(cursor, ctx, slug):
        space_id, error = request_space(cursor, ctx)
        if error:
            return error
        park = wiki_parks.get_park(cursor, slug, space_id=space_id)
        if not park or (park['status'] == 'archived'
                        and not _may_edit(ctx)):
            return jsonify({"error": "Парк не найден"}), 404
        park['offices'] = (wiki_offices.offices_by_park(cursor, [park['id']],
                                                        space_id=space_id)
                           .get(park['id'], []))
        park['phones'] = (wiki_offices.phones_by_park(cursor, [park['id']],
                                                      space_id=space_id)
                          .get(park['id'], {}).get(None, []))
        return jsonify(park)

    @wiki_route('/parks/<int:park_id>', methods=('PATCH', 'DELETE'))
    def wiki_park_item(cursor, ctx, park_id):
        if not _may_edit(ctx):
            return _forbidden()
        space_id, space_error = request_space(cursor, ctx)
        if space_error:
            return space_error

        if request.method == 'DELETE':
            # Архивируем: за парком могут стоять акции, а физическое удаление
            # снесло бы их связи каскадом.
            if not wiki_parks.update_park(cursor, park_id, {'status': 'archived'},
                                          space_id=space_id):
                return jsonify({"error": "Парк не найден"}), 404
            queries.log_action(cursor, actor_id=ctx['user_id'], action='park.archive',
                               entity_type='park', entity_id=park_id, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        numbers = _numbers(data)
        links, error = (None, None) if numbers is not None else _office_links(data)
        if error:
            return jsonify({"error": error}), 400

        fields = {}
        for key, limit in (('name', 255), ('description', 2000), ('city', 120),
                           ('address', 500), ('website', 500)):
            if key in data:
                fields[key] = _clean(data[key], limit)
        if 'commission' in data:
            fields['commission'] = _decimal_or_none(data['commission'])
        # Ключ есть, а значение пустое — «логотип сняли».
        if 'logo_file_id' in data:
            fields['logo_file_id'] = _logo_file(cursor, data['logo_file_id'],
                                                user_id=ctx['user_id'],
                                                space_id=space_id)
        if 'logo_frame' in data:
            fields['logo_frame'] = _frame_json(_logo_frame(data['logo_frame']))
        # Логотип сняли — ракурс уходит вместе с ним, даже если форма прислала
        # его по инерции: кадр без картинки достался бы следующей загрузке.
        if 'logo_file_id' in fields and not fields['logo_file_id']:
            fields['logo_frame'] = None
        # Ключ есть, а значение пустое — «адрес снят», а не «поле не прислали».
        if 'head_office_id' in data:
            fields['head_office_id'] = _head_office(cursor, data, space_id)
        if data.get('status') in ('active', 'archived'):
            fields['status'] = data['status']
        if 'position' in data:
            fields['position'] = _int_or_none(data['position']) or 0

        # Парк проверяем ДО правки номеров: иначе чужой парк ответил бы
        # «Нечего обновлять», успев переписать связи с офисами.
        cursor.execute('SELECT 1 FROM wiki_taxi_parks WHERE id = %s AND space_id = %s',
                       (park_id, space_id))
        if not cursor.fetchone():
            return jsonify({"error": "Парк не найден"}), 404

        changed = (wiki_parks.update_park(cursor, park_id, fields, space_id=space_id)
                   if fields else False)
        if _write_numbers(cursor, park_id, numbers, links, data, space_id=space_id):
            changed = True

        if not changed:
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='park.update',
                           entity_type='park', entity_id=park_id,
                           details=fields, ip_address=log_ip())
        return jsonify({"status": "ok"})

    # ── Логотип парка ────────────────────────────────────────────────────
    #
    # Отдельная дверь, а не общий /upload редактора: тот гейтится can_create
    # («может завести статью»), а справочник правит всякий, у кого есть что-то
    # сверх чтения. Через /upload супервайзер с одним правом на правку получил
    # бы отказ уже ПОСЛЕ выбора файла — то есть молчаливый отказ этажом ниже,
    # ровно та болезнь, от которой гейт справочника и уходил.
    #
    # Правило статическое, поэтому /parks/logo разбирается раньше /parks/<slug>;
    # GET по этому адресу до него всё равно доедет — методы у роутов разные.
    @wiki_route('/parks/logo', methods=('POST',))
    def wiki_park_logo(cursor, ctx):
        if not _may_edit(ctx):
            return _forbidden()
        # Пространство спрашиваем и здесь, хотя файл пока ничей: парк может быть
        # ещё не сохранён. Право грузить выдаётся в пространстве, и журнал
        # обязан знать, в чьём справочнике это случилось.
        space_id, space_error = request_space(cursor, ctx)
        if space_error:
            return space_error

        uploaded = request.files.get('file')
        if not uploaded or not uploaded.filename:
            return jsonify({"error": "Файл не выбран"}), 400

        content_type = (uploaded.mimetype or '').strip().lower()
        if content_type not in _LOGO_TYPES:
            return jsonify({"error": "Логотип — картинка PNG, JPEG или WebP"}), 400

        data = uploaded.read()
        if not data:
            return jsonify({"error": "Файл пустой"}), 400
        if len(data) > _LOGO_MAX_BYTES:
            return jsonify({"error": "Файл больше %d МБ" % (_LOGO_MAX_BYTES // (1024 * 1024))}), 400

        # Без привязки к статье: у логотипа её нет и не будет. Видимость ему
        # даёт сам справочник — parks.logo_space_ids, которую спрашивает
        # /file/<id>.
        file_id, url = wiki_storage.store_file(
            cursor, gcs, data=data, filename=uploaded.filename,
            content_type=content_type, uploaded_by=ctx['user_id'])
        if not file_id:
            return jsonify({"error": "Хранилище файлов не настроено"}), 503

        queries.log_action(cursor, actor_id=ctx['user_id'], action='park.logo_upload',
                           entity_type='park', entity_id=None,
                           details={'space_id': space_id, 'file_id': str(file_id),
                                    'size': len(data), 'content_type': content_type},
                           ip_address=log_ip())
        # file_id отдаём отдельно от адреса: форма кладёт в парк именно его, а
        # url ей нужен только чтобы показать картинку до сохранения.
        return jsonify({"file_id": file_id, "url": url}), 201

    # ── Акции ────────────────────────────────────────────────────────────
    @wiki_route('/promotions', methods=('GET', 'POST'))
    def wiki_promotions(cursor, ctx):
        space_id, error = request_space(cursor, ctx)
        if error:
            return error

        if request.method == 'GET':
            can_manage = _may_edit(ctx)
            return jsonify({
                'items': wiki_parks.list_promotions(cursor, include_archived=can_manage,
                                                    space_id=space_id),
                'can_manage': can_manage,
            })

        if not _may_edit(ctx):
            return _forbidden()

        data = _body()
        title = _clean(data.get('title'))
        if not title:
            return jsonify({"error": "Укажите название акции"}), 400

        promotion_id = wiki_parks.create_promotion(
            cursor, title=title, created_by=ctx['user_id'], space_id=space_id,
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
        space_id, error = request_space(cursor, ctx)
        if error:
            return error

        if request.method == 'DELETE':
            if not wiki_parks.update_promotion(cursor, promotion_id, {'status': 'archived'},
                                               space_id=space_id):
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

        cursor.execute('SELECT 1 FROM wiki_promotions WHERE id = %s AND space_id = %s',
                       (promotion_id, space_id))
        if not cursor.fetchone():
            return jsonify({"error": "Акция не найдена"}), 404

        changed = wiki_parks.update_promotion(cursor, promotion_id, fields,
                                              space_id=space_id)
        if 'park_ids' in data:
            wiki_parks.set_promotion_parks(
                cursor, promotion_id,
                [_int_or_none(p) for p in (data.get('park_ids') or [])],
                space_id=space_id)
            changed = True

        if not changed:
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='promotion.update',
                           entity_type='promotion', entity_id=promotion_id,
                           details={'fields': sorted(fields.keys())}, ip_address=log_ip())
        return jsonify({"status": "ok"})
