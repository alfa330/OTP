"""Эндпоинты статей раздела «Вики» (чтение).

Правка статей — этап 4; здесь только читающие пути. Все они без исключения
проходят через articles.visible_article_ids: если хоть один начнёт фильтровать
доступ по-своему, закрытая статья утечёт заголовком через «популярное» или
обратные ссылки.
"""

from flask import jsonify, redirect, request

from . import access as wiki_access
from . import articles as wiki_articles
from . import perimeter as wiki_perimeter
from . import queries
from . import schema as wiki_schema
from . import search as wiki_search
from . import structure


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bucket():
    """Корзина витрины из запроса: «Статьи», «Черновики» или «Архив».

    Неизвестное значение — как отсутствующее: витрина отдаёт всё, что видно.
    Молчаливый отказ на опечатку в адресе здесь хуже широкой выдачи — периметр
    всё равно считается отдельно и ничего лишнего не пропустит.
    """
    value = (request.args.get('bucket') or '').strip()
    return wiki_schema.ARTICLE_BUCKETS.get(value)


def _section_filter():
    """Раздел из запроса: (section_id, только_без_раздела).

    ?section_id=none — статьи, не привязанные ни к одному разделу. Отдельное
    слово, а не 0 и не пустая строка: 0 неотличим от «не передали», а плитка
    «Без раздела» в каталоге обязана открываться.
    """
    raw = (request.args.get('section_id') or '').strip().lower()
    if raw == 'none':
        return None, True
    return _int_or_none(raw), False


def _article_type():
    """Тип статьи из запроса — только из белого списка.

    Неизвестное значение гасим в None, а не в 400: фильтр витрины — украшение
    выдачи, и опечатка в адресе не повод отказать человеку в списке статей.
    """
    value = (request.args.get('article_type') or '').strip()
    return value if value in wiki_schema.ARTICLE_TYPES else None


def register(bp, wiki_route, db, log_ip, gcs):
    """gcs — словарь с ключами signed_url и bucket_name (внедряются из bot_schedule2)."""

    # Цепочка «субъекты → разделы → статьи» живёт в wiki/perimeter.py: её же
    # считает ИИ-помощник, и второй реализации быть не должно — именно на таком
    # раздвоении сломалась исходная вика (см. шапку wiki/perimeter.py).
    _perimeter = wiki_perimeter.read_perimeter

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
        # Пространство, выбранное переключателем в шапке. Сужает витрину до
        # одной вики: у супер-админа их несколько, а на экране живёт одна.
        # Проверять принадлежность параметра не нужно — сужаем УЖЕ посчитанный
        # периметр, и чужого пространства в нём нет по построению.
        return _perimeter(cursor, ctx, master_key=wants_all,
                          space_id=_int_or_none(request.args.get('space_id')))

    # ── Список ───────────────────────────────────────────────────────────
    @wiki_route('/articles')
    def wiki_articles_list(cursor, ctx):
        subjects, sections, visible = _browse(cursor, ctx)
        limit = min(max(_int_or_none(request.args.get('limit')) or 50, 1), 200)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)
        section_id, orphans_only = _section_filter()
        items = wiki_articles.list_articles(
            cursor, visible,
            section_id=section_id,
            orphans_only=orphans_only,
            status=(request.args.get('status') or None),
            statuses=_bucket(),
            article_type=_article_type(),
            query=(request.args.get('q') or None),
            limit=limit, offset=offset,
        )
        # Что человек вправе СДЕЛАТЬ с каждой статьёй — в самой выдаче: меню
        # действий в каталоге иначе предлагало бы «Редактировать» на статье,
        # которую сервер тут же откажется править. Роль такого ответа не даёт —
        # у статьи есть свои правила доступа, и знать про них может только тот,
        # кто их считает. Расчёт стоит два запроса на весь список, независимо от
        # его длины (permissions_for_articles).
        rights = wiki_articles.permissions_for_articles(
            cursor, ctx, items, subjects, sections, queries.section_rules_for_user)
        for item in items:
            item['permissions'] = wiki_access.permissions_only(rights[item['id']])
        return jsonify({"items": items, "total_visible": len(visible)})

    # ── Каталог по разделам ──────────────────────────────────────────────
    @wiki_route('/catalog')
    def wiki_catalog(cursor, ctx):
        """Плитки вкладки «Статьи»: разделы периметра и число статей в каждом.

        Периметр тот же ЛИЧНЫЙ, что у списка и поиска (_browse): плитка обязана
        открываться тем же содержимым, которое отдаст /articles по этому разделу.
        Считай их разные периметры — и раздел показывал бы «12 статей», а внутри
        лежало три.

        Архивные разделы и пространства отсеяны: архивируют обычно дубль с тем
        же именем, и в сетке плиток он неотличим от живого (см. sectionPicker.js
        во фронте — там та же причина).

        totals отдаём отдельно от суммы по разделам: статья лежит сразу в
        нескольких разделах, и сумма плиток её посчитала бы дважды. Именно
        totals стоит на счётчиках главной, поэтому «29 статей» и список за
        плиткой берутся из одного числа.
        """
        # Каталог — инструмент того, кто ВЕДЁТ базу знаний: он выкладывает разом
        # черновики, архив и объём каждого раздела. По решению владельца
        # читателю его не показывают, и гейт стоит здесь, а не только в меню:
        # гард во фронте отсекает вкладку, но не запрос по прямому адресу.
        caps = ctx['capabilities']
        if not (caps.get('can_create') or caps.get('can_edit') or caps.get('can_publish')):
            return jsonify({
                "error": "Каталог статей доступен редакторам вики",
                "code": "WIKI_EDITOR_ONLY",
            }), 403

        _subjects, allowed, visible = _browse(cursor, ctx)
        counts = wiki_articles.catalog_counts(cursor, visible)

        sections = [
            {
                'id': section['id'],
                'space_id': section['space_id'],
                'parent_section_id': section['parent_section_id'],
                'name': section['name'],
                'icon': section['icon'],
                'department_name': section['department_name'],
                'counts': counts['sections'].get(section['id'],
                                                 {k: 0 for k in wiki_schema.ARTICLE_BUCKETS}),
            }
            for section in structure.list_sections(cursor, include_archived=False)
            if section['id'] in allowed
        ]
        used_spaces = {s['space_id'] for s in sections}
        spaces = [
            {'id': sp['id'], 'name': sp['name'], 'icon': sp['icon']}
            for sp in structure.list_spaces(cursor, include_archived=False)
            if sp['id'] in used_spaces
        ]
        return jsonify({
            "spaces": spaces,
            "sections": sections,
            # Наследие импорта: статья без единого раздела. Плитку рисуем только
            # когда такие статьи есть — пустая строка «Без раздела» в каталоге
            # была бы вопросом без ответа.
            "orphans": counts['orphans'],
            "totals": counts['totals'],
            "sections_total": len(sections),
        })

    # ── Тренажёры ────────────────────────────────────────────────────────
    @wiki_route('/trainers')
    def wiki_trainers(cursor, ctx):
        """Где какой тренажёр вставлен — для вкладки «Тренажёры».

        Сами сценарии сервер не знает и знать не должен: они собраны во фронте
        (src/components/wiki/trainers), потому что тренажёр — это экраны и
        реплики помощника, а не данные. Сервер отвечает на единственный вопрос,
        который во фронте не выяснить: в каких статьях кнопка уже стоит.

        Гейт тот же, что у каталога, и по той же причине: вкладка принадлежит
        тому, кто ведёт базу знаний, а гард во фронте не защищает прямой запрос.
        """
        caps = ctx['capabilities']
        if not (caps.get('can_create') or caps.get('can_edit') or caps.get('can_publish')):
            return jsonify({
                "error": "Тренажёры доступны редакторам вики",
                "code": "WIKI_EDITOR_ONLY",
            }), 403

        _subjects, _allowed, visible = _browse(cursor, ctx)
        return jsonify({"usages": wiki_articles.trainer_usages(cursor, visible)})

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
            article_type=_article_type(),
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
        article['is_favorite'] = wiki_articles.is_favorite(
            cursor, ctx['user_id'], article['id'])
        return jsonify(article)

    # ── Избранное ────────────────────────────────────────────────────────
    @wiki_route('/articles/<int:article_id>/favorite', methods=('POST', 'DELETE'))
    def wiki_article_favorite(cursor, ctx, article_id):
        _subjects, _sections, visible = _perimeter(cursor, ctx)
        if article_id not in visible:
            return jsonify({"error": "Статья не найдена"}), 404
        favorite = request.method == 'POST'
        wiki_articles.set_favorite(cursor, ctx['user_id'], article_id, favorite)
        # Возвращаем состояние, а не голое «ok»: интерфейс рисует звезду по
        # ответу сервера, и договариваться о нём догадками не должен.
        return jsonify({"status": "ok", "is_favorite": favorite})

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
