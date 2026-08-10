# -*- coding: utf-8 -*-
"""Служебные эндпоинты ИИ-помощника: состояние, пересборка, витрина выдачи.

Чата здесь ещё нет — это точка, с которой помощника можно проверять на проде:
администратор собирает индекс, любой сотрудник смотрит, что находит поиск в
границах ЕГО прав.

Про права у витрины /ai/search сказано отдельно, потому что тут легко сделать
дыру. Она НЕ закрыта способностью и НЕ имеет глобального режима: периметр всегда
считается для вызывающего. Обратный вариант — «отладочная витрина под
can_manage_structure по всему индексу» — выглядит безопаснее, но им пользуется
глава отдела с ролью OTP 'admin': способность can_manage_structure он получает
(wiki/access.py), а can_manage_access нет, и личный периметр у него на проде 16
статей из 36. Такая витрина отдала бы ему текст чужих отделов, вернув 200 OK и не
оставив следа. Поэтому доступ шире, а видимость строго личная.

Пересборка разделена на два вызова намеренно. Обработчик держит соединение из
пула всё время работы (курсор открывается снаружи обработчика, wiki/routes.py),
а пул на 40 соединений делит с SSE аукциона и колокола. Нарезка кусков — чистая
работа с базой и укладывается в доли секунды, а вот получение векторов идёт во
внешний сервис с паузами против 429: 200 кусков это больше минуты. Держать на неё
соединение значит рисковать ЧУЖИМИ разделами, поэтому векторы досчитываются
порциями, отдельным вызовом.
"""

from flask import jsonify, request

from . import perimeter as wiki_perimeter
from .ai import embed as ai_embed
from .ai import index as ai_index
from .ai import retrieve as ai_retrieve


def _int_arg(name, default, low, high):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def register(bp, wiki_route, db, log_ip):
    @wiki_route('/ai/status')
    def wiki_ai_status(cursor, ctx):
        """Готов ли помощник и что он знает про этого человека."""
        scope = wiki_perimeter.assistant_perimeter(cursor, ctx)
        payload = {
            'perimeter': {
                'articles_for_ai': len(scope['article_ids']),
                'articles_readable': scope['read_count'],
                'hash': scope['hash'][:12],
            },
        }
        try:
            payload['index'] = ai_index.index_status(cursor)
        except Exception as error:            # таблиц ещё нет — до деплоя схемы
            payload['index'] = {'error': str(error).splitlines()[0][:160]}
        try:
            payload['embeddings'] = ai_embed.embedding_status(cursor)
        except Exception as error:
            payload['embeddings'] = {'error': str(error).splitlines()[0][:160]}
        return jsonify(payload)

    @wiki_route('/ai/reindex', methods=('POST',), capability='can_manage_structure')
    def wiki_ai_reindex(cursor, ctx):
        """Пересобрать куски. Только база, без внешних вызовов."""
        article_id = request.args.get('article_id')
        force = str(request.args.get('force') or '').strip() in ('1', 'true', 'yes')
        if article_id:
            try:
                target = int(article_id)
            except ValueError:
                return jsonify({'error': 'article_id должен быть числом'}), 400
            result = ai_index.reindex_article(cursor, target, force=force)
        else:
            result = ai_index.reindex_all(cursor, force=force)
        return jsonify({'result': result, 'index': ai_index.index_status(cursor)})

    @wiki_route('/ai/embed', methods=('POST',), capability='can_manage_structure')
    def wiki_ai_embed(cursor, ctx):
        """Досчитать векторы порцией. Вызывать повторно, пока pending_after > 0."""
        limit = _int_arg('limit', 25, 1, 100)
        try:
            result = ai_embed.embed_missing(cursor, limit=limit)
        except Exception as error:
            # Провайдер недоступен — это не ошибка раздела: помощник умеет
            # работать на одной лексике, и администратор должен увидеть причину.
            return jsonify({'error': 'провайдер векторов недоступен',
                            'detail': str(error).splitlines()[0][:200]}), 503
        return jsonify(result)

    @wiki_route('/ai/search')
    def wiki_ai_search(cursor, ctx):
        """Что найдёт помощник по этому вопросу в границах прав вызывающего."""
        query = (request.args.get('q') or '').strip()
        if not query:
            return jsonify({'error': 'нужен параметр q'}), 400
        limit = _int_arg('limit', 8, 1, 30)
        per_article = _int_arg('per_article', 3, 1, 10)

        scope = wiki_perimeter.assistant_perimeter(cursor, ctx)
        article_ids = scope['article_ids']

        query_vector = None
        vector_error = None
        if str(request.args.get('lexical_only') or '').strip() not in ('1', 'true'):
            try:
                query_vector = ai_embed.embed_query(query)
            except Exception as error:
                vector_error = str(error).splitlines()[0][:200]

        found = ai_retrieve.search_hybrid(
            cursor, article_ids=article_ids, query=query,
            query_vector=query_vector, limit=limit, per_article=per_article)

        return jsonify({
            'query': query,
            'perimeter_articles': len(article_ids),
            'branches': found['branches'],
            'degraded': found['degraded'],
            'vector_error': vector_error,
            'results': [{
                'chunk_id': row['chunk_id'],
                'article_id': row['article_id'],
                'title': row['title'],
                'slug': row['slug'],
                'heading_path': row['heading_path'],
                'requires_ack': row['requires_ack'],
                'rrf': round(row['rrf'], 6),
                'score': row.get('score'),
                'similarity': row.get('similarity'),
                'found_by': row['found_by'],
                'preview': row['text'][:400],
            } for row in found['rows']],
        })
