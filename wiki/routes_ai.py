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
from .ai import answer as ai_answer
from .ai import embed as ai_embed
from .ai import index as ai_index
from .ai import providers as ai_providers
from .ai import retrieve as ai_retrieve
from .ai import store as ai_store


def _int_arg(name, default, low, high):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(low, min(value, high))


def _space_id():
    """Пространство, в котором человек сейчас находится.

    У GET приходит строкой запроса, у POST — телом: помощник спрашивается и
    так, и так, а знать он обязан ровно ту вику, что открыта на экране.
    Мусор гасим в None, а не в 400: неизвестное пространство означает «не
    сужать», и сузить его всё равно нечем — периметр уже отсечён границей
    отдела, и чужой вики в нём нет.
    """
    raw = request.args.get('space_id')
    if raw is None:
        raw = (request.get_json(silent=True) or {}).get('space_id')
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def register(bp, wiki_route, db, log_ip):
    @wiki_route('/ai/status')
    def wiki_ai_status(cursor, ctx):
        """Готов ли помощник и что он знает про этого человека."""
        scope = wiki_perimeter.assistant_perimeter(cursor, ctx, _space_id())
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

        scope = wiki_perimeter.assistant_perimeter(cursor, ctx, _space_id())
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
            # Поля берутся только те, что реально возвращает fuse(). Раньше здесь
            # стоял row['rrf'] — остаток от слияния по RRF, от которого отказались
            # по замеру; ключа в строках больше нет, и витрина падала с 500.
            # Регрессию закрывает тест на контракт строки (test_wiki_ai_fusion).
            'results': [{
                'rank': position,
                'chunk_id': row['chunk_id'],
                'article_id': row['article_id'],
                'title': row['title'],
                'slug': row['slug'],
                'heading_path': row['heading_path'],
                'requires_ack': row['requires_ack'],
                'score': row.get('score'),
                'similarity': row.get('similarity'),
                'found_by': row['found_by'],
                'preview': row['text'][:400],
            } for position, row in enumerate(found['rows'], start=1)],
        })

    # ── Чат ──────────────────────────────────────────────────────────────────
    #
    # Про удержание соединения из пула. Обработчик держит курсор всё время
    # работы, включая вызов модели. Посчитано, а не отложено: основной провайдер
    # отвечает 0,5-1,1 с, а его же минутный лимит (12 000 токенов на всю
    # организацию, около 5 вопросов в минуту) ограничивает поток сверху. Пять
    # вопросов по две секунды — это 10 секунд занятости соединения на минуту,
    # то есть в среднем 0,17 соединения из 40. Даже десятикратный запас не
    # приближается к пулу. Опасный сценарий из разведки («десять одновременных
    # вопросов по 15 секунд») лимитами провайдера физически исключён. Пересмотреть
    # придётся на этапе 7 вместе со стримингом: там вызов живёт дольше и роут
    # надо будет объявлять вручную, вне wiki_route.

    @wiki_route('/ai/chats')
    def wiki_ai_chats(cursor, ctx):
        limit = _int_arg('limit', 30, 1, 100)
        offset = _int_arg('offset', 0, 0, 10000)
        return jsonify({'chats': ai_store.list_chats(cursor, ctx['user_id'],
                                                     limit=limit, offset=offset)})

    @wiki_route('/ai/chats', methods=('POST',))
    def wiki_ai_chat_create(cursor, ctx):
        return jsonify({'chat': ai_store.create_chat(cursor, ctx['user_id'])})

    @wiki_route('/ai/chats/<int:chat_id>')
    def wiki_ai_chat_read(cursor, ctx, chat_id):
        chat = ai_store.owned_chat(cursor, ctx['user_id'], chat_id)
        if not chat:
            return jsonify({'error': 'чат не найден'}), 404
        scope = wiki_perimeter.assistant_perimeter(cursor, ctx, _space_id())
        messages = ai_store.chat_messages(
            cursor, chat_id, visible_article_ids=scope['article_ids'])
        return jsonify({'chat': chat, 'messages': messages})

    @wiki_route('/ai/chats/<int:chat_id>', methods=('PATCH',))
    def wiki_ai_chat_rename(cursor, ctx, chat_id):
        payload = request.get_json(silent=True) or {}
        title = str(payload.get('title') or '').strip()
        if not title:
            return jsonify({'error': 'нужно название'}), 400
        if not ai_store.rename_chat(cursor, ctx['user_id'], chat_id, title[:120]):
            return jsonify({'error': 'чат не найден'}), 404
        return jsonify({'ok': True, 'title': title[:120]})

    @wiki_route('/ai/chats/<int:chat_id>', methods=('DELETE',))
    def wiki_ai_chat_delete(cursor, ctx, chat_id):
        # Идемпотентно: повторный вызов тоже 200. Двойной клик не должен
        # выглядеть ошибкой — та же логика, что в разборах ИИ после фикса.
        ai_store.delete_chat(cursor, ctx['user_id'], chat_id)
        return jsonify({'ok': True})

    @wiki_route('/ai/chats/<int:chat_id>/ask', methods=('POST',))
    def wiki_ai_ask(cursor, ctx, chat_id):
        payload = request.get_json(silent=True) or {}
        question = str(payload.get('question') or '').strip()
        if not question:
            return jsonify({'error': 'нужен вопрос'}), 400
        if len(question) > 1000:
            return jsonify({'error': 'вопрос слишком длинный'}), 400

        chat = ai_store.owned_chat(cursor, ctx['user_id'], chat_id)
        if not chat:
            return jsonify({'error': 'чат не найден'}), 404

        scope = wiki_perimeter.assistant_perimeter(cursor, ctx, _space_id())
        if not scope['article_ids']:
            return jsonify({'error': 'нет доступных статей',
                            'detail': 'помощнику не выдан доступ ни к одной статье'
                                      ' — обратитесь к администратору вики'}), 409

        # История берётся ДО записи нового вопроса, иначе он попал бы в контекст
        # дважды — и как история, и как сам вопрос.
        history = ai_store.recent_turns(cursor, chat_id, limit=6)
        after_clarify = bool(history) and history[-1].get('kind') == 'clarify'

        # ПОИСК идёт по обогащённому запросу: короткая реплика вроде «Taxi24» или
        # «отправь ссылку» сама по себе не содержит темы. Правило целиком живёт в
        # слое ответа — там же, где его замер и тесты.
        search_query = ai_answer.enrich_query(question, history)

        query_vector = None
        try:
            query_vector = ai_embed.embed_query(search_query)
        except Exception:
            query_vector = None      # деградация до лексики, не отказ

        found = ai_retrieve.search_hybrid(
            cursor, article_ids=scope['article_ids'], query=search_query,
            query_vector=query_vector, limit=8, per_article=3)

        ai_store.append_message(cursor, chat_id, role='user', kind='question',
                                text=question)
        try:
            result = ai_answer.compose(
                question, found['rows'], ai_providers.generate,
                history=history,
                # Переспрашивать можно только у ХОЛОДНОГО короткого вопроса.
                # Два случая, когда нельзя:
                #   * предыдущая реплика помощника сама была уточнением — иначе
                #     разговор ходит по кругу;
                #   * вопрос короткий, но это продолжение темы (запрос обогащён
                #     предыдущим). Гейт видит только длину текущей реплики и
                #     считал двусмысленным «а для новичков» после ответа про
                #     байги, хотя контекст его однозначно определяет.
                allow_clarify=not after_clarify and search_query == question)
        except ai_providers.ProviderError as error:
            return jsonify({'error': 'ИИ недоступен',
                            'detail': str(error)[:300]}), 503

        meta = result.get('meta') or {}
        usage = meta.get('usage') or {}
        stored = ai_store.append_message(
            cursor, chat_id, role='assistant', kind=result['kind'],
            text=result['text'], provider=meta.get('provider'),
            model=meta.get('model'),
            elapsed_ms=int((meta.get('elapsed') or 0) * 1000) or None,
            input_tokens=usage.get('prompt_tokens'),
            output_tokens=usage.get('completion_tokens'),
            sources=result.get('sources') or ())
        ai_store.touch_chat(cursor, ctx['user_id'], chat_id,
                            first_question=question)

        return jsonify({
            'message_id': stored['id'],
            'kind': result['kind'],
            'text': result['text'],
            'notes': result.get('notes') or [],
            'sources': [{
                'ord': position,
                'article_id': source.get('article_id'),
                'title': source.get('title'),
                'slug': source.get('slug'),
                'heading_path': source.get('heading_path'),
                'quote': source.get('quote'),
                # Цитату извлекает сервер, поэтому она дословна всегда, и флага
                # «подтверждена» больше нет. Отдаём другое: указала ли фрагмент
                # сама модель или сервер сопоставил его по пересечению с ответом.
                'attributed': bool(source.get('attributed')),
                'requires_ack': bool(source.get('requires_ack')),
            } for position, source in enumerate(result.get('sources') or [])],
            'provider': meta.get('provider'),
            'model': meta.get('model'),
            'elapsed': meta.get('elapsed'),
            'degraded_search': found['degraded'],
        })

    @wiki_route('/ai/messages/<int:message_id>/feedback', methods=('POST',))
    def wiki_ai_feedback(cursor, ctx, message_id):
        payload = request.get_json(silent=True) or {}
        raw = payload.get('feedback')
        if raw not in (1, -1, 0, '1', '-1', '0'):
            return jsonify({'error': 'feedback должен быть 1, -1 или 0'}), 400
        if not ai_store.set_feedback(cursor, ctx['user_id'], message_id, int(raw)):
            return jsonify({'error': 'реплика не найдена'}), 404
        return jsonify({'ok': True})
