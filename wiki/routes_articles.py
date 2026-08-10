"""Эндпоинты статей раздела «Вики» (чтение).

Правка статей — этап 4; здесь только читающие пути. Все они без исключения
проходят через articles.visible_article_ids: если хоть один начнёт фильтровать
доступ по-своему, закрытая статья утечёт заголовком через «популярное» или
обратные ссылки.
"""

from flask import jsonify, redirect, request

from . import access as wiki_access
from . import articles as wiki_articles
from . import queries
from . import schema as wiki_schema
from . import search as wiki_search


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def register(bp, wiki_route, db, log_ip, gcs):
    """gcs — словарь с ключами signed_url и bucket_name (внедряются из bot_schedule2)."""

    def _perimeter(cursor, ctx, *, master_key=True):
        """Субъекты, разрешённые разделы и видимые статьи — за один проход.

        Собрано в одну функцию, чтобы ни один эндпоинт не мог случайно
        посчитать периметр иначе, чем остальные.

        master_key=False — личный периметр без мастер-ключа администратора
        (см. шапку wiki/articles.py).
        """
        subjects = wiki_access.collect_subjects(
            user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            department_id=ctx['department_id'],
            headed_department_ids=ctx['headed_department_ids'],
            direction_id=ctx['direction_id'], group_ids=ctx['group_ids'],
            wiki_role_ids=[r.get('id') for r in ctx['wiki_roles']],
        )
        sections = queries.allowed_section_ids(cursor, ctx, subjects,
                                               master_key=master_key)
        visible = wiki_articles.visible_article_ids(cursor, ctx, subjects, sections,
                                                    master_key=master_key)
        return subjects, sections, visible

    def _browse(cursor, ctx):
        """Периметр ВИТРИНЫ: список, поиск, подсказки, главная раздела.

        По умолчанию личный — человек видит то, к чему имеет отношение.
        Администратор доступов может попросить показать всё содержимое портала
        (?scope=all, переключатель в интерфейсе): без этого он не найдёт статью,
        которую его же попросили починить. Просьба явная и от постороннего
        ничего не открывает — способность проверяется здесь же.
        """
        wants_all = (str(request.args.get('scope') or '').strip().lower() == 'all'
                     and bool(ctx['capabilities'].get('can_manage_access')))
        return _perimeter(cursor, ctx, master_key=wants_all)

    # ── Список ───────────────────────────────────────────────────────────
    @wiki_route('/articles')
    def wiki_articles_list(cursor, ctx):
        _subjects, _sections, visible = _browse(cursor, ctx)
        limit = min(max(_int_or_none(request.args.get('limit')) or 50, 1), 200)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)
        items = wiki_articles.list_articles(
            cursor, visible,
            section_id=_int_or_none(request.args.get('section_id')),
            status=(request.args.get('status') or None),
            query=(request.args.get('q') or None),
            limit=limit, offset=offset,
        )
        return jsonify({"items": items, "total_visible": len(visible)})

    # ── Поиск ────────────────────────────────────────────────────────────
    @wiki_route('/search')
    def wiki_search_articles(cursor, ctx):
        """Полнотекстовый поиск в границах периметра.

        Выдача пересекается с visible_article_ids так же, как список: подсказки
        поиска — это читающий путь, и без фильтра закрытая статья утекла бы
        заголовком и сниппетом.
        """
        query = (request.args.get('q') or '').strip()
        if len(query) < 2:
            return jsonify({"items": [], "query": query})

        _subjects, _sections, visible = _browse(cursor, ctx)
        limit = min(max(_int_or_none(request.args.get('limit')) or 20, 1), 50)
        items = wiki_search.search(
            cursor, visible, query,
            section_id=_int_or_none(request.args.get('section_id')),
            limit=limit,
            with_trigram=wiki_schema.trigram_available(cursor),
        )
        return jsonify({"items": items, "query": query})

    @wiki_route('/suggest')
    def wiki_suggest(cursor, ctx):
        """Подсказки по мере ввода — с двух символов, как в оригинале."""
        query = (request.args.get('q') or '').strip()
        if len(query) < 2:
            return jsonify({"items": []})
        _subjects, _sections, visible = _browse(cursor, ctx)
        return jsonify({"items": wiki_search.suggest(
            cursor, visible, query,
            with_trigram=wiki_schema.trigram_available(cursor))})

    # ── Главная раздела ──────────────────────────────────────────────────
    @wiki_route('/home')
    def wiki_home(cursor, ctx):
        """Недавнее, популярное и избранное — строго в границах периметра."""
        _subjects, _sections, visible = _browse(cursor, ctx)
        return jsonify(wiki_articles.recent_and_popular(cursor, visible, ctx['user_id']))

    # ── Статья ───────────────────────────────────────────────────────────
    @wiki_route('/articles/<slug>')
    def wiki_article_by_slug(cursor, ctx, slug):
        subjects, sections, visible = _perimeter(cursor, ctx)

        article = wiki_articles.get_article(cursor, slug=slug)
        if not article:
            return jsonify({"error": "Статья не найдена"}), 404

        # 404, а не 403: сообщение «нет доступа» само по себе раскрывает, что
        # статья с таким адресом существует. Для строгого режима это важно.
        if article['id'] not in visible:
            return jsonify({"error": "Статья не найдена"}), 404

        section_rules = queries.section_rules_for_user(
            cursor, [s for s in article['section_ids'] if s in sections],
            subjects, ctx['user_id'])
        flat_section_rules = [rule for rules in section_rules.values() for rule in rules]
        article_rules = wiki_articles.article_rules_for_user(
            cursor, [article['id']], subjects, ctx['user_id']).get(article['id'], [])

        permissions = wiki_access.resolve_article_permissions(
            capabilities=ctx['capabilities'],
            visibility_mode=article['visibility_mode'],
            strict_mode=article['strict_mode'],
            section_rules=flat_section_rules,
            article_rules=article_rules,
            otp_role=ctx['otp_role'],
            is_article_owner=(article['author_id'] == ctx['user_id']
                              or article['owner_user_id'] == ctx['user_id']),
        )

        # Обход строгого режима обязан попасть в журнал: это и есть смысл режима.
        if permissions.get('_bypassed_restriction') and article['strict_mode']:
            queries.log_action(
                cursor, actor_id=ctx['user_id'], action='article.strict_bypass',
                entity_type='article', entity_id=article['id'],
                details={'slug': article['slug'], 'reason': permissions['_reason']},
                ip_address=log_ip())

        wiki_articles.register_view(cursor, article['id'], ctx['user_id'], log_ip())

        article['permissions'] = wiki_access.permissions_only(permissions)
        article['why'] = permissions['_reason']
        article['backlinks'] = wiki_articles.backlinks(cursor, article['id'], visible)
        return jsonify(article)

    # ── Избранное ────────────────────────────────────────────────────────
    @wiki_route('/articles/<int:article_id>/favorite', methods=('POST', 'DELETE'))
    def wiki_article_favorite(cursor, ctx, article_id):
        _subjects, _sections, visible = _perimeter(cursor, ctx)
        if article_id not in visible:
            return jsonify({"error": "Статья не найдена"}), 404
        wiki_articles.set_favorite(cursor, ctx['user_id'], article_id,
                                   request.method == 'POST')
        return jsonify({"status": "ok"})

    # ── Файлы ────────────────────────────────────────────────────────────
    @wiki_route('/file/<file_id>')
    def wiki_file(cursor, ctx, file_id):
        """Стабильная ссылка на файл статьи.

        Почему не signed URL прямо в HTML, как это делает LMS: подписанная
        ссылка живёт 240 минут, а тело статьи хранится годами — все картинки
        протухли бы. Здесь адрес постоянный, а подпись выдаётся на каждый
        запрос, и только после проверки доступа к статье-владельцу.
        """
        record = wiki_articles.get_file(cursor, file_id)
        if not record:
            return jsonify({"error": "Файл не найден"}), 404

        if record['article_id']:
            _subjects, _sections, visible = _perimeter(cursor, ctx)
            if record['article_id'] not in visible:
                return jsonify({"error": "Файл не найден"}), 404
        elif record.get('uploaded_by') != ctx['user_id']:
            # Файл ещё не привязан к статье (загружен в редактор или пришёл из
            # импорта). Пока привязки нет, проверять нечего — значит доступ
            # только у того, кто его загрузил.
            return jsonify({"error": "Файл не найден"}), 404

        url = gcs['signed_url'](
            record['bucket'], record['blob_path'],
            expires_minutes=60,
            response_disposition='inline',
            response_type=record['content_type'] or None,
        )
        if not url:
            return jsonify({"error": "Файл временно недоступен"}), 503
        return redirect(url, code=302)
