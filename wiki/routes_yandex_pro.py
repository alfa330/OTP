# -*- coding: utf-8 -*-
"""Эндпоинты импорта из базы знаний Яндекс Про и сверки с ней.

Почему это свои роуты, а не параметр к /import.

`POST /import` разбирает ФАЙЛ и по своей шапке сознательно не выходит в сеть.
Здесь источник — живая страница, у которой текст лежит в состоянии Next.js, а
картинки — в чужом хранилище; и главное, статья остаётся с этой страницей
СВЯЗАННОЙ: раз в сутки её сверяют. Ветка внутри импорта документов означала бы
роут, который в половине случаев ходит в интернет, а в половине нет.

Дверей четыре, и каждая отвечает на свой вопрос:

  * `POST /yandex/preview` — «что там вообще написано?». Разбирает страницу и
    отдаёт готовое тело в редактор. Статью не создаёт: человек смотрит, правит
    и сохраняет сам — ровно как после разбора документа;
  * `POST /yandex/import` — «завести статью и подписать её на источник».
    Черновиком, с проверкой на дубль и записью в очередь «Перенос»;
  * `POST /yandex/<id>/sync` — «обновить сейчас». То же, что делает ночная
    сверка, но по кнопке и с возможностью переписать статью поверх ручных
    правок (`force`), чего автоматика не делает никогда;
  * `GET /yandex` и `PATCH|DELETE /yandex/<id>` — список связей и тумблеры.

ПРАВА. Завести статью — `can_create`, как и любой другой импорт. А вот сверка и
тумблеры — по ЭФФЕКТИВНЫМ правам на КОНКРЕТНУЮ статью (`can_edit`): сверка
переписывает её тело, и решать это должен тот, кому эту статью вообще можно
править. Обе проверки берутся готовыми из routes_edit (helpers) — второй копии
проверки прав здесь нет, как и в routes_migration.

ПОИСК ДУБЛЕЙ — ТОТ ЖЕ, ЧТО ВЕЗДЕ. `wiki_migration.duplicate_probe`, один код на
редактор, импорт документа, перенос и эту дверь. Своей проверки здесь нет
намеренно, и на это стоит сторож в тестах.
"""

from flask import jsonify, request

from . import edit as wiki_edit
from . import migration as wiki_migration
from . import queries
from . import yandex_pro
from . import yandex_sync
from .routes_structure import _int_or_none

# Ключ занятости, по которому фронт понимает, что делать с ответом. Держим
# рядом с роутами: строка ходит между сервером и экраном, и второе её написание
# однажды разошлось бы с первым.
_TRUTHY = ('1', 'true', 'yes', 'on', 'да')


def _flag(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY


def register(bp, wiki_route, db, log_ip, session_id_provider, helpers, gcs):
    load_with_permissions = helpers['load_with_permissions']
    forbidden_sections = helpers['forbidden_sections']
    section_forbidden = helpers['section_forbidden']
    sync_ai_index = helpers['sync_ai_index']
    perimeter = helpers['perimeter']

    def _body():
        return request.get_json(silent=True) or {}

    def _fail(error, status=400):
        """Ошибка источника — это не сбой сервера, а сообщение человеку."""
        return jsonify({"error": str(error), "code": "WIKI_YANDEX_SOURCE"}), status

    def _reindex(cursor, article_id):
        # Обёртка ради подписи: sync_ai_index уже не роняет вызывающего.
        return sync_ai_index(cursor, article_id)

    # ── Что написано на странице ─────────────────────────────────────────
    @wiki_route('/yandex/preview', methods=('POST',), capability='can_create')
    def wiki_yandex_preview(cursor, ctx):
        """Разобрать страницу и вернуть готовое тело статьи.

        Картинки при этом уже уезжают в наш бакет (в WebP). Иначе тело ушло бы
        в редактор с адресами хранилища Яндекса, человек сохранил бы его как
        есть — и в статье навсегда остались бы чужие ссылки. До сохранения
        статьи такой файл виден только загрузившему, так что лишнего мы не
        открываем.
        """
        data = _body()
        url = str(data.get('url') or '').strip()
        if not url:
            return jsonify({"error": "Укажите ссылку на статью базы знаний"}), 400
        try:
            result = yandex_sync.preview(
                cursor, gcs or {}, url=url, uploaded_by=ctx['user_id'],
                ai_format=_flag(data.get('ai_format')))
        except yandex_sync.SyncError as error:
            return _fail(error)

        # Вердикт дубля показываем сразу: чаще всего страница источника — это
        # не новая статья, а обновление существующей, и узнать об этом лучше
        # ДО того, как человек нажмёт «Создать».
        found = wiki_migration.duplicate_probe(
            cursor, ctx, title=result['title'], content=result['content'],
            allow_vector=(data.get('ai_support', True) is not False))
        result['duplicates'] = found
        queries.log_action(cursor, actor_id=ctx['user_id'],
                           action='article.yandex_preview',
                           entity_type='article', entity_id=None,
                           details={'url': result['source']['url'],
                                    'images': result['images']},
                           ip_address=log_ip())
        return jsonify(result)

    # ── Завести статью и подписать её на источник ────────────────────────
    @wiki_route('/yandex/import', methods=('POST',), capability='can_create')
    def wiki_yandex_import(cursor, ctx):
        data = _body()
        url = str(data.get('url') or '').strip()
        if not url:
            return jsonify({"error": "Укажите ссылку на статью базы знаний"}), 400

        section_ids = [int(s) for s in (data.get('section_ids') or []) if _int_or_none(s)]
        spaces = queries.spaces_for_user(cursor, ctx, include_guest=False)
        targets = list(section_ids)
        if not targets:
            fallback = wiki_edit.default_section_id(cursor, spaces)
            targets = [fallback] if fallback else []
        if not targets:
            return jsonify({
                "error": "Статью некуда положить: у вас нет ни одного раздела, "
                         "в который вы вправе писать",
                "code": "WIKI_NO_TARGET_SECTION",
            }), 403
        denied = forbidden_sections(cursor, ctx, targets)
        if denied:
            return section_forbidden(denied, 'создавать статьи в')

        # Вердикт снимается ДО создания: exclude_id ещё некому быть, а вопрос
        # ровно про то, что уже лежит в приёмнике. Считаем по РАЗОБРАННОМУ
        # телу, поэтому страницу приходится прочитать дважды — второй раз это
        # делает import_page. Обмен сознательный: сверять дубль по названию
        # значило бы не заметить переписанную статью с тем же именем.
        try:
            source = yandex_sync.read_source(url)
        except yandex_sync.SyncError as error:
            return _fail(error)
        found = wiki_migration.duplicate_probe(
            cursor, ctx, title=source['title'],
            content=yandex_pro.summary_of(source),
            allow_vector=(data.get('ai_support', True) is not False))
        verdict = wiki_migration.verdict_of(found)

        try:
            result = yandex_sync.import_page(
                cursor, gcs or {}, url=url, section_ids=section_ids,
                author_id=ctx['user_id'], space_ids=spaces,
                slug_taken=lambda slug: not wiki_edit.slug_is_free(cursor, slug),
                auto_sync=_flag(data.get('auto_sync'), True),
                ai_format=_flag(data.get('ai_format')),
                dedup=verdict)
        except yandex_sync.SyncError as error:
            return _fail(error)

        if not result.get('created'):
            # Повторная попытка для того же адреса. 200, а не 409: для человека
            # это «уже сделано», и падать на этом означало бы, что кнопку нельзя
            # нажать дважды.
            return jsonify({"id": result['article_id'], "slug": result.get('slug'),
                            "created": False, "status": result['status'],
                            "warnings": result.get('warnings') or []}), 200

        indexed = sync_ai_index(cursor, result['article_id'])
        queries.log_action(cursor, actor_id=ctx['user_id'],
                           action='article.yandex_import',
                           entity_type='article', entity_id=result['article_id'],
                           details={'url': result['source_url'],
                                    'title': result['title'],
                                    'images': result['images'],
                                    'dedup': verdict.get('verdict'),
                                    'auto_sync': _flag(data.get('auto_sync'), True)},
                           ip_address=log_ip())
        return jsonify({"id": result['article_id'], "slug": result['slug'],
                        "created": True, "status": "draft", "dedup": verdict,
                        "images": result['images'],
                        "warnings": result.get('warnings') or [],
                        "ai_index": indexed}), 201

    # ── Список связей ────────────────────────────────────────────────────
    @wiki_route('/yandex')
    def wiki_yandex_list(cursor, ctx):
        """Статьи, подписанные на источник, — в границах видимости человека."""
        _subjects, _sections, visible = perimeter(cursor, ctx)
        items = yandex_sync.linked_pages(cursor, article_ids=visible)
        return jsonify({
            "items": items,
            "totals": {
                "linked": len(items),
                "auto": sum(1 for item in items if item['auto_sync']),
                "conflicts": sum(1 for item in items
                                 if item['last_status'] == yandex_sync.STATUS_CONFLICT),
                "errors": sum(1 for item in items
                              if item['last_status'] == yandex_sync.STATUS_ERROR),
            },
        })

    # ── Подписать уже написанную статью ──────────────────────────────────
    @wiki_route('/yandex/<int:article_id>/link', methods=('POST',))
    def wiki_yandex_link_existing(cursor, ctx, article_id):
        """Связать существующую статью со страницей источника.

        Нужна ровно тогда, когда статья в вике уже написана руками, а следить
        надо за источником — так поставлена задача #248. Второй статьи при этом
        не появляется, и текст НЕ переписывается: связка только начинает
        сверку, а первое расхождение придёт конфликтом.
        """
        article, permissions, error = load_with_permissions(cursor, ctx, article_id)
        if error:
            return error
        if not permissions.get('can_edit'):
            return jsonify({"error": "Нет права править эту статью",
                            "code": "WIKI_FORBIDDEN"}), 403
        data = _body()
        url = str(data.get('url') or '').strip()
        if not url:
            return jsonify({"error": "Укажите ссылку на статью базы знаний"}), 400
        try:
            result = yandex_sync.link_article(
                cursor, article_id=article_id, url=url, linked_by=ctx['user_id'],
                auto_sync=_flag(data.get('auto_sync'), True),
                ai_format=_flag(data.get('ai_format')))
        except yandex_sync.SyncError as error:
            return _fail(error)
        queries.log_action(cursor, actor_id=ctx['user_id'],
                           action='article.yandex_link',
                           entity_type='article', entity_id=article_id,
                           details={'url': result['url'], 'title': article['title']},
                           ip_address=log_ip())
        return jsonify(result), 201

    # ── Обновить сейчас ──────────────────────────────────────────────────
    @wiki_route('/yandex/<int:article_id>/sync', methods=('POST',))
    def wiki_yandex_sync_now(cursor, ctx, article_id):
        article, permissions, error = load_with_permissions(cursor, ctx, article_id)
        if error:
            return error
        if not permissions.get('can_edit'):
            return jsonify({"error": "Нет права править эту статью",
                            "code": "WIKI_FORBIDDEN"}), 403
        data = _body()
        force = _flag(data.get('force'))
        try:
            result = yandex_sync.sync_article(
                cursor, gcs or {}, article_id=article_id, editor_id=ctx['user_id'],
                force=force, reindex=_reindex)
        except yandex_sync.SyncError as error:
            return _fail(error)
        queries.log_action(cursor, actor_id=ctx['user_id'],
                           action='article.yandex_sync',
                           entity_type='article', entity_id=article_id,
                           details={'status': result['status'], 'force': force,
                                    'title': article['title']},
                           ip_address=log_ip())
        return jsonify(result)

    # ── Тумблеры и отписка ───────────────────────────────────────────────
    @wiki_route('/yandex/<int:article_id>', methods=('PATCH', 'DELETE'))
    def wiki_yandex_link(cursor, ctx, article_id):
        article, permissions, error = load_with_permissions(cursor, ctx, article_id)
        if error:
            return error
        if not permissions.get('can_edit'):
            return jsonify({"error": "Нет права править эту статью",
                            "code": "WIKI_FORBIDDEN"}), 403
        if not yandex_sync.page_of_article(cursor, article_id):
            return jsonify({"error": "Статья не подписана на базу знаний Яндекс Про",
                            "code": "WIKI_NOT_LINKED"}), 404

        if request.method == 'DELETE':
            yandex_sync.unlink(cursor, article_id)
            queries.log_action(cursor, actor_id=ctx['user_id'],
                               action='article.yandex_unlink',
                               entity_type='article', entity_id=article_id,
                               details={'title': article['title']},
                               ip_address=log_ip())
            # Статья остаётся как есть: отписка — про сверку, а не про текст.
            return jsonify({"status": "unlinked"})

        data = _body()
        auto_sync = data.get('auto_sync')
        ai_format = data.get('ai_format')
        changed = yandex_sync.set_auto_sync(
            cursor, article_id,
            auto_sync=None if auto_sync is None else _flag(auto_sync),
            ai_format=None if ai_format is None else _flag(ai_format))
        if not changed:
            return jsonify({"error": "Нечего обновлять"}), 400
        return jsonify(yandex_sync.page_of_article(cursor, article_id))
