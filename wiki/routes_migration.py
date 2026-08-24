# -*- coding: utf-8 -*-
"""Эндпоинты переноса статей из внешней вики и их модерации.

Почему перенос — свой роут, а не `POST /articles` с параметрами.

Создание статьи отвечает на «человек написал текст». Перенос отвечает на другое:
«статья приехала оттуда, её никто не читал, и решение по ней ещё не принято».
Разница не в полях, а в трёх обязанностях, которых у создания нет:

  * ПОВТОРНЫЙ ПРОГОН НЕ ПЛОДИТ КОПИИ. Тот же (source, source_id) второй раз
    возвращает уже созданную статью, а не создаёт вторую. Скрипт переноса
    поэтому можно запускать сколько угодно раз — например, чтобы дозалить то,
    что в источнике добавилось;
  * ПРОВЕРКА НА ДУБЛЬ ИДЁТ НА СЕРВЕРЕ, ОДНИМ КОДОМ С РЕДАКТОРОМ. Скрипт не
    решает, дубль это или нет: он присылает текст, а вердикт снимает
    wiki/migration.py — тот же, что показывает кнопка «Такая статья уже есть?».
    Иначе перенос и редактор отвечали бы на один вопрос по-разному;
  * СТАТЬЯ НЕ ПУБЛИКУЕТСЯ. Через `POST /articles` можно прислать
    status='published', и один недосмотр в скрипте выложил бы операторам
    непроверенный текст. Здесь такого параметра нет вовсе — не «по умолчанию
    выключено», а нет.

Права. Переносить — `can_create`: это создание статей, пусть и пачкой. Решения в
очереди — по ЭФФЕКТИВНЫМ правам на конкретную статью, как везде в разделе:
публикует тот, кому можно публиковать ИМЕННО ЭТУ статью, а не тот, кому вообще
открыта очередь. Обе проверки берутся готовыми из routes_edit (helpers) —
второй копии проверки прав здесь нет.
"""

from flask import jsonify, request

from . import edit as wiki_edit
from . import migration as wiki_migration
from . import queries
from .routes_structure import _clean, _int_or_none, _slugify


def register(bp, wiki_route, db, log_ip, session_id_provider, helpers):
    """helpers — общие замыкания из routes_edit.register (см. его хвост)."""
    load_with_permissions = helpers['load_with_permissions']
    forbidden_sections = helpers['forbidden_sections']
    section_forbidden = helpers['section_forbidden']
    sync_ai_index = helpers['sync_ai_index']

    def _session_id():
        try:
            return session_id_provider() if session_id_provider else None
        except Exception:
            return None

    def _body():
        return request.get_json(silent=True) or {}

    # ── Очередь модерации ────────────────────────────────────────────────
    @wiki_route('/migration')
    def wiki_migration_queue(cursor, ctx):
        """Что перенесено и что ещё ждёт решения.

        `?all=1` — вместе с уже промодерированными: нужно, чтобы ответить «а что
        мы вообще перенесли», когда очередь уже пуста. По умолчанию отдаём
        только остаток работы: очередь — список дел, а не журнал.
        """
        pending_only = str(request.args.get('all') or '').strip().lower() not in (
            '1', 'true', 'yes', 'on', 'да')
        # Пространство — то же, что выбрано переключателем в шапке: половина
        # «Перенос» появляется по счётчику из /catalog, а он сужен пространством.
        space_id = _int_or_none(request.args.get('space_id'))
        return jsonify({
            "totals": wiki_migration.totals(cursor, ctx, space_id),
            "items": wiki_migration.queue(cursor, ctx, pending_only=pending_only,
                                          space_id=space_id),
            "pending_only": pending_only,
        })

    # ── Перенос одной статьи ─────────────────────────────────────────────
    @wiki_route('/migration/import', methods=('POST',), capability='can_create')
    def wiki_migration_import(cursor, ctx):
        data = _body()
        title = _clean(data.get('title'))
        if not title:
            return jsonify({"error": "Укажите название статьи"}), 400

        source = _clean(data.get('source'), 32) or wiki_migration.SOURCE_OLD_WIKI
        source_id = _int_or_none(data.get('source_id'))

        # Уже переносили — отдаём ту же статью. Ответ 200, а не 409: для скрипта
        # это штатное «уже сделано», и падать на нём означало бы, что повторный
        # прогон невозможен.
        existing = wiki_migration.already_imported(
            cursor, source=source, source_id=source_id)
        if existing:
            # Слаг отдаём ВСЕГДА, а не только при создании: по нему скрипт
            # переноса строит карту «путь в источнике → статья приёмника» и
            # переписывает внутренние ссылки. Без слага повторный прогон собирал
            # пустую карту и молча оставлял 367 ссылок указывать на источник.
            return jsonify({"id": existing['article_id'],
                            "slug": existing['slug'],
                            "created": False,
                            "status": "already_imported"})

        content = data.get('content') or ''
        # Проверка на дубль ДО создания: exclude_id ещё некому быть, а вердикт
        # нужен именно про «то, что уже лежит в приёмнике».
        found = wiki_migration.duplicate_probe(
            cursor, ctx, title=title, content=content,
            allow_vector=(data.get('ai_support', True) is not False))
        verdict = wiki_migration.verdict_of(found)

        section_ids = [int(s) for s in (data.get('section_ids') or [])
                       if _int_or_none(s)]
        # Раздел спрашиваем так же, как при создании: право can_create нужно в
        # КАЖДОМ выбранном, а пустой список не отменяет проверку — статья без
        # раздела падает в запасной «Общий сотрудник», и его тоже надо спросить.
        targets = list(section_ids)
        if not targets:
            fallback = wiki_edit.default_section_id(
                cursor, queries.spaces_for_user(cursor, ctx))
            targets = [fallback] if fallback else []
        denied = forbidden_sections(cursor, ctx, targets)
        if denied:
            return section_forbidden(denied, 'создавать статьи в')

        slug = _clean(data.get('slug'), 200) or _slugify(title)
        base_slug, suffix = slug, 2
        while not wiki_edit.slug_is_free(cursor, slug):
            slug = '%s-%d' % (base_slug, suffix)
            suffix += 1

        article_id = wiki_edit.create_article(
            cursor, slug=slug, title=title,
            summary=_clean(data.get('summary'), 2000),
            content=content,
            article_type='general',
            section_ids=section_ids, tags=data.get('tags') or [],
            author_id=ctx['user_id'],
            space_ids=queries.spaces_for_user(cursor, ctx),
        )

        wiki_migration.record(
            cursor, article_id=article_id, source=source, source_id=source_id,
            source_slug=_clean(data.get('source_slug'), 255),
            source_title=_clean(data.get('source_title'), 255) or title,
            source_status=_clean(data.get('source_status'), 32),
            dedup=verdict, imported_by=ctx['user_id'])

        # Индекс ИИ черновик не возьмёт — периметр про это знает. Вызываем всё
        # равно: решение «брать или нет» принимает одно место, и обходить его
        # здесь значило бы завести второе.
        indexed = sync_ai_index(cursor, article_id)

        queries.log_action(cursor, actor_id=ctx['user_id'],
                           action='article.migrate',
                           entity_type='article', entity_id=article_id,
                           details={'title': title, 'slug': slug,
                                    'source': source, 'source_id': source_id,
                                    'dedup': verdict.get('verdict'),
                                    'dedup_score': verdict.get('score')},
                           ip_address=log_ip())
        return jsonify({"id": article_id, "slug": slug, "created": True,
                        "status": "draft", "dedup": verdict,
                        "ai_index": indexed}), 201

    # ── Решение по перенесённой статье ───────────────────────────────────
    def _decide(cursor, ctx, article_id, *, publish):
        row = wiki_migration.pending_row(cursor, article_id)
        if not row:
            return jsonify({"error": "Эта статья не переносилась из внешней вики",
                            "code": "WIKI_NOT_MIGRATED"}), 404
        if row['reviewed']:
            # Не 409: второй человек нажал ту же кнопку, и это не ошибка, а
            # состояние. Врать об успехе тоже нельзя — говорим, как есть.
            return jsonify({"status": "already_reviewed",
                            "review_action": row['review_action']}), 200

        article, permissions, error = load_with_permissions(cursor, ctx, article_id)
        if error:
            return error

        note = _clean(_body().get('note'), 500)
        was_published = article.get('status') == 'published'

        if publish:
            if not permissions.get('can_publish'):
                return jsonify({
                    "error": "Нет права публиковать эту статью",
                    "code": "WIKI_FORBIDDEN", "required": "can_publish",
                }), 403
            # Уже опубликованную не «публикуем» второй раз: такая статья приехала
            # до появления очереди и живёт на витрине. Решение здесь —
            # подтвердить, что она актуальна, и снять её из очереди. Лишняя
            # версия в истории с комментарием «Публикация после переноса»
            # соврала бы, будто текст в этот момент выпускали.
            action = 'kept' if was_published else 'published'
            if not was_published:
                wiki_edit.update_article(
                    cursor, article_id, {'status': 'published'},
                    editor_id=ctx['user_id'], session_id=_session_id(),
                    comment='Публикация после переноса из внешней вики')
        else:
            if not permissions.get('can_delete'):
                return jsonify({
                    "error": "Нет права удалять эту статью",
                    "code": "WIKI_FORBIDDEN", "required": "can_delete",
                }), 403
            # «Удалить» в разделе всегда означает архив, а не строку из базы, —
            # то же, что делает корзина у обычной статьи. Решение о переносе
            # можно будет отменить, а восстановить удалённый текст было бы нечем.
            wiki_edit.delete_article(cursor, article_id)
            action = 'discarded'

        closed = wiki_migration.mark_reviewed(
            cursor, article_id, action=action,
            reviewer_id=ctx['user_id'], note=note)
        indexed = sync_ai_index(cursor, article_id)

        queries.log_action(
            cursor, actor_id=ctx['user_id'], action='article.migrate_review',
            entity_type='article', entity_id=article_id,
            details={'title': article.get('title'), 'decision': action,
                     'source': row['source'], 'source_id': row['source_id'],
                     'ai_index': indexed.get('action')},
            ip_address=log_ip())
        return jsonify({"status": "ok", "review_action": action,
                        "closed": closed, "ai_index": indexed})

    @wiki_route('/migration/<int:article_id>/publish', methods=('POST',))
    def wiki_migration_publish(cursor, ctx, article_id):
        return _decide(cursor, ctx, article_id, publish=True)

    @wiki_route('/migration/<int:article_id>/discard', methods=('POST',))
    def wiki_migration_discard(cursor, ctx, article_id):
        return _decide(cursor, ctx, article_id, publish=False)
