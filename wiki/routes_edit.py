"""Эндпоинты правки статей: создание, обновление, версии, права на статью.

Каждая проверка идёт по ЭФФЕКТИВНЫМ правам на конкретную статью, а не по роли.
В оригинальной вике удаление гейтилось только `requireRole(['Admin','Editor'])`,
причём «Editor» разворачивался в восемь ролей — то есть любой супервайзер мог
снести любую статью, включая чужого отдела.
"""

from flask import jsonify, request

from . import access as wiki_access
from . import articles as wiki_articles
from . import edit as wiki_edit
from . import queries
from .ai import embed as ai_embed
from .ai import index as ai_index
from .routes_structure import PERMISSION_FIELDS, _clean, _int_or_none, _slugify

ARTICLE_TYPES = ('general', 'job_description', 'regulation', 'instruction', 'tool_description')
ARTICLE_STATUSES = ('draft', 'on_approval', 'published', 'requires_verification',
                    'archived', 'expired')


def _body():
    return request.get_json(silent=True) or {}


# Сколько кусков досчитываем векторами прямо в обработчике сохранения. Предел
# есть, и он важен: эмбеддинги считает внешний сервис, а обработчик всё это
# время держит соединение из пула на 40, общего с SSE аукциона и колокола.
# Средняя статья корпуса — 8 кусков, то есть один пакетный вызов на секунду;
# остаток (после массового импорта) досчитает следующее сохранение или ручной
# /ai/embed. Лучше слегка отстающий индекс, чем занятый пул.
_EMBED_ON_SAVE = 32


def register(bp, wiki_route, db, log_ip, session_id_provider):
    """session_id_provider — _current_session_id_from_access_token из bot_schedule2."""

    def _sync_ai_index(cursor, article_id):
        """Обновить индекс помощника под только что сохранённую статью.

        До этого индекс не обновлялся НИКОГДА и наполнялся вручную: владелец
        опубликовал статью «Реестр акций таксопарка iGroup» — она попала в
        периметр помощника, но кусков у неё было ноль, и помощник её не видел.
        Со стороны это выглядит как «ИИ игнорирует статью», хотя дело в том, что
        её просто нет в индексе.

        Ошибка индексации НЕ роняет сохранение: статья уже записана, и потерять
        правку из-за недоступного эмбеддера было бы худшим обменом. Помощник
        подхватит её при следующем сохранении или ручной пересборке.
        """
        result = {'action': 'skipped'}
        try:
            result = ai_index.reindex_article(cursor, article_id)
        except Exception as error:                       # noqa: BLE001
            return {'action': 'failed', 'error': str(error)[:200]}
        if result.get('action') != 'indexed':
            return result
        try:
            result['embedded'] = ai_embed.embed_missing(
                cursor, limit=_EMBED_ON_SAVE).get('embedded', 0)
        except Exception as error:                       # noqa: BLE001
            # Без векторов помощник ищет лексикой — хуже, но не слепой.
            result['embed_error'] = str(error)[:200]
        return result

    def _session_id():
        try:
            return session_id_provider() if session_id_provider else None
        except Exception:
            return None

    def _perimeter(cursor, ctx):
        subjects = wiki_access.collect_subjects(
            user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            department_id=ctx['department_id'],
            headed_department_ids=ctx['headed_department_ids'],
            direction_id=ctx['direction_id'], group_ids=ctx['group_ids'],
            wiki_role_ids=[r.get('id') for r in ctx['wiki_roles']],
        )
        sections = queries.allowed_section_ids(cursor, ctx, subjects)
        visible = wiki_articles.visible_article_ids(cursor, ctx, subjects, sections)
        return subjects, sections, visible

    def _load_with_permissions(cursor, ctx, article_id):
        """Статья + эффективные права. Возвращает (article, permissions, error)."""
        subjects, sections, visible = _perimeter(cursor, ctx)
        article = wiki_articles.get_article(cursor, article_id=article_id)
        if not article or article['id'] not in visible:
            # 404, а не 403: «нет доступа» само по себе раскрывает существование.
            return None, None, (jsonify({"error": "Статья не найдена"}), 404)
        permissions = wiki_articles.effective_permissions(
            cursor, ctx, article, subjects, sections, queries.section_rules_for_user)
        return article, permissions, None

    # ── Создание ─────────────────────────────────────────────────────────
    @wiki_route('/articles', methods=('POST',), capability='can_create')
    def wiki_article_create(cursor, ctx):
        data = _body()
        title = _clean(data.get('title'))
        section_ids = [s for s in (data.get('section_ids') or []) if _int_or_none(s)]
        if not title:
            return jsonify({"error": "Укажите название статьи"}), 400

        subjects, sections, _visible = _perimeter(cursor, ctx)
        # Создавать можно только в разделах, где есть право на создание.
        if section_ids:
            rules = queries.section_rules_for_user(
                cursor, [int(s) for s in section_ids], subjects, ctx['user_id'])
            allowed_here = any(
                rule.get('can_create')
                for section_rules in rules.values() for rule in section_rules
            )
            if not allowed_here and not ctx['capabilities'].get('can_manage_access'):
                return jsonify({
                    "error": "Нет права создавать статьи в выбранном разделе",
                    "code": "WIKI_SECTION_FORBIDDEN",
                }), 403

        slug = _clean(data.get('slug'), 200) or _slugify(title)
        base_slug, suffix = slug, 2
        while not wiki_edit.slug_is_free(cursor, slug):
            slug = '%s-%d' % (base_slug, suffix)
            suffix += 1

        article_type = data.get('article_type')
        if article_type not in ARTICLE_TYPES:
            article_type = 'general'

        article_id = wiki_edit.create_article(
            cursor, slug=slug, title=title,
            summary=_clean(data.get('summary'), 2000),
            content=data.get('content') or '',
            article_type=article_type,
            section_ids=section_ids, tags=data.get('tags') or [],
            author_id=ctx['user_id'],
            visibility_mode=('restricted' if data.get('visibility_mode') == 'restricted'
                             else 'inherit'),
            strict_mode=bool(data.get('strict_mode')),
            # «Поддержка ИИ» приходит от редактора как ai_support, а в базе живёт
            # обратным флагом-рубильником. Инверсия делается здесь, в одном месте:
            # положительная формулировка нужна человеку («ИИ помогает с этой
            # статьёй»), отрицательная — периметру, где по умолчанию разрешено.
            ai_opt_out=(not data['ai_support']) if 'ai_support' in data
                       else bool(data.get('ai_opt_out')),
        )
        # СТАТУС ПРИ СОЗДАНИИ. Раньше он игнорировался молча: create_article
        # всегда пишет 'draft', а кнопка «Опубликовать» в редакторе присылала
        # status='published' — статья оставалась черновиком, но интерфейс
        # рапортовал «Статья опубликована». Ложный успех хуже отказа: человек
        # уходит уверенным, что дело сделано, и узнаёт правду случайно.
        #
        # Публикуем вторым шагом, а не параметром INSERT, ради права: can_publish
        # считается по ЭФФЕКТИВНЫМ правам на конкретную статью, а их нельзя
        # посчитать, пока статьи нет.
        status = data.get('status')
        if status == 'published':
            _article, permissions, error = _load_with_permissions(cursor, ctx, article_id)
            if error:
                return error
            if permissions.get('can_publish'):
                wiki_edit.update_article(
                    cursor, article_id, {'status': 'published'},
                    editor_id=ctx['user_id'], session_id=_session_id(),
                    comment='Публикация при создании')
                status = 'published'
            else:
                status = 'draft'

        indexed = _sync_ai_index(cursor, article_id)

        queries.log_action(cursor, actor_id=ctx['user_id'], action='article.create',
                           entity_type='article', entity_id=article_id,
                           details={'title': title, 'slug': slug,
                                    'status': status or 'draft',
                                    'ai_index': indexed.get('action')},
                           ip_address=log_ip())
        # Статус возвращается ВСЕГДА: интерфейс должен говорить о том, что
        # получилось, а не о том, что просили.
        return jsonify({"id": article_id, "slug": slug,
                        "status": status or 'draft',
                        "ai_index": indexed}), 201

    # ── Обновление и архивирование ───────────────────────────────────────
    @wiki_route('/articles/<int:article_id>', methods=('PATCH', 'DELETE'))
    def wiki_article_edit(cursor, ctx, article_id):
        article, permissions, error = _load_with_permissions(cursor, ctx, article_id)
        if error:
            return error

        if request.method == 'DELETE':
            if not permissions.get('can_delete'):
                return jsonify({
                    "error": "Нет права удалять эту статью",
                    "code": "WIKI_FORBIDDEN", "required": "can_delete",
                }), 403
            wiki_edit.delete_article(cursor, article_id)
            # Снятая с публикации статья не должна кормить ответы помощника.
            _sync_ai_index(cursor, article_id)
            queries.log_action(cursor, actor_id=ctx['user_id'], action='article.archive',
                               entity_type='article', entity_id=article_id,
                               details={'title': article['title']}, ip_address=log_ip())
            return jsonify({"status": "archived"})

        if not permissions.get('can_edit'):
            return jsonify({
                "error": "Нет права править эту статью",
                "code": "WIKI_FORBIDDEN", "required": "can_edit",
            }), 403

        data = _body()
        fields = {}
        for key in ('title', 'summary'):
            if key in data:
                fields[key] = _clean(data[key], 2000 if key == 'summary' else 255)
        if 'content' in data:
            fields['content'] = data['content'] or ''
        if data.get('article_type') in ARTICLE_TYPES:
            fields['article_type'] = data['article_type']
        # Поддержка ИИ — право того, кто правит статью, а не администратора
        # доступов: она не расширяет чтение, а только решает, уходит ли текст в
        # индекс помощника. По умолчанию она и так включена, так что запрет здесь
        # означал бы «выключить нельзя», а это ровно наоборот тому, зачем
        # рубильник заводился.
        if 'ai_support' in data:
            fields['ai_opt_out'] = not data['ai_support']
        elif 'ai_opt_out' in data:
            fields['ai_opt_out'] = bool(data['ai_opt_out'])
        if 'owner_user_id' in data:
            fields['owner_user_id'] = _int_or_none(data['owner_user_id'])

        # Публикация — отдельное право, а не частный случай правки.
        if data.get('status') in ARTICLE_STATUSES:
            new_status = data['status']
            if new_status == 'published' and not permissions.get('can_publish'):
                return jsonify({
                    "error": "Нет права публиковать эту статью",
                    "code": "WIKI_FORBIDDEN", "required": "can_publish",
                }), 403
            fields['status'] = new_status

        # Режим видимости и строгий режим меняет только тот, кто раздаёт доступы.
        for key in ('visibility_mode', 'strict_mode'):
            if key in data:
                if not ctx['capabilities'].get('can_manage_access'):
                    return jsonify({
                        "error": "Режим доступа статьи меняет администратор доступов",
                        "code": "WIKI_FORBIDDEN", "required": "can_manage_access",
                    }), 403
                fields[key] = (data[key] == 'restricted' if key == 'visibility_mode'
                               else bool(data[key]))

        changed = wiki_edit.update_article(
            cursor, article_id, fields, editor_id=ctx['user_id'],
            session_id=_session_id(), comment=_clean(data.get('comment'), 500))

        if 'section_ids' in data:
            wiki_edit.set_sections(cursor, article_id, data['section_ids'])
            changed = True
        if 'tags' in data:
            wiki_edit.set_tags(cursor, article_id, data['tags'])
            changed = True

        if not changed:
            return jsonify({"error": "Нечего обновлять"}), 400

        # Вышла новая версия — прежние незакрытые назначения устаревают.
        # Подтверждённые не трогаем: они свидетельство, что человек читал
        # именно ту редакцию.
        if 'content' in fields:
            from . import ack as wiki_ack
            wiki_ack.supersede_older_versions(cursor, article_id)

        # Индекс трогаем на ЛЮБОЙ правке: текст меняет куски, статус и рубильник
        # решают, быть им вообще, а разделы — попадает ли статья под отказ раздела.
        indexed = _sync_ai_index(cursor, article_id)

        queries.log_action(cursor, actor_id=ctx['user_id'], action='article.update',
                           entity_type='article', entity_id=article_id,
                           details={'fields': sorted(fields.keys()),
                                    'title': article['title'],
                                    'ai_index': indexed.get('action')},
                           ip_address=log_ip())
        return jsonify({"status": "ok", "ai_index": indexed})

    # ── Версии ───────────────────────────────────────────────────────────
    @wiki_route('/articles/<int:article_id>/versions')
    def wiki_article_versions(cursor, ctx, article_id):
        _article, _permissions, error = _load_with_permissions(cursor, ctx, article_id)
        if error:
            return error
        return jsonify({"items": wiki_edit.list_versions(cursor, article_id)})

    @wiki_route('/articles/<int:article_id>/versions/<int:version_id>')
    def wiki_article_version(cursor, ctx, article_id, version_id):
        _article, _permissions, error = _load_with_permissions(cursor, ctx, article_id)
        if error:
            return error
        version = wiki_edit.get_version(cursor, article_id, version_id)
        if not version:
            return jsonify({"error": "Версия не найдена"}), 404
        return jsonify(version)

    @wiki_route('/articles/<int:article_id>/versions/<int:version_id>/restore',
                methods=('POST',))
    def wiki_article_restore(cursor, ctx, article_id, version_id):
        _article, permissions, error = _load_with_permissions(cursor, ctx, article_id)
        if error:
            return error
        if not permissions.get('can_edit'):
            return jsonify({"error": "Нет права править эту статью",
                            "code": "WIKI_FORBIDDEN"}), 403
        if not wiki_edit.restore_version(cursor, article_id, version_id,
                                         editor_id=ctx['user_id'],
                                         session_id=_session_id()):
            return jsonify({"error": "Версия не найдена"}), 404
        queries.log_action(cursor, actor_id=ctx['user_id'], action='article.restore',
                           entity_type='article', entity_id=article_id,
                           details={'version_id': version_id}, ip_address=log_ip())
        return jsonify({"status": "restored"})

    # ── Права на конкретную статью ───────────────────────────────────────
    @wiki_route('/articles/<int:article_id>/access-rules', methods=('GET', 'POST'),
                capability='can_manage_access')
    def wiki_article_rules(cursor, ctx, article_id):
        if request.method == 'GET':
            return jsonify({"items": wiki_edit.list_article_rules(cursor, article_id)})

        data = _body()
        subject_type = data.get('subject_type')
        if subject_type not in ('department', 'direction', 'group', 'otp_role',
                                'wiki_role', 'user'):
            return jsonify({"error": "Не выбран субъект правила"}), 400

        subject_id, subject_role = None, None
        if subject_type == 'otp_role':
            subject_role = str(data.get('subject_role') or '').strip()
            if subject_role not in wiki_access.ROLE_LEVELS and subject_role != 'supervisor':
                return jsonify({"error": "Неизвестная роль"}), 400
        else:
            subject_id = _int_or_none(data.get('subject_id'))
            if not subject_id:
                return jsonify({"error": "Не выбран субъект"}), 400

        mode = 'deny' if data.get('mode') == 'deny' else 'grant'
        permissions = {key: bool(data.get(key)) for key in PERMISSION_FIELDS}
        if mode == 'deny':
            # Запрет читать закрывает и всё остальное — править невидимое нельзя.
            if permissions['can_read']:
                permissions = {key: True for key in PERMISSION_FIELDS}
        elif any(permissions[k] for k in PERMISSION_FIELDS[1:]):
            permissions['can_read'] = True

        rule_id = wiki_edit.upsert_article_rule(
            cursor, article_id=article_id, subject_type=subject_type,
            subject_id=subject_id, subject_role=subject_role, mode=mode,
            permissions=permissions, created_by=ctx['user_id'])
        queries.log_action(cursor, actor_id=ctx['user_id'],
                           action='article_rule.%s' % mode,
                           entity_type='article', entity_id=article_id,
                           target_user_id=subject_id if subject_type == 'user' else None,
                           details={'rule_id': rule_id, 'subject_type': subject_type,
                                    'subject_id': subject_id,
                                    'subject_role': subject_role, **permissions},
                           ip_address=log_ip())
        return jsonify({"id": rule_id}), 201

    @wiki_route('/access/article-rules/<int:rule_id>', methods=('DELETE',),
                capability='can_manage_access')
    def wiki_article_rule_item(cursor, ctx, rule_id):
        article_id = wiki_edit.delete_article_rule(cursor, rule_id)
        if article_id is None:
            return jsonify({"error": "Правило не найдено"}), 404
        queries.log_action(cursor, actor_id=ctx['user_id'], action='article_rule.delete',
                           entity_type='article', entity_id=article_id,
                           details={'rule_id': rule_id}, ip_address=log_ip())
        return jsonify({"status": "deleted"})
