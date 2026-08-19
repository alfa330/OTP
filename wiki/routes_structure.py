"""Эндпоинты структуры и доступов раздела «Вики».

Подключается из wiki/routes.py: там общий декоратор wiki_route, здесь —
обработчики, чтобы ни один файл не разросся до нечитаемого состояния.
"""

from flask import jsonify, request

from . import access as wiki_access
from . import articles as wiki_articles
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

    def _grant_ceiling(ctx):
        """До какого уровня должности человек вправе открывать разделы.

        None — не вправе вовсе. Способность can_manage_access, ПРИШЕДШАЯ ОТ РОЛИ
        ВИКИ, поднимает потолок: администратора вики назначают руками ровно для
        этого. От должности она приходит и к 'admin' — там потолок берётся по
        должности, иначе руководитель отдела молча получил бы права директора.
        """
        from_wiki_role = bool(ctx['wiki_roles']) and ctx['capabilities'].get('can_manage_access')
        return wiki_access.grant_ceiling(ctx['otp_role'], is_wiki_admin=bool(from_wiki_role))

    def _grant_departments(ctx):
        """Отделы, внутри которых человек вправе настраивать доступ.

        None — без границы (коммерческий директор и администратор вики).
        Решение владельца: супервайзер и руководитель работают только со своим
        отделом, иначе СВ продаж раздавал бы доступ к веткам СЗоВ.
        """
        if wiki_access.normalize_role(ctx['otp_role']) == 'super_admin':
            return None
        if bool(ctx['wiki_roles']) and ctx['capabilities'].get('can_manage_access'):
            return None
        own = set(ctx.get('headed_department_ids') or [])
        if ctx.get('department_id'):
            own.add(ctx['department_id'])
        return sorted(own)

    def _may_grant_on_section(cursor, ctx, section_id):
        """Вправе ли человек трогать правила ИМЕННО ЭТОГО раздела.

        Две проверки: потолок должности (есть ли право вообще) и граница отдела
        (та ли это ветка). Раздел вне любой ветки отдела остаётся за директором:
        «свой отдел» для него не определён, и пускать туда руководителя нельзя.
        """
        if _grant_ceiling(ctx) is None:
            return False
        departments = _grant_departments(ctx)
        if departments is None:
            return True
        branch = structure.section_branch_department(cursor, section_id)
        return branch is not None and branch in departments

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

        # Право раздавать доступ: потолок должности и граница отдела. Считаем
        # один раз на запрос, а отдел ветки — по уже загруженному дереву, а не
        # рекурсивным запросом на каждый раздел.
        ceiling = _grant_ceiling(ctx)
        grant_departments = _grant_departments(ctx)
        by_id = {s['id']: s for s in sections}
        branch_of = {}

        def branch_department(section_id):
            if section_id in branch_of:
                return branch_of[section_id]
            chain, current = [], section_id
            found = None
            # Ограничитель тот же, что в SQL-версии: петель сервер не допускает,
            # но зациклиться здесь — подвесить весь ответ.
            for _ in range(50):
                node = by_id.get(current)
                if not node:
                    break
                chain.append(current)
                if node['department_id']:
                    found = node['department_id']
                    break
                current = node['parent_section_id']
                if not current:
                    break
            for ident in chain:
                branch_of[ident] = found
            return found

        visible = []
        for section in sections:
            # Может ли текущий человек раздавать доступ ИМЕННО ТУТ: решает
            # сервер, а не фронт. Иначе граница отдела считалась бы дважды и
            # однажды разошлась бы — а расходится она всегда в сторону «показали
            # кнопку, а API ответил 403».
            can_grant_here = bool(
                ceiling is not None
                and (grant_departments is None
                     or branch_department(section['id']) in grant_departments)
            )
            # Раздел, которым человек УПРАВЛЯЕТ, остаётся в ответе, даже если он
            # его не читает. Иначе супервайзер не увидел бы во вкладке
            # «Структура» ни одной ветки своего отдела: правило на чтение ему
            # никто не выписывал, и раздавать операторам было бы негде.
            # Витрина статей такие разделы отсеивает по accessible=false —
            # тем же способом, что и чужие разделы у администратора.
            if section['id'] not in allowed and not can_manage and not can_grant_here:
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
            # Может ли текущий человек раздавать доступ ИМЕННО ТУТ: решает
            # сервер, а не фронт. Иначе граница отдела считалась бы дважды и
            # однажды разошлась бы — а расходится она всегда в сторону «показали
            # кнопку, а API ответил 403».
            section['can_grant_access'] = can_grant_here
            visible.append(section)

        used_spaces = {s['space_id'] for s in visible}
        return jsonify({
            "spaces": [sp for sp in spaces if can_manage or sp['id'] in used_spaces],
            "sections": visible,
            "can_manage_structure": can_manage,
            "can_manage_access": bool(ctx['capabilities'].get('can_manage_access')),
        })

    # ── Пространства ─────────────────────────────────────────────────────
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

        # Отдел ветки. Из него выводится section_kind, и он же подставляется в
        # правила доступа подразделов: ветка «СЗоВ» делает раздел «Оператор»
        # внутри неё правилом «отдел СЗоВ + порог должности», а не голой ролью,
        # которая пробила бы границу отдела.
        parent_section_id = _int_or_none(data.get('parent_section_id'))
        department_id = _int_or_none(data.get('department_id'))
        taken = structure.department_branch_taken(
            cursor, space_id=space_id, parent_section_id=parent_section_id,
            department_id=department_id)
        if taken:
            return jsonify({
                "error": "Ветка этого отдела здесь уже есть — «%s»" % taken,
                "code": "WIKI_DEPARTMENT_BRANCH_TAKEN",
            }), 400

        section_id = structure.create_section(
            cursor, space_id=space_id,
            parent_section_id=parent_section_id,
            name=name, department_id=department_id,
            # Слаг обязан быть уникален в пространстве, и занять его мог даже
            # архивный раздел. Дописываем номер, как это делают статьи, — иначе
            # повтор названия падал в 500.
            slug=structure.free_section_slug(
                cursor, space_id, _clean(data.get('slug'), 200) or _slugify(name)),
            description=_clean(data.get('description'), 2000),
            icon=_clean(data.get('icon'), 64), visibility_scope=scope,
            owner_user_id=_int_or_none(data.get('owner_user_id')),
            created_by=ctx['user_id'],
        )
        queries.log_action(cursor, actor_id=ctx['user_id'], action='section.create',
                           entity_type='section', entity_id=section_id,
                           details={'name': name, 'space_id': space_id,
                                    'visibility_scope': scope,
                                    'department_id': department_id},
                           ip_address=log_ip())
        return jsonify({"id": section_id}), 201

    @wiki_route('/sections/<int:section_id>', methods=('PATCH', 'DELETE'),
                capability='can_manage_structure')
    def wiki_section_item(cursor, ctx, section_id):
        cursor.execute(
            """
            SELECT s.name, sp.department_id, s.space_id, s.parent_section_id
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
        section_exists_space_id = row[2]
        section_parent_id = row[3]

        if request.method == 'DELETE':
            structure.update_section(cursor, section_id, {'status': 'archived'})
            queries.log_action(cursor, actor_id=ctx['user_id'], action='section.archive',
                               entity_type='section', entity_id=section_id,
                               details={'name': row[0]}, ip_address=log_ip())
            return jsonify({"status": "archived"})

        data = _body()
        fields = {}
        for key in ('name', 'description', 'icon'):
            if key in data:
                fields[key] = _clean(data[key], 2000 if key == 'description' else 255)
        if 'slug' in data:
            fields['slug'] = structure.free_section_slug(
                cursor, section_exists_space_id, _clean(data['slug'], 200) or _slugify(row[0]),
                exclude_id=section_id)
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
        # Отдел ветки: вид раздела выводим из него же, вторым полем не берём —
        # иначе пара «kind=department, department_id=NULL» проскочит мимо
        # уникального индекса и ветка перестанет быть веткой.
        if 'department_id' in data:
            department_id = _int_or_none(data['department_id'])
            taken = structure.department_branch_taken(
                cursor, space_id=section_exists_space_id,
                parent_section_id=fields.get('parent_section_id', section_parent_id),
                department_id=department_id, exclude_id=section_id)
            if taken:
                return jsonify({
                    "error": "Ветка этого отдела здесь уже есть — «%s»" % taken,
                    "code": "WIKI_DEPARTMENT_BRANCH_TAKEN",
                }), 400
            fields['department_id'] = department_id
            fields['section_kind'] = structure.section_kind_of(department_id)

        if not structure.update_section(cursor, section_id, fields):
            return jsonify({"error": "Нечего обновлять"}), 400
        queries.log_action(cursor, actor_id=ctx['user_id'], action='section.update',
                           entity_type='section', entity_id=section_id,
                           details=fields, ip_address=log_ip())
        return jsonify({"status": "ok"})

    # ── Правила доступа ──────────────────────────────────────────────────
    # Гейт не одной способностью, а любой из двух: справочник нужен и форме
    # раздела («отдел ветки» выбирается из него — её открывает управляющий
    # структурой, а глава отдела мастер-ключа не носит), и форме правила.
    # Роль вики может нести can_manage_access без can_manage_structure, поэтому
    # проверка идёт «или», а не сменой одной способности на другую. Отдаются
    # только названия отделов, направлений, групп и ролей вики; само
    # выписывание правил по-прежнему требует can_manage_access.
    @wiki_route('/access/subjects')
    def wiki_access_subjects(cursor, ctx):
        """Справочники для выбора субъекта правила и отдела ветки."""
        caps = ctx['capabilities']
        # Третий вход — право раздавать доступ: супервайзеру справочник нужен
        # для точечных правил (группа, направление), а способностей can_manage_*
        # у него нет. Без этого форма открывалась с пустыми списками.
        if not (caps.get('can_manage_structure') or caps.get('can_manage_access')
                or _grant_ceiling(ctx) is not None):
            return jsonify({"error": "Недостаточно прав для этого действия",
                            "code": "WIKI_FORBIDDEN"}), 403
        catalog = structure.subject_catalog(cursor)
        catalog['otp_role'] = [
            {'id': code, 'name': label} for code, label in (
                ('super_admin', 'Супер-администратор'), ('admin', 'Администратор'),
                ('sv', 'Супервайзер'), ('supervisor', 'Супервайзер (устар.)'),
                ('trainer', 'Тренер'), ('operator', 'Оператор'), ('trainee', 'Стажёр'),
            )
        ]
        return jsonify(catalog)

    # Гейт не «can_manage_access», а лестница: право раздавать доступ само
    # ограничено должностью раздающего (wiki_access.GRANT_CEILING) и его
    # отделом. До этого выдача была всё-или-ничего: у кого мастер-ключ — тот
    # правил любой раздел, у кого нет — не видел вкладки вовсе.
    @wiki_route('/access/section-rules', methods=('GET', 'POST'))
    def wiki_section_rules(cursor, ctx):
        ceiling = _grant_ceiling(ctx)
        if ceiling is None:
            return jsonify({"error": "Доступ раздают супервайзер и выше",
                            "code": "WIKI_FORBIDDEN"}), 403

        section_id = _int_or_none(
            request.args.get('section_id') if request.method == 'GET'
            else _body().get('section_id'))
        if section_id and not _may_grant_on_section(cursor, ctx, section_id):
            return jsonify({"error": "Раздел относится к другому отделу",
                            "code": "WIKI_DEPARTMENT_SCOPE"}), 403

        if request.method == 'GET':
            return jsonify({
                "items": structure.list_section_rules(cursor, section_id=section_id),
                # Потолок едет вместе со списком: форме нужно погасить строки
                # должностей выше него, а второй запрос ради одного числа лишний.
                "grant_ceiling": ceiling,
            })

        data = _body()
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

        # ── Потолок должности ────────────────────────────────────────
        # Роль адресата нужна отдельно от порога: у правила на конкретного
        # человека порог обычно пуст, и одна лишь проверка порога пропустила бы
        # «супервайзер выписывает правило на самого себя» — то есть выдачу себе
        # полного доступа к любому разделу своего отдела.
        target_role = None
        if subject_type == 'user':
            cursor.execute('SELECT role, department_id FROM users WHERE id = %s', (subject_id,))
            target = cursor.fetchone()
            if not target:
                return jsonify({"error": "Сотрудник не найден"}), 404
            target_role = target[0]
            departments = _grant_departments(ctx)
            if departments is not None and target[1] not in departments:
                return jsonify({
                    "error": "Этот сотрудник из другого отдела",
                    "code": "WIKI_DEPARTMENT_SCOPE",
                }), 403

        if not wiki_access.may_grant_with_ceiling(ceiling, min_role_level, target_role):
            return jsonify({
                "error": "Такой доступ выдаёт только вышестоящий руководитель",
                "code": "WIKI_GRANT_CEILING",
            }), 403

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

    @wiki_route('/access/section-rules/<int:rule_id>', methods=('DELETE',))
    def wiki_section_rule_item(cursor, ctx, rule_id):
        ceiling = _grant_ceiling(ctx)
        if ceiling is None:
            return jsonify({"error": "Доступ раздают супервайзер и выше",
                            "code": "WIKI_FORBIDDEN"}), 403

        # Читаем правило ДО удаления: иначе проверять границу уже не по чему,
        # а снятие чужого правила — такое же вмешательство, как выдача. Без
        # этого супервайзер отобрал бы доступ у руководителя своего отдела.
        cursor.execute(
            'SELECT section_id, min_role_level FROM wiki_section_access_rules WHERE id = %s',
            (rule_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Правило не найдено"}), 404
        if not _may_grant_on_section(cursor, ctx, row[0]):
            return jsonify({"error": "Раздел относится к другому отделу",
                            "code": "WIKI_DEPARTMENT_SCOPE"}), 403
        if not wiki_access.may_grant_with_ceiling(ceiling, row[1]):
            return jsonify({"error": "Это правило снимает только вышестоящий руководитель",
                            "code": "WIKI_GRANT_CEILING"}), 403

        section_id = structure.delete_section_rule(cursor, rule_id)
        if section_id is None:
            return jsonify({"error": "Правило не найдено"}), 404
        queries.log_action(cursor, actor_id=ctx['user_id'], action='rule.delete',
                           entity_type='section', entity_id=section_id,
                           details={'rule_id': rule_id}, ip_address=log_ip())
        return jsonify({"status": "deleted"})

    @wiki_route('/access/people')
    def wiki_access_people(cursor, ctx):
        """Сотрудники, которым текущий человек вправе выдать доступ.

        Список уже отфильтрован потолком и отделом, поэтому форма не может
        предложить того, кого сервер потом отвергнет: список и проверка
        считаются по одним и тем же правилам.
        """
        ceiling = _grant_ceiling(ctx)
        if ceiling is None:
            return jsonify({"error": "Доступ раздают супервайзер и выше",
                            "code": "WIKI_FORBIDDEN"}), 403
        return jsonify({"items": structure.grantable_people(
            cursor, max_role_level=ceiling, department_ids=_grant_departments(ctx))})

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
