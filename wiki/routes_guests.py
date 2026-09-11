# -*- coding: utf-8 -*-
"""Эндпоинты гостевого доступа: выдать, продлить, отозвать, посмотреть.

Читающая сторона гостевого доступа жила в вики с самого начала и работала
(wiki/guests.py, шапка). Здесь появляется дверь, через которую доступ ВЫДАЮТ, —
и три границы, за которые она не пускает.

ПРАВО. Гейт стоит в теле обработчиков, а не параметром capability= на роуте:
право выдавать гостевой доступ — не способность, а ДОЛЖНОСТЬ по лестнице
access.GUEST_GRANT_CEILING (супервайзер и выше), а параметр capability=
принимает имя из CAPABILITY_COLUMNS и такого права выразить не может.

ОБЪЕКТ. Раздел или статья обязаны лежать в ветке отдела выдающего. Проверяется
на КАЖДОЙ двери отдельно, включая продление и отзыв: право могло исчезнуть между
выдачей и отзывом, и «раз выдал — значит вправе трогать» здесь неверно.

ТРЕТИЙ ОБЪЕКТ — ПРОСТРАНСТВО ЦЕЛИКОМ (решение владельца 11.09.2026). Ветка
отдела к нему неприменима по смыслу: пространство и есть граница между отделами,
и «своей ветки» в чужом пространстве не бывает ни у кого. Поэтому у него своё,
отдельное право — роль OTP super_admin (access.may_grant_guest_space), и оно НЕ
лестница: администратор вики, которому весь остальной раздел выдаёт мастер-ключ,
пространства не раздаёт. Владелец выбрал самый узкий ответ на прямой вопрос.
Границы получателя это НЕ трогает: у супер-админа их и так нет, но обе проверки
(чин и отдел) остаются на своих местах — они про человека, а не про объект.

ПОЛУЧАТЕЛЬ. Свой подчинённый: и по чину (потолок должности), и по отделу.
«Если СВ из СЗоВ, то он и видит операторов из СЗоВ» — решение владельца.

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


# Права нет вовсе — по должности. Отдельный текст от «нечего открывать»: беды
# разные, и чинятся они разным. Прежняя формулировка отсылала к тумблеру в
# «Структуре»; тумблера больше нет — право даёт должность (решение владельца
# 25.08.2026).
_NO_RIGHT = 'Гостевой доступ выдают супервайзер и выше'

# Право есть, а открывать нечего: в ветке отдела нет ни одного раздела, который
# человек видит сам.
_NOTHING_TO_SHARE = ('В вашей ветке отдела нет разделов, которые вы могли бы '
                     'открыть гостю')


def _body():
    return request.get_json(silent=True) or {}


def register(bp, wiki_route, db, log_ip):

    def _is_wiki_admin(ctx):
        """Мастер-ключ раздела: роль вики с can_manage_access, а не должность.

        Проверка «роль вики И способность» осталась и после того, как должность
        'admin' перестала раздавать мастер-ключ: у роли вики он по-прежнему есть.
        """
        return bool(ctx['wiki_roles']) and bool(ctx['capabilities'].get('can_manage_access'))

    def _ceiling(ctx):
        """До какого уровня должности человек выдаёт. None — не выдаёт вовсе.

        По этому же признаку в интерфейсе появляется сам раздел «Гостевой
        доступ»: вопрос «вижу ли я раздел» и «кому вправе выдать» — один вопрос
        (access.GUEST_GRANT_CEILING).
        """
        return wiki_access.guest_grant_ceiling(
            ctx['otp_role'], is_wiki_admin=_is_wiki_admin(ctx))

    def _departments(ctx):
        """Свои отделы: и чьи ветки открываем, и чьих людей зовём. None — все.

        Границы нет у супер-админа и администратора вики — им сказано «может
        всем». Правило то же, что у выдачи правил разделов
        (routes_structure._grant_departments); повторено намеренно — там оно
        замыкание внутри register(), импортировать его нельзя, а разъезжаться
        правилам про одно и то же не дают тесты.
        """
        if wiki_access.normalize_role(ctx['otp_role']) == 'super_admin' or _is_wiki_admin(ctx):
            return None
        own = set(ctx.get('headed_department_ids') or [])
        if ctx.get('department_id'):
            own.add(ctx['department_id'])
        return sorted(own)

    def _may_grant_space(ctx):
        """Вправе ли человек открыть гостю ЦЕЛОЕ пространство. Только супер-админ.

        Отдельно от _ceiling и _departments намеренно: те отвечают «кому по
        чину» и «чьим людям», а это — «что вообще можно открыть». Свести их в
        один признак значило бы, что следующее послабление потолка молча
        раздаст пространства (см. access.may_grant_guest_space).
        """
        return wiki_access.may_grant_guest_space(ctx['otp_role'])

    def _shareable(cursor, ctx):
        """Разделы, которые человек вправе открыть гостю. None — любые."""
        return wiki_guests.shareable_section_ids(
            cursor, ctx, ctx['subjects'], departments=_departments(ctx))

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
        ceiling = _ceiling(ctx)
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

        # Право выдавать — по должности; наличие разделов в своей ветке решает
        # уже другой вопрос («есть ли что открыть») и объясняется отдельно.
        may_grant = ceiling is not None and (shareable is None or bool(shareable))
        return jsonify({
            "items": wiki_guests.list_grants(
                cursor, actor_id=ctx['user_id'], section_ids=shareable or (),
                unbounded=shareable is None, space_id=space_id,
                query=query, limit=limit, offset=offset),
            # Право выдавать. Без него вкладка показывает историю своих выдач и
            # прячет кнопку — не пустой экран без объяснения.
            "can_grant": may_grant,
            # Ради честного пустого экрана: «права нет вовсе» и «право есть, а
            # разделов в моей ветке нет» — разные беды с разными подсказками.
            "may_grant_by_role": ceiling is not None,
            # Третий объект выдачи виден не всем. Считает сервер и отдаёт
            # готовым — по той же причине, что и can_grant_guest в /ping: вторая
            # формула во фронте однажды разойдётся с этой, и переключатель
            # «Пространство» появится у того, кому сервер ответит 403.
            "can_grant_space": _may_grant_space(ctx),
            "max_days": MAX_GUEST_DAYS,
            # Календарные рамки считает СЕРВЕР, и форма берёт их только отсюда.
            # У браузера западнее Алматы «сегодня» на сутки раньше нашего, и
            # пикер, построенный от new Date(), предложил бы дату, которую
            # resolve_expiry тут же отвергнет со словами «уже прошла» — отказ на
            # значении, которое сам же интерфейс и показал как допустимое.
            "today": today.isoformat(),
            # Час «сейчас» — для пикера времени: на СЕГОДНЯШНЕМ дне прошедшие
            # часы выбирать незачем, сервер их всё равно отвергнет («это время
            # уже прошло»). Считает опять же сервер: у браузера западнее Алматы
            # свой час, и граница разъехалась бы.
            "now_time": wiki_guests.now_almaty().strftime('%H:%M'),
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
        if _ceiling(ctx) is None:
            return _forbidden(_NO_RIGHT)
        shareable = _shareable(cursor, ctx)
        if shareable is not None and not shareable:
            return _forbidden(_NOTHING_TO_SHARE, 'WIKI_GUEST_NOTHING_TO_SHARE')

        space_id = _int_or_none(request.args.get('space_id'))
        query = (request.args.get('q') or '').strip()[:120]

        sections = [s for s in structure.list_sections(cursor)
                    if (shareable is None or s['id'] in shareable)
                    and (space_id is None or s['space_id'] == space_id)]
        # Пространства — ВСЕ активные, а не только текущее: выдача пространства
        # ради того и заведена, чтобы открыть человеку СОСЕДНЮЮ вику, и
        # отфильтруй мы список по space_id, единственным выбором осталось бы то,
        # в котором выдающий уже стоит. Пустой список у того, кто раздавать
        # пространства не вправе: список объектов не должен рассказывать о
        # существовании чужих вик тому, кому они не предназначены.
        spaces = ([{'id': sp['id'], 'name': sp['name'], 'icon': sp['icon'],
                    'description': sp['description'],
                    'sections_count': sp['sections_count']}
                   for sp in structure.list_spaces(cursor)]
                  if _may_grant_space(ctx) else [])
        return jsonify({
            "sections": sections,
            "articles": wiki_guests.shareable_articles(
                cursor, section_ids=shareable, space_id=space_id, query=query),
            "spaces": spaces,
        })

    @wiki_route('/guests/people')
    def wiki_guests_people(cursor, ctx):
        """Сотрудники, которым текущий человек вправе выдать гостевой доступ.

        Свои подчинённые: и по чину, и по отделу. Список считается теми же
        правилами, что и проверка на записи, — форма не может предложить того,
        кого сервер потом отвергнет.
        """
        ceiling = _ceiling(ctx)
        if ceiling is None:
            return _forbidden(_NO_RIGHT)
        query = (request.args.get('q') or '').strip()[:120]
        return jsonify({"items": wiki_guests.guest_candidates(
            cursor, actor_id=ctx['user_id'], ceiling=ceiling,
            departments=_departments(ctx), query=query)})

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
        if _ceiling(ctx) is None:
            return _forbidden(_NO_RIGHT)
        shareable = _shareable(cursor, ctx)
        # «Открывать нечего» — про ветку отдела, и к выдаче ПРОСТРАНСТВА это
        # отношения не имеет: пространство раздают поверх веток. Проверка стоит
        # ниже разбора объекта ровно поэтому.
        section_id = _int_or_none(data.get('section_id'))
        article_id = _int_or_none(data.get('article_id'))
        space_id = _int_or_none(data.get('space_id'))
        chosen = [value for value in (section_id, article_id, space_id) if value]
        if len(chosen) != 1:
            return jsonify({"error": "Выберите раздел, статью ИЛИ пространство"}), 400
        if not space_id and shareable is not None and not shareable:
            return _forbidden(_NOTHING_TO_SHARE, 'WIKI_GUEST_NOTHING_TO_SHARE')

        # ОБЪЕКТ. Статья проверяется по своим разделам: открыть её вправе тот,
        # кто вправе открыть хотя бы один раздел, в котором она лежит. Статья
        # без разделов не принадлежит никакой ветке — её раздаёт только тот, у
        # кого границы нет вовсе (та же логика, что и у границы пространства в
        # articles._VISIBLE_ARTICLES_SQL).
        if space_id:
            # Пространство границей отдела не проверяется — её здесь нет по
            # смыслу (см. шапку модуля). Проверяется ПРАВО и то, что открывают
            # живое пространство: архивное выдать можно было бы без единой
            # ошибки, а увидел бы получатель пустой переключатель —
            # spaces_for_user считает только активные.
            if not _may_grant_space(ctx):
                return _forbidden(
                    'Пространство целиком выдаёт только супер-администратор',
                    'WIKI_GUEST_SPACE_RIGHT')
            if not structure.space_is_active(cursor, space_id):
                return jsonify({"error": "Пространство не найдено или в архиве"}), 404
        elif section_id:
            if structure.section_exists(cursor, section_id) is None:
                return jsonify({"error": "Раздел не найден"}), 404
            if not _may_share_section(shareable, section_id):
                return _forbidden('Этот раздел не в вашей ветке отдела',
                                  'WIKI_GUEST_SECTION_SCOPE')
        else:
            sections = wiki_guests.article_section_ids(cursor, article_id)
            if shareable is not None and not (sections & shareable):
                return _forbidden('Эта статья не в вашей ветке отдела',
                                  'WIKI_GUEST_SECTION_SCOPE')

        # ПОЛУЧАТЕЛЬ.
        target_id = _int_or_none(data.get('user_id'))
        if not target_id:
            return jsonify({"error": "Выберите сотрудника"}), 400
        cursor.execute(
            """
            SELECT u.role, u.name, u.status, u.department_id,
                   EXISTS (SELECT 1 FROM departments d
                            WHERE d.head_user_id = u.id AND d.is_active)
              FROM users u WHERE u.id = %s
            """,
            (target_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Сотрудник не найден"}), 404
        target_role, target_name, target_status, target_department, target_heads = row
        if target_status != 'working':
            return jsonify({"error": "Сотрудник не числится работающим"}), 400
        if target_id == ctx['user_id']:
            return _forbidden('Себе гостевой доступ не выдают')
        if not wiki_access.may_grant_guest_to(ctx['otp_role'], target_role,
                                             is_wiki_admin=_is_wiki_admin(ctx)):
            return _forbidden('Этот сотрудник выше вас по оргструктуре — '
                              'гостевой доступ выдают своим подчинённым',
                              'WIKI_GUEST_LADDER')
        if not wiki_access.may_grant_guest_in_department(
                target_department, _departments(ctx)):
            return _forbidden('Этот сотрудник из другого отдела — гостевой '
                              'доступ выдают своим подчинённым',
                              'WIKI_GUEST_DEPARTMENT')

        # СРОК.
        try:
            expires_at = wiki_guests.resolve_expiry(
                wiki_guests.now_almaty(),
                days=data.get('days'), until=data.get('until'),
                at_time=data.get('at_time'))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        reason = (data.get('reason') or '').strip()[:500] or None
        # «Включая подразделы» — только у раздела: ни у статьи, ни у
        # пространства подразделов нет, и сохранённый TRUE на такой строке
        # однажды прочитали бы как признак. Пространство и так открывается
        # целиком — раскрывать в нём нечего.
        deep = bool(data.get('include_subsections', True)) if section_id else False

        grant_id, created = wiki_guests.create_grant(
            cursor, user_id=target_id, section_id=section_id, article_id=article_id,
            space_id=space_id, granted_by=ctx['user_id'], expires_at=expires_at,
            reason=reason, include_subsections=deep)

        queries.log_action(
            cursor, actor_id=ctx['user_id'],
            action='guest.grant' if created else 'guest.extend',
            entity_type=('space' if space_id else 'section' if section_id else 'article'),
            entity_id=space_id or section_id or article_id,
            target_user_id=target_id,
            details={'grant_id': grant_id, 'expires_at': expires_at.isoformat(),
                     'include_subsections': deep, 'reason': reason,
                     'user_name': target_name},
            ip_address=log_ip())

        # Оператору вики открывается только после QR-подтверждения сессии
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
        # Выдачу ПРОСТРАНСТВА мастер-ключ не открывает: продление — это выдача
        # тем же нажатием, и разреши мы её всякому, у кого нет границы отдела,
        # администратор вики раздавал бы пространства продлением того, чего сам
        # выдать не вправе. Ветка стоит ВЫШЕ shareable is None ровно поэтому.
        if grant['space_id']:
            return _may_grant_space(ctx)
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
            # Причины две и объясняются они разным: чужая ветка отдела — одно,
            # выдача целого пространства — другое, и «чужая ветка» на ней
            # читалось бы как поломка (ветки у пространства нет вовсе).
            if grant['space_id']:
                return _forbidden(
                    'Выдачу пространства трогает тот, кто её сделал, '
                    'или супер-администратор', 'WIKI_GUEST_SPACE_RIGHT')
            return _forbidden('Эта выдача относится к чужой ветке отдела',
                              'WIKI_GUEST_SECTION_SCOPE')

        # Тип объекта для журнала — один на обе двери ниже: отзыв и продление
        # пишут его порознь, и разъехаться им негде только пока он считается
        # один раз.
        entity_type = grant['kind']
        entity_id = grant['space_id'] or grant['section_id'] or grant['article_id']

        if request.method == 'DELETE':
            if not wiki_guests.revoke_grant(cursor, grant_id, ctx['user_id']):
                # Строка есть, а отзыв ничего не изменил — значит её уже
                # отозвали. Не ошибка: кнопку нажали дважды.
                return jsonify({"status": "already_revoked"})
            queries.log_action(
                cursor, actor_id=ctx['user_id'], action='guest.revoke',
                entity_type=entity_type, entity_id=entity_id,
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
                days=data.get('days'), until=data.get('until'),
                at_time=data.get('at_time'))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        wiki_guests.extend_grant(cursor, grant_id, expires_at)
        queries.log_action(
            cursor, actor_id=ctx['user_id'], action='guest.extend',
            entity_type=entity_type, entity_id=entity_id,
            target_user_id=grant['user_id'],
            details={'grant_id': grant_id, 'expires_at': expires_at.isoformat()},
            ip_address=log_ip())
        return jsonify({"status": "extended", "expires_at": expires_at.isoformat()})
