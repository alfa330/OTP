"""Эндпоинты структуры и доступов раздела «Вики».

Подключается из wiki/routes.py: там общий декоратор wiki_route, здесь —
обработчики, чтобы ни один файл не разросся до нечитаемого состояния.
"""

from flask import jsonify, request

from . import access as wiki_access
from . import articles as wiki_articles
from . import org as wiki_org
from . import queries, structure
from .schema import SUBJECT_TYPES


def _body():
    return request.get_json(silent=True) or {}


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean(value, limit=255):
    text = str(value or '').strip()
    return text[:limit] if text else None


def _slugify(value):
    """Слаг из названия: латиница/цифры/дефис. Кириллица транслитерируется.

    Полноценный порт utils/text.ts (транслитерация + 110 групп алиасов) придёт
    на этапе поиска; здесь нужен только предсказуемый ключ для URL.
    """
    table = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '',
        'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    }
    out = []
    for char in str(value or '').strip().lower():
        if char in table:
            out.append(table[char])
        elif char.isalnum():
            out.append(char)
        elif out and out[-1] != '-':
            out.append('-')
    return ''.join(out).strip('-')[:200] or 'section'


PERMISSION_FIELDS = ('can_read', 'can_create', 'can_edit',
                     'can_delete', 'can_publish', 'can_approve')


def register(bp, wiki_route, db, log_ip):
    """Вешает обработчики на Blueprint. bp и wiki_route приходят из routes.py."""

    def _may_manage_space(ctx, department_id):
        """Глава отдела управляет структурой только своего отдела.

        Это повторение штатного правила портала: админ с возглавляемым отделом
        НЕ является глобальным админом (_is_global_admin_requester). Без этой
        проверки глава одного отдела правил бы пространства чужого.
        """
        caps = ctx['capabilities']
        if caps.get('can_manage_access'):
            return True
        if not caps.get('can_manage_structure'):
            return False
        headed = set(ctx.get('headed_department_ids') or [])
        if not headed:
            return False
        return department_id is not None and int(department_id) in headed

    # ── Дерево структуры ─────────────────────────────────────────────────
    @wiki_route('/structure')
    def wiki_structure(cursor, ctx):
        """Пространства и разделы, доступные пользователю, плюс его права на них.

        Периметр здесь ЛИЧНЫЙ (master_key=False): по этому же ответу рисуется
        дерево разделов на вкладке «Статьи», а это витрина чтения. С мастер-ключом
        флаг accessible был бы истинным у всех разделов сразу, и управляющий
        структурой видел бы в дереве чужие отделы. Раздел вне периметра остаётся
        в ответе (он нужен вкладке «Структура»), но помечен accessible=false.
        """
        subjects = wiki_access.collect_subjects(
            user_id=ctx['user_id'], otp_role=ctx['otp_role'],
            department_id=ctx['department_id'],
            headed_department_ids=ctx['headed_department_ids'],
            direction_id=ctx['direction_id'], group_ids=ctx['group_ids'],
            wiki_role_ids=[r.get('id') for r in ctx['wiki_roles']],
        )
        allowed = queries.allowed_section_ids(cursor, ctx, subjects, master_key=False)
        rules_by_section = queries.section_rules_for_user(cursor, allowed, subjects,
                                                          ctx['user_id'])
        can_manage = bool(ctx['capabilities'].get('can_manage_structure')
                          or ctx['capabilities'].get('can_manage_access'))

        # Счётчик статей в дереве — по тем же статьям, что человек реально
        # откроет. Общее число остаётся отдельным полем: вкладке «Структура»
        # нужен факт «в разделе 15 статей», даже если читателю видно три.
        readable = wiki_articles.visible_article_ids(cursor, ctx, subjects, allowed,
                                                     master_key=False)
        readable_counts = structure.article_counts_by_section(cursor, readable)

        spaces = structure.list_spaces(cursor, include_archived=can_manage)
        sections = structure.list_sections(cursor, include_archived=can_manage)

        visible = []
        for section in sections:
            if section['id'] not in allowed and not can_manage:
                continue
            permissions = wiki_access.resolve_article_permissions(
                capabilities=ctx['capabilities'],
                section_rules=rules_by_section.get(section['id'], []),
                otp_role=ctx['otp_role'],
                is_section_owner=(section['owner_user_id'] == ctx['user_id']),
            )
            section['permissions'] = wiki_access.permissions_only(permissions)
            section['why'] = permissions['_reason']
            section['accessible'] = section['id'] in allowed
            section['readable_count'] = readable_counts.get(section['id'], 0)
            visible.append(section)

        used_spaces = {s['space_id'] for s in visible}
        return jsonify({
            "spaces": [sp for sp in spaces if can_manage or sp['id'] in used_spaces],
            "sections": visible,
            "can_manage_structure": can_manage,
            "can_manage_access": bool(ctx['capabilities'].get('can_manage_access')),
        })

    # ── Пространства ─────────────────────────────────────────────────────
    @wiki_route('/structure/commercial', methods=('POST',),
                capability='can_manage_structure')
    def wiki_structure_commercial(cursor, ctx):
        """Пересобрать дерево «Коммерческого отдела» и правила доступа к нему.

        Отдельная кнопка, а не автозапуск при старте: автоматическая пересборка
        молча возвращала бы правки, сделанные владельцем руками. Операция
        идемпотентна и ничего не удаляет — снятая ветка уходит в архив.
        """
        report = wiki_org.ensure_commercial_structure(cursor, created_by=ctx['user_id'])
        queries.log_action(cursor, actor_id=ctx['user_id'], action='structure.commercial',
                           entity_type='space', entity_id=report['space']['id'],
                           details=report, ip_address=log_ip())
        return jsonify(report)

    @wiki_route('/spaces', methods=('GET', 'POST'), capability='can_manage_structure')
    def wiki_spaces(cursor, ctx):
        if request.method == 'GET':
            return jsonify({"items": structure.list_spaces(cursor, include_archived=True)})

        data = _body()
        name = _clean(data.get('name'))
        if not name:
            return jsonify({"error": "Укажите название пространства"}), 400

        department_id = _int_or_none(data.get('department_id'))
        if not _may_manage_space(ctx, department_id):
            return jsonify({
                "error": "Пространство можно создать только в своём отделе",
                "code": "WIKI_DEPARTMENT_SCOPE",
            }), 403

        space_id = structure.create_space(
            cursor, name=name, code=_clean(data.get('code'), 80),
            description=_clean(data.get('description'), 2000),
            icon=_clean(data.get('icon'), 64), department_id=department_id,
            created_by=ctx['user_id'],
        )
        queries.log_action(cursor, actor_id=ctx['user_id'], action='space.create',
                           entity_type='space', entity_id=space_id,
                           details={'name': name}, ip_address=log_ip())
        return jsonify({"id": space_id}), 201

    @wiki_route('/spaces/<int:space_id>', methods=('PATCH', 'DELETE'),
                capability='can_manage_structure')
    def wiki_space_item(cursor, ctx, space_id):
        cursor.execute('SELECT department_id, name FROM wiki_spaces WHERE id = %s', (space_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Пространство не найдено"}), 404
        if not _may_manage_space(ctx, row[0]):
            return jsonify({"error": "Это пространство относится к другому отделу"}), 403

        if request.method == 'DELETE':
            # Архивируем, а не удаляем: за пространством стоят разделы и статьи,
            # а физическое удаление сняло бы их каскадом без возможности вернуть.
            structure.update_space(cursor, space_id, {'status': 'archived'})
            queries.log_action(cursor, actor_id=ctx['user_id'], action='space.archive',
                               entity_type='space', entity_id=space_id,
                               details={'name': row[1]}, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        fields = {}
        for key in ('name', 'description', 'icon', 'code'):
            if key in data:
                fields[key] = _clean(data[key], 2000 if key == 'description' else 255)
        if 'department_id' in data:
            fields['department_id'] = _int_or_none(data['department_id'])
        if data.get('status') in ('active', 'archived'):
            fields['status'] = data['status']
        if 'position' in data:
            fields['position'] = _int_or_none(data['position']) or 0

        if not structure.update_space(cursor, space_id, fields):
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='space.update',
                           entity_type='space', entity_id=space_id,
                           details=fields, ip_address=log_ip())
        return jsonify({"status": "ok"})

    # ── Разделы ──────────────────────────────────────────────────────────
    @wiki_route('/sections', methods=('GET', 'POST'), capability='can_manage_structure')
    def wiki_sections(cursor, ctx):
        if request.method == 'GET':
            return jsonify({"items": structure.list_sections(
                cursor, space_id=_int_or_none(request.args.get('space_id')),
                include_archived=True)})

        data = _body()
        name = _clean(data.get('name'))
        space_id = _int_or_none(data.get('space_id'))
        if not name or not space_id:
            return jsonify({"error": "Укажите пространство и название раздела"}), 400

        cursor.execute('SELECT department_id FROM wiki_spaces WHERE id = %s', (space_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Пространство не найдено"}), 404
        if not _may_manage_space(ctx, row[0]):
            return jsonify({"error": "Это пространство относится к другому отделу"}), 403

        scope = data.get('visibility_scope')
        if scope not in ('public', 'restricted'):
            scope = 'restricted'

        section_id = structure.create_section(
            cursor, space_id=space_id,
            parent_section_id=_int_or_none(data.get('parent_section_id')),
            name=name, slug=_clean(data.get('slug'), 200) or _slugify(name),
            description=_clean(data.get('description'), 2000),
            icon=_clean(data.get('icon'), 64), visibility_scope=scope,
            owner_user_id=_int_or_none(data.get('owner_user_id')),
            created_by=ctx['user_id'],
        )
        queries.log_action(cursor, actor_id=ctx['user_id'], action='section.create',
                           entity_type='section', entity_id=section_id,
                           details={'name': name, 'space_id': space_id,
                                    'visibility_scope': scope},
                           ip_address=log_ip())
        return jsonify({"id": section_id}), 201

    @wiki_route('/sections/<int:section_id>', methods=('PATCH', 'DELETE'),
                capability='can_manage_structure')
    def wiki_section_item(cursor, ctx, section_id):
        cursor.execute(
            """
            SELECT s.name, sp.department_id
              FROM wiki_sections s JOIN wiki_spaces sp ON sp.id = s.space_id
             WHERE s.id = %s
            """,
            (section_id,),
        )
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Раздел не найден"}), 404
        if not _may_manage_space(ctx, row[1]):
            return jsonify({"error": "Раздел относится к другому отделу"}), 403

        if request.method == 'DELETE':
            structure.update_section(cursor, section_id, {'status': 'archived'})
            queries.log_action(cursor, actor_id=ctx['user_id'], action='section.archive',
                               entity_type='section', entity_id=section_id,
                               details={'name': row[0]}, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        fields = {}
        for key in ('name', 'slug', 'description', 'icon'):
            if key in data:
                fields[key] = _clean(data[key], 2000 if key == 'description' else 255)
        if data.get('visibility_scope') in ('public', 'restricted'):
            fields['visibility_scope'] = data['visibility_scope']
        if data.get('status') in ('active', 'archived'):
            fields['status'] = data['status']
        if 'owner_user_id' in data:
            fields['owner_user_id'] = _int_or_none(data['owner_user_id'])
        if 'position' in data:
            fields['position'] = _int_or_none(data['position']) or 0
        if 'parent_section_id' in data:
            parent = _int_or_none(data['parent_section_id'])
            if structure.section_would_cycle(cursor, section_id, parent):
                return jsonify({
                    "error": "Раздел нельзя вложить в самого себя или в свой подраздел",
                    "code": "WIKI_SECTION_CYCLE",
                }), 400
            fields['parent_section_id'] = parent

        if not structure.update_section(cursor, section_id, fields):
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='section.update',
                           entity_type='section', entity_id=section_id,
                           details=fields, ip_address=log_ip())
        return jsonify({"status": "ok"})

    # ── Правила доступа ──────────────────────────────────────────────────
    @wiki_route('/access/subjects', capability='can_manage_access')
    def wiki_access_subjects(cursor, ctx):
        """Справочники для выбора субъекта правила."""
        catalog = structure.subject_catalog(cursor)
        catalog['otp_role'] = [
            {'id': code, 'name': label} for code, label in (
                ('super_admin', 'Супер-администратор'), ('admin', 'Администратор'),
                ('sv', 'Супервайзер'), ('supervisor', 'Супервайзер (устар.)'),
                ('trainer', 'Тренер'), ('operator', 'Оператор'), ('trainee', 'Стажёр'),
            )
        ]
        return jsonify(catalog)

    @wiki_route('/access/section-rules', methods=('GET', 'POST'),
                capability='can_manage_access')
    def wiki_section_rules(cursor, ctx):
        if request.method == 'GET':
            return jsonify({"items": structure.list_section_rules(
                cursor, section_id=_int_or_none(request.args.get('section_id')))})

        data = _body()
        section_id = _int_or_none(data.get('section_id'))
        subject_type = data.get('subject_type')
        if not section_id or subject_type not in SUBJECT_TYPES:
            return jsonify({"error": "Укажите раздел и субъект правила"}), 400
        if structure.section_exists(cursor, section_id) is None:
            return jsonify({"error": "Раздел не найден"}), 404

        subject_id, subject_role = None, None
        if subject_type == 'otp_role':
            subject_role = str(data.get('subject_role') or '').strip()
            if subject_role not in wiki_access.ROLE_LEVELS and subject_role != 'supervisor':
                return jsonify({"error": "Неизвестная роль"}), 400
        else:
            subject_id = _int_or_none(data.get('subject_id'))
            if not subject_id:
                return jsonify({"error": "Не выбран субъект"}), 400

        # Уровень должности — второе измерение правила: «не ниже супервайзера».
        # Пустое значение (ничего не выбрано) означает «без ограничения»,
        # поэтому 0 и None здесь равнозначны и оба кладутся как NULL.
        min_role_level = _int_or_none(data.get('min_role_level'))
        if min_role_level is not None and min_role_level not in wiki_access.ROLE_LEVELS.values():
            return jsonify({"error": "Неизвестный уровень должности"}), 400

        permissions = {key: bool(data.get(key)) for key in PERMISSION_FIELDS}
        # Право без чтения бессмысленно: нельзя править то, чего не видишь.
        if any(permissions[k] for k in PERMISSION_FIELDS[1:]):
            permissions['can_read'] = True

        rule_id = structure.upsert_section_rule(
            cursor, section_id=section_id, subject_type=subject_type,
            subject_id=subject_id, subject_role=subject_role,
            permissions=permissions,
            grant_subsections=bool(data.get('grant_subsections', True)),
            min_role_level=min_role_level,
            created_by=ctx['user_id'],
        )
        queries.log_action(cursor, actor_id=ctx['user_id'], action='rule.upsert',
                           entity_type='section', entity_id=section_id,
                           target_user_id=subject_id if subject_type == 'user' else None,
                           details={'rule_id': rule_id, 'subject_type': subject_type,
                                    'subject_id': subject_id, 'subject_role': subject_role,
                                    'min_role_level': min_role_level, **permissions},
                           ip_address=log_ip())
        return jsonify({"id": rule_id}), 201

    @wiki_route('/access/section-rules/<int:rule_id>', methods=('DELETE',),
                capability='can_manage_access')
    def wiki_section_rule_item(cursor, ctx, rule_id):
        section_id = structure.delete_section_rule(cursor, rule_id)
        if section_id is None:
            return jsonify({"error": "Правило не найдено"}), 404
        queries.log_action(cursor, actor_id=ctx['user_id'], action='rule.delete',
                           entity_type='section', entity_id=section_id,
                           details={'rule_id': rule_id}, ip_address=log_ip())
        return jsonify({"status": "deleted"})

    # ── «Почему этот человек это видит» ──────────────────────────────────
    @wiki_route('/access/effective', capability='can_manage_access')
    def wiki_access_effective(cursor, ctx):
        """Периметр указанного пользователя с объяснением по каждому разделу.

        Такого эндпоинта в оригинале нет, но при четырёх уровнях правил
        (раздел → потомки → статья → запрет) без него невозможно ответить на
        вопрос «почему Иванов видит этот регламент».

        Разделы считаются ЛИЧНЫМ периметром — ровно тем, что человек увидит в
        «Все статьи». Мастер-ключ сюда не подмешивается: иначе у носителя роли
        'admin' ответ был бы «видит все восемь разделов», хотя в его списке
        статей лежит один. Сам мастер-ключ виден в capabilities ответа.
        """
        target_id = _int_or_none(request.args.get('user_id'))
        if not target_id:
            return jsonify({"error": "Укажите user_id"}), 400

        target = queries.load_access_context(cursor, target_id)
        if not target:
            return jsonify({"error": "Пользователь не найден"}), 404

        target['capabilities'] = wiki_access.resolve_capabilities(
            target['otp_role'], target['wiki_roles'],
            is_department_head=bool(target['headed_department_ids']),
        )
        subjects = wiki_access.collect_subjects(
            user_id=target['user_id'], otp_role=target['otp_role'],
            department_id=target['department_id'],
            headed_department_ids=target['headed_department_ids'],
            direction_id=target['direction_id'], group_ids=target['group_ids'],
            wiki_role_ids=[r.get('id') for r in target['wiki_roles']],
        )
        allowed = queries.allowed_section_ids(cursor, target, subjects, master_key=False)
        rules_by_section = queries.section_rules_for_user(cursor, allowed, subjects,
                                                          target['user_id'])

        sections = []
        for section in structure.list_sections(cursor):
            if section['id'] not in allowed:
                continue
            permissions = wiki_access.resolve_article_permissions(
                capabilities=target['capabilities'],
                section_rules=rules_by_section.get(section['id'], []),
                otp_role=target['otp_role'],
                is_section_owner=(section['owner_user_id'] == target['user_id']),
            )
            sections.append({
                'id': section['id'], 'name': section['name'],
                'space_id': section['space_id'],
                'visibility_scope': section['visibility_scope'],
                'permissions': wiki_access.permissions_only(permissions),
                'why': permissions['_reason'],
            })

        return jsonify({
            "user_id": target['user_id'],
            "otp_role": target['otp_role'],
            "access_mode": target['access_mode'],
            "capabilities": target['capabilities'],
            "subjects": subjects,
            "sections": sections,
        })

    # ── Журнал ───────────────────────────────────────────────────────────
    @wiki_route('/audit', capability='can_manage_access')
    def wiki_audit(cursor, ctx):
        limit = min(max(_int_or_none(request.args.get('limit')) or 100, 1), 500)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)
        return jsonify({"items": structure.list_audit(cursor, limit=limit, offset=offset)})
