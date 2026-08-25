# -*- coding: utf-8 -*-
"""Эндпоинты гостевого доступа: выдать, продлить, отозвать, посмотреть.

Читающая сторона гостевого доступа жила в вике с самого начала и работала
(wiki/guests.py, шапка). Здесь появляется дверь, через которую доступ ВЫДАЮТ, —
и три границы, за которые она не пускает.

ПРАВО. Гейт стоит в теле обработчиков, а не параметром capability= на роуте, и
это не небрежность: право выдавать гостевой доступ — не способность, а правило
на конкретной ветке (wiki_section_access_rules.can_grant_guest), и «есть ли оно
вообще» отвечает не словарь способностей, а запрос по дереву. Параметр
capability= принимает имя из CAPABILITY_COLUMNS и такого права выразить не может.

ОБЪЕКТ. Раздел или статья обязаны лежать в ветке отдела выдающего. Проверяется
на КАЖДОЙ двери отдельно, включая продление и отзыв: право могло исчезнуть между
выдачей и отзывом, и «раз выдал — значит вправе трогать» здесь неверно.

ПОЛУЧАТЕЛЬ. Уровень должности строго ниже своего (access.may_grant_guest_to).
Отдел получателя не проверяется вовсе — решение владельца, см. шапку guests.py.

Списки для формы (кому, что) считаются ТЕМИ ЖЕ правилами, что и проверки на
записи. Иначе форма предложит то, что сервер отвергнет, — молчаливый отказ с
обратной стороны стола, который в этом разделе уже случался дважды
(WIKI_GRANT_BEYOND_SELF, публикация у супервайзера).
"""

from datetime import timedelta

from flask import jsonify, request

from . import access as wiki_access
from . import guests as wiki_guests
from . import queries
from . import structure
from .routes_structure import _int_or_none
from .schema import MAX_GUEST_DAYS


def _body():
    return request.get_json(silent=True) or {}


def register(bp, wiki_route, db, log_ip):

    def _unbounded(ctx):
        """Снимаются ли с человека обе границы выдачи — отдел и лестница.

        Тот же мастер-ключ, что и всюду в разделе: супер-админ и роль вики с
        can_manage_access. Правило повторено из routes_structure._grant_departments
        намеренно — там оно замыкание внутри register(), импортировать его
        нельзя, а разъезжаться правилам про одно и то же не дают тесты.
        """
        if wiki_access.normalize_role(ctx['otp_role']) == 'super_admin':
            return True
        return bool(ctx['wiki_roles']) and bool(ctx['capabilities'].get('can_manage_access'))

    def _departments(ctx):
        """Отделы, чьи ветки человек вправе открывать гостю. None — без границы."""
        if _unbounded(ctx):
            return None
        own = set(ctx.get('headed_department_ids') or [])
        if ctx.get('department_id'):
            own.add(ctx['department_id'])
        return sorted(own)

    def _shareable(cursor, ctx):
        """Разделы, которые человек вправе открыть гостю. None — любые."""
        return wiki_guests.shareable_section_ids(
            cursor, ctx['subjects'], ctx['user_id'],
            unbounded=_unbounded(ctx), departments=_departments(ctx))

    def _may_share_section(shareable, section_id):
        return shareable is None or section_id in shareable

    def _forbidden(message, code='WIKI_GUEST_FORBIDDEN'):
        return jsonify({"error": message, "code": code}), 403

    # ── Список выдач и справочники формы ─────────────────────────────────
    @wiki_route('/guests')
    def wiki_guests_list(cursor, ctx):
        """Кому и до какого срока открыты разделы и статьи моей ветки.

        Периметр списка — «мои ветки плюс мои выдачи» (wiki/guests.py). Отдаём
        вместе со списком и рамки выдачи: потолок срока, признак «есть ли право
        вообще» и границу отдела. Форма узнавала бы про них только по 403 на
        заполненной форме — то есть отказом уже после того, как человек всё
        заполнил и нажал «Выдать».
        """
        shareable = _shareable(cursor, ctx)
        today = wiki_guests.now_almaty().date()
        space_id = _int_or_none(request.args.get('space_id'))
        limit = min(max(_int_or_none(request.args.get('limit')) or 100, 1),
                    wiki_guests.MAX_PAGE)
        offset = max(_int_or_none(request.args.get('offset')) or 0, 0)
        # Поиск от двух символов — как в журнале: по одной букве ILIKE
        # перебирает всю таблицу и всё равно возвращает почти всё.
        query = (request.args.get('q') or '').strip()[:120]
        if len(query) < 2:
            query = ''

        may_grant = shareable is None or bool(shareable)
        return jsonify({
            "items": wiki_guests.list_grants(
                cursor, actor_id=ctx['user_id'], section_ids=shareable or (),
                unbounded=shareable is None, space_id=space_id,
                query=query, limit=limit, offset=offset),
            # Право выдавать. Без него вкладка показывает историю своих выдач и
            # прячет кнопку — не пустой экран без объяснения.
            "can_grant": may_grant,
            "max_days": MAX_GUEST_DAYS,
            # Календарные рамки считает СЕРВЕР, и форма берёт их только отсюда.
            # У браузера западнее Алматы «сегодня» на сутки раньше нашего, и
            # пикер, построенный от new Date(), предложил бы дату, которую
            # resolve_expiry тут же отвергнет со словами «уже прошла» — отказ на
            # значении, которое сам же интерфейс и показал как допустимое.
            "today": today.isoformat(),
            "max_until": (today + timedelta(days=MAX_GUEST_DAYS)).isoformat(),
            "grant_departments": _departments(ctx),
            # Сколько разделов человек вправе открыть. Число, а не список:
            # список приезжает отдельной ручкой, когда форму действительно
            # открыли, и тащить его в каждый список выдач незачем.
            "shareable_sections": None if shareable is None else len(shareable),
        })

    @wiki_route('/guests/targets')
    def wiki_guests_targets(cursor, ctx):
        """Разделы и статьи, которые человек вправе открыть гостю.

        Обе половины одним ответом: форма выбирает «раздел ИЛИ статья»
        переключателем, и второй запрос при щелчке по переключателю выглядел бы
        как подвисание на пустом списке.
        """
        shareable = _shareable(cursor, ctx)
        if shareable is not None and not shareable:
            return _forbidden('Вам не выдано право открывать разделы гостям. '
                              'Его включают в «Структуре» — тумблер «Гостевой '
                              'доступ» в правиле раздела')

        space_id = _int_or_none(request.args.get('space_id'))
        query = (request.args.get('q') or '').strip()[:120]

        sections = [s for s in structure.list_sections(cursor)
                    if (shareable is None or s['id'] in shareable)
                    and (space_id is None or s['space_id'] == space_id)]
        return jsonify({
            "sections": sections,
            "articles": wiki_guests.shareable_articles(
                cursor, section_ids=shareable, space_id=space_id, query=query),
        })

    @wiki_route('/guests/people')
    def wiki_guests_people(cursor, ctx):
        """Сотрудники, которым текущий человек вправе выдать гостевой доступ.

        Список НЕ ограничен отделом — это и есть «любому сотруднику из icore».
        Ограничен он лестницей: строго ниже себя по оргструктуре.
        """
        query = (request.args.get('q') or '').strip()[:120]
        return jsonify({"items": wiki_guests.guest_candidates(
            cursor, actor_id=ctx['user_id'],
            actor_level=wiki_access.role_level_of(ctx['otp_role']),
            unbounded=_unbounded(ctx), query=query)})

    # ── Выдача ───────────────────────────────────────────────────────────
    @wiki_route('/guests', methods=('POST',))
    def wiki_guests_create(cursor, ctx):
        """Выдать гостевой доступ. Три границы проверяются по очереди.

        Порядок проверок — от общего к частному, и он же порядок, в котором
        человек заполнял форму: право вообще → объект → получатель → срок. Так
        отказ показывает первое место, где выдача расходится с правилами, а не
        последнее.
        """
        data = _body()
        shareable = _shareable(cursor, ctx)
        if shareable is not None and not shareable:
            return _forbidden('Вам не выдано право открывать разделы гостям. '
                              'Его включают в «Структуре» — тумблер «Гостевой '
                              'доступ» в правиле раздела')

        section_id = _int_or_none(data.get('section_id'))
        article_id = _int_or_none(data.get('article_id'))
        if bool(section_id) == bool(article_id):
            return jsonify({"error": "Выберите раздел ИЛИ статью"}), 400

        # ОБЪЕКТ. Статья проверяется по своим разделам: открыть её вправе тот,
        # кто вправе открыть хотя бы один раздел, в котором она лежит. Статья
        # без разделов не принадлежит никакой ветке — её раздаёт только тот, у
        # кого границы нет вовсе (та же логика, что и у границы пространства в
        # articles._VISIBLE_ARTICLES_SQL).
        if section_id:
            if structure.section_exists(cursor, section_id) is None:
                return jsonify({"error": "Раздел не найден"}), 404
            if not _may_share_section(shareable, section_id):
                return _forbidden('Этот раздел не в вашей ветке отдела — '
                                  'или на нём нет права выдавать гостевой доступ',
                                  'WIKI_GUEST_SECTION_SCOPE')
        else:
            sections = wiki_guests.article_section_ids(cursor, article_id)
            if shareable is not None and not (sections & shareable):
                return _forbidden('Эта статья не в вашей ветке отдела — '
                                  'или на её разделе нет права выдавать '
                                  'гостевой доступ', 'WIKI_GUEST_SECTION_SCOPE')

        # ПОЛУЧАТЕЛЬ.
        target_id = _int_or_none(data.get('user_id'))
        if not target_id:
            return jsonify({"error": "Выберите сотрудника"}), 400
        cursor.execute(
            """
            SELECT u.role, u.name, u.status,
                   EXISTS (SELECT 1 FROM departments d
                            WHERE d.head_user_id = u.id AND d.is_active)
              FROM users u WHERE u.id = %s
            """,
            (target_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Сотрудник не найден"}), 404
        target_role, target_name, target_status, target_heads = row
        if target_status != 'working':
            return jsonify({"error": "Сотрудник не числится работающим"}), 400
        if target_id == ctx['user_id']:
            return _forbidden('Себе гостевой доступ не выдают')
        if not wiki_access.may_grant_guest_to(ctx['otp_role'], target_role,
                                             unbounded=_unbounded(ctx)):
            return _forbidden('Гостевой доступ выдают тем, кто ниже по '
                              'оргструктуре', 'WIKI_GUEST_LADDER')

        # СРОК.
        try:
            expires_at = wiki_guests.resolve_expiry(
                wiki_guests.now_almaty(),
                days=data.get('days'), until=data.get('until'))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        reason = (data.get('reason') or '').strip()[:500] or None
        # «Включая подразделы» — только у раздела: у статьи подразделов нет, и
        # сохранённый TRUE на строке статьи однажды прочитали бы как признак.
        deep = bool(data.get('include_subsections', True)) if section_id else False

        grant_id, created = wiki_guests.create_grant(
            cursor, user_id=target_id, section_id=section_id, article_id=article_id,
            granted_by=ctx['user_id'], expires_at=expires_at, reason=reason,
            include_subsections=deep)

        queries.log_action(
            cursor, actor_id=ctx['user_id'],
            action='guest.grant' if created else 'guest.extend',
            entity_type='section' if section_id else 'article',
            entity_id=section_id or article_id,
            target_user_id=target_id,
            details={'grant_id': grant_id, 'expires_at': expires_at.isoformat(),
                     'include_subsections': deep, 'reason': reason,
                     'user_name': target_name},
            ip_address=log_ip())

        # Оператору вика открывается только после QR-подтверждения сессии
        # (access.requires_sensitive_qr), и подтверждать его нужно КАЖДУЮ
        # сессию заново. Выдача этого гейта не снимает — он про рабочее место,
        # а не про права, — но выдающий обязан узнать об этом здесь, а не от
        # получателя через неделю: «доступ до 5 сентября» и «каждый раз зови
        # старшего» иначе расходятся молча.
        return jsonify({"status": "ok", "id": grant_id, "created": created,
                        "expires_at": expires_at.isoformat(),
                        "needs_qr": wiki_access.requires_sensitive_qr(
                            target_role, is_department_head=bool(target_heads))})

    # ── Продление и отзыв ────────────────────────────────────────────────
    def _may_touch(cursor, ctx, grant):
        """Вправе ли человек трогать ЭТУ выдачу.

        Своя выдача — всегда: отозвать выданное собой человек обязан мочь даже
        после того, как право на разделе у него сняли, иначе отзывать будет
        некому. Чужая — только внутри своей ветки, по тем же правилам, что и
        сама выдача.
        """
        if grant['granted_by'] == ctx['user_id']:
            return True
        shareable = _shareable(cursor, ctx)
        if shareable is None:
            return True
        if grant['section_id']:
            return grant['section_id'] in shareable
        return bool(wiki_guests.article_section_ids(cursor, grant['article_id'])
                    & shareable)

    @wiki_route('/guests/<int:grant_id>', methods=('PATCH', 'DELETE'))
    def wiki_guests_change(cursor, ctx, grant_id):
        grant = wiki_guests.get_grant(cursor, grant_id)
        if not grant:
            return jsonify({"error": "Выдача не найдена"}), 404
        if not _may_touch(cursor, ctx, grant):
            return _forbidden('Эта выдача относится к чужой ветке отдела',
                              'WIKI_GUEST_SECTION_SCOPE')

        if request.method == 'DELETE':
            if not wiki_guests.revoke_grant(cursor, grant_id, ctx['user_id']):
                # Строка есть, а отзыв ничего не изменил — значит её уже
                # отозвали. Не ошибка: кнопку нажали дважды.
                return jsonify({"status": "already_revoked"})
            queries.log_action(
                cursor, actor_id=ctx['user_id'], action='guest.revoke',
                entity_type='section' if grant['section_id'] else 'article',
                entity_id=grant['section_id'] or grant['article_id'],
                target_user_id=grant['user_id'],
                details={'grant_id': grant_id}, ip_address=log_ip())
            return jsonify({"status": "revoked"})

        # Продление. Срок считается от «сейчас» и тем же потолком, что и выдача:
        # иначе продлением набирается любой горизонт по четырнадцать дней за раз.
        if grant['revoked_at']:
            return jsonify({"error": "Отозванную выдачу не продлевают — "
                                     "выдайте доступ заново"}), 400
        data = _body()
        try:
            expires_at = wiki_guests.resolve_expiry(
                wiki_guests.now_almaty(),
                days=data.get('days'), until=data.get('until'))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        wiki_guests.extend_grant(cursor, grant_id, expires_at)
        queries.log_action(
            cursor, actor_id=ctx['user_id'], action='guest.extend',
            entity_type='section' if grant['section_id'] else 'article',
            entity_id=grant['section_id'] or grant['article_id'],
            target_user_id=grant['user_id'],
            details={'grant_id': grant_id, 'expires_at': expires_at.isoformat()},
            ip_address=log_ip())
        return jsonify({"status": "extended", "expires_at": expires_at.isoformat()})
