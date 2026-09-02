# -*- coding: utf-8 -*-
"""HTTP-эндпоинты раздела «Новости» (Flask Blueprint).

Собран фабрикой с внедрением зависимостей — как «Вики», «Обращения» и центр
уведомлений: импортировать bot_schedule2 отсюда нельзя, вышел бы цикл.

ДВЕ ДВЕРИ, И ОНИ РАЗНЫЕ. Это главное в модуле:

  ЧИТАТЬ (`/pending`, `/<id>/read`) — только аутентификация. Ни тумблера
  `departments.wiki_enabled`, ни QR-подтверждения сессии, которые стоят на
  роутах вики. Так требует постановка: «чтобы увидеть новость необязательно
  иметь доступ к чувствительным данным или к вики». Оператор отдела, которому
  вики не выдали, обязан увидеть окно — иначе объявление не доходит ровно до
  тех, ради кого оно пишется.

  ПИСАТЬ (всё остальное) — потолок должности news_access.publish_ceiling.
  Ниже супервайзера его нет ни у кого, и по этому же признаку прячется вкладка.
"""

import logging
from datetime import datetime
from functools import wraps

from flask import Blueprint, jsonify, request

from wiki.sanitize import sanitize_html

from . import access as news_access
from . import photos as news_photos
from . import queries
from .schema import (DEFAULT_CONFIRM_DELAY_SECONDS, MAX_LOOSE_PHOTOS_PER_USER,
                     photos_ready as schema_photos_ready, schema_is_ready)

# Отказ, который видит не-редактор. Одной строкой: текст показывают человеку,
# и «недостаточно прав» без объяснения отправляет его писать в поддержку.
_NOT_A_PUBLISHER = ('Новости публикуют супервайзер и выше', 403)


def build_news_blueprint(*, db, require_api_key, build_cors_preflight_response,
                         resolve_requester, gcs=None):
    """db — Database (ради _get_cursor); остальное — общие вещи из bot_schedule2.

    gcs=None по умолчанию НАМЕРЕННО: порядок деплоя не должен ронять старт
    портала. Без хранилища раздел работает как вчера, только фотографию не
    приложить — роут загрузки честно отвечает 503.

    Журнала действий у раздела нет намеренно: кто, когда и кому выпустил
    новость, записано в самой новости (author_id, published_at, адресаты), а
    журнал прочтений отвечает на второй вопрос — кто её увидел. Третья запись
    о том же была бы дублем.
    """

    bp = Blueprint('news', __name__, url_prefix='/api/news')

    # Схема разворачивается один раз при старте и никуда не девается, поэтому
    # спрашивать про неё базу на каждый запрос незачем — списком храним только
    # УСПЕХ. Отрицательный ответ не кешируем: он означает, что миграция ещё не
    # прошла, и следующий запрос обязан увидеть развёрнутую схему.
    schema_ready_once = []

    def _schema_ready(cursor):
        if schema_ready_once:
            return True
        if schema_is_ready(cursor):
            schema_ready_once.append(True)
            return True
        return False

    # Готовность таблицы кадров. Отдельно от _schema_ready намеренно: тот
    # отвечает за весь раздел, и «нет таблицы» превратилось бы в «раздел
    # разворачивается» у всех вошедших в портал. Здесь отсутствие таблицы
    # означает ровно «у новостей нет фотографий».
    #
    # Работает в одну сторону: False перепроверяется (таблица доедет следующим
    # деплоем), True запоминается навсегда — таблицы не исчезают.
    _photos_table = {'ready': False}

    def _photos_ready(cursor):
        if not _photos_table['ready']:
            _photos_table['ready'] = schema_photos_ready(cursor)
        return _photos_table['ready']

    def news_route(rule, methods=('GET',), publisher=False, rights=False,
                   defer_cursor=False):
        """Общий декоратор: preflight, авторизация, контекст, ошибки.

        publisher=True добавляет потолок должности. Флаг стоит НА ОБЪЯВЛЕНИИ
        роута, а не в теле обработчика, чтобы у каждой двери было видно, про
        чтение она или про выпуск.

        rights=True считает потолок и границу отдела, не требуя их наличия
        (это нужно ровно одному роуту — /access, который отвечает и тому, кто
        публиковать не вправе). publisher его подразумевает.

        Читающим роутам права НЕ считаются вовсе, и это не микрооптимизация:
        /pending дёргает КАЖДЫЙ вошедший в портал, каждая открытая вкладка на
        каждый тычок канала колокола и каждый возврат во вкладку. Расчёт
        потолка стоит двух лишних обращений к базе (проверка таблицы ролей вики
        плюс сам EXISTS), то есть больше половины запроса — ради ответа на
        вопрос, которого чтение не задаёт. Пул на портал — 40 соединений на
        всё, и его уже делит SSE аукциона.

        defer_cursor=True — контекст и права считаются в коротком курсоре, он
        ЗАКРЫВАЕТСЯ, и обработчик получает только ctx, а курсоры открывает сам.
        Нужен там, где между проверкой прав и записью в базу стоит ЧУЖАЯ СЕТЬ:
        пережатие кадра на 12 мегапикселей и заливка в бакет заняли бы слот
        пула на секунды, а слотов сорок на весь портал. Заодно это единственный
        способ снести блоб ПОСЛЕ фиксации транзакции, а не до неё.
        """
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _requester, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status

                    with db._get_cursor() as cursor:
                        if not _schema_ready(cursor):
                            # Схемы нет — раздел просто пуст. Не 500: новости
                            # не должны уметь сломать вход в портал.
                            #
                            # Ответ несёт ВСЕ ключи, которых ждут витрины:
                            # окно читает items, вкладка — can_publish. Отдай мы
                            # один лишь items, вкладка прочла бы отсутствующий
                            # can_publish как «нет прав» и сказала бы человеку
                            # неправду — вместо «раздел разворачивается».
                            return jsonify({"items": [], "total": 0,
                                            "can_publish": False,
                                            "schema_ready": False})

                        ctx = queries.load_viewer_context(cursor, requester_id)
                        if not ctx:
                            return jsonify({"error": "Пользователь не найден"}), 404

                        # Значения по умолчанию, чтобы читающий роут не мог
                        # уронить запрос обращением к несчитанному праву.
                        ctx['ceiling'] = None
                        ctx['departments'] = None
                        if publisher or rights:
                            is_wiki_admin = queries.is_wiki_admin(
                                cursor, ctx['user_id'])
                            ctx['ceiling'] = news_access.publish_ceiling(
                                ctx['otp_role'], is_wiki_admin=is_wiki_admin)
                            ctx['departments'] = news_access.publish_departments(
                                ctx['otp_role'],
                                headed_department_ids=ctx['headed_department_ids'],
                                department_id=ctx['department_id'],
                                is_wiki_admin=is_wiki_admin)

                        if publisher and ctx['ceiling'] is None:
                            message, status = _NOT_A_PUBLISHER
                            return jsonify({"error": message,
                                            "code": "NEWS_FORBIDDEN"}), status

                        if not defer_cursor:
                            return handler(*args, cursor=cursor, ctx=ctx, **kwargs)

                    # Курсор ОТДАН обратно в пул, и только теперь обработчик
                    # уходит в сеть (пережатие кадра, заливка в бакет).
                    return handler(*args, ctx=ctx, **kwargs)
                except Exception as exc:  # noqa: BLE001 — общего errorhandler нет
                    logging.exception('news: ошибка в %s', rule)
                    return jsonify({"error": "Внутренняя ошибка раздела «Новости»",
                                    "detail": str(exc)[:200]}), 500

            return wrapper

        return decorator

    def _int_or_none(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _timestamp_or_none(value):
        """Срок показа из формы. Не разобрали — считаем, что срока нет.

        Разбираем ЗДЕСЬ, а не отдаём строку постгресу: невнятная дата уронила
        бы запрос пятисоткой, и автор увидел бы «внутренняя ошибка» вместо
        поля, которое надо поправить.
        """
        if not value:
            return None
        text = str(value).strip().replace('T', ' ')
        if not text:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.strptime(text[:len(fmt) + 4], fmt)
            except ValueError:
                continue
        return None

    def _may_read_post(ctx, post):
        """Вправе ли человек ОТКРЫТЬ карточку новости и её журнал.

        Периметр обязан совпадать со списком (queries.list_posts), а он у
        редактора звучит так: своё плюс чужое своего отдела, но только от
        авторов НЕ ВЫШЕ себя. Точечные пути это правило раньше не повторяли —
        и супервайзер, не видя черновик своего руководителя в списке, открывал
        его прямым обращением по id вместе с журналом «кто прочитал».

        Правило дублируется намеренно, но проверяется тестом
        (tests/test_news.py::NewsPerimeterTests): держать его одним SQL нельзя —
        список это выборка, а здесь уже готовая строка.
        """
        if post.get('author_id') == ctx['user_id']:
            return True
        if ctx['departments'] is None:
            return True
        if post.get('author_department_id') not in ctx['departments']:
            return False
        return (news_access.effective_role_level(post.get('author_role'))
                <= news_access.effective_role_level(ctx['otp_role']))

    def _may_edit(ctx, post):
        """Вправе ли человек править эту новость.

        Свою — всегда. Чужую — только без границы отдела (супер-админ,
        администратор вики): переписывать объявление коллеги того же уровня
        по чужому усмотрению нельзя, даже внутри одного отдела.
        """
        if post.get('author_id') == ctx['user_id']:
            return True
        return ctx['departments'] is None

    def _may_take_down(ctx, post):
        """Вправе ли человек СНЯТЬ новость с показа.

        Шире правки намеренно. Править чужой текст нельзя — это чужие слова.
        Но обязательное окно, которое видит весь отдел, обязано иметь тормоз у
        того, кто за отдел отвечает: иначе ошибочное объявление супервайзера
        снимается только им самим, а он может быть на смене, в отпуске или уже
        не работать. «Новость идёт вниз» — значит и снять её вправе тот, кто
        выше автора, а не только сам автор.
        """
        if _may_edit(ctx, post):
            return True
        if not _may_read_post(ctx, post):
            return False
        return (news_access.effective_role_level(ctx['otp_role'])
                > news_access.effective_role_level(post.get('author_role')))

    def _with_rights(ctx, post):
        """Дописывает в карточку признак «мне это править можно».

        Считает СЕРВЕР и отдаёт готовым — та же причина, что у действий в
        строке каталога вики: вторая формула во фронте разошлась бы с этой, и
        человек получал бы пункт меню, на который сервер отвечает 403.
        """
        post['can_edit'] = _may_edit(ctx, post)
        # Снять шире, чем править: обязательному окну нужен тормоз у того, кто
        # выше автора, — см. _may_take_down.
        post['can_take_down'] = _may_take_down(ctx, post)
        return post

    def _dress(cursor, ctx, post):
        """Карточка, готовая к отдаче: права плюс подписанные адреса кадров.

        Кадры берутся ОТДЕЛЬНЫМ запросом, а не подзапросом внутри get_post, и
        это не лишнее обращение из лени. get_post зовут семь мест, из них два —
        журнал прочтений и удаление — свой результат наружу не отдают. Положив
        bucket и blob_path в общий словарь, мы завели бы семь возможностей
        уронить путь в бакете в jsonify, и правильность держалась бы на том,
        что никто не забудет его выкинуть.
        """
        post = _with_rights(ctx, post)
        post['photos'] = (news_photos.sign_urls(gcs, queries.post_photos(cursor, post['id']))
                          if _photos_ready(cursor) else [])
        return post

    def _set_photos_refusal(cursor, ctx, post_id, payload):
        """Привязка кадров, если форма их прислала. Строка отказа или None.

        Ключа нет — кадры не трогаем: тем же правилом живёт `audience`, и оно
        нужно, чтобы правка одного поля не сносила остальные.
        """
        if 'photos' not in payload:
            return None
        if not _photos_ready(cursor):
            # Кадры прислали, а таблицы нет: молча проглотить нельзя — автор
            # решит, что фотографии прикреплены, и опубликует объявление без них.
            return 'Фотографии ещё разворачиваются, попробуйте чуть позже'
        return queries.set_photos(cursor, post_id=post_id,
                                  photo_ids=payload.get('photos') or [],
                                  user_id=ctx['user_id'])

    def _rules_from_request(payload):
        """Адресаты из тела запроса, приведённые к виду таблицы."""
        rules = []
        for raw in (payload.get('audience') or []):
            if not isinstance(raw, dict):
                continue
            subject_type = str(raw.get('subject_type') or '').strip()
            rules.append({
                'subject_type': subject_type,
                'subject_id': (None if subject_type == 'otp_role'
                               else _int_or_none(raw.get('subject_id'))),
                'subject_role': (str(raw.get('subject_role') or '').strip().lower()
                                 if subject_type == 'otp_role' else None),
                'min_role_level': _int_or_none(raw.get('min_role_level')),
            })
        return rules

    def _audience_refusal(cursor, ctx, rules):
        """Отказ по адресатам либо None. Отделы адресатов достаются одним
        запросом — по одному на строку формы было бы десять обращений к базе."""
        return news_access.audience_refusal(
            rules,
            ceiling=ctx['ceiling'],
            departments=ctx['departments'],
            subject_departments=queries.subject_departments(cursor, rules),
            target_roles=queries.roles_of_users(
                cursor, [r.get('subject_id') for r in rules
                         if r.get('subject_type') == 'user']),
        )

    # ── ЧТЕНИЕ: доступно каждому сотруднику ──────────────────────────────
    @news_route('/pending')
    def news_pending(cursor, ctx):
        """Новости, которые надо показать этому человеку прямо сейчас.

        Отметку о показе ставим ЗДЕСЬ же, а не отдельным запросом с фронта: она
        и есть точка отсчёта задержки кнопки «Прочитал», и лишний рейс на
        сервер ради неё означал бы окно, в котором кнопка не загорится никогда,
        если второй запрос не дошёл.
        """
        with_photos = _photos_ready(cursor)
        items = queries.pending_for_user(
            cursor, user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            subjects=ctx['subjects'], with_photos=with_photos)
        if with_photos:
            # Подписи берутся из процессного кэша и базу не трогают: обращений к
            # ней у этого роута столько же, сколько было до фотографий.
            for item in items:
                item['photos'] = news_photos.sign_urls(gcs, item['photos'])
        if items:
            # Только ПЕРВУЮ: окно показывает по одной, а отметка «открыл» — это
            # запись в журнал и точка отсчёта задержки. Поставив её всей
            # очереди, мы бы написали «открыл» про то, чего человек не видел.
            # Следующую отметит следующий /pending — окно его и запрашивает,
            # подтвердив текущую.
            queries.mark_shown(cursor, news_ids=[items[0]['id']],
                               user_id=ctx['user_id'])
        return jsonify({"items": items, "schema_ready": True})

    @news_route('/<int:post_id>/read', methods=('POST',))
    def news_read(cursor, ctx, post_id):
        """«Прочитал». Задержку проверяет сервер (queries.confirm_read)."""
        status, remaining = queries.confirm_read(
            cursor, news_id=post_id, user_id=ctx['user_id'],
            otp_role=ctx['otp_role'], subjects=ctx['subjects'])
        if status == 'not_found':
            return jsonify({"error": "Новость не найдена"}), 404
        if status == 'too_early':
            return jsonify({"error": "Кнопка станет активной чуть позже",
                            "code": "NEWS_TOO_EARLY",
                            "remaining_seconds": remaining}), 409
        return jsonify({"status": "ok"})

    # ── ВЫПУСК: супервайзер и выше ───────────────────────────────────────
    @news_route('/access', rights=True)
    def news_access_info(cursor, ctx):
        """Что этот человек вправе делать в разделе + справочники адресата.

        Одним ответом, а не тремя: форма открывается сразу с заполненными
        списками, и «вход в раздел стоит один запрос» остаётся правдой.
        """
        if ctx['ceiling'] is None:
            return jsonify({"can_publish": False, "ceiling": None,
                            "subjects": {}, "people": [], "roles": []})
        return jsonify({
            "can_publish": True,
            "ceiling": ctx['ceiling'],
            "bounded": ctx['departments'] is not None,
            "default_confirm_delay_seconds": DEFAULT_CONFIRM_DELAY_SECONDS,
            "subjects": queries.subject_catalog(cursor, ctx['departments']),
            "people": queries.targetable_people(
                cursor, max_role_level=ctx['ceiling'],
                department_ids=ctx['departments']),
            # Должность как адресат доступна только тому, у кого нет границы
            # отдела: правило на роль адресует людей по всей компании.
            "roles": (queries.targetable_roles(ctx['ceiling'])
                      if ctx['departments'] is None else []),
        })

    @news_route('/posts', publisher=True)
    def news_posts(cursor, ctx):
        limit = min(max(_int_or_none(request.args.get('limit')) or 50, 1), 200)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)
        status = request.args.get('status') or None
        total, items = queries.list_posts(
            cursor, viewer_id=ctx['user_id'],
            viewer_level=news_access.effective_role_level(ctx['otp_role']),
            departments=ctx['departments'], status=status,
            limit=limit, offset=offset, with_photos=_photos_ready(cursor))
        return jsonify({"items": [_with_rights(ctx, item) for item in items],
                        "total": total})

    @news_route('/posts/<int:post_id>', publisher=True)
    def news_post_item(cursor, ctx, post_id):
        post = queries.get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_read_post(ctx, post):
            return jsonify({"error": "Эта новость не из вашего периметра"}), 403
        post['audience_size'] = queries.audience_size(cursor, post_id)
        return jsonify(_dress(cursor, ctx, post))

    @news_route('/posts', methods=('POST',), publisher=True)
    def news_post_create(cursor, ctx):
        payload = request.get_json(silent=True) or {}
        title = news_access.normalize_title(payload.get('title'))
        if not title:
            return jsonify({"error": "Укажите заголовок"}), 400
        body = sanitize_html(payload.get('body') or '')
        rules = _rules_from_request(payload)
        refusal = _audience_refusal(cursor, ctx, rules)
        if refusal:
            return jsonify({"error": refusal, "code": "NEWS_AUDIENCE"}), 403

        post_id = queries.create_post(
            cursor, title=title, body=body, author_id=ctx['user_id'],
            author_department_id=ctx['department_id'],
            is_mandatory=bool(payload.get('is_mandatory', True)),
            confirm_delay_seconds=news_access.normalize_delay(
                payload.get('confirm_delay_seconds', DEFAULT_CONFIRM_DELAY_SECONDS)),
            expires_at=_timestamp_or_none(payload.get('expires_at')),
            created_by=ctx['user_id'])
        queries.set_audience(cursor, post_id=post_id, rules=rules,
                             audience_max_role_level=ctx['ceiling'])
        # ДО публикации и в той же транзакции. Иначе между «новость есть» и
        # «кадры прикреплены» открывается окно, в котором объявление уже
        # всплыло у всего отдела без фотографий — а второй раз оно не всплывёт
        # никогда: очередь /pending человек получает один раз.
        photo_refusal = _set_photos_refusal(cursor, ctx, post_id, payload)
        if photo_refusal:
            return jsonify({"error": photo_refusal, "code": "NEWS_PHOTO_LIMIT"}), 400
        if payload.get('publish'):
            queries.publish_post(cursor, post_id=post_id,
                                 audience_max_role_level=ctx['ceiling'])
        return jsonify(_dress(cursor, ctx, queries.get_post(cursor, post_id))), 201

    @news_route('/posts/<int:post_id>', methods=('PATCH',), publisher=True)
    def news_post_update(cursor, ctx, post_id):
        post = queries.get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_edit(ctx, post):
            return jsonify({"error": "Править новость может её автор"}), 403

        # Правка ОПУБЛИКОВАННОЙ новости не сбрасывает подтверждения. Это
        # решение, а не недосмотр: правят обычно опечатку, а сброс показал бы
        # окно заново всему отделу. Нужно спросить заново — публикуется новая
        # новость; так же устроено и обязательное ознакомление в вике, где
        # новая редакция статьи создаёт новое назначение, а не переоткрывает
        # старое. Автору это сказано прямо в форме.
        payload = request.get_json(silent=True) or {}
        title = news_access.normalize_title(payload.get('title', post['title']))
        if not title:
            return jsonify({"error": "Укажите заголовок"}), 400
        body = (sanitize_html(payload['body']) if 'body' in payload else post['body'])

        rules = (_rules_from_request(payload) if 'audience' in payload
                 else [{'subject_type': r['subject_type'], 'subject_id': r['subject_id'],
                        'subject_role': r['subject_role'],
                        'min_role_level': r['min_role_level']}
                       for r in post['audience']])
        refusal = _audience_refusal(cursor, ctx, rules)
        if refusal:
            return jsonify({"error": refusal, "code": "NEWS_AUDIENCE"}), 403

        # Обязательность у ВЫПУЩЕННОЙ новости не меняется. У необязательной
        # крестик пишет ту же отметку, что кнопка «Прочитал», — переключив
        # тумблер задним числом, автор превратил бы «закрыл, не читая» в
        # «подтвердил прочтение» у всех, кто уже успел её закрыть, и журнал
        # соврал бы ровно там, где к нему обращаются.
        wants_mandatory = bool(payload.get('is_mandatory', post['is_mandatory']))
        if post['status'] == 'published' and wants_mandatory != bool(post['is_mandatory']):
            return jsonify({
                "error": "У опубликованной новости обязательность не меняется — "
                         "снимите её с показа и опубликуйте заново",
                "code": "NEWS_MANDATORY_LOCKED",
            }), 409

        queries.update_post(
            cursor, post_id=post_id, title=title, body=body,
            is_mandatory=wants_mandatory,
            confirm_delay_seconds=news_access.normalize_delay(
                payload.get('confirm_delay_seconds', post['confirm_delay_seconds'])),
            expires_at=_timestamp_or_none(
                payload.get('expires_at', post['expires_at'])))
        if 'audience' in payload:
            queries.set_audience(cursor, post_id=post_id, rules=rules,
                                 audience_max_role_level=ctx['ceiling'])
        photo_refusal = _set_photos_refusal(cursor, ctx, post_id, payload)
        if photo_refusal:
            return jsonify({"error": photo_refusal, "code": "NEWS_PHOTO_LIMIT"}), 400
        return jsonify(_dress(cursor, ctx, queries.get_post(cursor, post_id)))

    @news_route('/posts/<int:post_id>/publish', methods=('POST',), publisher=True)
    def news_post_publish(cursor, ctx, post_id):
        post = queries.get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_edit(ctx, post):
            return jsonify({"error": "Публикует новость её автор"}), 403
        rules = [{'subject_type': r['subject_type'], 'subject_id': r['subject_id'],
                  'subject_role': r['subject_role'],
                  'min_role_level': r['min_role_level']} for r in post['audience']]
        refusal = _audience_refusal(cursor, ctx, rules)
        if refusal:
            return jsonify({"error": refusal, "code": "NEWS_AUDIENCE"}), 403
        queries.publish_post(cursor, post_id=post_id,
                             audience_max_role_level=ctx['ceiling'])
        return jsonify(_dress(cursor, ctx, queries.get_post(cursor, post_id)))

    @news_route('/posts/<int:post_id>/archive', methods=('POST',), publisher=True)
    def news_post_archive(cursor, ctx, post_id):
        post = queries.get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_take_down(ctx, post):
            return jsonify({"error": "Снять новость может её автор "
                                     "или руководитель выше него"}), 403
        queries.set_status(cursor, post_id=post_id, status='archived')
        return jsonify(_dress(cursor, ctx, queries.get_post(cursor, post_id)))

    @news_route('/posts/<int:post_id>', methods=('DELETE',), publisher=True)
    def news_post_delete(cursor, ctx, post_id):
        post = queries.get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_edit(ctx, post):
            return jsonify({"error": "Удалить новость может её автор"}), 403
        if post['published_at']:
            # Удаляется только то, что НИ РАЗУ не выходило к людям. Проверять
            # текущий статус мало: снятая новость перестаёт быть 'published', и
            # через «снять → удалить» журнал прочтений стирался в два нажатия —
            # а он и есть ответ на вопрос «был ли сотрудник проинформирован»,
            # ради которого раздел делали.
            return jsonify({"error": "Выпущенную новость можно только снять с показа — "
                                     "журнал прочтений остаётся",
                            "code": "NEWS_PUBLISHED"}), 409
        queries.delete_post(cursor, post_id)
        return jsonify({"status": "deleted"})

    # ── ФОТОГРАФИИ ───────────────────────────────────────────────────────
    # Роута ОТДАЧИ кадра здесь нет, и это решение: тег <img> не шлёт
    # заголовков, значит такой роут пришлось бы авторизовать кукой, а на
    # мобильном её SameSite понижается до Lax и кросс-сайтовый запрос её не
    # приложит. Браузер идёт прямо в GCS по подписи; подробности и честная
    # оговорка про пересылку адреса — в шапке news/photos.py.
    @news_route('/photos', methods=('POST',), publisher=True, defer_cursor=True)
    def news_photo_add(ctx):
        """Приложить фотографию. Один файл на запрос.

        По одному, а не пачкой: очередь грузится последовательно, и осечка на
        третьем снимке не уносит два уже уехавших. Плюс только так осмысленна
        проверка content_length — глобального MAX_CONTENT_LENGTH в приложении нет.
        """
        if not gcs or not (gcs.get('bucket_name') and gcs['bucket_name']()):
            return jsonify({"error": "Хранилище фотографий не настроено",
                            "code": "NEWS_PHOTO_STORAGE_OFF"}), 503
        # `or 0` обязателен: при chunked заголовка нет, и None > int дал бы 500
        # вместо честного 413.
        if (request.content_length or 0) > news_photos.MAX_BYTES:
            return jsonify({"error": 'Файл больше %d МБ'
                                     % (news_photos.MAX_BYTES // (1024 * 1024)),
                            "code": "NEWS_PHOTO_TOO_LARGE"}), 413
        item = request.files.get('file')
        if item is None:
            return jsonify({"error": "Файл не выбран",
                            "code": "NEWS_PHOTO_REJECTED"}), 400

        # Потолок «ничьих» — ДО чтения байтов: тот, кто не собирается сохранять
        # новость, не должен уметь набить бакет циклом.
        with db._get_cursor() as cursor:
            if not _photos_ready(cursor):
                return jsonify({"error": "Фотографии ещё разворачиваются",
                                "code": "NEWS_PHOTOS_NOT_READY"}), 409
            stale = queries.sweep_loose_photos(cursor)
            if queries.count_loose_photos(cursor, ctx['user_id']) >= MAX_LOOSE_PHOTOS_PER_USER:
                news_photos.drop_blobs(gcs, stale)
                return jsonify({"error": "Слишком много неприкреплённых фотографий — "
                                         "сохраните новость или закройте форму",
                                "code": "NEWS_PHOTO_LOOSE_LIMIT"}), 409
        news_photos.drop_blobs(gcs, stale)      # после фиксации, а не внутри неё

        try:                                    # пережатие и заливка — ВНЕ курсора
            prepared = news_photos.prepare(item.read(), filename=item.filename,
                                           content_type=item.mimetype)
            bucket, blob_path = news_photos.upload(gcs, prepared)
        except news_photos.PhotoError as refusal:
            return jsonify({"error": refusal.message,
                            "code": refusal.code}), refusal.status
        except Exception:
            # Наружу — общая фраза: текст ошибки хранилища несёт полный адрес
            # объекта, а обёртка news_route кладёт detail прямо в тело ответа.
            logging.exception('news: фотография не уехала в бакет')
            return jsonify({"error": "Не удалось сохранить фотографию",
                            "code": "NEWS_PHOTO_UPLOAD_FAILED"}), 502

        try:
            with db._get_cursor() as cursor:
                row = queries.insert_loose_photo(
                    cursor, prepared=prepared, bucket=bucket, blob_path=blob_path,
                    uploaded_by=ctx['user_id'])
        except Exception:
            # Байты уже в бакете, а строки не будет — снимаем блоб, иначе он
            # останется сиротой навсегда: сборщика сирот в проекте нет.
            news_photos.drop_blobs(gcs, [(bucket, blob_path)])
            raise

        signed = news_photos.sign_urls(gcs, [row])
        if not signed:
            return jsonify({"error": "Фотография сохранена, но адрес не выдался",
                            "code": "NEWS_PHOTO_UNSIGNED"}), 502
        return jsonify({"photo": signed[0]}), 201

    @news_route('/photos/<uuid:photo_id>', methods=('DELETE',),
                publisher=True, defer_cursor=True)
    def news_photo_drop(ctx, photo_id):
        """Снять фотографию.

        Конвертер <uuid:…> отсеивает мусор ещё в маршрутизации: без него «abc»
        доехало бы до psycopg2 и вернулось пятисоткой вместо 404.
        """
        with db._get_cursor() as cursor:
            if not _photos_ready(cursor):
                return jsonify({"error": "Фотография не найдена"}), 404
            row = queries.photo_by_id(cursor, str(photo_id))
            if not row:
                return jsonify({"error": "Фотография не найдена"}), 404
            if row['news_id'] is None:
                # Ничья — только своя. Второго читателя у неё нет по построению.
                if row['uploaded_by'] != ctx['user_id']:
                    return jsonify({"error": "Фотография не найдена"}), 404
            else:
                post = queries.get_post(cursor, row['news_id'])
                if not post:
                    return jsonify({"error": "Новость не найдена"}), 404
                # _may_edit, а не _may_read_post: чужой текст не правят, и
                # фотография здесь — часть текста.
                if not _may_edit(ctx, post):
                    return jsonify({"error": "Править новость может её автор"}), 403
            refs = queries.drop_photo(cursor, str(photo_id))
            refs += queries.sweep_loose_photos(cursor)
        # ПОСЛЕ фиксации транзакции: снести блоб раньше, чем БД подтвердила
        # удаление строки, значило бы получить запись, ссылающуюся в пустоту.
        news_photos.drop_blobs(gcs, refs)
        return jsonify({"status": "deleted"})

    @news_route('/posts/<int:post_id>/report', publisher=True)
    def news_post_report(cursor, ctx, post_id):
        """Кто прочитал, кто нет. Ради этого журнала раздел и делали."""
        post = queries.get_post(cursor, post_id)
        if not post:
            return jsonify({"error": "Новость не найдена"}), 404
        if not _may_read_post(ctx, post):
            return jsonify({"error": "Эта новость не из вашего периметра"}), 403
        rows = queries.read_report(cursor, post_id)
        # Знаменатель — только НЫНЕШНИЕ адресаты: «из скольких» отвечает на
        # вопрос «сколько человек это касается сейчас». Числитель — по тем же
        # людям, чтобы «12 из 30» нельзя было прочитать двумя способами.
        # Подтвердившие, которых уже нет в периметре, в списке остаются
        # (in_audience=false) и посчитаны отдельно.
        addressed = [row for row in rows if row['in_audience']]
        return jsonify({
            "items": rows,
            "total": len(addressed),
            "confirmed": sum(1 for row in addressed if row['confirmed_at']),
            "confirmed_outside": sum(1 for row in rows
                                     if row['confirmed_at'] and not row['in_audience']),
        })

    return bp
