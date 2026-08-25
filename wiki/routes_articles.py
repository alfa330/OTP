"""Эндпоинты статей раздела «Вики» (чтение).

Правка статей — этап 4; здесь только читающие пути. Все они без исключения
проходят через articles.visible_article_ids: если хоть один начнёт фильтровать
доступ по-своему, закрытая статья утечёт заголовком через «популярное» или
обратные ссылки.
"""

from flask import jsonify, redirect, request

from . import access as wiki_access
from . import articles as wiki_articles
from . import guests as wiki_guests
from . import migration as wiki_migration
from . import parks as wiki_parks
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
            # Остаток работы по переносу из внешней вики. Здесь, а не отдельным
            # запросом: периметр уже посчитан, и второй его расчёт ради одного
            # счётчика был бы платой ни за что. Из этого числа интерфейс решает,
            # показывать ли половину «Перенос», поэтому периметр обязан быть тот
            # же, что у очереди за ней.
            "migration": wiki_migration.totals_for(cursor, visible),
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

        # Запрос — в журнал. Под савпоинтом: поиск и запись идут в ОДНОЙ
        # транзакции, и падение INSERT'а иначе превратило бы рабочую выдачу в
        # 500 «Внутренняя ошибка раздела». Журнал — приставка к поиску, и цена
        # его поломки не должна быть выше цены самой поломки.
        #
        # Размер периметра пишется рядом с числом находок намеренно: ноль
        # находок при периметре в три статьи и при периметре в тридцать шесть —
        # разные диагнозы, и лечатся они по-разному (написать статью против
        # выдать доступ).
        cursor.execute('SAVEPOINT wiki_search_log')
        try:
            wiki_search.log_query(
                cursor,
                user_id=ctx['user_id'],
                query=query,
                results_count=len(items),
                perimeter_size=len(visible),
                department_id=ctx.get('department_id'),
                space_id=_int_or_none(request.args.get('space_id')),
            )
        except Exception:
            cursor.execute('ROLLBACK TO SAVEPOINT wiki_search_log')
        else:
            cursor.execute('RELEASE SAVEPOINT wiki_search_log')

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

        # Гостевая выдача: до какого срока статья открыта этому человеку.
        # Считается ДО прав, потому что она и есть основание читать, когда
        # других оснований нет: правил на человека не выписано, автором он не
        # является, и без этой строки resolve_article_permissions вернул бы
        # can_read=False на статье, которую витрина ему уже показала (гость
        # попадает в _VISIBLE_ARTICLES_SQL своей веткой). Расхождение витрины и
        # прав читалось бы как «статья открылась пустой».
        guest_until = wiki_guests.article_grant_expiry(
            cursor, ctx['user_id'], article['id'], article['section_ids'])

        permissions = wiki_access.resolve_article_permissions(
            capabilities=ctx['capabilities'],
            visibility_mode=article['visibility_mode'],
            strict_mode=article['strict_mode'],
            section_rules=flat_section_rules,
            article_rules=article_rules,
            otp_role=ctx['otp_role'],
            is_article_owner=(article['author_id'] == ctx['user_id']
                              or article['owner_user_id'] == ctx['user_id']),
            guest_allows_read=bool(guest_until),
        )

        # Обход строгого режима обязан попасть в журнал: это и есть смысл режима.
        if permissions.get('_bypassed_restriction') and article['strict_mode']:
            queries.log_action(
                cursor, actor_id=ctx['user_id'], action='article.strict_bypass',
                entity_type='article', entity_id=article['id'],
                details={'slug': article['slug'], 'reason': permissions['_reason']},
                ip_address=log_ip())

        wiki_articles.register_view(cursor, article['id'], ctx['user_id'], log_ip(),
                                    department_id=ctx.get('department_id'),
                                    role=ctx.get('otp_role'))

        article['permissions'] = wiki_access.permissions_only(permissions)
        article['why'] = permissions['_reason']
        # Срок кладём в ответ, только если гостевая выдача и ЕСТЬ основание
        # читать (reason сказал именно это). У человека, которому статья открыта
        # и правилом тоже, подпись «доступ до 5 сентября» была бы неправдой:
        # пятого он её увидит как обычно, и предупреждение об исчезновении
        # доступа читалось бы как сбой.
        article['guest_access'] = (
            {'expires_at': guest_until.isoformat(),
             'days_left': wiki_guests.days_left(guest_until, wiki_guests.now_almaty())}
            if guest_until and permissions['_reason'] == 'гостевой доступ' else None)
        article['backlinks'] = wiki_articles.backlinks(cursor, article['id'], visible)
        # «Связанные материалы» считаются из ТЕЛА, которое уже здесь, а не из
        # таблицы связей: блок обязан совпадать с тем, что человек видит в
        # тексте. Отдельного роута нет намеренно — периметр в этом обработчике
        # уже посчитан, и вторая дверь стоила бы второго его расчёта и второго
        # места, где правило «404 вместо 403» пришлось бы повторить руками.
        article['related'] = wiki_articles.related_articles(
            cursor, article.get('content'), article['id'], visible)
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
            #
            # Кроме одного случая: файл может быть картинкой СПРАВОЧНИКА —
            # логотипом парка. Статьи у него нет и не будет, а видеть его
            # обязаны все, кому видно само пространство: иначе аватарка парка
            # открывалась бы у одного загрузившего, а у остальных в рельсе
            # витрины стояла бы битая картинка. Границей служит то же
            # пространство, что и у самого справочника.
            spaces = wiki_parks.logo_space_ids(cursor, record['id'])
            # include_guest=False: логотип парка — часть СПРАВОЧНИКА, а его
            # гостю не открывают (routes_structure.request_space). Границей
            # служит то же пространство и то же правило, что у справочника.
            if not spaces or not (spaces & set(queries.spaces_for_user(
                    cursor, ctx, include_guest=False))):
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
