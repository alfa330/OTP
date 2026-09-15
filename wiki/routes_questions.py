# -*- coding: utf-8 -*-
"""Вкладка «Вопросы»: разбор вопросов операторов и запись ответа в базу знаний.

Задача #321. Кто передаёт вопрос и кому — wiki/questions.py, черновики модели —
wiki/ai/knowledge.py. Здесь двери и порядок шагов.

КТО ВХОДИТ. Тот, кто вправе адресовать отделу новость (questions.reviewer_scope):
супервайзер — свой отдел, руководитель — возглавляемые, директор и
администратор вики — все. Граница отдела стоит в КАЖДОМ пути по id, а не только
в списке: точечные пути, не повторившие правило списка, уже открывали
супервайзеру чужие черновики в «Новостях» (news/routes.py: _may_read_post).

ЧЕРНОВИК — ДВУМЯ ЗАПРОСАМИ, А НЕ ОДНИМ. Правка статьи и новость с тестом — два
вызова модели, и каждый держит соединение из пула всё время генерации
(wiki_route открывает курсор снаружи обработчика). Одним запросом это было бы
вдвое дольше на одном соединении из сорока, которые делят SSE аукциона и
колокола, а сбой второго шага выбрасывал бы готовый первый.

ПУБЛИКАЦИЯ — ОДНОЙ ТРАНЗАКЦИЕЙ. Статья, новость с тестом и отметка на вопросе
пишутся вместе или не пишутся вовсе: статья без новости молча не дошла бы до
отдела, новость без статьи рассказывала бы о том, чего в базе нет. Проверки
прав стоят ДО первой записи; после неё откатывать приходится только гонку двух
супервайзеров, и её закрывает савпоинт.

ГРАНИЦ ДВЕ. Отдел — чей это вопрос, пространство — в какой вике он живёт.
Список идёт по обеим (questions._space_scope), пути по id — только по отделу:
карточку открывают из колокола, находясь в другой вике, и вкладка сама
переключает шапку на вику вопроса (WikiQuestions.jsx).
"""

from flask import jsonify, request

from news import access as news_access
from news import queries as news_queries
from news import schema as news_schema

from . import ack as wiki_ack
from . import articles as wiki_articles
from . import edit as wiki_edit
from . import perimeter as wiki_perimeter
from . import queries
from . import questions as wiki_questions
from .ai import knowledge as ai_knowledge
from .ai import providers as ai_providers
from .routes_structure import _clean, _int_or_none, _slugify

_LIST_LIMIT = 50


def register(bp, wiki_route, db, log_ip, edit_helpers):
    """edit_helpers — проверки статьи из routes_edit.register, а не их копия."""
    load_with_permissions = edit_helpers['load_with_permissions']
    sync_ai_index = edit_helpers['sync_ai_index']
    perimeter = edit_helpers['perimeter']

    def _refusal(message, status, code):
        return jsonify({'error': message, 'code': code}), status

    def _body():
        return request.get_json(silent=True) or {}

    def _reviewer(cursor, ctx):
        """(отделы, отказ). Отделы None — «без границы», поэтому беду выдаёт отказ."""
        allowed, departments = wiki_questions.reviewer_scope(ctx)
        if not allowed:
            return None, _refusal('Вопросы операторов разбирает супервайзер и выше',
                                  403, 'WIKI_QUESTIONS_FORBIDDEN')
        if not wiki_questions.table_ready(cursor):
            return None, _refusal('Вопросы операторов ещё разворачиваются — '
                                  'загляните чуть позже', 503, 'WIKI_QUESTIONS_NOT_READY')
        return departments, None

    def _resolved(item):
        who = item.get('resolved_by_name')
        return jsonify({
            'error': 'Вопрос уже разобран' + (' — %s' % who if who else ''),
            'code': 'WIKI_QUESTION_RESOLVED',
            # Свежая строка: экран сразу покажет, КАК вопрос разобран, а не
            # попросит обновить страницу.
            'item': item,
        }), 409

    def _load(cursor, ctx, question_id, stage=None):
        """(вопрос, отказ) — с границей отдела и проверкой шага цепочки."""
        departments, error = _reviewer(cursor, ctx)
        if error:
            return None, error
        item = wiki_questions.get_question(cursor, question_id, departments=departments)
        if not item:
            # 404, а не 403: «не ваш отдел» само по себе сообщало бы, что такой
            # вопрос существует.
            return None, _refusal('Вопрос не найден', 404, 'WIKI_QUESTION_NOT_FOUND')
        if stage == 'open' and item['status'] != 'open':
            return None, _resolved(item)
        if stage == 'knowledge':
            if item['status'] != 'answered':
                return None, _refusal('Сначала ответьте оператору', 409,
                                      'WIKI_QUESTION_STAGE')
            if item['kb_status']:
                return None, _refusal('Этот вопрос уже оформлен', 409,
                                      'WIKI_QUESTION_STAGE')
        return item, None

    def _fresh(cursor, question_id):
        # Граница уже проверена в _load — повторять её на перечитке незачем.
        return wiki_questions.get_question(cursor, question_id, departments=None)

    def _article_ref(article):
        return {'id': article['id'], 'title': article['title'], 'slug': article['slug']}

    def _article_refusal(permissions):
        """Статью из ответа правят и тут же выпускают — нужны оба права."""
        if not permissions.get('can_edit'):
            return _refusal('Нет права править эту статью', 403, 'WIKI_FORBIDDEN')
        if not permissions.get('can_publish'):
            return _refusal('Нет права публиковать эту статью', 403, 'WIKI_FORBIDDEN')
        return None

    def _publishable_sections(cursor, ctx, section_ids):
        """Разделы из списка, куда человек вправе и положить статью, и выпустить её.

        Выпуск спрашиваем сразу, а не на последнем шаге: новая статья из ответа
        публикуется той же кнопкой, что и новость, и раздел «писать можно,
        выпускать нельзя» закончился бы отказом уже после двух вызовов модели.
        Право — как у редактора: способность должности И правило раздела
        (routes_edit: _forbidden_sections), мастер-ключ проходит без правил.
        """
        wanted = sorted({int(s) for s in (section_ids or ()) if _int_or_none(s)})
        capabilities = ctx['capabilities']
        if not wanted or not (capabilities.get('can_create')
                              and capabilities.get('can_publish')):
            return []
        if capabilities.get('can_manage_access'):
            return wanted
        rules = queries.section_rules_for_user(cursor, wanted, ctx['subjects'],
                                               ctx['user_id'])
        return [sid for sid in wanted
                if any(rule.get('can_create') and rule.get('can_publish')
                       for rule in rules.get(sid, ()))]

    def _section_allowed(cursor, ctx, section_id):
        if not section_id:
            return False
        cursor.execute("SELECT 1 FROM wiki_sections WHERE id = %s AND status = 'active'",
                       (section_id,))
        return bool(cursor.fetchone()) and bool(_publishable_sections(cursor, ctx, [section_id]))

    def _section_choices(cursor, ctx, readable_sections, space_id):
        """Разделы пространства вопроса, куда можно выпустить новую статью."""
        cursor.execute(
            """
            SELECT s.id, s.name, parent.name
              FROM wiki_sections s
              LEFT JOIN wiki_sections parent ON parent.id = s.parent_section_id
             WHERE s.id = ANY(%(ids)s) AND s.status = 'active'
               AND (%(space)s::int IS NULL OR s.space_id = %(space)s::int)
             ORDER BY parent.name NULLS FIRST, s.position, s.name
            """,
            {'ids': sorted(readable_sections) or [-1], 'space': space_id},
        )
        rows = cursor.fetchall()
        allowed = set(_publishable_sections(cursor, ctx, [row[0] for row in rows]))
        # Подпись родителя обязательна: ветки СЗоВ и ОП устроены одинаково, и
        # «Регламенты» без родителя в списке встречались бы дважды.
        return [{'id': row[0], 'name': row[1], 'parent_name': row[2] or ''}
                for row in rows if row[0] in allowed]

    def _ai_unavailable(error):
        return jsonify({'error': 'ИИ недоступен', 'detail': str(error)[:300],
                        'code': 'WIKI_AI_UNAVAILABLE'}), 503

    # ── Очередь и разбор ─────────────────────────────────────────────────────

    def _space(cursor, ctx):
        """(вика на экране, вики, открытые разбирающему — в порядке переключателя).

        Вика приходит из запроса, но берётся только из открытых человеку. Чужое
        или устаревшее значение заменяется первой по переключателю, а не снятием
        границы — тот же довод, что у помощника (routes_ai.effective_space):
        «не понял — не сужаю» вернуло бы ровно ту смесь вопросов разных вик,
        от которой граница и заведена.
        """
        # Порядок — как в переключателе (position, id), но без подсчёта разделов
        # structure.list_spaces: список перечитывается на каждый тычок колокола.
        cursor.execute('SELECT id FROM wiki_spaces WHERE id = ANY(%s) ORDER BY position, id',
                       (sorted(queries.spaces_for_user(cursor, ctx)) or [-1],))
        ordered = [row[0] for row in cursor.fetchall()]
        reachable = set(ordered)
        requested = _int_or_none(request.args.get('space_id'))
        if requested in reachable:
            return requested, ordered
        return (ordered[0] if ordered else None), ordered

    @wiki_route('/questions')
    def wiki_questions_list(cursor, ctx):
        departments, error = _reviewer(cursor, ctx)
        if error:
            return error
        bucket = request.args.get('bucket')
        if bucket not in wiki_questions.BUCKETS:
            bucket = 'open'
        limit = max(1, min(_int_or_none(request.args.get('limit')) or _LIST_LIMIT, 100))
        offset = max(0, _int_or_none(request.args.get('offset')) or 0)
        space_id, reachable = _space(cursor, ctx)
        items, counts = wiki_questions.list_questions(
            cursor, departments=departments, bucket=bucket, limit=limit, offset=offset,
            space_id=space_id, reachable_spaces=reachable)
        return jsonify({
            'bucket': bucket, 'items': items, 'counts': counts,
            # Отдел в строке нужен только тому, кто видит больше одного: у
            # супервайзера он в каждой строке один и тот же — это шум.
            'many_departments': departments is None or len(departments) > 1,
        })

    @wiki_route('/questions/<int:question_id>')
    def wiki_questions_item(cursor, ctx, question_id):
        item, error = _load(cursor, ctx, question_id)
        if error:
            return error
        return jsonify({'item': item})

    @wiki_route('/questions/<int:question_id>/answer', methods=('POST',))
    def wiki_questions_answer(cursor, ctx, question_id):
        """Ответ уходит оператору СРАЗУ — в тот же разговор с помощником."""
        item, error = _load(cursor, ctx, question_id, stage='open')
        if error:
            return error
        answer = str(_body().get('answer') or '').strip()
        if not answer:
            return _refusal('Напишите ответ', 400, 'WIKI_QUESTION_EMPTY')
        if len(answer) > wiki_questions.MAX_ANSWER_LENGTH:
            return _refusal('Ответ длиннее %d знаков — такое оформляется статьёй'
                            % wiki_questions.MAX_ANSWER_LENGTH, 400,
                            'WIKI_QUESTION_TOO_LONG')
        if wiki_questions.answer_question(cursor, question_id=question_id,
                                          reviewer_id=ctx['user_id'],
                                          answer=answer) is None:
            # Между открытием карточки и ответом успел коллега.
            return _resolved(_fresh(cursor, question_id) or item)
        return jsonify({'item': _fresh(cursor, question_id)})

    @wiki_route('/questions/<int:question_id>/dismiss', methods=('POST',))
    def wiki_questions_dismiss(cursor, ctx, question_id):
        item, error = _load(cursor, ctx, question_id, stage='open')
        if error:
            return error
        if not wiki_questions.dismiss_question(cursor, question_id=question_id,
                                               reviewer_id=ctx['user_id']):
            return _resolved(_fresh(cursor, question_id) or item)
        return jsonify({'item': _fresh(cursor, question_id)})

    # ── База знаний ──────────────────────────────────────────────────────────

    @wiki_route('/questions/<int:question_id>/knowledge/targets', methods=('POST',))
    def wiki_questions_targets(cursor, ctx, question_id):
        """Куда записать ответ: статьи-кандидаты и разделы для новой статьи.

        POST, а не GET: запрос уходит во внешний сервис векторов, и браузер не
        должен повторять его сам — при предзагрузке или возврате по истории.
        """
        item, error = _load(cursor, ctx, question_id, stage='knowledge')
        if error:
            return error
        subjects, sections, _visible = perimeter(cursor, ctx)
        # Пространство — то, где задан вопрос: ответ из «Тез» в «Таксопарки» не
        # пишут, даже если супервайзеру открыты оба.
        scope = wiki_perimeter.assistant_perimeter(cursor, ctx, item['space_id'])
        candidates, degraded = ai_knowledge.find_targets(
            cursor, article_ids=scope['article_ids'], question=item['question'],
            answer=item['answer'])

        nearest_section = None
        for candidate in candidates:
            article = wiki_articles.get_article(cursor, article_id=candidate['article_id'])
            permissions = (wiki_articles.effective_permissions(
                cursor, ctx, article, subjects, sections, queries.section_rules_for_user)
                if article else {})
            candidate['can_edit'] = bool(permissions.get('can_edit')
                                         and permissions.get('can_publish'))
            if nearest_section is None and article and article['section_ids']:
                nearest_section = article['section_ids'][0]

        choices = _section_choices(cursor, ctx, sections, item['space_id'])
        ids = [choice['id'] for choice in choices]
        # Новую статью по умолчанию кладём рядом с ближайшей по смыслу — туда,
        # где её и станут искать.
        default_section = nearest_section if nearest_section in ids else (ids[0] if ids else None)
        return jsonify({
            'candidates': candidates,
            'sections': choices,
            'suggested': ai_knowledge.suggest_target(candidates,
                                                     default_section_id=default_section),
            'degraded': degraded,
        })

    @wiki_route('/questions/<int:question_id>/knowledge/draft', methods=('POST',))
    def wiki_questions_draft(cursor, ctx, question_id):
        """Шаг 1 из 2: правка статьи или новая статья. Ничего не записывается."""
        item, error = _load(cursor, ctx, question_id, stage='knowledge')
        if error:
            return error
        data = _body()
        action = data.get('action')
        try:
            if action == 'update':
                article_id = _int_or_none(data.get('article_id'))
                if not article_id:
                    return _refusal('Выберите статью', 400, 'WIKI_QUESTION_TARGET')
                article, permissions, error = load_with_permissions(cursor, ctx, article_id)
                if error:
                    return error
                refusal = _article_refusal(permissions)
                if refusal:
                    return refusal
                result = ai_knowledge.draft_update(
                    article=article, question=item['question'], answer=item['answer'],
                    generate_fn=ai_providers.generate_article)
                return jsonify({
                    'action': 'update', 'article': _article_ref(article),
                    'title': article['title'], 'content': result['content'],
                    'changes': result['changes'], 'questions': result['questions'],
                    'warnings': result['warnings'],
                    'model': (result.get('meta') or {}).get('model'),
                })
            if action == 'create':
                section_id = _int_or_none(data.get('section_id'))
                if not _section_allowed(cursor, ctx, section_id):
                    return _refusal('Нет права выпускать статьи в этом разделе', 403,
                                    'WIKI_SECTION_FORBIDDEN')
                result = ai_knowledge.draft_create(
                    question=item['question'], answer=item['answer'],
                    generate_fn=ai_providers.generate_article)
                return jsonify({
                    'action': 'create', 'section_id': section_id,
                    'title': result['title'], 'summary': result['summary'],
                    'content': result['content'], 'changes': [], 'questions': [],
                    'warnings': result['warnings'],
                    'model': (result.get('meta') or {}).get('model'),
                })
        except ai_providers.ProviderError as exc:
            return _ai_unavailable(exc)
        return _refusal('Выберите статью или раздел для новой', 400, 'WIKI_QUESTION_TARGET')

    @wiki_route('/questions/<int:question_id>/knowledge/news', methods=('POST',))
    def wiki_questions_news(cursor, ctx, question_id):
        """Шаг 2 из 2: новость с тестом по готовой правке. Ничего не записывается."""
        item, error = _load(cursor, ctx, question_id, stage='knowledge')
        if error:
            return error
        data = _body()
        raw_changes = data.get('changes') if isinstance(data.get('changes'), list) else []
        changes = [str(line) for line in raw_changes if str(line or '').strip()][:20]
        try:
            result = ai_knowledge.draft_news(
                question=item['question'], answer=item['answer'],
                article_title=_clean(data.get('article_title')) or '',
                changes=changes, generate_fn=ai_providers.generate_article)
        except ai_providers.ProviderError as exc:
            return _ai_unavailable(exc)
        return jsonify({
            'title': result['title'], 'body': result['body'], 'quiz': result['quiz'],
            'warnings': result['warnings'],
            'model': (result.get('meta') or {}).get('model'),
        })

    @wiki_route('/questions/<int:question_id>/knowledge/publish', methods=('POST',))
    def wiki_questions_publish(cursor, ctx, question_id):
        """Статья + новость с тестом отделу + отметка на вопросе — одной транзакцией."""
        item, error = _load(cursor, ctx, question_id, stage='knowledge')
        if error:
            return error
        data = _body()
        action = data.get('action')
        if action not in ('update', 'create'):
            return _refusal('Выберите статью или раздел для новой', 400, 'WIKI_QUESTION_TARGET')
        content = str(data.get('content') or '')
        if not content.strip():
            return _refusal('Текст статьи пуст', 400, 'WIKI_QUESTION_EMPTY')

        news_title = news_access.normalize_title(data.get('news_title'))
        news_body = ai_knowledge.news_body_html(data.get('news_body'))
        if not news_title or not news_body:
            return _refusal('Заполните заголовок и текст новости', 400,
                            'WIKI_QUESTION_NEWS_EMPTY')
        quiz, problem = news_access.normalize_quiz(data.get('quiz'))
        if problem:
            return _refusal(problem, 400, 'WIKI_QUESTION_QUIZ')

        # ── Новость: адресат и право — ТЕМИ ЖЕ функциями, что у «Новостей». ──
        # Своя формула здесь однажды пропустила бы то, что раздел «Новости»
        # отвергает: например, супервайзера, адресующего чужой отдел.
        if item['department_id'] is None:
            return _refusal('У оператора не указан отдел — новость некому адресовать',
                            409, 'WIKI_QUESTION_NO_DEPARTMENT')
        if not (news_schema.schema_is_ready(cursor) and news_schema.quiz_ready(cursor)):
            return _refusal('Новости ещё разворачиваются — опубликуйте чуть позже',
                            503, 'WIKI_QUESTION_NEWS_NOT_READY')
        author = news_queries.load_viewer_context(cursor, ctx['user_id'])
        is_wiki_admin = news_queries.is_wiki_admin(cursor, ctx['user_id'])
        ceiling = news_access.publish_ceiling(author['otp_role'], is_wiki_admin=is_wiki_admin)
        rules = [{'subject_type': 'department', 'subject_id': item['department_id'],
                  'subject_role': None, 'min_role_level': None}]
        refusal = news_access.audience_refusal(
            rules, ceiling=ceiling,
            departments=news_access.publish_departments(
                author['otp_role'], headed_department_ids=author['headed_department_ids'],
                department_id=author['department_id'], is_wiki_admin=is_wiki_admin),
            subject_departments=news_queries.subject_departments(cursor, rules),
            target_roles={})
        if refusal:
            return _refusal(refusal, 403, 'NEWS_AUDIENCE')

        # ── Статья: права — до первой записи. ──
        article = None
        title = section_id = None
        if action == 'update':
            article_id = _int_or_none(data.get('article_id'))
            if not article_id:
                return _refusal('Выберите статью', 400, 'WIKI_QUESTION_TARGET')
            article, permissions, error = load_with_permissions(cursor, ctx, article_id)
            if error:
                return error
            refusal = _article_refusal(permissions)
            if refusal:
                return refusal
        else:
            title = _clean(data.get('title'))
            if not title:
                return _refusal('Укажите название статьи', 400, 'WIKI_QUESTION_TITLE')
            section_id = _int_or_none(data.get('section_id'))
            if not _section_allowed(cursor, ctx, section_id):
                return _refusal('Нет права выпускать статьи в этом разделе', 403,
                                'WIKI_SECTION_FORBIDDEN')

        cursor.execute('SAVEPOINT wiki_question_publish')
        if article is not None:
            wiki_edit.update_article(cursor, article['id'], {'content': content},
                                     editor_id=ctx['user_id'], session_id=None,
                                     comment='Дополнено ответом на вопрос оператора')
            # Новая редакция — прежние незакрытые ознакомления устаревают, как
            # при правке из редактора.
            wiki_ack.supersede_older_versions(cursor, article['id'])
            target = _article_ref(article)
            audit = ('article.update', {'fields': ['content'], 'title': article['title']})
        else:
            slug = base_slug = _slugify(title)
            suffix = 2
            while not wiki_edit.slug_is_free(cursor, slug):
                slug = '%s-%d' % (base_slug, suffix)
                suffix += 1
            article_id = wiki_edit.create_article(
                cursor, slug=slug, title=title, summary=_clean(data.get('summary'), 2000),
                content=content, article_type='general', section_ids=[section_id],
                tags=[], author_id=ctx['user_id'],
                space_ids=queries.spaces_for_user(cursor, ctx, include_guest=False))
            # Право выпуска у ГОТОВОЙ статьи — последнее слово за эффективными
            # правами, как при создании из редактора: правило раздела выше лишь
            # отсекало заведомо закрытое до вызовов модели.
            _created, permissions, error = load_with_permissions(cursor, ctx, article_id)
            if error or not permissions.get('can_publish'):
                cursor.execute('ROLLBACK TO SAVEPOINT wiki_question_publish')
                return _refusal('Нет права публиковать статьи в этом разделе', 403,
                                'WIKI_FORBIDDEN')
            wiki_edit.update_article(cursor, article_id, {'status': 'published'},
                                     editor_id=ctx['user_id'], session_id=None,
                                     comment='Публикация при создании')
            target = {'id': article_id, 'title': title, 'slug': slug}
            audit = ('article.create', {'title': title, 'slug': slug, 'status': 'published'})

        # ── Новость с тестом отделу оператора. ──
        post_id = news_queries.create_post(
            cursor, title=news_title, body=news_body, author_id=ctx['user_id'],
            author_department_id=author['department_id'], is_mandatory=True,
            confirm_delay_seconds=news_schema.DEFAULT_CONFIRM_DELAY_SECONDS,
            expires_at=None, created_by=ctx['user_id'])
        news_queries.set_audience(cursor, post_id=post_id, rules=rules,
                                  audience_max_role_level=ceiling)
        # Тест — ДО выпуска и в той же транзакции: иначе окно успело бы всплыть у
        # отдела без вопросов, а второй раз оно не всплывёт никогда.
        news_queries.set_quiz(cursor, post_id=post_id, quiz=quiz)
        news_queries.publish_post(cursor, post_id=post_id, audience_max_role_level=ceiling)

        if not wiki_questions.finish_knowledge(cursor, question_id=question_id,
                                               status='published', by=ctx['user_id'],
                                               article_id=target['id'], news_id=post_id):
            # Коллега оформил тот же вопрос раньше — наша статья и новость были
            # бы дублем, поэтому откатываем всё.
            cursor.execute('ROLLBACK TO SAVEPOINT wiki_question_publish')
            return _refusal('Этот вопрос уже оформил коллега', 409, 'WIKI_QUESTION_STAGE')
        wiki_questions.attach_article_source(
            cursor, message_id=item['answer_message_id'], article_id=target['id'],
            title=target['title'], slug=target['slug'])
        cursor.execute('RELEASE SAVEPOINT wiki_question_publish')

        indexed = sync_ai_index(cursor, target['id'])
        action_name, details = audit
        details.update({'ai_index': indexed.get('action'), 'question_id': question_id})
        queries.log_action(cursor, actor_id=ctx['user_id'], action=action_name,
                           entity_type='article', entity_id=target['id'],
                           details=details, ip_address=log_ip())
        return jsonify({'item': _fresh(cursor, question_id), 'article': target,
                        'news_id': post_id})

    @wiki_route('/questions/<int:question_id>/knowledge/skip', methods=('POST',))
    def wiki_questions_skip(cursor, ctx, question_id):
        """«Не для базы знаний» — разовый вопрос, которому место не в статье.

        Постановка ставит запись в базу ПОСЛЕ подтверждения супервайзера, а
        подтверждение подразумевает и отказ. Без этой двери личный вопрос
        («когда моя смена») висел бы в «Ждут статьи» вечно, и корзина перестала
        бы означать «здесь есть работа».
        """
        item, error = _load(cursor, ctx, question_id, stage='knowledge')
        if error:
            return error
        if not wiki_questions.finish_knowledge(cursor, question_id=question_id,
                                               status='skipped', by=ctx['user_id']):
            return _refusal('Этот вопрос уже оформил коллега', 409, 'WIKI_QUESTION_STAGE')
        return jsonify({'item': _fresh(cursor, question_id)})
